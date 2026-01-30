
import logging
import json
import os
from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import SystemConfig, AppSettings, SocialProfile, AIClient
from app.config import LegacyConfig, settings

logger = logging.getLogger(__name__)

async def run_migration(session: AsyncSession):
    """
    Check if migration is needed (AppSettings is empty) and run it.
    Includes auto-recovery from 'verify_migration' test data.
    """
    # 1. Check if already migrated
    stmt = select(AppSettings).where(AppSettings.id == 1)
    result = await session.execute(stmt)
    existing_settings = result.scalar_one_or_none()
    
    # SAFETY CHECK for Test Data (Recovery Logic)
    if existing_settings:
        stmt_test = select(SocialProfile).where(SocialProfile.username == "test_migration_user")
        res_test = await session.execute(stmt_test)
        is_test_user = res_test.scalar_one_or_none()
        
        # If we see the specific signature of the verification script
        if is_test_user and existing_settings.cron_schedule == "30 2 * * *":
             logger.warning("[Migration] ⚠️ DETECTED TEST DATA. Purging to restore real config...")
             await session.execute(delete(AppSettings))
             await session.execute(delete(SocialProfile))
             await session.execute(delete(AIClient))
             existing_settings = None # Force re-migration
        else:
             # Check if tables are actually populated
             from sqlalchemy import func
             stmt_p = select(func.count()).select_from(SocialProfile)
             count_p = (await session.execute(stmt_p)).scalar()
             
             stmt_c = select(func.count()).select_from(AIClient)
             count_c = (await session.execute(stmt_c)).scalar()
             
             # If settings exist but tables are empty, we FORCE migration/seeding
             if count_p == 0 and count_c == 0:
                 logger.warning("[Migration] ⚠️ Settings exist but tables are empty. Forcing migration from config.json...")
                 existing_settings = None
             else:
                 logger.info("[Migration] Relational tables already populated. Skipping migration.")
                 return

    logger.info("[Migration] Starting migration from SystemConfig JSON to Relational Tables...")
    
    # 2. Fetch Legacy Config from DB
    stmt_legacy = select(SystemConfig).where(SystemConfig.key == "main_config")
    result_legacy = await session.execute(stmt_legacy)
    legacy_record = result_legacy.scalar_one_or_none()
    
    raw_data = {}
    if legacy_record:
        raw_data = legacy_record.value
        logger.info(f"[Migration] Found legacy config in DB.")
    else:
        # EMERGENCY RECOVERY from file (if DB was wiped by verification)
        path = settings.get_config_path()
        if os.path.exists(path):
            logger.info(f"[Migration] 📂 No DB config found (SystemConfig missing). Restoring from {path}...")
            try:
                with open(path, 'r') as f:
                    raw_data = json.load(f)
                # Restore SystemConfig for consistency
                session.add(SystemConfig(key="main_config", value=raw_data))
            except Exception as e:
                logger.error(f"[Migration] Failed to load backup file: {e}")
        else:
            logger.warning("[Migration] No legacy config or backup file found. Creating defaults.")
    
    # ensure it matches Pydantic defaults if empty
    try:
        legacy_obj = LegacyConfig(**raw_data)
    except Exception as e:
        logger.error(f"[Migration] Failed to parse legacy config: {e}")
        # Use defaults if parsing fails
        legacy_obj = LegacyConfig(limits={"instagram": 10, "tiktok": 10, "youtube": 2})
    
    # 3. Populate SocialProfile
    profiles_count = 0
    if legacy_obj.profiles:
        for p in legacy_obj.profiles:
            new_profile = SocialProfile(
                username=p.username,
                theme_key=p.theme_key,
                enabled=p.enabled if p.enabled is not None else True,
                platforms=p.platforms or [],
                instagram_limit=p.instagramLimit,
                tiktok_limit=p.tiktokLimit,
                youtube_limit=p.youtube_limit,
                updated_at=datetime.utcnow()
            )
            session.add(new_profile)
            profiles_count += 1
            
    # 4. Populate AIClient
    clients_count = 0
    if legacy_obj.clients:
        for c in legacy_obj.clients:
            new_client = AIClient(
                name=c.name,
                prompt=c.prompt,
                regex=c.regex,
                updated_at=datetime.utcnow()
            )
            session.add(new_client)
            clients_count += 1
            
    # 5. Populate AppSettings
    schedule_enabled = False
    schedule_time = "00:00"
    
    cron = legacy_obj.cronSchedule or ""
    parts = cron.split(" ")
    if len(parts) >= 5 and cron != "":
        if cron.strip():
            schedule_enabled = True
            try:
                m = parts[0].zfill(2)
                h = parts[1].zfill(2)
                schedule_time = f"{h}:{m}"
            except:
                pass
    
    new_settings = AppSettings(
        id=1,
        cron_schedule=cron,
        days_to_generate=legacy_obj.daysToGenerate or 1,
        yandex_folders=legacy_obj.yandexFolders or [],
        global_limits={
            "instagram": legacy_obj.limits.instagram,
            "tiktok": legacy_obj.limits.tiktok,
            "youtube": legacy_obj.limits.youtube
        },
        theme_aliases=legacy_obj.themeAliases or {},
        brand_quotas=legacy_obj.brandQuotas or {},
        schedule_enabled=schedule_enabled,
        schedule_time=schedule_time,
        schedule_timezone="Europe/Moscow",
        updated_at=datetime.utcnow()
    )
    session.add(new_settings)
    
    await session.commit()
    logger.info(f"[Migration] ✅ Success! Migrated {profiles_count} profiles, {clients_count} clients, and settings.")
