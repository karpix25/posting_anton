
import logging
import json
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import SystemConfig, AppSettings, SocialProfile, AIClient
from app.config import LegacyConfig

logger = logging.getLogger(__name__)

async def run_migration(session: AsyncSession):
    """
    Check if migration is needed (AppSettings is empty) and run it.
    """
    # 1. Check if already migrated
    stmt = select(AppSettings).where(AppSettings.id == 1)
    result = await session.execute(stmt)
    existing_settings = result.scalar_one_or_none()
    
    if existing_settings:
        logger.info("[Migration] Relational tables already populated. Skipping migration.")
        return

    logger.info("[Migration] Starting migration from SystemConfig JSON to Relational Tables...")
    
    # 2. Fetch Legacy Config
    stmt_legacy = select(SystemConfig).where(SystemConfig.key == "main_config")
    result_legacy = await session.execute(stmt_legacy)
    legacy_record = result_legacy.scalar_one_or_none()
    
    raw_data = {}
    if legacy_record:
        raw_data = legacy_record.value
        logger.info(f"[Migration] Found legacy config in DB.")
    else:
        logger.warning("[Migration] No legacy config found. Creating defaults.")
    
    # ensure it matches Pydantic defaults if empty
    legacy_obj = LegacyConfig(**raw_data)
    
    # 3. Populate SocialProfile
    profiles_count = 0
    if legacy_obj.profiles:
        for p in legacy_obj.profiles:
            # Check exist first? No, table is empty.
            new_profile = SocialProfile(
                username=p.username,
                theme_key=p.theme_key,
                enabled=p.enabled if p.enabled is not None else True,
                platforms=p.platforms or [],
                instagram_limit=p.instagramLimit,
                tiktok_limit=p.tiktokLimit,
                youtube_limit=p.youtubeLimit,
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
    # Parse Cron Schedule to simple fields if possible
    schedule_enabled = False
    schedule_time = "00:00"
    
    cron = legacy_obj.cronSchedule or ""
    parts = cron.split(" ")
    if len(parts) >= 5 and cron != "":
        # Simple heuristic: if not empty, it was enabled. 
        # But we need to check if it was 'disabled' by user logic (usually cron="" means disabled)
        if cron.strip():
            schedule_enabled = True
            try:
                # cron: min hour * * *
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
        schedule_timezone="Europe/Moscow", # Default
        updated_at=datetime.utcnow()
    )
    session.add(new_settings)
    
    await session.commit()
    logger.info(f"[Migration] ✅ Success! Migrated {profiles_count} profiles, {clients_count} clients, and settings.")
