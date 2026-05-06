import json
import os
import asyncio
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Depends, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings, LegacyConfig
from app.database import get_session, init_db, async_session_maker
from app.models import BrandStats
from app.services.yandex import yandex_service
from app.utils import extract_theme, extract_brand, extract_author
from app.logging_conf import setup_logging
from app.services.dynamic_scheduler import dynamic_scheduler
from app.services.platforms import upload_post_client
from app.services.profile_status import (
    apply_webhook_event_to_profile,
    extract_statuses_from_api_profile,
    merge_api_profiles_into_config,
)

app = FastAPI(title="Automation Dashboard API", version="2.0.0")

# Setup Logging
logger = setup_logging()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.services.config_db import migrate_file_to_db, get_db_config, save_db_config

# In-memory caches for expensive Yandex stats scans
stats_cache: Optional[Dict[str, Any]] = None
stats_cache_time: float = 0.0
STATS_CACHE_TTL = 60  # seconds

files_cache: List[Dict[str, Any]] = []
files_cache_time: float = 0.0
FILES_CACHE_TTL = 30 * 60  # 30 minutes

profile_status_sync_task: Optional[asyncio.Task] = None


async def reconcile_profile_statuses_once(upsert_missing_profiles: bool = True) -> Dict[str, Any]:
    """Fetch profile statuses from Upload Post and persist them into DB config."""
    if not settings.UPLOAD_POST_API_KEY:
        return {"success": False, "error": "UPLOAD_POST_API_KEY is missing"}

    api_profiles = await upload_post_client.get_profiles()

    async with async_session_maker() as session:
        config = await get_db_config(session)
        config_data = config.dict()
        summary = merge_api_profiles_into_config(
            config_data,
            api_profiles,
            status_source="uploadpost_sync",
            upsert_missing_profiles=upsert_missing_profiles,
        )
        await save_db_config(session, config_data)

    return {"success": True, "summary": summary, "profiles_count": len(api_profiles)}


async def profile_status_sync_loop():
    """Background self-healing loop for profile statuses."""
    if not settings.PROFILE_STATUS_SYNC_ENABLED:
        logger.info("[ProfileStatusSync] Disabled by settings.")
        return

    interval = max(60, int(settings.PROFILE_STATUS_SYNC_INTERVAL_SECONDS or 600))
    logger.info(f"[ProfileStatusSync] Started (interval={interval}s).")
    while True:
        try:
            await reconcile_profile_statuses_once(upsert_missing_profiles=False)
        except Exception as e:
            logger.warning(f"[ProfileStatusSync] Reconcile failed: {e}")
        await asyncio.sleep(interval)

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

             # Run one immediate profile-status sync and then keep self-healing loop.
             try:
                 await reconcile_profile_statuses_once(upsert_missing_profiles=False)
                 logger.info("✅ Initial profile status reconcile complete.")
             except Exception as e:
                 logger.warning(f"Initial profile status reconcile failed: {e}")

             global profile_status_sync_task
             profile_status_sync_task = asyncio.create_task(profile_status_sync_loop())
             
    except Exception as e:
        logger.error(f"Startup failed: {e}")


@app.on_event("shutdown")
async def on_shutdown():
    global profile_status_sync_task
    if profile_status_sync_task and not profile_status_sync_task.done():
        profile_status_sync_task.cancel()

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}

@app.get("/api/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    config = await get_db_config(session)
    return config.dict()

@app.post("/api/config")
async def update_config(config_data: Dict[str, Any], session: AsyncSession = Depends(get_session)):
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

@app.get("/api/profiles/sync")
async def sync_profiles():
    logger.info("[API] /api/profiles/sync requested")
    try:
        result = await reconcile_profile_statuses_once(upsert_missing_profiles=True)
        if result.get("success"):
            logger.info(f"[API] Sync success: {result.get('summary')}")
            # Backward-compat: return raw profiles for existing UI clients.
            api_profiles = await upload_post_client.get_profiles()
            return {"success": True, "profiles": api_profiles, **result}
        return result
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/profiles/status/reconcile")
async def reconcile_profile_statuses():
    """Manual reconcile endpoint for profile statuses."""
    try:
        result = await reconcile_profile_statuses_once(upsert_missing_profiles=False)
        return result
    except Exception as e:
        logger.error(f"Manual status reconcile failed: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/profiles/status/summary")
async def get_profile_status_summary():
    """Return live Upload Post connectivity summary (connected / total / attention)."""
    try:
        api_profiles = await upload_post_client.get_profiles()
        total = len(api_profiles)
        connected_profiles = 0
        attention_profiles = 0
        disconnected_profiles = 0

        for p in api_profiles:
            statuses, connected_platforms = extract_statuses_from_api_profile(p)
            if connected_platforms:
                connected_profiles += 1

            status_values = [str(v).lower() for v in statuses.values()]
            has_reauth = any(v == "reauth_required" for v in status_values)
            has_disconnected = any(v == "disconnected" for v in status_values)

            if has_reauth or has_disconnected or not connected_platforms:
                attention_profiles += 1
            if has_disconnected or not connected_platforms:
                disconnected_profiles += 1

        return {
            "success": True,
            "total": total,
            "connected": connected_profiles,
            "attention": attention_profiles,
            "disconnected": disconnected_profiles,
        }
    except Exception as e:
        logger.error(f"Live profile status summary failed: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/uploadpost/webhook")
async def upload_post_webhook(request: Request):
    """
    Receive Upload Post profile connectivity events and patch local profile statuses.
    Supported events:
    - social_account_connected
    - social_account_disconnected
    - social_account_reauth_required
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Optional shared-token check for safer endpoint exposure.
    if settings.UPLOAD_POST_WEBHOOK_TOKEN:
        header_token = request.headers.get("x-uploadpost-webhook-token") or request.headers.get("x-webhook-token")
        auth_header = request.headers.get("authorization", "")
        bearer = auth_header.replace("Bearer ", "").strip() if auth_header else ""
        query_token = request.query_params.get("token")
        if settings.UPLOAD_POST_WEBHOOK_TOKEN not in {header_token, bearer, query_token}:
            raise HTTPException(status_code=401, detail="Invalid webhook token")

    event_name = str(
        body.get("event")
        or body.get("type")
        or body.get("event_name")
        or ""
    ).strip()
    username = str(
        body.get("username")
        or body.get("profile_username")
        or body.get("user")
        or body.get("account")
        or ""
    ).strip()
    platform = str(
        body.get("platform")
        or body.get("social_account")
        or body.get("provider")
        or ""
    ).strip()

    if not event_name or not username:
        raise HTTPException(status_code=400, detail="Missing event or username")

    async with async_session_maker() as session:
        config = await get_db_config(session)
        config_data = config.dict()
        profiles = config_data.get("profiles") or []
        target = None
        for p in profiles:
            if str(p.get("username", "")).strip().lower() == username.lower():
                target = p
                break

        if not target:
            logger.warning(f"[Webhook] Profile not found in config: {username}")
            return {"success": True, "updated": False, "reason": "profile_not_found"}

        changed = apply_webhook_event_to_profile(
            target,
            event_name=event_name,
            platform_name=platform,
            status_source="uploadpost_webhook",
        )
        if changed:
            await save_db_config(session, config_data)
            logger.info(f"[Webhook] Updated profile status: user={username} event={event_name} platform={platform}")

    return {"success": True, "updated": changed}

@app.get("/api/stats")
async def get_stats(refresh: bool = False, session: AsyncSession = Depends(get_session)):
    # This logic mimics existing server.ts /api/stats
    # It fetches ALL videos from Yandex and groups them by metadata
    
    # 1. Fetch Files
    # Note: We should implement caching like in TS version?
    # For now, let's just fetch live. Yandex Service logic handles internal details? 
    # Yandex service currently fetches fresh.
    # In production, cache this result in memory or Redis.
    
    global stats_cache, stats_cache_time, files_cache, files_cache_time
    try:
        now_ts = time.time()
        if not refresh and stats_cache and (now_ts - stats_cache_time) < STATS_CACHE_TTL:
            return stats_cache

        # Load folders from config
        config = await get_db_config(session)
        # Ensure theme alias resolution in utils uses DB config, not stale file config
        settings._legacy_config = config
        all_videos = []
        
        # We fetch all flat files once (wrapper handles limit)
        # If we want to filter by folder, we do it in memory for now (simpler than multiple requests)
        # We fetch all flat files once (wrapper handles limit)
        # If we want to filter by folder, we do it in memory for now (simpler than multiple requests)
        use_cached_files = (not refresh) and files_cache and ((now_ts - files_cache_time) < FILES_CACHE_TTL)
        if use_cached_files:
            files = files_cache
        else:
            files = await yandex_service.list_files(limit=100000)
            files_cache = files
            files_cache_time = now_ts
        
        stats = {
            "totalVideos": 0,
            "publishedCount": 0, # TODO: fetch from DB history count?
            "byCategory": {},
            "byCategoryRaw": {},
            "byAuthor": {},
            "byBrand": {},
            "profilesByCategory": {}
        }

        # Pre-fill all author folders from disk:/ВИДЕО with zero counts,
        # so UI shows authors even when they currently have no video files.
        all_authors: List[str] = []
        try:
            all_authors = await yandex_service.list_directories("disk:/ВИДЕО", limit=10000)
            for author in all_authors:
                if author and author != "unknown":
                    stats["byAuthor"].setdefault(author, 0)
        except Exception:
            pass

        # Pre-fill all category folders from disk:/ВИДЕО/<author> with zero counts,
        # so UI shows complete category list even when there are no videos in a category yet.
        if all_authors:
            try:
                category_tasks = [
                    yandex_service.list_directories(f"disk:/ВИДЕО/{author}", limit=10000)
                    for author in all_authors
                ]
                author_categories = await asyncio.gather(*category_tasks, return_exceptions=True)

                for categories in author_categories:
                    if isinstance(categories, Exception):
                        continue
                    for category in categories:
                        if category and category != "unknown":
                            stats["byCategoryRaw"].setdefault(category, 0)
            except Exception:
                pass
        
        # Filter and Aggregate
        config_folders_norm = [f.replace("disk:", "").strip("/").lower() for f in config.yandexFolders if f]
        
        for f in files:
            path = f["path"]
            path_norm = path.replace("disk:", "").strip("/").lower()
            
            # Check folder
            # If folders are not configured, include all files.
            if not config_folders_norm:
                in_folder = True
            else:
                in_folder = False
                for folder in config_folders_norm:
                    # Keep matching tolerant to path formatting/subfolders.
                    if folder and (path_norm.startswith(folder) or folder in path_norm):
                        in_folder = True
                        break
            
            if not in_folder: continue
            
            stats["totalVideos"] += 1
            
            theme = extract_theme(path, config.themeAliases, use_aliases=True)
            theme_raw = extract_theme(path, config.themeAliases, use_aliases=False)
            author = extract_author(path)
            brand = extract_brand(path)

            if theme != "unknown":
                stats["byCategory"][theme] = stats["byCategory"].get(theme, 0) + 1
            if theme_raw != "unknown":
                stats["byCategoryRaw"][theme_raw] = stats["byCategoryRaw"].get(theme_raw, 0) + 1
            
            if author != "unknown":
                stats["byAuthor"][author] = stats["byAuthor"].get(author, 0) + 1
                
            if brand != "unknown":
                stats["byBrand"][brand] = stats["byBrand"].get(brand, 0) + 1

        # Profiles mapping
        # Build profile mapping for canonical keys and aliases so raw categories also resolve.
        aliases_map = config.themeAliases or {}
        aliases_lookup = {str(k).lower().strip(): [str(a).lower().strip() for a in v] for k, v in aliases_map.items()}

        for p in config.profiles:
            if p.theme_key:
                tk = p.theme_key.lower().strip()
                if tk not in stats["profilesByCategory"]:
                    stats["profilesByCategory"][tk] = []
                stats["profilesByCategory"][tk].append(p.username)

                # Also map aliases for this canonical theme
                for alias in aliases_lookup.get(tk, []):
                    if alias not in stats["profilesByCategory"]:
                        stats["profilesByCategory"][alias] = []
                    if p.username not in stats["profilesByCategory"][alias]:
                        stats["profilesByCategory"][alias].append(p.username)
        
        stats_cache = stats
        stats_cache_time = time.time()
        return stats

    except Exception as e:
        print(f"Stats Error: {e}")
        # Return empty stats on error to not crash UI
        return {
            "totalVideos": 0,
            "publishedCount": 0,
            "byCategory": {},
            "byCategoryRaw": {},
            "byAuthor": {},
            "profilesByCategory": {},
            "error": str(e)
        }

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
        from app.seed_data import CLIENTS_SEED
        config = await get_db_config(session)
        
        # Convert Pydantic to dict
        config_dict = config.dict()
        
        # Inject clients
        config_dict["clients"] = CLIENTS_SEED
        
        # Save back
        await save_db_config(session, config_dict)
        logger.info(f"Force-restored {len(CLIENTS_SEED)} clients from seed.")
        
        return {"success": True, "message": f"Restored {len(CLIENTS_SEED)} clients", "clients": CLIENTS_SEED}
    except Exception as e:
        logger.error(f"Failed to restore defaults: {e}")
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
    
    # Also update config.json for sync?
    # The original implementation updated both DB and config.json.
    # Backward compatibility:
    try:
        config = settings.load_legacy_config()
        if not config.brandQuotas: config.brandQuotas = {}
        if category not in config.brandQuotas: config.brandQuotas[category] = {}
        config.brandQuotas[category][brand] = quota
        
        # Save
        path = settings.get_config_path()
        with open(path, "w", encoding="utf-8") as f:
            # Reconstruct full dict
            json.dump(config.dict(), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to sync quota to config.json: {e}")

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

# Serve static files (Frontend)
# Providing access to public directory if exists
public_path = os.path.join(os.getcwd(), 'public')
if os.path.exists(public_path):
    app.mount("/", StaticFiles(directory=public_path, html=True), name="public")
