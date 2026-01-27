import asyncio
import logging
from datetime import datetime
from app.database import get_session, async_session_maker
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
            now = datetime.now()
            # USE SYNC SESSION for locking (async locking with sqlalchemy+asyncpg is tricky, 
            # but we can do it if we are careful. Better to use standard SELECT FOR UPDATE SKIP LOCKED)
            
            async with async_session_maker() as session:
                async with session.begin(): # Start transaction
                    # 1. Select AND Lock rows (SKIP LOCKED ensures we don't block waiting for other workers)
                    # We limit to 5 to match semaphore, so we don't over-flood processing queue
                    stmt = select(PostingHistory).where(
                        PostingHistory.status == "queued",
                        PostingHistory.posted_at <= now
                    ).with_for_update(skip_locked=True).limit(5)
                    
                    result = await session.execute(stmt)
                    due_posts = result.scalars().all()
                    
                    if due_posts:
                        logger.info(f"[BackgroundPublisher] Locked {len(due_posts)} posts for processing")
                        
                        for post in due_posts:
                            # 2. Update status IMMEDIATELY to 'processing' so no one else takes it
                            # even if they ignore locks (safeguard)
                            post.status = 'processing'
                            session.add(post)
                            
                            # 3. Spawn background task
                            # We pass the ID, and worker will re-fetch it.
                            # Since we just marked it 'processing', the worker needs to know that's OK.
                            asyncio.create_task(
                                post_content(
                                    post.id,
                                    post.video_path,
                                    post.profile_username,
                                    post.platform,
                                    post.posted_at.isoformat()
                                )
                            )
                            logger.info(f"[BackgroundPublisher] Spawning task for post #{post.id}")
                        
                        # Commit releases locks, but status is already 'processing'
                        # so they won't be picked up again.
                        await session.commit()
                
            # No break here! We want to keep checking if we processed a batch.
            # But sleep to be nice to CPU
            if not due_posts:
                 await asyncio.sleep(60)
            else:
                 await asyncio.sleep(5) # If we found work, check again sooner
        except Exception as e:
            logger.error(f"[BackgroundPublisher] Error: {e}")
            await asyncio.sleep(60)
