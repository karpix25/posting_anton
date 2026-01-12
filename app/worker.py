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
from sqlalchemy import select

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
            
            post_content_task.apply_async(
                kwargs={
                    "video_path": video["path"],
                    "profile_username": profile.username,
                    "platform": platform,
                    "publish_time_iso": publish_time_iso
                },
                eta=publish_dt 
            )
            logger.info(f"Queued post for {profile.username} on {platform} at {publish_time_iso}")

@celery_app.task
def generate_daily_schedule():
    async_to_sync(async_generate_schedule)()

async def async_post_content(video_path: str, profile_username: str, platform: str, 
                             publish_time_iso: str, caption: str = "", title: str = ""):
    
    logger.info(f"Processing post: {profile_username} on {platform}")
    config = settings.load_legacy_config()
    
    # helper to find client config
    brand_name = extract_brand(video_path)
    client_config = next((c for c in config.clients if normalize_client(c.name) == brand_name), None)
    
    # Generate content if needed
    if not caption and client_config:
        author = extract_author(video_path)
        generated = await content_generator.generate_caption(video_path, platform, client_config, author)
        if generated:
            if platform == 'youtube' and "$$$" in generated:
                parts = generated.split("$$$")
                title = parts[0].strip()
                caption = parts[1].strip()
            else:
                caption = generated.strip()
    
    # Fallback caption
    if not caption:
        caption = f"{extract_author(video_path)} video #shorts"

    publish_dt = datetime.fromisoformat(publish_time_iso)
    
    success = await platform_manager.publish_post(
        profile_username, platform, video_path, caption, title, publish_dt
    )
    
    status = "success" if success else "failed"
    
    # Log to DB
    async for session in get_session():
        # Log History
        history = PostingHistory(
            profile_username=profile_username,
            platform=platform,
            video_path=video_path,
            video_name=video_path.split("/")[-1],
            author=extract_author(video_path),
            status=status,
            meta={"caption": caption, "title": title}
        )
        session.add(history)
        
        # Update Brand Stats
        category = extract_theme(video_path)
        brand = extract_brand(video_path)
        month = datetime.now().strftime("%Y-%m")
        
        # Upsert logic for BrandStats
        # SQLModel doesn't have native upsert in simple API, need raw SQL or check-update
        # Doing simple check-update for now
        stmt = select(BrandStats).where(BrandStats.category == category, BrandStats.brand == brand, BrandStats.month == month)
        result = await session.execute(stmt)
        stat = result.scalars().first()
        
        if not stat:
            stat = BrandStats(category=category, brand=brand, month=month, published_count=0, quota=0)
            session.add(stat)
        
        if success:
            stat.published_count += 1
            stat.updated_at = datetime.utcnow()
            
        await session.commit()
    
    # Handle Cleanup (Delete file from Yandex) if success?
    # TS code deletes only if ALL platforms success. 
    # Here we have decoupled tasks. 
    # Deletion is tricky in decoupled items.
    # We might need a separate cleanup task that checks "Did all posts for this video succeed?"
    # Or just don't delete for now in this version.
    # User requirement: "High load". Deletion is important to free space.
    # But safer to implement "Cleanup Worker" that runs daily and checks DB for fully published videos.

def normalize_client(name):
    return name.lower().replace(" ", "")

@celery_app.task(bind=True, max_retries=3)
def post_content_task(self, video_path, profile_username, platform, publish_time_iso, caption="", title=""):
    try:
        async_to_sync(async_post_content)(video_path, profile_username, platform, publish_time_iso, caption, title)
    except Exception as exc:
        logger.error(f"Task failed: {exc}")
        self.retry(exc=exc, countdown=60 * 5)
