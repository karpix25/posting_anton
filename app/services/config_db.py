
import json
import os
import logging
from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import SystemConfig, SocialProfile, AIClient, AppSettings
from app.config import settings, LegacyConfig, SocialProfile as PydanticProfile, ClientConfig as PydanticClient, GlobalLimits as LimitsConfig
from app.database import async_session_maker
from app.services.migration_service import run_migration

logger = logging.getLogger(__name__)

async def migrate_file_to_db():
    """
    Initialize DB config. 
    Now simply triggers the relational migration.
    """
    async with async_session_maker() as session:
        try:
            # Ensure tables are created (Automatic by SQLModel usually, but migration logic needs them)
            # Run migration from legacy JSON if needed
            await run_migration(session)
        except Exception as e:
            logger.error(f"[ConfigDB] Init Failed: {e}")

async def get_db_config(session: AsyncSession) -> LegacyConfig:
    """
    Construct LegacyConfig object from Relational Tables (SocialProfile, AIClient, AppSettings).
    """
    # 1. Fetch Settings
    stmt_settings = select(AppSettings).where(AppSettings.id == 1)
    res_settings = await session.execute(stmt_settings)
    app_settings = res_settings.scalar_one_or_none()
    
    if not app_settings:
        # Fallback if migration failed or DB empty? Should not happen after migrate_file_to_db
        return LegacyConfig(limits={"instagram": 10, "tiktok": 10, "youtube": 2})

    # 2. Fetch Profiles
    stmt_profiles = select(SocialProfile)
    res_profiles = await session.execute(stmt_profiles)
    db_profiles = res_profiles.scalars().all()
    
    # Map to Pydantic
    pyd_profiles = []
    for p in db_profiles:
        pyd_profiles.append(PydanticProfile(
            username=p.username,
            theme_key=p.theme_key,
            enabled=p.enabled,
            platforms=p.platforms or [],
            instagramLimit=p.instagram_limit,
            tiktokLimit=p.tiktok_limit,
            youtubeLimit=p.youtube_limit,
            # limit fallback?
        ))

    # 3. Fetch Clients
    stmt_clients = select(AIClient)
    res_clients = await session.execute(stmt_clients)
    db_clients = res_clients.scalars().all()
    
    pyd_clients = []
    
    # Helper to find quota in brand_quotas (Category -> Brand -> Quota)
    quotas_map = app_settings.brand_quotas or {}
    
    def find_quota(client_name):
        clean = client_name.lower().replace(" ", "")
        for cat, brands in quotas_map.items():
            # brands could be None if malformed
            if not brands: continue
            if clean in brands:
                return brands[clean]
        return 0

    for c in db_clients:
        pyd_clients.append(PydanticClient(
            name=c.name,
            prompt=c.prompt,
            regex=c.regex,
            quota=find_quota(c.name)
        ))

    # 4. Construct
    # AppSettings.global_limits is dict, convert to Pydantic
    g_limits = app_settings.global_limits or {"instagram": 10, "tiktok": 10, "youtube": 2}
    
    # Ensure cron matches schedule_enabled + schedule_time if we want stricter logic, 
    # but for now rely on what's stored in cron_schedule
    
    config = LegacyConfig(
        yandexFolders=app_settings.yandex_folders or [],
        daysToGenerate=app_settings.days_to_generate,
        limits=LimitsConfig(**g_limits),
        cronSchedule=app_settings.cron_schedule,
        
        profiles=pyd_profiles,
        clients=pyd_clients,
        
        themeAliases=app_settings.theme_aliases,
        brandQuotas=app_settings.brand_quotas
    )
    
    return config

async def save_db_config(session: AsyncSession, config_data: dict):
    """
    Save massive JSON dict into Relational Tables.
    This is complex because it involves Diffing or Truncate-and-Replace.
    For MVP: Update Settings, and UPSERT Profiles/Clients.
    To handle deletions (e.g. user removed a profile in UI), we might need to sync the list.
    """
    logger.info(f"Saving Full Config to Relational DB...")
    
    # 1. Update AppSettings
    stmt_settings = select(AppSettings).where(AppSettings.id == 1)
    res = await session.execute(stmt_settings)
    settings_row = res.scalar_one_or_none()
    
    if not settings_row:
        settings_row = AppSettings(id=1)
        session.add(settings_row)
    
    # Map fields
    settings_row.cron_schedule = config_data.get("cronSchedule", "")
    settings_row.days_to_generate = config_data.get("daysToGenerate", 1)
    settings_row.yandex_folders = config_data.get("yandexFolders", [])
    
    # Limits map back to dict
    limits_data = config_data.get("limits", {})
    # If it comes as object, convert to dict
    if hasattr(limits_data, "dict"): limits_data = limits_data.dict()
    settings_row.global_limits = limits_data
    
    settings_row.theme_aliases = config_data.get("themeAliases", {})
    settings_row.brand_quotas = config_data.get("brandQuotas", {})
    
    # Update schedule fields derived from Cron (simplified)
    # If cron changed, we might want to update schedule_time?
    # Let's trust config_data for now.
    
    # AUTO-SYNC QUOTAS from Clients list (Critical for startup/seed)
    updated_quotas = config_data.get("brandQuotas", {}).copy()
    if "clients" in config_data:
        incoming_clients = config_data["clients"]
        for c in incoming_clients:
             # handle dict or object
             c_dict = c.dict() if hasattr(c, "dict") else c
             
             name = c_dict.get("name", "")
             quota = c_dict.get("quota")
             regex = c_dict.get("regex", "")
             
             if quota is not None:
                 # Heuristic category extraction
                 category = "unknown"
                 if regex:
                     parts = regex.replace("\\", "/").split("/")
                     if len(parts) >= 3:
                         category = parts[-2]
                 category = category.lower().strip()
                 
                 if category not in updated_quotas:
                     updated_quotas[category] = {}
                 
                 brand_clean = name.lower().replace(" ", "")
                 updated_quotas[category][brand_clean] = quota
                 
    settings_row.brand_quotas = updated_quotas
    
    settings_row.updated_at = datetime.utcnow()
    
    # 2. Sync Profiles (Full Sync Strategy)
    # A. Get current keys
    # B. Upsert incoming
    # C. Delete missing
    
    incoming_profiles = config_data.get("profiles", [])
    incoming_usernames = set()
    
    for p in incoming_profiles:
        # P is dict or object
        if hasattr(p, "dict"): p = p.dict()
        
        uname = p.get("username")
        if not uname: continue
        incoming_usernames.add(uname)
        
        # Check if exists
        stmt_p = select(SocialProfile).where(SocialProfile.username == uname)
        res_p = await session.execute(stmt_p)
        db_p = res_p.scalar_one_or_none()
        
        if not db_p:
            db_p = SocialProfile(username=uname)
            session.add(db_p)
            
        # Update fields
        db_p.theme_key = p.get("theme_key")
        db_p.enabled = p.get("enabled", True)
        db_p.platforms = p.get("platforms", [])
        db_p.instagram_limit = p.get("instagramLimit")
        db_p.tiktok_limit = p.get("tiktokLimit")
        db_p.youtube_limit = p.get("youtubeLimit")
        db_p.updated_at = datetime.utcnow()
        
    # Delete missing profiles
    stmt_del_p = delete(SocialProfile).where(SocialProfile.username.not_in(incoming_usernames))
    await session.execute(stmt_del_p)

    # 3. Sync Clients (Full Sync Strategy)
    if "clients" in config_data:
        incoming_clients = config_data["clients"]
        incoming_client_names = set()
        
        for c in incoming_clients:
            if hasattr(c, "dict"): c = c.dict()
            
            name = c.get("name")
            if not name: continue
            incoming_client_names.add(name)
            
            stmt_c = select(AIClient).where(AIClient.name == name)
            res_c = await session.execute(stmt_c)
            db_c = res_c.scalar_one_or_none()
            
            if not db_c:
                db_c = AIClient(name=name)
                session.add(db_c)
                
            db_c.prompt = c.get("prompt")
            db_c.regex = c.get("regex")
            db_c.updated_at = datetime.utcnow()
            
        # Delete missing clients
        stmt_del_c = delete(AIClient).where(AIClient.name.not_in(incoming_client_names))
        await session.execute(stmt_del_c)

    await session.commit()
    logger.info("DB Config (Relational) Commit Successful")

