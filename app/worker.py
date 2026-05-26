import asyncio
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
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

CANONICAL_PLATFORMS = {"instagram", "tiktok", "youtube"}
UPLOAD_POST_CONCURRENCY = 1
UPLOAD_POST_MIN_INTERVAL_SECONDS = 3.0
MSK = timezone(timedelta(hours=3))


def _normalize_platform_name(value: str) -> str:
    """Normalize platform labels from UI/API into canonical values."""
    raw = (value or "").strip().lower()
    compact = raw.replace(" ", "").replace("_", "").replace("-", "")
    if compact in {"instagram", "insta", "ig"}:
        return "instagram"
    if compact in {"tiktok", "tt"}:
        return "tiktok"
    if compact in {"youtube", "yt"}:
        return "youtube"
    return raw


def _extract_post_platforms(post: Dict[str, Any]) -> List[str]:
    """Extract all platform names from Upload Post schedule payload."""
    raw_values: List[str] = []

    single = post.get("platform")
    if isinstance(single, str) and single.strip():
        raw_values.append(single)

    plural = post.get("platforms")
    if isinstance(plural, list):
        raw_values.extend([p for p in plural if isinstance(p, str) and p.strip()])
    elif isinstance(plural, str) and plural.strip():
        raw_values.extend([p.strip() for p in plural.split(",") if p.strip()])

    normalized: List[str] = []
    for p in raw_values:
        norm = _normalize_platform_name(p)
        if norm in CANONICAL_PLATFORMS and norm not in normalized:
            normalized.append(norm)

    return normalized


def _summarize_ai_clients_for_log(clients: List[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "name": client.name,
            "regex": client.regex,
            "quota": client.quota,
        }
        for client in clients
    ]


def _summarize_profiles_for_log(profiles: List[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "username": profile.username,
            "theme_key": profile.theme_key,
            "platforms": profile.platforms,
        }
        for profile in profiles
    ]


def _short_youtube_title_from_text(text: str) -> str:
    """Build a compact YouTube title from free-form text."""
    text = _strip_youtube_template_tokens(text)
    clean = re.sub(r"#\S+", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    words = clean.split()
    title = " ".join(words[:7]).strip(" .,!?:;\"'")
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    return title


def _extract_tagged_section(text: str, tag_name: str) -> str:
    match = re.search(rf"(?is)\[{tag_name}\]\s*(.*?)\s*\[/{tag_name}\]", text or "")
    return match.group(1).strip() if match else ""


def _strip_youtube_template_tokens(text: str) -> str:
    text = text or ""
    text = re.sub(r"(?is)\[/?YT_(?:TITLE|DESCRIPTION)\]", " ", text)
    text = re.sub(r"(?i)/?YT_(?:TITLE|DESCRIPTION)\b", " ", text)
    text = re.sub(r"(?im)^\s*(?:YT_TITLE|YT_DESCRIPTION)\s*$", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_youtube_placeholder(text: str) -> bool:
    compact = re.sub(r"[\s\[\]/_:-]+", "", (text or "").strip().lower())
    return compact in {"yttitle", "ytdescription"}


def _clean_social_caption(generated: str) -> str:
    text = (generated or "").strip()
    if not text:
        return ""

    tagged_description = _extract_tagged_section(text, "YT_DESCRIPTION")
    if tagged_description:
        return tagged_description

    if "$$$" in text:
        parts = [p.strip() for p in text.split("$$$") if p.strip()]
        return ("\n\n".join(parts[1:]).strip() if len(parts) > 1 else (parts[0] if parts else text)).strip()

    return _strip_youtube_template_tokens(text)


def _parse_youtube_text(generated: str) -> tuple[str, str]:
    """
    Parse model output into (title, description) with robust fallbacks.
    Supported formats:
    1) [YT_TITLE]...[/YT_TITLE][YT_DESCRIPTION]...[/YT_DESCRIPTION]
    2) TITLE: ... + DESCRIPTION: ...
    3) title $$$ description
    4) first line as title, rest as description
    """
    text = (generated or "").strip()
    if not text:
        return "", ""

    title = ""
    description = ""

    # 1) Preferred tagged format
    tagged = re.search(
        r"(?is)\[YT_TITLE\]\s*(.*?)\s*\[/YT_TITLE\]\s*\[YT_DESCRIPTION\]\s*(.*?)\s*\[/YT_DESCRIPTION\]",
        text
    )
    if tagged:
        title = tagged.group(1).strip()
        description = tagged.group(2).strip()

    # 2) Labeled sections fallback
    if not title and not description:
        labeled = re.search(
            r"(?is)^\s*(?:title|заголовок)\s*[:\-]\s*(.*?)\s*(?:\n+)(?:description|описание)\s*[:\-]\s*(.*)$",
            text
        )
        if labeled:
            title = labeled.group(1).strip()
            description = labeled.group(2).strip()

    # 3) Legacy $$$ split fallback
    if not title and not description and "$$$" in text:
        parts = [p.strip() for p in text.split("$$$") if p.strip()]
        title = parts[0] if parts else ""
        description = "\n\n".join(parts[1:]).strip() if len(parts) > 1 else ""

    # 4) Final fallback: line split
    if not title and not description:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) > 1:
            title = lines[0]
            description = "\n".join(lines[1:]).strip()
        else:
            description = text

    title = _strip_youtube_template_tokens(title)
    description = _strip_youtube_template_tokens(description)
    if _is_youtube_placeholder(title):
        title = ""
    if _is_youtube_placeholder(description):
        description = ""

    # If title clearly looks like full description, move it to description
    looks_like_description = (
        len(title) > 100
        or "#" in title
        or "\n" in title
        or title.count(".") > 1
    )
    if looks_like_description:
        description = f"{title}\n\n{description}".strip() if description else title
        title = ""

    # Ensure we always have a reasonable short title
    if not title:
        title = _short_youtube_title_from_text(description)
    else:
        title = " ".join(title.split()).strip()
        if len(title) > 100:
            title = title[:97].rstrip() + "..."

    return title, description.strip()

async def generate_daily_schedule(test_mode: bool = False, dry_run: bool = False):
    """Main automation function - generates schedule and queues posts."""
    mode_labels = []
    if test_mode:
        mode_labels.append("TEST MODE: 1 post per platform")
    if dry_run:
        mode_labels.append("DRY RUN: no DB writes or UploadPost calls")
    test_str = f" ({'; '.join(mode_labels)})" if mode_labels else ""
    logger.info(f"🚀 [Worker] Starting schedule generation task{test_str}...")
    
    # Load config from DATABASE, not file!
    try:
        async for session in get_session():
            from app.services.config_db import get_db_config
            config = await get_db_config(session)
            break
        logger.info("   ✅ Config loaded from DB")
        logger.info(
            f"[Worker] AI clients configured for matching after Yandex scan "
            f"({len(config.clients)}): {_summarize_ai_clients_for_log(config.clients)}"
        )
    except Exception as e:
        logger.error(f"   ❌ Failed to load config: {e}")
        return
    
    # Get ALL videos from Yandex (list_files doesn't support path filtering)
    folders = list(config.yandexFolders or [])
    logger.info(f"[Worker] Configured folders (for reference): {folders}")
    logger.info(f"[Worker] Days to generate: {config.daysToGenerate}")
    logger.info(f"[Worker] Global limits: IG={config.limits.instagram}, TT={config.limits.tiktok}, YT={config.limits.youtube}")
    
    # Do not auto-add AI client names to the Yandex folder filter.
    # UploadPost profiles are bound to categories, while AI clients match brands
    # by regex after files are scanned. Mixing brand names into the folder filter
    # can exclude valid category files before regex matching ever runs.

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
    # Normalize configured platforms to canonical values.
    for p in active_profiles:
        raw_platforms = p.platforms or []
        normalized = []
        for pl in raw_platforms:
            if not pl:
                continue
            norm = _normalize_platform_name(pl)
            if norm in CANONICAL_PLATFORMS and norm not in normalized:
                normalized.append(norm)
        p.platforms = normalized
    logger.info(f"[Worker] Active profiles: {len(active_profiles)}")
    if active_profiles:
        active_theme_counts = Counter(p.theme_key for p in active_profiles)
        logger.info(f"[Worker] Active profiles by theme before API validation: {dict(active_theme_counts.most_common())}")
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
            # VALUE is now a tuple: (Original_Username, Connected_Set)
            valid_connections[uname.lower()] = (uname, connected)
            
        # Filter active_profiles
        validated_profiles = []
        for p in active_profiles:
            uname_lower = p.username.lower()
            if uname_lower not in valid_connections:
                logger.warning(f"❌ [Worker] Profile '{p.username}' found in config/DB but NOT found in Upload Post API. Skipping.")
                continue
                
            # Retrieve exact API username and connections
            api_username, connected_platforms = valid_connections[uname_lower]
            
            # Key Fix: Overwrite local username with exact API casing
            # This ensures subsequent API calls use 'Pokypki-wb91' instead of 'pokypki-wb91'
            if p.username != api_username:
                logger.info(f"🔧 [Worker] Correcting username case: '{p.username}' -> '{api_username}'")
                p.username = api_username
            
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
        logger.error(f"⚠️ [Worker] Failed to validate profiles with API: {e}. Falling back to cached profile statuses.")
        safe_profiles = []
        for p in active_profiles:
            cached_status = (getattr(p, "profile_status", None) or "unknown").lower()
            if cached_status in {"disconnected", "reauth_required", "not_found"}:
                logger.warning(f"⚠️ [Worker] Skipping '{p.username}' by cached status='{cached_status}'")
                continue
            safe_profiles.append(p)
        active_profiles = safe_profiles

    if active_profiles:
        validated_theme_counts = Counter(p.theme_key for p in active_profiles)
        logger.info(f"[Worker] Validated profiles by theme: {dict(validated_theme_counts.most_common())}")
        logger.info(f"[Worker] Validated profiles for publishing: {_summarize_profiles_for_log(active_profiles)}")

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
            platforms = _extract_post_platforms(post)
            
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

                    for platform in platforms:
                        existing_counts[date_key][profile][platform] = (
                            existing_counts[date_key][profile].get(platform, 0) + 1
                        )
                    
                except Exception as parse_err:
                    logger.warning(f"[Worker] Failed to parse date '{scheduled_date_str}': {parse_err}")
        
        total_occupied = sum(len(v) for v in occupied_slots.values())
        logger.info(f"[Worker] Found {total_occupied} existing scheduled posts across {len(occupied_slots)} profiles.")
    except Exception as e:
        logger.warning(f"[Worker] Failed to fetch scheduled posts: {e} - continuing with empty slots")

    logger.info("[Worker] 🏁 Starting scheduler generation...")

    async for session in get_session():
        scheduler = ContentScheduler(config, session)
        # Use active_profiles (already validated against API) instead of config.profiles
        # Generate Schedule
        force_limit = 1 if test_mode else None
        schedule = await scheduler.generate_schedule(all_videos, active_profiles, occupied_slots, existing_counts, force_limit=force_limit)
        logger.info(f"📅 [Worker] Schedule generated with {len(schedule)} posts")
        if schedule:
            profile_counts = Counter(post["profile"].username for post in schedule)
            platform_counts = Counter(post["platform"] for post in schedule)
            brand_counts = Counter(extract_brand(post["video"]["path"]) for post in schedule)
            theme_counts = Counter(extract_theme(post["video"]["path"], config.themeAliases) for post in schedule)
            logger.info(f"[Worker] Schedule distribution by profile: {dict(profile_counts.most_common())}")
            logger.info(f"[Worker] Schedule distribution by platform: {dict(platform_counts.most_common())}")
            logger.info(f"[Worker] Schedule distribution by theme: {dict(theme_counts.most_common())}")
            logger.info(f"[Worker] Schedule distribution by brand: {dict(brand_counts.most_common())}")
        logger.info(f"✅ [Worker] Generated {len(schedule)} new posts to schedule.")

        if dry_run:
            preview = [
                {
                    "profile": post["profile"].username,
                    "platform": post["platform"],
                    "theme": extract_theme(post["video"]["path"], config.themeAliases),
                    "brand": extract_brand(post["video"]["path"]),
                    "publish_at": post["publish_at"],
                    "video": post["video"]["path"],
                }
                for post in schedule[:20]
            ]
            logger.info(f"🧪 [Worker] Dry run preview (first {len(preview)}/{len(schedule)} posts): {preview}")
            logger.info("🧪 [Worker] Dry run complete - no DB records created and no UploadPost requests sent.")
            return
        
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
            
            logger.info(f"📥 Queued for Upload Post: {profile.username} → {platform} | Brand: {brand_name} | Schedule: {publish_time_iso} (ID: {history.id})")
            
            asyncio.create_task(
                post_content(history.id, video["path"], profile.username, platform, publish_time_iso)
            )
        
        logger.info(f"✅ [Worker] Queued {len(schedule)} posts for Upload Post scheduling")
        logger.info("🏁 [Worker] Schedule generation task finished.")

async def schedule_post_with_delay(delay: float, history_id: int, video_path: str, 
                                   profile_username: str, platform: str, publish_time_iso: str):
    """Wait for specified delay, then execute post."""
    await asyncio.sleep(delay)
    await post_content(history_id, video_path, profile_username, platform, publish_time_iso)

# Global limiters to avoid overwhelming Upload Post and local resources.
deploy_semaphore = asyncio.Semaphore(UPLOAD_POST_CONCURRENCY)
upload_post_rate_lock = asyncio.Lock()
last_upload_post_call_at = 0.0


async def throttle_upload_post_api():
    global last_upload_post_call_at
    async with upload_post_rate_lock:
        now = time.monotonic()
        wait_for = UPLOAD_POST_MIN_INTERVAL_SECONDS - (now - last_upload_post_call_at)
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        last_upload_post_call_at = time.monotonic()

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
    
    client_config = find_ai_client(config.clients, brand_name, video_path)
    
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
            if platform == 'youtube':
                title, caption = _parse_youtube_text(generated)
                if not caption:
                    # Never leave description empty for YouTube
                    caption = _strip_youtube_template_tokens(generated)
                if not title:
                    title = _short_youtube_title_from_text(caption) or "Видео Shorts"
            else:
                caption = _clean_social_caption(generated)
                title = caption

            logger.info(f"   📝 AI Generated: Title='{title[:30]}...', Caption='{caption[:100]}...'")
        else:
            # AI completely failed - use informative fallback
            logger.error(f"   💥 [Post #{history_id}] AI FAILED for brand '{brand_name}' - using fallback")
            caption = f"Новинка от {brand_name}! 🔥 #shorts #новинка #by{author_name.replace(' ', '') if author_name else ''}"
            title = _short_youtube_title_from_text(caption) if platform == 'youtube' else ""
            logger.warning(f"   ⚠️ Using fallback caption")
    else:
        caption = f"{author_name} video #shorts"
        title = _short_youtube_title_from_text(caption) if platform == 'youtube' else ""
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
            logger.info(f"   ⏳ Waiting for Upload Post API throttle (min {UPLOAD_POST_MIN_INTERVAL_SECONDS}s between calls)")
            await throttle_upload_post_api()
            logger.info(f"   📤 Calling Upload Post API with schedule: {schedule_param}")
        else:
            logger.info(f"   ⏳ Waiting for Upload Post API throttle (min {UPLOAD_POST_MIN_INTERVAL_SECONDS}s between calls)")
            await throttle_upload_post_api()
            logger.info(f"   📤 Calling Upload Post API (immediate publish)")
            
        resp = await platform_manager.publish_post(
            video_url=download_link,
            video_path=video_path, # ✅ FIXED: Pass video_path for hashtagging
            caption=caption,
            profile_username=profile_username,
            platform=platform,
            title=title, # ✅ FIXED: Pass title for all platforms (platforms.py will handle)
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
                
                # ✅ Save the ACTUAL strings sent to API so webhook can match them
                # resp is now returning final_caption/final_title
                final_caption = resp.get('final_caption', caption)
                final_title = resp.get('final_title', title)
                
                updated_meta['caption'] = final_caption  
                updated_meta['title'] = final_title
                
                # Also keep original just in case
                updated_meta['original_caption'] = caption
                updated_meta['original_title'] = title
                
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
    month = datetime.now().strftime("%Y-%m")
    
    async for session in get_session():
        # Load aliases from DB to avoid config.json fallback
        from app.services.config_db import get_db_config
        config = await get_db_config(session)
        
        # Compile Regexes for Brand Extraction
        import re
        from app.utils import extract_brand_with_regex
        
        client_regexes = []
        client_regexes = []
        if hasattr(config, "clients") and config.clients:
            # Sort clients by regex length descending to prevent substring collisions
            sorted_clients = sorted(config.clients, key=lambda c: len(c.regex) if c.regex else 0, reverse=True)
            for c in sorted_clients:
                if c.regex:
                    try:
                        client_regexes.append((c.name, re.compile(c.regex, re.IGNORECASE)))
                    except re.error:
                        pass
        
        category = extract_theme(video_path, config.themeAliases)
        brand = extract_brand_with_regex(video_path, client_regexes)
        
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
                # 1. Determine Brand Name (Clean)
                # Load config again to get clients (optimization: could pass config to check_cleanup)
                from app.services.config_db import get_db_config
                config = await get_db_config(session)
                
                raw_brand = extract_brand(video_path)
                client = find_ai_client(config.clients, raw_brand)
                
                brand_folder_name = "Unknown"
                if client:
                    brand_folder_name = client.name.strip() # Use canonical name from AI Client
                else:
                    brand_folder_name = raw_brand.capitalize() if raw_brand != "unknown" else "Other"
                
                # 2. Determine Date Folder
                from datetime import datetime
                date_folder_name = datetime.now().strftime("%Y-%m-%d")
                
                # 3. Construct Destination Path
                # Structure: disk:/опубликовано/<Brand>/<Date>
                brand_path = f"disk:/опубликовано/{brand_folder_name}"
                date_path = f"{brand_path}/{date_folder_name}"
                
                # 4. Check/Create Folders
                # We need to ensure brand folder exists, then date folder exists.
                # yandex_service.move_file ensures the immediate parent exists? 
                # Let's verify yandex_service.move_file implementation or do it manually here for safety.
                # Looking at yandex_service.move_file, it does: if not exists(dest_folder): mkdir(dest_folder).
                # So if we pass date_path as dest_folder, it will try to create it.
                # However, mkdir usually requires parent to exist. 
                # Let's safe-create both levels.
                
                if not await yandex_service.exists("disk:/опубликовано"):
                     # Should exist, but just in case
                     pass 

                if not await yandex_service.exists(brand_path):
                    try:
                        import yadisk
                        async with yadisk.AsyncClient(token=settings.YANDEX_TOKEN) as client:
                            await client.mkdir(brand_path)
                            logger.info(f"[Cleanup] Created brand folder: {brand_path}")
                    except Exception as e:
                        logger.warning(f"[Cleanup] Failed to create brand folder {brand_path}: {e}")

                # Now date path will be handled by move_file logic OR we create it explicitly
                # yandex.py move_file implementation:
                #   if not await client.exists(dest_folder): await client.mkdir(dest_folder)
                # This works if parent exists. Since we just ensured brand_path exists, this should work.
                
                await yandex_service.move_file(video_path, date_path)
                logger.info(f"✅ Cleanup: Archived {video_path} -> {date_path}")
                
            except Exception as e:
                logger.error(f"❌ Cleanup Failed for {video_path}: {e}")
        elif all_completed and not has_any_success:
            logger.warning(f"⚠️ Cleanup: {video_path} has 0 successes (all {total_posts} failed) - NOT archiving")
        else:
            logger.info(f"⏳ Cleanup: {video_path} still has pending posts (queued={queued_count}, processing={processing_count})")
        
        break

def normalize_client(name: str) -> str:
    """Normalize client name for comparison (lowercase, no spaces)"""
    return (name or "").lower().replace(" ", "").replace("-", "")

def find_ai_client(clients, brand_name: str, video_path: str = ""):
    """
    Find AI client using the extracted brand only.
    """
    import re
    
    normalized_brand = normalize_client(brand_name)
    
    for client in clients:
        normalized_client = normalize_client(client.name)

        # Method 1: Exact client name match against extracted brand.
        if normalized_client == normalized_brand:
            return client

        # Method 2: Strict regex match against extracted brand only.
        if client.regex:
            try:
                if re.fullmatch(client.regex, brand_name, re.IGNORECASE):
                    return client
                # app.utils.extract_brand returns normalized brand names, so also
                # test the regex normalized the same way. This remains strict:
                # no path search and no substring matching.
                if re.fullmatch(normalize_client(client.regex), normalized_brand, re.IGNORECASE):
                    return client
            except re.error as e:
                logger.warning(f"Invalid regex for client {client.name}: {client.regex} - {e}")
    
    return None
