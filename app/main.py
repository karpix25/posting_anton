import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings, LegacyConfig, BrandStats as BrandStatsModel
from app.database import get_session, init_db
from app.models import BrandStats
from app.services.yandex import yandex_service
from app.utils import extract_theme, extract_brand, extract_author
from app.logging_conf import setup_logging
from app.services.dynamic_scheduler import dynamic_scheduler

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

# Startup event
@app.on_event("startup")
async def on_startup():
    logger.info("Application starting up...")
    await init_db()
    await migrate_file_to_db()
    dynamic_scheduler.start()

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
            quota = client.get("quota", 0)
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

@app.get("/api/stats")
async def get_stats(refresh: bool = False, session: AsyncSession = Depends(get_session)):
    # This logic mimics existing server.ts /api/stats
    # It fetches ALL videos from Yandex and groups them by metadata
    
    # 1. Fetch Files
    # Note: We should implement caching like in TS version?
    # For now, let's just fetch live. Yandex Service logic handles internal details? 
    # Yandex service currently fetches fresh.
    # In production, cache this result in memory or Redis.
    
    try:
        # Load folders from config
        config = await get_db_config(session)
        all_videos = []
        
        # We fetch all flat files once (wrapper handles limit)
        # If we want to filter by folder, we do it in memory for now (simpler than multiple requests)
        files = await yandex_service.list_files(limit=20000)
        
        stats = {
            "totalVideos": 0,
            "publishedCount": 0, # TODO: fetch from DB history count?
            "byCategory": {},
            "byAuthor": {},
            "byBrand": {},
            "profilesByCategory": {}
        }
        
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
            
            theme = extract_theme(path)
            author = extract_author(path)
            brand = extract_brand(path)
            
            if theme != "unknown":
                stats["byCategory"][theme] = stats["byCategory"].get(theme, 0) + 1
            
            if author != "unknown":
                stats["byAuthor"][author] = stats["byAuthor"].get(author, 0) + 1
                
            if brand != "unknown":
                stats["byBrand"][brand] = stats["byBrand"].get(brand, 0) + 1

        # Profiles mapping
        for p in config.profiles:
            if p.theme_key:
                tk = p.theme_key.lower().strip()
                if tk not in stats["profilesByCategory"]:
                    stats["profilesByCategory"][tk] = []
                stats["profilesByCategory"][tk].append(p.username)
        
        return stats

    except Exception as e:
        print(f"Stats Error: {e}")
        # Return empty stats on error to not crash UI
        return {
            "totalVideos": 0,
            "publishedCount": 0,
            "byCategory": {},
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

@app.post("/api/run")
async def run_automation():
    """Manually trigger the daily schedule generation."""
    from app.worker import generate_daily_schedule
    # Use delay() for async execution
    task = generate_daily_schedule.delay()
    return {"success": True, "message": "Automation started", "task_id": str(task.id)}

# Serve static files (Frontend)
# Providing access to public directory if exists
public_path = os.path.join(os.getcwd(), 'public')
if os.path.exists(public_path):
    app.mount("/", StaticFiles(directory=public_path, html=True), name="public")
