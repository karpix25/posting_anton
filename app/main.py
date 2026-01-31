import json
import os
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings, LegacyConfig
from app.database import get_session, init_db
from app.models import BrandStats, PostingHistory
from app.services.yandex import yandex_service
from app.utils import extract_theme, extract_brand, extract_author, normalize_theme_key
from app.logging_conf import setup_logging
from app.services.dynamic_scheduler import dynamic_scheduler
from app.services.event_broadcaster import event_broadcaster

app = FastAPI(title="Automation Dashboard API", version="2.0.0")

# Setup Logging
logger = setup_logging()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.services.config_db import migrate_file_to_db, get_db_config, save_db_config

# Startup event
@app.on_event("startup")
async def on_startup():
    logger.info("Application starting up...")
    await init_db()
    await migrate_file_to_db()
    
    # Log current schedule state and Force-Check Clients
    try:
        from app.services.config_db import get_db_config, save_db_config
        from app.database import async_session_maker
        from app.seed_data import CLIENTS_SEED
        
        async with async_session_maker() as session:
             cfg = await get_db_config(session)
             
             # AGGRESSIVE AUTO-SEED
             if not cfg.clients and CLIENTS_SEED:
                 logger.warning(f"⚠️ Startup: No clients found in DB. Injecting {len(CLIENTS_SEED)} default clients...")
                 cfg_dict = cfg.dict()
                 cfg_dict["clients"] = CLIENTS_SEED
                 await save_db_config(session, cfg_dict)
                 logger.info("✅ Startup: Injected default clients.")
                 # Re-read to confirm for logging
                 cfg = await get_db_config(session)

             logger.info(f"✅ System Ready. Current Schedule: {cfg.cronSchedule or 'Disabled'}. Clients: {len(cfg.clients)}")
             
             # Start dynamic scheduler
             dynamic_scheduler.start()
             
             # Start background publisher for queued posts
             from app.background_publisher import background_publisher
             asyncio.create_task(background_publisher())
             logger.info("🚀 Started background post publisher")
             
             # Status polling worker DISABLED - using webhook instead
             # Webhook is more efficient and prevents duplicate processing
             # from app.services.status_polling import start_status_polling_worker
             # await start_status_polling_worker()
             logger.info("✅ Using webhook for status updates (polling disabled)")
             
    except Exception as e:
        logger.error(f"Startup failed: {e}")

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}

@app.get("/api/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    config = await get_db_config(session)
    return {"success": True, "config": config.dict()}

@app.post("/api/config")
async def update_config(config_data: Dict[str, Any], session: AsyncSession = Depends(get_session)):
    import logging
    logger = logging.getLogger("app.main")
    # Log critical sections of payload to debug "wiping" issues
    clients_len = len(config_data.get("clients", [])) if "clients" in config_data else "MISSING"
    profiles_len = len(config_data.get("profiles", [])) if "profiles" in config_data else "MISSING"
    logger.info(f"Update Config Payload: Clients={clients_len}, Profiles={profiles_len}")

    # Sync 'clients' quotas to 'brandQuotas' for scheduler compatibility
    if "clients" in config_data:
        if "brandQuotas" not in config_data:
            config_data["brandQuotas"] = {}
            
        for client in config_data["clients"]:
            name = client.get("name", "")
            quota = client.get("quota")
            if quota is None: quota = 0
            regex = client.get("regex", "")
            
            # Try to extract category from regex e.g. /Category/Brand
            category = "unknown"
            if regex:
                parts = regex.replace("\\", "/").split("/")
                # heuristic: find part that is not 'Brand'
                # If regex is simple path: /Videos/Category/Brand
                if len(parts) >= 3:
                     # e.g. ['', 'Videos', 'Category', 'Brand']
                     category = parts[-2]
            
            # Normalize
            if category: 
                 category = category.lower().strip()
                 # Update map
                 if category not in config_data["brandQuotas"]:
                     config_data["brandQuotas"][category] = {}
                 
                 # Clean brand name
                 brand_clean = name.lower().replace(" ", "")
                 config_data["brandQuotas"][category][brand_clean] = quota

    try:
        await save_db_config(session, config_data)
        return {"success": True, "message": "Config saved to DB"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/profiles/sync")
async def sync_profiles_endpoint():
    """Trigger safe synchronization of profiles with API."""
    from app.services.sync_profiles import sync_profiles_service
    try:
        stats = await sync_profiles_service()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return {"success": False, "error": str(e)}

# Global Cache
_files_cache = []
_files_cache_timestamp = None

@app.get("/api/stats")
async def get_stats(refresh: bool = False, session: AsyncSession = Depends(get_session)):
    global _files_cache, _files_cache_timestamp
    
    # 1. Load config
    config = await get_db_config(session)
    
    files = []
    
    # 2. Determine source of files
    if refresh:
        # FORCE REFRESH: Scan Yandex Disk
        try:
            files = await yandex_service.list_files(limit=100000, force_refresh=True)
            _files_cache = files
            _files_cache_timestamp = datetime.now()
        except Exception as e:
            logger.error(f"Yandex Scan Failed: {e}")
            # Fallback to DB cache if scan fails
            if config.cached_yandex_stats and "files" in config.cached_yandex_stats:
                files = config.cached_yandex_stats["files"]
    else:
        # NO REFRESH: Try memory cache -> DB cache -> Return empty
        if _files_cache:
            files = _files_cache
        elif config.cached_yandex_stats and "files" in config.cached_yandex_stats:
             files = config.cached_yandex_stats["files"]
             # Restore memory cache
             _files_cache = files
        else:
            files = []
            
    # 3. Calculate Stats
    # 3. Calculate Stats
    stats = {
        "totalVideos": 0,
        "publishedCount": 0, 
        "byCategory": {},
        "byAuthor": {},
        "byBrand": {},
        "byAuthorBrand": {},  
        "profilesByCategory": {},
        "publishedByCategory": {}, # New field
        "publishedByBrand": {}     # New field for future use
    }
    
    # ... (skipping files empty check logic update: we need it later)
    
    if not files:
         # Fill profilesByCategory even if no files
         for p in config.profiles:
            if p.theme_key:
                # FIX: Use normalize_theme_key so it matches extract_theme results!
                tk = normalize_theme_key(p.theme_key, config.themeAliases)
                if tk not in stats["profilesByCategory"]:
                    stats["profilesByCategory"][tk] = []
                stats["profilesByCategory"][tk].append(p.username)
    else:
        # Filter and Aggregate
        config_folders_norm = [f.replace("disk:", "").strip("/").lower() for f in config.yandexFolders]
        
        for f in files:
            path = f["path"]
            path_norm = path.replace("disk:", "").strip("/").lower()
            
            # Check folder
            in_folder = False
            for folder in config_folders_norm:
                if path_norm.startswith(folder):
                    in_folder = True
                    break
            
            if not in_folder: continue
            
            stats["totalVideos"] += 1
            
            theme = extract_theme(path, config.themeAliases)
            author = extract_author(path)
            brand = extract_brand(path)
            
            if theme != "unknown":
                stats["byCategory"][theme] = stats["byCategory"].get(theme, 0) + 1
            
            if author != "unknown":
                stats["byAuthor"][author] = stats["byAuthor"].get(author, 0) + 1
                
                # Track brand breakdown per author
                if author not in stats["byAuthorBrand"]:
                    stats["byAuthorBrand"][author] = {}
                if brand != "unknown":
                    stats["byAuthorBrand"][author][brand] = stats["byAuthorBrand"][author].get(brand, 0) + 1
                
            if brand != "unknown":
                stats["byBrand"][brand] = stats["byBrand"].get(brand, 0) + 1

        # Profiles mapping (Post-loop)
        for p in config.profiles:
            if p.theme_key:
                # FIX: Use normalize_theme_key
                tk = normalize_theme_key(p.theme_key, config.themeAliases)
                if tk not in stats["profilesByCategory"]:
                    stats["profilesByCategory"][tk] = []
                stats["profilesByCategory"][tk].append(p.username)
        
    # NEW: Calculate Published Stats from DB
    stmt_pub = select(PostingHistory.video_path).where(PostingHistory.status == 'success')
    res_pub = await session.execute(stmt_pub)
    published_paths = res_pub.scalars().all()
    
    stats["publishedCount"] = len(published_paths)
    
    stats["publishedByAuthor"] = {}
    stats["publishedByBrand"] = {}
    
    for path in published_paths:
        theme = extract_theme(path, config.themeAliases)
        author = extract_author(path)
        brand = extract_brand(path)
        
        if theme != "unknown":
            stats["publishedByCategory"][theme] = stats["publishedByCategory"].get(theme, 0) + 1
            
        if author != "unknown":
             stats["publishedByAuthor"][author] = stats["publishedByAuthor"].get(author, 0) + 1
             
        if brand != "unknown":
             stats["publishedByBrand"][brand] = stats["publishedByBrand"].get(brand, 0) + 1
            
    # 4. Save to DB if refreshed (and successful)
    if refresh:
        try:
            # We save the FILES list to cache, so we can re-process them later even if config changes
            # Saving entire stats object is also fine, but saving files is more flexible.
            # Warning: Files list can be large. 
            # If files list > 1MB, it might be heavy for DB row. 
            # Optimization: Save only necessary paths.
            simple_files = [{"path": f["path"]} for f in files]
            new_cache = {"files": simple_files, "updated_at": datetime.now().isoformat()}

            # Fix: Update DB directly using AppSettings model (config is Pydantic, cannot be added to session)
            from sqlalchemy import update
            from app.models import AppSettings
            
            stmt = update(AppSettings).where(AppSettings.id == 1).values(cached_yandex_stats=new_cache)
            await session.execute(stmt)
            await session.commit()
            
            config.cached_yandex_stats = new_cache
            logger.info("✅ Saved Yandex Disk cache to DB")
        except Exception as e:
            logger.error(f"Failed to save DB cache: {e}")

    return stats

@app.get("/api/brands/stats")
async def get_brand_stats(month: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    target_month = month or datetime.now().strftime("%Y-%m")
    
    stmt = select(BrandStats).where(BrandStats.month == target_month)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    
    stats = {}
    for r in rows:
        key = f"{r.category}:{r.brand}"
        stats[key] = {"published_count": r.published_count, "quota": r.quota}
        
    return {"success": True, "stats": stats, "month": target_month}

@app.post("/api/config/restore-defaults")
async def restore_defaults(session: AsyncSession = Depends(get_session)):
    """Force restores client prompts from seed data."""
    try:
        from app.seed_data import DEFAULT_CLIENTS
        from app.services.config_db import get_db_config, save_db_config
        
        config = await get_db_config(session)
        config_dict = config.dict()
        
        # Replace clients
        config_dict['clients'] = [c.dict() for c in DEFAULT_CLIENTS]
        
        await save_db_config(session, config_dict)
        return {"success": True, "message": "Restored default AI clients"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
@app.post("/api/brands/quotas")
async def update_brand_quota(
    payload: Dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session)
):
    category = payload.get("category")
    brand = payload.get("brand")
    quota = payload.get("quota")
    
    if not category or not brand or quota is None:
        raise HTTPException(status_code=400, detail="Missing fields")
        
    month = datetime.now().strftime("%Y-%m")
    
    # Upsert
    stmt = select(BrandStats).where(BrandStats.category == category, BrandStats.brand == brand, BrandStats.month == month)
    result = await session.execute(stmt)
    stat = result.scalars().first()
    
    if stat:
        stat.quota = quota
        stat.updated_at = datetime.utcnow()
    else:
        stat = BrandStats(category=category, brand=brand, month=month, quota=quota, published_count=0)
        session.add(stat)
        
    await session.commit()
    
    # Updated DB Only (Legacy config sync removed)

    return {"success": True, "message": f"Updated quota for {category}:{brand} to {quota}"}

@app.get("/api/schedule")
async def get_schedule(session: AsyncSession = Depends(get_session)):
    config = await get_db_config(session)
    cron = config.cronSchedule or ""
    logger.info(f"[API] get_schedule loaded cron: '{cron}'")
    
    # Default state
    enabled = False
    daily_time = "00:00"
    timezone = "Europe/Moscow" # Hardcode or add to config if needed
    
    # Parse Cron: "min hour * * *"
    # Simple check: does it have 5 parts?
    parts = cron.split(" ")
    if len(parts) >= 5:
        # Check if it looks like a daily schedule: "* * * * *" is not enabled per se, but "M H * * *" is.
        # We assume if it's set, it's enabled.
        enabled = True
        try:
            minute = parts[0].zfill(2)
            hour = parts[1].zfill(2)
            daily_time = f"{hour}:{minute}"
        except:
            pass
            
    return {
        "enabled": enabled,
        "dailyRunTime": daily_time,
        "timezone": timezone
    }

@app.post("/api/schedule")
async def save_schedule(payload: Dict[str, Any] = Body(...), session: AsyncSession = Depends(get_session)):
    enabled = payload.get("enabled", False)
    daily_time = payload.get("dailyRunTime", "00:00")
    
    # get current config data (dict)
    current_config_obj = await get_db_config(session)
    data = current_config_obj.dict()

    if enabled:
        # Convert HH:MM to Cron
        try:
            h, m = daily_time.split(":")
            # Cron: m h * * *
            # Note: We save literal user time (e.g. 05:30 -> 30 5 * * *)
            # The DynamicScheduler now explicitly checks against Europe/Moscow time.
            new_cron = f"{int(m)} {int(h)} * * *"
            data["cronSchedule"] = new_cron
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid time format")
    else:
        # Disable
        data["cronSchedule"] = ""

    # Persist
    try:
        await save_db_config(session, data)
        return {"success": True, "message": "Schedule updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats/today")
async def get_today_stats(session: AsyncSession = Depends(get_session)):
    """Get statistics for today's publications (Moscow timezone)."""
    try:
        from datetime import timezone, timedelta
        from sqlalchemy import func, distinct
        from app.models import PostingHistory
        
        # Moscow timezone (UTC+3)
        MSK = timezone(timedelta(hours=3))
        now_msk = datetime.now(MSK)
        today_start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_msk = now_msk.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Convert to UTC for database query
        today_start_utc = today_start_msk.astimezone(timezone.utc).replace(tzinfo=None)
        today_end_utc = today_end_msk.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Count successful posts today
        success_stmt = select(func.count(PostingHistory.id)).where(
            PostingHistory.status == "success",
            PostingHistory.posted_at >= today_start_utc,
            PostingHistory.posted_at <= today_end_utc
        )
        success_result = await session.execute(success_stmt)
        success_count = success_result.scalar() or 0
        
        # Count unique profiles with successful posts today
        profiles_stmt = select(func.count(distinct(PostingHistory.profile_username))).where(
            PostingHistory.status == "success",
            PostingHistory.posted_at >= today_start_utc,
            PostingHistory.posted_at <= today_end_utc
        )
        profiles_result = await session.execute(profiles_stmt)
        profiles_count = profiles_result.scalar() or 0
        
        # Count failed posts today
        failed_stmt = select(func.count(PostingHistory.id)).where(
            PostingHistory.status == "failed",
            PostingHistory.posted_at >= today_start_utc,
            PostingHistory.posted_at <= today_end_utc
        )
        failed_result = await session.execute(failed_stmt)
        failed_count = failed_result.scalar() or 0
        
        # Count queued posts
        queued_stmt = select(func.count(PostingHistory.id)).where(
            PostingHistory.status == "queued"
        )
        queued_result = await session.execute(queued_stmt)
        queued_count = queued_result.scalar() or 0
        
        return {
            "date": now_msk.strftime("%d.%m.%Y"),
            "time_msk": now_msk.strftime("%H:%M"),
            "success_count": success_count,
            "failed_count": failed_count,
            "queued_count": queued_count,
            "profiles_count": profiles_count
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return {
            "date": datetime.now().strftime("%d.%m.%Y"),
            "time_msk": "??:??",
            "success_count": 0,
            "failed_count": 0,
            "queued_count": 0,
            "profiles_count": 0
        }

@app.get("/api/stats/publishing")
async def get_publishing_stats(
    date_from: Optional[str] = None, 
    date_to: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Get comprehensive publishing statistics for all posts with optional date filtering."""
    try:
        from sqlalchemy import func, and_
        from app.models import PostingHistory
        from app.services.config_db import get_db_config
        from datetime import datetime, timezone, timedelta
        
        # Get total profiles count
        config = await get_db_config(session)
        total_profiles = len(config.profiles)
        active_profiles = len([p for p in config.profiles if p.enabled])
        
        # Date filtering conditions
        # We assume dates come as YYYY-MM-DD strings in local time (or user intent)
        # DB stores naive UTC (usually). If using naive, we compare directly or ensuring consistency.
        filters = []
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from)
                filters.append(PostingHistory.posted_at >= dt_from)
            except ValueError:
                pass # Ignore invalid dates
        
        if date_to:
            try:
                # Add one day to include the end date fully if it's just a date
                dt_to = datetime.fromisoformat(date_to) + timedelta(days=1)
                filters.append(PostingHistory.posted_at < dt_to)
            except ValueError:
                pass

        # Count posts by status
        status_counts = {}
        for status in ["queued", "processing", "success", "failed"]:
            stmt = select(func.count(PostingHistory.id)).where(
                PostingHistory.status == status,
                *filters
            )
            result = await session.execute(stmt)
            status_counts[status] = result.scalar() or 0
        
        # Count posts by platform
        platform_counts = {}
        for platform in ["instagram", "tiktok", "youtube"]:
            stmt = select(func.count(PostingHistory.id)).where(
                PostingHistory.platform == platform,
                *filters
            )
            result = await session.execute(stmt)
            platform_counts[platform] = result.scalar() or 0
        
        # Calculate total expected posts (approximation based on active profiles and platform limits)
        # Expected is trickier with date range. 
        # If date range is set, we can estimate: (days in range) * (daily limit).
        # If no date range, use config.daysToGenerate (future buffer).
        total_expected = 0
        
        if date_from and date_to:
             try:
                d1 = datetime.fromisoformat(date_from)
                d2 = datetime.fromisoformat(date_to)
                delta = (d2 - d1).days + 1
                days_factor = max(1, delta)
             except:
                days_factor = config.daysToGenerate
        else:
             days_factor = config.daysToGenerate

        for profile in config.profiles:
            if profile.enabled:
                for platform in profile.platforms:
                    if platform == "instagram":
                        limit = profile.instagramLimit or config.limits.instagram
                    elif platform == "tiktok":
                        limit = profile.tiktokLimit or config.limits.tiktok
                    elif platform == "youtube":
                        limit = profile.youtubeLimit or config.limits.youtube
                    else:
                        limit = 0
                    total_expected += limit * days_factor
        
        total_posts = sum(status_counts.values())
        success_rate = (status_counts["success"] / total_posts * 100) if total_posts > 0 else 0
        
        return {
            "success": True,
            "total_profiles": total_profiles,
            "active_profiles": active_profiles,
            "total_expected_posts": total_expected,
            "total_actual_posts": total_posts,
            "posts_by_status": status_counts,
            "posts_by_platform": platform_counts,
            "avg_success_rate": round(success_rate, 2),
            "last_updated": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Publishing stats error: {e}")
        return {
            "success": False,
            "error": str(e),
            "total_profiles": 0,
            "active_profiles": 0,
            "total_expected_posts": 0,
            "total_actual_posts": 0,
            "posts_by_status": {},
            "posts_by_platform": {},
            "avg_success_rate": 0,
            "last_updated": datetime.utcnow().isoformat()
        }


@app.get("/api/stats/history")
async def get_history_stats(days: int = 30, session: AsyncSession = Depends(get_session)):
    """Get daily publication history for the last N days."""
    try:
        from app.models import PostingHistory
        from datetime import timezone, timedelta
        
        # Moscow timezone (UTC+3)
        MSK = timezone(timedelta(hours=3))
        # Use Naive UTC for DB (assuming Postgres stores naive timestamps or consistent UTC)
        now_utc = datetime.utcnow() 
        start_date = now_utc - timedelta(days=days)
        
        # Fetch all posts in range (both success and failed)
        stmt = select(PostingHistory).where(
            PostingHistory.posted_at >= start_date
        )
        result = await session.execute(stmt)
        posts = result.scalars().all()
        
        # Group by Date (MSK)
        daily_stats = {}
        for post in posts:
            # Convert UTC posted_at to MSK
            dt_msk = post.posted_at.replace(tzinfo=timezone.utc).astimezone(MSK)
            date_str = dt_msk.strftime("%Y-%m-%d")
            
            if date_str not in daily_stats:
                daily_stats[date_str] = {"success": 0, "failed": 0}
            
            if post.status == 'success':
                daily_stats[date_str]["success"] += 1
            elif post.status == 'failed':
                daily_stats[date_str]["failed"] += 1
            
        # Format as list sorted by date
        history = [
            {"date": k, "success": v["success"], "failed": v["failed"]} 
            for k, v in sorted(daily_stats.items(), reverse=True)
        ]
        
        return {"success": True, "history": history}
        
    except Exception as e:
        logger.error(f"History stats error: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/webhooks/upload-post")
async def upload_post_webhook(payload: Dict[str, Any] = Body(...), session: AsyncSession = Depends(get_session)):
    """
    Webhook endpoint for Upload Post API notifications.
    Called by Upload Post when an upload completes (success or failure).
    
    Payload example:
    {
        "event": "upload_completed",
        "profile_username": "username",
        "platform": "instagram",
        "result": {
            "success": true,
            "url": "https://instagram.com/p/...",
            "error": null
        }
    }
    """
    try:
        # ✅ ENHANCED DEBUGGING: Log full payload for verification
        import json
        logger.info(f"[Webhook] ═══════════════════════════════════════════")
        logger.info(f"[Webhook] Received Upload Post notification")
        logger.info(f"[Webhook] Full payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")
        logger.info(f"[Webhook] ═══════════════════════════════════════════")
        
        # Extract data from payload
        event = payload.get('event')
        profile_username = payload.get('profile_username')
        platform = payload.get('platform')
        caption_from_webhook = payload.get('caption', '')  # ✅ Get caption from webhook
        result = payload.get('result', {})
        
        # ✅ Handle different event types (Upload Post sends notifications for all stages)
        if event == 'upload_started':
            logger.info(f"[Webhook] Upload started for {profile_username}/{platform} - ignoring (waiting for completion)")
            return {"success": True, "message": "Upload started notification received"}
        elif event == 'upload_in_progress':
            logger.info(f"[Webhook] Upload in progress for {profile_username}/{platform} - ignoring (waiting for completion)")
            return {"success": True, "message": "Upload in progress notification received"}
        elif event != 'upload_completed':
            logger.warning(f"[Webhook] Unknown event type: {event}")
            return {"success": False, "error": "Unknown event type"}
        
        # Only process 'upload_completed' events
        logger.info(f"[Webhook] Processing upload_completed event")
        
        # Find the post in database by caption (most accurate)
        # Fallback to profile_username + platform if caption not available
        from sqlalchemy import update
        
        if caption_from_webhook:
            # Match by caption (100% accurate - AI generates unique captions)
            logger.info(f"[Webhook] 🔍 PRIMARY MATCH: Using caption matching")
            logger.info(f"[Webhook] Caption from webhook: '{caption_from_webhook[:100]}...'")
            stmt = select(PostingHistory).where(
                PostingHistory.profile_username == profile_username,
                PostingHistory.platform == platform,
                PostingHistory.status.in_(['queued', 'processing']),  # Match queued or processing posts
                PostingHistory.meta['caption'].astext == caption_from_webhook
            ).limit(1)
            
            result_obj = await session.execute(stmt)
            post = result_obj.scalar_one_or_none()
            
            # If not found in queued/processing, try ALL statuses (webhook might arrive late)
            if not post:
                logger.warning(f"[Webhook] No match in queued/processing, trying all statuses...")
                stmt_all = select(PostingHistory).where(
                    PostingHistory.profile_username == profile_username,
                    PostingHistory.platform == platform,
                    PostingHistory.meta['caption'].astext == caption_from_webhook
                ).order_by(PostingHistory.posted_at.desc()).limit(1)
                
                result_obj = await session.execute(stmt_all)
                post = result_obj.scalar_one_or_none()
                
                if post:
                    logger.warning(f"[Webhook] Found post #{post.id} in status '{post.status}' (late/duplicate webhook)")
        else:
            # Fallback: match by profile + platform + most recent
            logger.warning(f"[Webhook] ⚠️ FALLBACK MATCH: No caption in payload, using profile/platform")
            stmt = select(PostingHistory).where(
                PostingHistory.profile_username == profile_username,
                PostingHistory.platform == platform,
                PostingHistory.status.in_(['queued', 'processing'])  # Match queued or processing posts
            ).order_by(PostingHistory.posted_at.desc()).limit(1)
            
            result_obj = await session.execute(stmt)
            post = result_obj.scalar_one_or_none()
        
        if not post:
            # Debug: Check how many posts exist for this profile/platform
            logger.error(f"[Webhook] ❌ NO MATCH FOUND!")
            debug_stmt = select(PostingHistory).where(
                PostingHistory.profile_username == profile_username,
                PostingHistory.platform == platform
            )
            debug_result = await session.execute(debug_stmt)
            all_posts = debug_result.scalars().all()
            logger.warning(f"[Webhook] Total posts for {profile_username}/{platform}: {len(all_posts)}")
            
            if all_posts:
                statuses = [p.status for p in all_posts[:5]]
                logger.warning(f"[Webhook] Recent post statuses: {statuses}")
                
                # ✅ ENHANCED: Compare captions if webhook has caption
                if caption_from_webhook:
                    logger.warning(f"[Webhook] 🔍 Caption comparison for debugging:")
                    for p in all_posts[:3]:
                        saved_caption = p.meta.get('caption', '') if p.meta else ''
                        logger.warning(f"[Webhook]   Post #{p.id} ({p.status}):")
                        logger.warning(f"[Webhook]     Meta keys: {list(p.meta.keys()) if p.meta else 'None'}")
                        logger.warning(f"[Webhook]     Saved ({len(saved_caption)} chars):   '{saved_caption[:80]}...'")
                        logger.warning(f"[Webhook]     Webhook ({len(caption_from_webhook)} chars): '{caption_from_webhook[:80]}...'")
                        logger.warning(f"[Webhook]     Match: {saved_caption == caption_from_webhook}")
            return {"success": False, "error": "Post not found"}
        
        # Update post status based on result
        upload_success = result.get('success', False)
        new_status = 'success' if upload_success else 'failed'
        
        # ✅ IDEMPOTENCY: Check if post is already in final state
        if post.status in ['success', 'failed']:
            logger.warning(f"[Webhook] ⚠️ Post #{post.id} already in final state '{post.status}' - ignoring duplicate webhook")
            return {"success": True, "message": "Duplicate webhook ignored (idempotency)"}
        
        # Preserve existing meta and add result info
        updated_meta = post.meta.copy() if post.meta else {}
        if not upload_success:
            updated_meta['error'] = result.get('error', 'Upload failed')
        if result.get('url'):
            updated_meta['url'] = result.get('url')
        
        # Update database
        stmt_update = update(PostingHistory).where(PostingHistory.id == post.id).values(
            status=new_status,
            meta=updated_meta
        )
        await session.execute(stmt_update)
        await session.commit()
        
        logger.info(f"✅ [Webhook] Updated post #{post.id}: {post.status} → {new_status}")
        
        # Broadcast real-time event to UI
        await event_broadcaster.broadcast_post_status(
            post_id=post.id,
            status=new_status,
            meta={'video_path': post.video_path, 'profile': profile_username}
        )
        
        # If successful, increment brand stats and trigger cleanup
        if upload_success:
            from app.worker import increment_brand_stats, check_cleanup
            await increment_brand_stats(post.video_path)
            asyncio.create_task(check_cleanup(post.video_path))
        else:
            # Publication failed - try to restore video from archive
            import os
            video_filename = os.path.basename(post.video_path)
            archived_path = f"disk:/опубликовано/{video_filename}"
            
            # Check if video was already archived
            if await yandex_service.exists(archived_path):
                logger.info(f"↩️ [Webhook] Video found in archive, restoring to original location")
                try:
                    # Move back to original location
                    await yandex_service.move_file(archived_path, os.path.dirname(post.video_path))
                    logger.info(f"✅ [Webhook] Restored failed video: {archived_path} → {post.video_path}")
                except Exception as e:
                    logger.error(f"❌ [Webhook] Failed to restore video from archive: {e}")
            else:
                logger.info(f"📁 [Webhook] Video not in archive (still in original location or already processed)")
        
        return {"success": True, "message": "Webhook processed"}
        
    except Exception as e:
        logger.error(f"[Webhook] Error processing webhook: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/stats/errors")
async def get_grouped_errors(days: int = 7, session: AsyncSession = Depends(get_session)):
    """Get grouped errors for the last N days."""
    try:
        from datetime import timezone, timedelta
        from sqlalchemy import desc
        from app.models import PostingHistory
        
        # Moscow timezone (UTC+3)
        MSK = timezone(timedelta(hours=3))
        # Use Naive UTC for DB comparison to avoid offset mismatch
        now_utc = datetime.utcnow()
        start_date = now_utc - timedelta(days=days)
        
        stmt = select(PostingHistory).where(
            PostingHistory.status == "failed",
            PostingHistory.posted_at >= start_date
        ).order_by(desc(PostingHistory.posted_at))
        
        result = await session.execute(stmt)
        posts = result.scalars().all()
        
        # Group: Date -> ErrorType -> Count
        grouped = {}
        
        for p in posts:
            # Date Key
            dt_msk = p.posted_at.replace(tzinfo=timezone.utc).astimezone(MSK)
            date_key = dt_msk.strftime("%Y-%m-%d")
            
            # Error Key
            err_msg = "Unknown error"
            if p.meta and "error" in p.meta:
                raw_err = p.meta["error"]
                # Simplify common errors for grouping
                if "429" in raw_err: err_msg = "Rate Limit (429)"
                elif "401" in raw_err: err_msg = "Auth Error (401)"
                elif "timeout" in raw_err.lower(): err_msg = "Timeout Error"
                elif "no tracking id" in raw_err.lower(): err_msg = "Stuck (No ID)"
                elif "stuck in processing" in raw_err.lower(): err_msg = "Stuck (Cleanup)"
                elif "upload post api failed" in raw_err.lower(): 
                     parts = raw_err.split(":")
                     err_msg = parts[1].strip() if len(parts) > 1 else "Upload API Failed"
                else: 
                     # Truncate long custom errors
                     err_msg = raw_err[:50] + "..." if len(raw_err) > 50 else raw_err

            if date_key not in grouped:
                grouped[date_key] = {}
            
            if err_msg not in grouped[date_key]:
                grouped[date_key][err_msg] = 0
            
            grouped[date_key][err_msg] += 1
            
        # Transform to list
        output = []
        for date, errs in sorted(grouped.items(), reverse=True):
            error_list = [{"type": k, "count": v} for k, v in errs.items()]
            # Sort errors by count desc
            error_list.sort(key=lambda x: x["count"], reverse=True)
            output.append({
                "date": date,
                "total": sum(e["count"] for e in error_list),
                "errors": error_list
            })
            
        return {"success": True, "grouped_errors": output}
    except Exception as e:
        logger.error(f"Failed to fetch errors: {e}")
        return {"success": False, "errors": [], "message": str(e)}

@app.get("/api/errors/recent")
async def get_recent_errors(limit: int = 50, session: AsyncSession = Depends(get_session)):
    """Get raw list of recent failed posts."""
    try:
        from sqlalchemy import desc
        from app.models import PostingHistory
        
        stmt = select(PostingHistory).where(
            PostingHistory.status == "failed"
        ).order_by(desc(PostingHistory.posted_at)).limit(limit)
        
        result = await session.execute(stmt)
        posts = result.scalars().all()
        
        return {"success": True, "errors": posts}
    except Exception as e:
        logger.error(f"Failed to fetch recent errors: {e}")
        return {"success": False, "errors": [], "message": str(e)}

@app.post("/api/cleanup")
async def cleanup_queue(session: AsyncSession = Depends(get_session)):
    """Delete all queued (not yet published) posts."""
    try:
        from sqlalchemy import delete
        stmt = delete(PostingHistory).where(PostingHistory.status == "queued")
        result = await session.execute(stmt)
        await session.commit()
        
        deleted_count = result.rowcount
        logger.info(f"🗑️ Cleanup: Deleted {deleted_count} queued posts")
        return {"success": True, "message": f"Удалено {deleted_count} запланированных постов", "deleted": deleted_count}
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return {"success": False, "message": f"Ошибка: {str(e)}"}

@app.post("/api/run")
async def run_automation():
    """Manually trigger the daily schedule generation."""
    from app.worker import generate_daily_schedule
    # Run in background without blocking
    asyncio.create_task(generate_daily_schedule())
    return {"success": True, "message": "Automation started in background"}

@app.get("/api/logs")
async def get_logs(lines: int = 100):
    """Return last N lines of application logs."""
    try:
        log_file = "/tmp/app.log"
        
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                all_lines = f.readlines()
                recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
                return {"success": True, "logs": recent}
        else:
            return {
                "success": False, 
                "message": "File logging not configured. Use Docker/EasyPanel logs.",
                "logs": []
            }
    except Exception as e:
        logger.error(f"Failed to fetch logs: {e}")
        return {"success": False, "message": str(e), "logs": []}

@app.get("/api/events/stream")
async def event_stream():
    """
    Server-Sent Events (SSE) endpoint for real-time updates.
    Streams post status changes and statistics updates to connected clients.
    """
    from fastapi.responses import StreamingResponse
    
    async def event_generator():
        # Subscribe to all events
        queue = event_broadcaster.subscribe('all')
        
        try:
            logger.info("[SSE] Client connected to event stream")
            
            # Send initial connection event IMMEDIATELY
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
            # Keep sending events as they arrive
            while True:
                try:
                    # Wait for new event (with shorter timeout for faster keepalive)
                    # Using 5 seconds to be more aggressive with keepalives
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    
                    # Send event to client
                    data_str = json.dumps(event)
                    yield f"data: {data_str}\n\n"
                    
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent connection timeout
                    # Colon usually indicates a comment in SSE
                    yield ": keepalive\n\n"
                    
        except asyncio.CancelledError:
            logger.info("[SSE] Client disconnected from event stream (Cancelled)")
            event_broadcaster.unsubscribe('all', queue)
            raise # Important to propagate cancellation
        except Exception as e:
            logger.error(f"[SSE] Error in event stream: {e}")
            event_broadcaster.unsubscribe('all', queue)
        finally:
             event_broadcaster.unsubscribe('all', queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # "Connection": "keep-alive",  <-- REMOVED: Forbidden in HTTP/2 and causes ERR_HTTP2_PROTOCOL_ERROR
            "X-Accel-Buffering": "no"      # Disable nginx buffering
        }
    )


@app.get("/api/analytics/global")
async def get_global_analytics(session: AsyncSession = Depends(get_session)):
    """Get aggregated analytics stats globally."""
    from app.services.analytics_service import analytics_service
    try:
        data = await analytics_service.get_aggregated_stats()
        return {"success": True, "stats": data}
    except Exception as e:
        logger.error(f"Global analytics error: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/analytics/refresh")
async def refresh_analytics():
    """Trigger manual refresh of daily analytics."""
    from app.services.analytics_service import analytics_service
    # Run in background
    asyncio.create_task(analytics_service.fetch_and_save_daily_stats())
    return {"success": True, "message": "Analytics refresh started in background"}

# Serve static files (Frontend) - SPA Configuration
public_path = os.path.join(os.getcwd(), 'public')

# 1. Mount assets explicitly (Vite puts JS/CSS here)
assets_path = os.path.join(public_path, 'assets')
if os.path.exists(assets_path):
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

# 2. Serve other static root files (favicon, etc) BUT exclude index.html to avoid conflict
# We can't easily exclude just one file with StaticFiles, so we rely on specific mounts or check existence.
# For simplicity, we can trust the catch-all for root files if they don't match specific API routes.

# 3. SPA Catch-All: Serve index.html for any other route (unless it's an API route matched above)
from fastapi.responses import FileResponse

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Check if a static file exists for this path (e.g. favicon.ico, vite.svg)
    file_path = os.path.join(public_path, full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
        
    # Otherwise, return index.html for SPA routing
    index_path = os.path.join(public_path, 'index.html')
    if os.path.exists(index_path):
        return FileResponse(index_path)
    
    return {"error": "Frontend not found (index.html missing)"}
