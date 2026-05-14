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


def preserve_profile_theme_keys(config_data: dict, current_value: dict) -> int:
    """Restore existing profile category bindings when an incoming save omits them."""
    incoming_profiles = config_data.get("profiles")
    current_profiles = (current_value or {}).get("profiles") or []
    if not isinstance(incoming_profiles, list) or not current_profiles:
        return 0

    current_by_username = {
        str(profile.get("username", "")).strip().lower(): profile
        for profile in current_profiles
        if isinstance(profile, dict) and str(profile.get("username", "")).strip()
    }

    restored = 0
    for profile in incoming_profiles:
        if not isinstance(profile, dict):
            continue
        if str(profile.get("theme_key") or "").strip():
            continue

        username_key = str(profile.get("username", "")).strip().lower()
        current_theme_key = str((current_by_username.get(username_key) or {}).get("theme_key") or "").strip()
        if current_theme_key:
            profile["theme_key"] = current_theme_key
            restored += 1

    return restored


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


async def save_db_config(session: AsyncSession, config_data: dict, preserve_schedule: bool = True):
    incoming_cron = config_data.get("cronSchedule")
    logger.info(f"Saving Config to DB. Incoming Cron: {incoming_cron}")
    stmt = select(PostingSystemConfig).where(PostingSystemConfig.key == CONFIG_KEY)
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()

    if record:
        current_value = record.value or {}
        restored_theme_keys = preserve_profile_theme_keys(config_data, current_value)
        if restored_theme_keys:
            logger.warning(
                "Preserved %s profile category binding(s) while saving config.",
                restored_theme_keys,
            )

        if preserve_schedule:
            if "cronSchedule" in current_value:
                config_data["cronSchedule"] = current_value.get("cronSchedule")
            if "schedule" in current_value:
                config_data["schedule"] = current_value.get("schedule")
            logger.info(f"Preserved DB Schedule. Cron: {config_data.get('cronSchedule')}")

        record.value = config_data
        record.updated_at = datetime.utcnow()
    else:
        new_config = PostingSystemConfig(key=CONFIG_KEY, value=config_data)
        session.add(new_config)

    await session.commit()
    logger.info(f"DB Commit Successful. Final Cron: {config_data.get('cronSchedule')}")
