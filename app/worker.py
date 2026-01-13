import asyncio
import logging
from datetime import datetime
from asgiref.sync import async_to_sync
from app.celery_app import celery_app
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

async def async_generate_schedule():
    logger.info("Starting schedule generation task...")
    config = settings.load_legacy_config()
    folders = config.yandexFolders
    all_videos = []
    
    for folder in folders:
        try:
            files = await yandex_service.list_files(limit=10000)
            for f in files:
                path = f["path"]
                norm_path = path.replace("disk:", "").strip("/")
                norm_folder = folder.replace("disk:", "").strip("/")
                if norm_path.startswith(norm_folder):
                    all_videos.append(f)
        except Exception as e:
            logger.error(f"Failed to list folder {folder}: {e}")

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
            
            # 1. Create DB Record as QUEUED
            # We need a unique way to identify this task later.
            # We can pass the DB ID to the task.
            
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
            await session.flush() # get ID
            
            # 2. Queue Task
            post_content_task.apply_async(
                kwargs={
                    "history_id": history.id,
                    "video_path": video["path"],
                    "profile_username": profile.username,
                    "platform": platform,
                    "publish_time_iso": publish_time_iso
                },
                eta=publish_dt 
            )
            logger.info(f"Queued post for {profile.username} on {platform} at {publish_time_iso} (ID: {history.id})")
        
        await session.commit()

@celery_app.task
def generate_daily_schedule():
    async_to_sync(async_generate_schedule)()

async def async_post_content(history_id: int, video_path: str, profile_username: str, platform: str, 
                             publish_time_iso: str):
    
    logger.info(f"Processing post ID {history_id}: {profile_username} on {platform}")
    config = settings.load_legacy_config()
    
    # helper
    brand_name = extract_brand(video_path)
    client_config = next((c for c in config.clients if normalize_client(c.name) == brand_name), None)
    
    caption = ""
    title = ""
    
    if client_config:
        author = extract_author(video_path)
        generated = await content_generator.generate_caption(video_path, platform, client_config, author)
        if generated:
            if platform == 'youtube' and "$$$" in generated:
                parts = generated.split("$$$")
                title = parts[0].strip()
                caption = parts[1].strip()
            else:
                caption = generated.strip()
    
    if not caption:
        caption = f"{extract_author(video_path)} video #shorts"
    
    publish_dt = datetime.fromisoformat(publish_time_iso)
    
    # Update DB to processing? (Optional, skipping to save write)
    
    success = await platform_manager.publish_post(
        profile_username, platform, video_path, caption, title, publish_dt
    )
    
    status = "success" if success else "failed"
    
    async for session in get_session():
        # Update specific record
        stmt = select(PostingHistory).where(PostingHistory.id == history_id)
        result = await session.execute(stmt)
        record = result.scalars().first()
        
        if record:
            record.status = status
            record.meta = {"caption": caption, "title": title}
            session.add(record)
        
        # Update Brand Stats (only if success)
        if success:
            category = extract_theme(video_path)
            brand = extract_brand(video_path)
            month = datetime.now().strftime("%Y-%m")
            
            stmt = select(BrandStats).where(BrandStats.category == category, BrandStats.brand == brand, BrandStats.month == month)
            result = await session.execute(stmt)
            stat = result.scalars().first()
            
            if not stat:
                stat = BrandStats(category=category, brand=brand, month=month, published_count=0, quota=0)
                session.add(stat)
            
            stat.published_count += 1
            stat.updated_at = datetime.utcnow()
            
        await session.commit()
    
    # Check Cleanup
    check_cleanup_task.delay(video_path)

def normalize_client(name):
    return name.lower().replace(" ", "")

@celery_app.task(bind=True, max_retries=3)
def post_content_task(self, history_id, video_path, profile_username, platform, publish_time_iso):
    try:
        async_to_sync(async_post_content)(history_id, video_path, profile_username, platform, publish_time_iso)
    except Exception as exc:
        logger.error(f"Task failed: {exc}")
        self.retry(exc=exc, countdown=60 * 5)

async def async_check_cleanup(video_path: str):
    async for session in get_session():
        # Check all records for this video that are NOT success
        # If any is 'queued', 'processing', or 'failed' (if we treat failed as 'retryable'?)
        # Let's say: if any is 'queued' -> wait.
        # If all are 'success' -> delete.
        # If mixed 'success' and 'failed'? 
        #   If retries exhausted, 'failed' is final state.
        #   Ideally, we delete if NO 'queued' tasks remain.
        
        stmt = select(PostingHistory).where(PostingHistory.video_path == video_path)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        if not rows: return # Should not happen
        
        has_queued = any(r.status == 'queued' for r in rows)
        has_success = any(r.status == 'success' for r in rows)
        
        if not has_queued and has_success:
            logger.info(f"Cleanup: All tasks done for {video_path}. Deleting...")
            try:
                await yandex_service.delete_file(video_path)
                logger.info(f"Cleanup: Deleted {video_path}")
            except Exception as e:
                logger.error(f"Cleanup Failed for {video_path}: {e}")

@celery_app.task
def check_cleanup_task(video_path):
    async_to_sync(async_check_cleanup)(video_path)
