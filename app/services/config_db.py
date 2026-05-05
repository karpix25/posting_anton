import json
import os
import logging
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import PostingSystemConfig
from app.config import settings, LegacyConfig
from app.database import async_session_maker

from app.seed_data import CLIENTS_SEED

logger = logging.getLogger(__name__)

CONFIG_KEY = "main_config"

DEFAULT_LIMITS = {"instagram": 10, "tiktok": 10, "youtube": 2}


async def migrate_file_to_db():
    from app.database import async_session_maker
    async with async_session_maker() as session:
        stmt = select(PostingSystemConfig).where(PostingSystemConfig.key == CONFIG_KEY)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            db_val = existing.value or {}

            is_corrupt = not db_val.get("limits")
            needs_clients = not db_val.get("clients")

            if is_corrupt:
                logger.warning("Auto-Healing: config record is empty/corrupt. Rebuilding with defaults.")
                db_val = {
                    "cronSchedule": "1 0 * * *",
                    "limits": DEFAULT_LIMITS,
                    "clients": CLIENTS_SEED,
                    "profiles": [],
                    "yandexFolders": [],
                    "daysToGenerate": 7,
                    "themeAliases": {},
                    "brandQuotas": {},
                }
            elif needs_clients and CLIENTS_SEED:
                logger.info("Auto-Healing: Injecting Seed Clients into existing DB config.")
                db_val["clients"] = CLIENTS_SEED

            if is_corrupt or needs_clients:
                existing.value = db_val
                existing.updated_at = datetime.utcnow()
                session.add(existing)
                await session.commit()
            else:
                logger.info("Config found in DB. Skipping migration.")
            return

        # No record — try loading from local config file first
        path = settings.get_config_path()
        file_data = {}

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                logger.info(f"Loaded config from file: {path}")
            except Exception as e:
                logger.error(f"Failed to read config file: {e}")
        else:
            logger.warning("No config file found. Will create default in DB.")

        # Ensure required fields are always present
        if "limits" not in file_data:
            file_data["limits"] = DEFAULT_LIMITS
        if "cronSchedule" not in file_data:
            file_data["cronSchedule"] = "1 0 * * *"
        if not file_data.get("clients") and CLIENTS_SEED:
            logger.info("Injecting Seed Clients into new DB config.")
            file_data["clients"] = CLIENTS_SEED

        logger.info("Creating new config record in posting_system_config.")
        new_config = PostingSystemConfig(key=CONFIG_KEY, value=file_data)
        session.add(new_config)
        await session.commit()


async def get_db_config(session: AsyncSession) -> LegacyConfig:
    stmt = select(PostingSystemConfig).where(PostingSystemConfig.key == CONFIG_KEY)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()

    if record:
        try:
            return LegacyConfig(**record.value)
        except Exception as e:
            logger.error(f"Corrupt config in DB, using defaults: {e}")

    return LegacyConfig(limits=DEFAULT_LIMITS)


async def save_db_config(session: AsyncSession, config_data: dict):
    logger.info(f"Saving Config to DB. Cron: {config_data.get('cronSchedule')}")
    stmt = select(PostingSystemConfig).where(PostingSystemConfig.key == CONFIG_KEY)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()

    if record:
        record.value = config_data
        record.updated_at = datetime.utcnow()
    else:
        new_config = PostingSystemConfig(key=CONFIG_KEY, value=config_data)
        session.add(new_config)

    await session.commit()
    logger.info("DB Commit Successful")
