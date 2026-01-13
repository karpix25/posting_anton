import json
import os
import logging
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import SystemConfig
from app.config import settings, LegacyConfig
from app.database import async_session_maker

logger = logging.getLogger(__name__)

CONFIG_KEY = "main_config"

async def migrate_file_to_db():
    """
    Checks if DB has config. If not, attempts to load from file and save to DB.
    """
    async with async_session_maker() as session:
        # Check if exists
        stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.info("Config found in DB. Skipping migration.")
            return

        # Not in DB, try file
        path = settings.get_config_path()
        if os.path.exists(path):
            logger.info(f"Migrating config from file {path} to DB...")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                
                # Create DB entry
                new_config = SystemConfig(key=CONFIG_KEY, value=file_data)
                session.add(new_config)
                await session.commit()
                logger.info("Migration successful.")
                
                # Optional: Rename file to avoid confusion?
                # os.rename(path, path + ".migrated")
            except Exception as e:
                logger.error(f"Failed to migrate config: {e}")
        else:
            logger.warning("No config file found. Creating default in DB.")
            default_config = LegacyConfig(
                limits={"instagram": 10, "tiktok": 10, "youtube": 2},
                cronSchedule="1 0 * * *"
            ).dict()
            new_config = SystemConfig(key=CONFIG_KEY, value=default_config)
            session.add(new_config)
            await session.commit()

async def get_db_config(session: AsyncSession) -> LegacyConfig:
    stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    
    if record:
        # logger.debug(f"DB Config Loaded: {record.value.get('cronSchedule')}")
        return LegacyConfig(**record.value)
    
    return LegacyConfig(limits={"instagram": 10, "tiktok": 10, "youtube": 2})

async def save_db_config(session: AsyncSession, config_data: dict):
    logger.info(f"Saving Config to DB. Cron: {config_data.get('cronSchedule')}")
    stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    
    if record:
        record.value = config_data
        record.updated_at = datetime.utcnow() # Fix dependency on settings.utc_now if not exists
    else:
        new_config = SystemConfig(key=CONFIG_KEY, value=config_data)
        session.add(new_config)
    
    await session.commit()
    logger.info("DB Commit Successful")
