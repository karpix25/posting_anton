import json
import os
import logging
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import SystemConfig
from app.config import settings, LegacyConfig
from app.database import async_session_maker

from app.seed_data import CLIENTS_SEED

logger = logging.getLogger(__name__)

CONFIG_KEY = "main_config"

async def migrate_file_to_db():
    from app.database import async_session_maker
    async with async_session_maker() as session:
        # Check if exists
        stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        path = settings.get_config_path()
        file_data = {}
        
        # 1. Try to load local config file
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read config file: {e}")

        # 2. SEED INJECTION: If clients missing, use seed
        if "clients" not in file_data or not file_data["clients"]:
            if CLIENTS_SEED:
                logger.info("Injecting Seed Clients into migration data...")
                file_data["clients"] = CLIENTS_SEED
                
        if existing:
            # Auto-heal existing DB if clients missing
            db_val = existing.value
            if not db_val.get("clients") and CLIENTS_SEED:
                 logger.info("Auto-Healing: Injecting Seed Clients into existing DB config.")
                 db_val["clients"] = CLIENTS_SEED
                 existing.value = db_val
                 existing.updated_at = datetime.utcnow()
                 session.add(existing)
                 await session.commit()
            else:
                logger.info("Config found in DB. Skipping migration.")
            return

        # 3. Create new DB entry
        logger.info(f"Migrating config to DB...")
        
        # Ensure minimal fields
        if "cronSchedule" not in file_data:
            file_data["cronSchedule"] = "1 0 * * *"
        # 1. Try to load local config file
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read config file: {e}")
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
