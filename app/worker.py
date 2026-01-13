import asyncio
import logging
from datetime import datetime
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
    logger.info("Starting schedule generation task...")
    
    # Load config from DATABASE, not file!
    async for session in get_session():
        from app.services.config_db import get_db_config
        config = await get_db_config(session)
        break
    
    # Get ALL videos from Yandex (list_files doesn't support path filtering)
    folders = config.yandexFolders
    logger.info(f"[Worker] Configured folders (for reference): {folders}")
    logger.info(f"[Worker] Days to generate: {config.daysToGenerate}")
    
    try:
        all_videos = await yandex_service.list_files(limit=100000)
        logger.info(f"[Worker] Fetched {len(all_videos)} total videos from Yandex")
    except Exception as e:
        logger.error(f"Failed to list files: {e}")
        all_videos = []
    
    logger.info(f"[Worker] Total videos collected: {len(all_videos)}")
    logger.info(f"[Worker] Total profiles configured: {len(config.profiles)}")
    
    active_profiles = [p for p in config.profiles if p.enabled]
    logger.info(f"[Worker] Active profiles: {len(active_profiles)}")
    if active_profiles:
        for p in active_profiles[:5]:  # Show first 5
            logger.info(f"  - {p.username}: theme_key='{p.theme_key}', platforms={p.platforms}")

    async for session in get_session():
        scheduler = ContentScheduler(config, session)
        schedule = await scheduler.generate_schedule(all_videos, config.profiles, {})
        logger.info(f"Generated {len(schedule)} posts.")
        
        for post in schedule:
            video = post["video"]
            profile = post["profile"]
            platform = post["platform"]
            publish_time_iso = post["publish_at"]
            publish_dt = datetime.fromisoformat(publish_time_iso)
            
            # Create DB Record as QUEUED
            history = PostingHistory(
                profile_username=profile.username,
                platform=platform,
                video_path=video["path"],
                video_name=video["name"],
                author=extract_author(video["path"]),
                status="queued",
                posted_at=publish_dt,
                meta={"planned": True}
            )
            session.add(history)
            await session.flush()  # get ID
            
            # Schedule async task for future execution
            delay = (publish_dt - datetime.now()).total_seconds()
            if delay > 0:
                asyncio.create_task(
                    schedule_post_with_delay(
                        delay, history.id, video["path"],
                        profile.username, platform, publish_time_iso
                    )
                )
                logger.info(f"Scheduled post for {profile.username} on {platform} at {publish_time_iso} (ID: {history.id})")
            else:
                # Past time - execute immediately
                asyncio.create_task(
                    post_content(history.id, video["path"], profile.username, platform, publish_time_iso)
                )
                logger.info(f"Executing immediate post for {profile.username} on {platform} (ID: {history.id})")
        
        await session.commit()

async def schedule_post_with_delay(delay: float, history_id: int, video_path: str, 
                                   profile_username: str, platform: str, publish_time_iso: str):
    """Wait for specified delay, then execute post."""
    await asyncio.sleep(delay)
    await post_content(history_id, video_path, profile_username, platform, publish_time_iso)

async def post_content(history_id: int, video_path: str, profile_username: str, platform: str, 
                       publish_time_iso: str):
    """Execute single post publication."""
    logger.info(f"Processing post ID {history_id}: {profile_username} on {platform}")
    
    # Load config from DATABASE
    async for session in get_session():
        from app.services.config_db import get_db_config
        config = await get_db_config(session)
        break
    
    brand_name = extract_brand(video_path)
    client_config = next((c for c in config.clients if normalize_client(c.name) == brand_name), None)
    
    caption = ""
    title = ""
    
    if client_config:
        author = extract_author(video_path)
        generated = await content_generator.generate_caption(video_path, platform, client_config, author)
        if generated:
            if platform == 'youtube' and '$$$' in generated:
                parts = generated.split('$$$')
                title = parts[0].strip()
                caption = parts[1].strip() if len(parts) > 1 else ""
            else:
                caption = generated
    else:
        caption = f"{extract_author(video_path)} video #shorts"
    
    # Get download link
    try:
        download_link = await yandex_service.get_download_link(video_path)
    except Exception as e:
        logger.error(f"Failed to get download link for {video_path}: {e}")
        await update_post_status(history_id, "failed", str(e))
        return
    
    # Update status to processing
    await update_post_status(history_id, "processing")
    
    # Publish
    success = False
    error_msg = None
    try:
        resp = await platform_manager.publish_post(
            video_url=download_link,
            caption=caption,
            profile_username=profile_username,
            platform=platform,
            title=title if platform == 'youtube' else None
        )
        if resp and resp.get("success"):
            success = True
            logger.info(f"Successfully published post ID {history_id}")
        else:
            error_msg = resp.get("error") or "Unknown error"
            logger.error(f"Publish failed for post ID {history_id}: {error_msg}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Exception during publish for post ID {history_id}: {e}")
    
    # Update final status
    if success:
        await update_post_status(history_id, "success")
        # Update brand stats
        await increment_brand_stats(video_path)
        # Check cleanup
        asyncio.create_task(check_cleanup(video_path))
    else:
        await update_post_status(history_id, "failed", error_msg)

async def update_post_status(history_id: int, status: str, error_msg: str = None):
    """Update posting history status in DB."""
    async for session in get_session():
        stmt = update(PostingHistory).where(PostingHistory.id == history_id).values(
            status=status,
            meta={"error": error_msg} if error_msg else {}
        )
        await session.execute(stmt)
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
    """Check if all posts for video are done, then delete from Yandex."""
    async for session in get_session():
        stmt = select(PostingHistory).where(PostingHistory.video_path == video_path)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        if not rows:
            return
        
        has_queued = any(r.status == 'queued' for r in rows)
        has_success = any(r.status == 'success' for r in rows)
        
        if not has_queued and has_success:
            logger.info(f"Cleanup: All tasks done for {video_path}. Deleting...")
            try:
                await yandex_service.delete_file(video_path)
                logger.info(f"Cleanup: Deleted {video_path}")
            except Exception as e:
                logger.error(f"Cleanup Failed for {video_path}: {e}")
        break

def normalize_client(name):
    return name.lower().replace(" ", "")
