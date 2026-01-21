import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, update
from app.database import get_session
from app.models import PostingHistory
from app.services.status_checker import upload_status_checker
from app.worker import increment_brand_stats, check_cleanup
from app.services.event_broadcaster import event_broadcaster

logger = logging.getLogger(__name__)

async def status_polling_worker():
    """
    Background worker that polls UploadPost API for async upload statuses.
    Runs every 30 seconds, checks posts with status='processing' and updates them.
    """
    logger.info("[StatusPolling] Worker started")
    
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            
            # Get posts with status='processing' that have request_id or job_id
            async for session in get_session():
                stmt = select(PostingHistory).where(
                    PostingHistory.status == 'processing'
                )
                result = await session.execute(stmt)
                pending_posts = result.scalars().all()
                
                if not pending_posts:
                    # No pending posts, skip
                    break
                
                logger.info(f"[StatusPolling] Checking {len(pending_posts)} pending posts...")
                
                for post in pending_posts:
                    request_id = post.meta.get('request_id') if post.meta else None
                    job_id = post.meta.get('job_id') if post.meta else None
                    
                    if not request_id and not job_id:
                        # Check if stuck (active for > 60 mins without ID)
                        # Worker uses Naive MSK for posted_at, so we compare with Naive MSK
                        now_msk = datetime.utcnow() + timedelta(hours=3)
                        age = now_msk - post.posted_at if post.posted_at else timedelta(hours=999)
                        
                        if age > timedelta(minutes=60):
                            logger.error(f"[StatusPolling] Post #{post.id} stuck (no ID) for {age} - marking failed")
                            
                            new_meta = post.meta.copy() if post.meta else {}
                            new_meta['error'] = 'Stuck in processing (no ID)'
                            
                            stmt = update(PostingHistory).where(PostingHistory.id == post.id).values(
                                status='failed',
                                meta=new_meta
                            )
                            await session.execute(stmt)
                            await session.commit()
                        else:
                            logger.warning(f"[StatusPolling] Post #{post.id} has no tracking ID - skipping")
                        continue
                    
                    # Check status via API
                    try:
                        status_data = await upload_status_checker.check_status(
                            request_id=request_id,
                            job_id=job_id
                        )
                        
                        api_status = status_data.get('status')
                        
                        if api_status == 'completed':
                            # All platforms finished
                            results = status_data.get('results', [])
                            all_success = all(r.get('success') for r in results)
                            
                            if all_success:
                                # Mark as success
                                stmt = update(PostingHistory).where(PostingHistory.id == post.id).values(
                                    status='success'
                                )
                                await session.execute(stmt)
                                await session.commit()
                                
                                logger.info(f"✅ [StatusPolling] Post #{post.id} completed successfully")
                                
                                # Broadcast real-time event
                                await event_broadcaster.broadcast_post_status(
                                    post_id=post.id,
                                    status='success',
                                    meta={'video_path': post.video_path, 'profile': post.profile_username}
                                )
                                
                                # Increment brand stats and trigger cleanup
                                await increment_brand_stats(post.video_path)
                                asyncio.create_task(check_cleanup(post.video_path))
                            else:
                                # Some platforms failed
                                failed_platforms = [r.get('platform') for r in results if not r.get('success')]
                                error_msg = f"Failed on platforms: {', '.join(failed_platforms)}"
                                
                                stmt = update(PostingHistory).where(PostingHistory.id == post.id).values(
                                    status='failed',
                                    meta={**post.meta, 'error': error_msg} if post.meta else {'error': error_msg}
                                )
                                await session.execute(stmt)
                                await session.commit()
                                
                                logger.error(f"❌ [StatusPolling] Post #{post.id} failed: {error_msg}")
                                
                                # Broadcast real-time event
                                await event_broadcaster.broadcast_post_status(
                                    post_id=post.id,
                                    status='failed',
                                    meta={'error': error_msg, 'video_path': post.video_path}
                                )
                        
                        elif api_status == 'in_progress':
                            # Still processing, check again later
                            completed = status_data.get('completed', 0)
                            total = status_data.get('total', 0)
                            logger.info(f"🔄 [StatusPolling] Post #{post.id} in progress ({completed}/{total})")
                            
                            # Broadcast progress update
                            await event_broadcaster.broadcast_post_status(
                                post_id=post.id,
                                status='in_progress',
                                meta={'completed': completed, 'total': total}
                            )
                        
                        elif api_status == 'pending':
                            # Not started yet
                            logger.info(f"⏳ [StatusPolling] Post #{post.id} pending...")
                        
                        elif api_status == 'error':
                            # API error occurred
                            error_msg = status_data.get('error', 'Unknown API error')
                            logger.error(f"❌ [StatusPolling] Error checking post #{post.id}: {error_msg}")
                            # Don't mark as failed yet, might be temporary
                    
                    except Exception as e:
                        logger.error(f"❌ [StatusPolling] Exception checking post #{post.id}: {e}")
                        # Don't mark as failed, will retry on next iteration
                
                break  # Exit async for loop
        
        except Exception as e:
            logger.error(f"[StatusPolling] Worker error: {e}")
            await asyncio.sleep(60)  # Wait longer on error

async def start_status_polling_worker():
    """Start the status polling worker as a background task."""
    asyncio.create_task(status_polling_worker())
    logger.info("[StatusPolling] Background worker launched")
