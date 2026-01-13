import asyncio
import logging
from datetime import datetime
from app.database import get_session
from app.models import PostingHistory
from sqlalchemy import select
from app.worker import post_content

logger = logging.getLogger(__name__)

async def background_publisher():
    """
    Background worker that checks for queued posts every minute
    and executes them when their time has come.
    """
    logger.info("[BackgroundPublisher] Started - checking queued posts every 60 seconds")
    
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            
            now = datetime.now()
            async for session in get_session():
                # Find queued posts that should be published now
                stmt = select(PostingHistory).where(
                    PostingHistory.status == "queued",
                    PostingHistory.posted_at <= now
                )
                result = await session.execute(stmt)
                due_posts = result.scalars().all()
                
                if due_posts:
                    logger.info(f"[BackgroundPublisher] Found {len(due_posts)} posts ready to publish")
                    
                    for post in due_posts:
                        # Execute post in background
                        asyncio.create_task(
                            post_content(
                                post.id,
                                post.video_path,
                                post.profile_username,
                                post.platform,
                                post.posted_at.isoformat()
                            )
                        )
                        logger.info(f"[BackgroundPublisher] Triggered post #{post.id}")
                
                break
        except Exception as e:
            logger.error(f"[BackgroundPublisher] Error: {e}")
            await asyncio.sleep(60)
