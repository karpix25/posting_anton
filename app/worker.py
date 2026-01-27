import asyncio
import logging
from datetime import datetime
from typing import Dict, List
from app.config import settings
from app.services.yandex import yandex_service
from app.services.scheduler import ContentScheduler
from app.services.platforms import platform_manager
from app.services.content_generator import content_generator
from app.database import get_session
from app.models import PostingHistory, BrandStats
from app.utils import extract_brand, extract_author, extract_theme
from sqlalchemy import select, update

logger = logging.getLogger(__name__)

async def generate_daily_schedule():
    """Main automation function - generates schedule and queues posts."""
    logger.info("🚀 [Worker] Starting schedule generation task (triggered manually)...")
    
    # Load config from DATABASE, not file!
    try:
        async for session in get_session():
            from app.services.config_db import get_db_config
            config = await get_db_config(session)
            break
        logger.info("   ✅ Config loaded from DB")
    except Exception as e:
        logger.error(f"   ❌ Failed to load config: {e}")
        return
    
    # Get ALL videos from Yandex (list_files doesn't support path filtering)
    folders = config.yandexFolders
    logger.info(f"[Worker] Configured folders (for reference): {folders}")
    logger.info(f"[Worker] Days to generate: {config.daysToGenerate}")
    logger.info(f"[Worker] Global limits: IG={config.limits.instagram}, TT={config.limits.tiktok}, YT={config.limits.youtube}")
    
    try:
        # Force refresh for scheduler to get latest files
        # Optimization: Pass folders to only fetch and process what we need
        all_videos = await yandex_service.list_files(limit=100000, force_refresh=True, folders=folders)
        logger.info(f"[Worker] Fetched {len(all_videos)} relevant videos from Yandex (Filtered by {folders})")
    except Exception as e:
        logger.error(f"Failed to list files: {e}")
        all_videos = []
    
    logger.info(f"[Worker] Total videos collected: {len(all_videos)}")
    logger.info(f"[Worker] Total profiles configured: {len(config.profiles)}")
    
    active_profiles = [p for p in config.profiles if p.enabled]
    logger.info(f"[Worker] Active profiles: {len(active_profiles)}")
    # Fetch profiles from API to validate connections
    from app.services.platforms import upload_post_client
    try:
        api_profiles = await upload_post_client.get_profiles()
        logger.info(f"[Worker] Fetched {len(api_profiles)} profiles from API for validation")
        
        # Build map: username -> available platforms (set)
        valid_connections = {}
        for p in api_profiles:
            uname = p.get('username')
            if not uname:
                continue
            
            socials = p.get('social_accounts', {}) or {}
            # A platform is connected if value is truthy (usually a dict or non-empty string)
            # and if it is a dict, it should not have reauth_required=True
            connected = set()
            for plat, val in socials.items():
                if not val:
                    continue
                
                # If it's a dict, check for reauth_required
                if isinstance(val, dict):
                    if val.get('reauth_required') is True:
                        logger.warning(f"⚠️ [Worker] Profile '{uname}' platform '{plat}' requires re-authentication. Skipping.")
                        continue
                
                connected.add(plat.lower())
                
            # Store with lowercase username for case-insensitive matching
            valid_connections[uname.lower()] = connected
            
        # Filter active_profiles
        validated_profiles = []
        for p in active_profiles:
            uname_lower = p.username.lower()
            if uname_lower not in valid_connections:
                logger.warning(f"❌ [Worker] Profile '{p.username}' found in config/DB but NOT found in Upload Post API. Skipping.")
                continue
                
            connected_platforms = valid_connections[uname_lower]
            
            # Check if required platforms are connected
            missing = []
            for req_platform in p.platforms:
                # Platforms in config are usually 'instagram', 'tiktok', 'youtube' (lowercase)
                if req_platform.lower() not in connected_platforms:
                    missing.append(req_platform)
            
            if missing:
                logger.error(f"❌ [Worker] Profile '{p.username}' needs {missing} but they are NOT connected! (Available: {list(connected_platforms)})")
                continue
            
            validated_profiles.append(p)
            
        logger.info(f"[Worker] Profiles after validation: {len(validated_profiles)} (Original: {len(active_profiles)})")
        active_profiles = validated_profiles
        
    except Exception as e:
        logger.error(f"⚠️ [Worker] Failed to validate profiles with API: {e}. Proceeding with config-based list (RISKY).")

    if active_profiles:
        for p in active_profiles[:5]:  # Show first 5 after validation
            logger.info(f"  - {p.username}: theme_key='{p.theme_key}', platforms={p.platforms}")

    # Fetch existing scheduled posts from Upload Post API to avoid conflicts
    from app.services.platforms import upload_post_client
    occupied_slots: Dict[str, List[datetime]] = {}
    existing_counts: Dict[str, Dict[str, Dict[str, int]]] = {}
    
    try:
        scheduled_posts = await upload_post_client.get_scheduled_posts()
        logger.info(f"[Worker] Raw scheduled posts count: {len(scheduled_posts) if scheduled_posts else 0}")
        
        for post in scheduled_posts:
            # Skip if not a dictionary
            if not isinstance(post, dict):
                continue
                
            profile = post.get('profile_username', '')
            scheduled_date_str = post.get('scheduled_date', '')
            platform = post.get('platform') or (post.get('platforms')[0] if post.get('platforms') else 'unknown')
            
            if profile and scheduled_date_str:
                if profile not in occupied_slots:
                    occupied_slots[profile] = []
                try:
                    # Parse ISO datetime
                    scheduled_dt = datetime.fromisoformat(scheduled_date_str.replace('Z', '+00:00'))
                    # Convert to naive datetime for comparison
                    if scheduled_dt.tzinfo:
                        scheduled_dt = scheduled_dt.replace(tzinfo=None)
                    occupied_slots[profile].append(scheduled_dt)
                    
                    # Populate existing_counts for backfilling
                    date_key = scheduled_dt.strftime("%Y-%m-%d")
                    if date_key not in existing_counts:
                         existing_counts[date_key] = {}
                    if profile not in existing_counts[date_key]:
                         existing_counts[date_key][profile] = {}
                    
                    existing_counts[date_key][profile][platform] = existing_counts[date_key][profile].get(platform, 0) + 1
                    
                except Exception as parse_err:
                    logger.warning(f"[Worker] Failed to parse date '{scheduled_date_str}': {parse_err}")
        
        total_occupied = sum(len(v) for v in occupied_slots.values())
        logger.info(f"[Worker] Found {total_occupied} existing scheduled posts across {len(occupied_slots)} profiles.")
    except Exception as e:
        logger.warning(f"[Worker] Failed to fetch scheduled posts: {e} - continuing with empty slots")

    logger.info("[Worker] 🏁 Starting scheduler generation...")

    async for session in get_session():
        scheduler = ContentScheduler(config, session)
        schedule = await scheduler.generate_schedule(all_videos, config.profiles, occupied_slots, existing_counts)
        logger.info(f"✅ [Worker] Generated {len(schedule)} new posts to schedule.")
        
        for post in schedule:
            video = post["video"]
            profile = post["profile"]
            platform = post["platform"]
            publish_time_iso = post["publish_at"]
            publish_dt = datetime.fromisoformat(publish_time_iso)
            
            # Remove timezone info for database (TIMESTAMP WITHOUT TIME ZONE)
            if publish_dt.tzinfo is not None:
                publish_dt = publish_dt.replace(tzinfo=None)
            
            # Extract brand for logging
            brand_name = extract_brand(video["path"])
            author_name = extract_author(video["path"])
            
            # Create DB Record as QUEUED (Upload Post will schedule it)
            history = PostingHistory(
                profile_username=profile.username,
                platform=platform,
                video_path=video["path"],
                video_name=video["name"],
                author=author_name,
                status="queued",
                posted_at=publish_dt,
                meta={"planned": True, "brand": brand_name}
            )
            session.add(history)
            await session.commit()  # Commit immediately so it's visible to background workers
            
            # Send to Upload Post API with scheduled time
            logger.info(f"📤 Sending to Upload Post: {profile.username} → {platform} | Brand: {brand_name} | Schedule: {publish_time_iso} (ID: {history.id})")
            
            asyncio.create_task(
                post_content(history.id, video["path"], profile.username, platform, publish_time_iso)
            )
        
        logger.info(f"✅ [Worker] Sent {len(schedule)} posts to Upload Post API with scheduling")
        logger.info("🏁 [Worker] Schedule generation task finished.")

async def schedule_post_with_delay(delay: float, history_id: int, video_path: str, 
                                   profile_username: str, platform: str, publish_time_iso: str):
    """Wait for specified delay, then execute post."""
    await asyncio.sleep(delay)
    await post_content(history_id, video_path, profile_username, platform, publish_time_iso)

# Global concurrency limiter to prevent "Too many open files"
# Limits concurrent execution of post_content (DB connections + HTTP requests)
deploy_semaphore = asyncio.Semaphore(5)

async def post_content(history_id: int, video_path: str, profile_username: str, platform: str, 
                       publish_time_iso: str):
    """Execute single post publication."""
    # Acquire semaphore to limit concurrency
    async with deploy_semaphore:
        return await _post_content_impl(history_id, video_path, profile_username, platform, publish_time_iso)

async def _post_content_impl(history_id: int, video_path: str, profile_username: str, platform: str, 
                       publish_time_iso: str):
    """Internal implementation of post_content."""
    brand_name = extract_brand(video_path)
    author_name = extract_author(video_path)
    
    logger.info(f"🚀 [Post #{history_id}] Starting publication:")
    logger.info(f"   Profile: {profile_username}")
    logger.info(f"   Platform: {platform}")
    logger.info(f"   Brand: {brand_name}")
    logger.info(f"   Author: {author_name}")
    logger.info(f"   Video: {video_path}")
    
    # Load config from DATABASE
    async for session in get_session():
        from app.services.config_db import get_db_config
        config = await get_db_config(session)
        break
    
    client_config = find_ai_client(config.clients, brand_name)
    
    if client_config:
        logger.info(f"   ✅ AI Client found: {client_config.name}")
    else:
        logger.warning(f"   ⚠️ No AI client for brand '{brand_name}' - using default")
    
    caption = ""
    title = ""
    
    if client_config:
        # Try AI generation with retries
        generated = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                logger.info(f"   🤖 AI Generation attempt {attempt + 1}/{max_retries}...")
                generated = await content_generator.generate_caption(video_path, platform, client_config, author_name)
                
                if generated and len(generated.strip()) > 10:  # Minimum quality check
                    break
                else:
                    logger.warning(f"   ⚠️ AI returned empty/short caption, retrying...")
                    generated = None
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
            except Exception as e:
                logger.error(f"   ❌ AI generation error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"   💥 AI generation FAILED after {max_retries} attempts")
        
        if generated:
            if platform == 'youtube' and '$$$' in generated:
                parts = generated.split('$$$')
                title = parts[0].strip()
                caption = parts[1].strip() if len(parts) > 1 else ""
            else:
                caption = generated
            logger.info(f"   📝 AI Generated caption ({len(caption)} chars): {caption[:100]}...")
        else:
            # AI completely failed - use informative fallback
            logger.error(f"   💥 [Post #{history_id}] AI FAILED for brand '{brand_name}' - using fallback")
            caption = f"Новинка от {brand_name}! 🔥 #shorts #новинка #by{author_name.replace(' ', '') if author_name else ''}"
            logger.warning(f"   ⚠️ Using fallback caption")
    else:
        caption = f"{author_name} video #shorts"
        logger.info(f"   📝 Using default caption (no AI client)")
    
    # Get download link
    try:
        download_link = await yandex_service.get_download_link(video_path)
        logger.info(f"   ⬇️ Download link obtained")
    except Exception as e:
        logger.error(f"   ❌ Failed to get download link: {e}")
        await update_post_status(history_id, "failed", str(e))
        return
    
    # Update status to processing
    await update_post_status(history_id, "processing")
    
    # Parse scheduled time
    # The scheduler generates time in User's timezone (Moscow, UTC+3) but stores as naive ISO
    # We need to explicitely handle this conversion for UploadPost API which likely expects UTC or specific ISO
    from datetime import timezone, timedelta
    
    MSK = timezone(timedelta(hours=3))
    publish_dt_naive = datetime.fromisoformat(publish_time_iso.replace('Z', ''))
    
    # Treat as Moscow Time
    publish_dt_msk = publish_dt_naive.replace(tzinfo=MSK)
    
    # Convert to UTC for API consistency
    publish_dt_utc = publish_dt_msk.astimezone(timezone.utc)
    
    now_utc = datetime.now(timezone.utc)
    
    # Only send scheduled_date if it's in the future
    schedule_param = None
    if publish_dt_utc > now_utc:
        schedule_param = publish_dt_utc
        # Format strictly as ISO 8601 with Z (JS style) for API
        # .isoformat() might use +00:00, let's force Z if needed or standard isoformat() is usually fine if API parses standard ISO. 
        # But to be safe and match TS toISOString(), we use Z.
        schedule_str_log = publish_dt_utc.isoformat().replace('+00:00', 'Z')
        logger.info(f"   ⏰ Will schedule for: {schedule_str_log} (UTC) | Local was: {publish_time_iso}")
    else:
        logger.info(f"   ▶️ Publishing immediately (scheduled time {publish_time_iso} MSK passed)")
    
    # Publish via Upload Post API with scheduled time (if future)
    success = False
    error_msg = None
    try:
        if schedule_param:
            logger.info(f"   📤 Calling Upload Post API with schedule: {schedule_param}")
        else:
            logger.info(f"   📤 Calling Upload Post API (immediate publish)")
            
        resp = await platform_manager.publish_post(
            video_url=download_link,
            caption=caption,
            profile_username=profile_username,
            platform=platform,
            title=title if platform == 'youtube' else None,
            publish_at=schedule_param  # datetime with tzinfo
        )
        if resp and resp.get("success"):
            success = True
            # Extract tracking IDs for async uploads
            request_id = resp.get('request_id')
            job_id = resp.get('job_id')
            logger.info(f"   ✅ Async request accepted (request_id={request_id}, job_id={job_id})")
        else:
            error_msg = resp.get("error") or "Unknown error"
            logger.error(f"   ❌ Upload Post API failed: {error_msg}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"   ❌ Exception during publish: {e}")
    
    # Update status based on async response
    if success:
        # Save tracking IDs to meta for status polling
        async for session in get_session():
            # Fetch existing post to preserve meta fields
            stmt_get = select(PostingHistory).where(PostingHistory.id == history_id)
            result = await session.execute(stmt_get)
            post = result.scalar_one_or_none()
            
            if post:
                # Preserve existing meta and add tracking IDs + caption
                updated_meta = post.meta.copy() if post.meta else {}
                updated_meta['request_id'] = request_id
                updated_meta['job_id'] = job_id
                updated_meta['caption'] = caption  # ✅ Save caption for webhook matching
                
                # ✅ DEBUG: Log what we're saving
                logger.info(f"   💾 [Post #{history_id}] Saving caption to DB ({len(caption)} chars): '{caption[:100]}...'")
                logger.info(f"   💾 [Post #{history_id}] Meta keys: {list(updated_meta.keys())}")
                
                stmt = update(PostingHistory).where(PostingHistory.id == history_id).values(
                    status='processing',  # Will be updated by status_checker
                    meta=updated_meta
                )
                await session.execute(stmt)
                await session.commit()
            break
        
        logger.info(f"🔄 [Post #{history_id}] Async upload initiated - will be checked by background worker")
    else:
        await update_post_status(history_id, "failed", error_msg)
        logger.error(f"💥 [Post #{history_id}] Publication FAILED: {error_msg}")

async def update_post_status(history_id: int, status: str, error_msg: str = None):
    """Update posting history status in DB."""
    async for session in get_session():
        # Fetch first to preserve existing meta values
        stmt_get = select(PostingHistory).where(PostingHistory.id == history_id)
        result = await session.execute(stmt_get)
        post = result.scalar_one_or_none()
        
        if post:
            new_meta = post.meta.copy() if post.meta else {}
            if error_msg:
                new_meta["error"] = error_msg
            
            post.status = status
            post.meta = new_meta
            session.add(post)
            await session.commit()
        break

async def increment_brand_stats(video_path: str):
    """Increment published count for brand in current month."""
    category = extract_theme(video_path)
    brand = extract_brand(video_path)
    month = datetime.now().strftime("%Y-%m")
    
    async for session in get_session():
        stmt = select(BrandStats).where(
            BrandStats.category == category,
            BrandStats.brand == brand,
            BrandStats.month == month
        )
        result = await session.execute(stmt)
        stat = result.scalar_one_or_none()
        
        if stat:
            stat.published_count += 1
        else:
            stat = BrandStats(category=category, brand=brand, month=month, published_count=1, quota=0)
            session.add(stat)
        
        await session.commit()
        break

async def check_cleanup(video_path: str):
    """Check if all posts for video are done, then archive if at least 1 succeeded."""
    async for session in get_session():
        stmt = select(PostingHistory).where(PostingHistory.video_path == video_path)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        if not rows:
            return
        
        # Check statuses
        total_posts = len(rows)
        queued_count = sum(1 for r in rows if r.status == 'queued')
        processing_count = sum(1 for r in rows if r.status == 'processing')
        success_count = sum(1 for r in rows if r.status == 'success')
        failed_count = sum(1 for r in rows if r.status == 'failed')
        
        logger.info(f"Cleanup check for {video_path}: Total={total_posts}, Queued={queued_count}, Processing={processing_count}, Success={success_count}, Failed={failed_count}")
        
        # Archive if: all posts completed AND at least 1 success
        all_completed = (queued_count == 0 and processing_count == 0)
        has_any_success = (success_count > 0)
        
        if all_completed and has_any_success:
            logger.info(f"✅ Cleanup: {success_count}/{total_posts} posts successful for {video_path}. Moving to archive...")
            try:
                await yandex_service.move_file(video_path, "disk:/опубликовано")
                logger.info(f"✅ Cleanup: Archived {video_path}")
            except Exception as e:
                logger.error(f"❌ Cleanup Failed for {video_path}: {e}")
        elif all_completed and not has_any_success:
            logger.warning(f"⚠️ Cleanup: {video_path} has 0 successes (all {total_posts} failed) - NOT archiving")
        else:
            logger.info(f"⏳ Cleanup: {video_path} still has pending posts (queued={queued_count}, processing={processing_count})")
        
        break

def normalize_client(name: str) -> str:
    """Normalize client name for comparison (lowercase, no spaces)"""
    return name.lower().replace(" ", "").replace("-", "")

def find_ai_client(clients, brand_name: str):
    """
    Find AI client for brand using:
    1. Exact name match (normalized)
    2. Regex pattern match (if regex field is set)
    """
    import re
    
    normalized_brand = normalize_client(brand_name)
    
    for client in clients:
        # Method 1: Exact name match (normalized)
        if normalize_client(client.name) == normalized_brand:
            return client
        
        # Method 2: Regex match
        if client.regex:
            try:
                if re.search(client.regex, brand_name, re.IGNORECASE):
                    return client
            except re.error as e:
                logger.warning(f"Invalid regex for client {client.name}: {client.regex} - {e}")
    
    return None

