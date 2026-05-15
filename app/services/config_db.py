import json
import os
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import PostingSystemConfig
from app.config import settings, LegacyConfig
from app.database import async_session_maker

from app.seed_data import CLIENTS_SEED

logger = logging.getLogger(__name__)

CONFIG_KEY = "main_config"

DEFAULT_LIMITS = {"instagram": 10, "tiktok": 10, "youtube": 2}
AI_CLIENT_TABLE_NAMES = ("ai_clients_db", "Ai Clients Db", "ai clients db")
AI_CLIENT_FALLBACK_TABLE_NAMES = ("ai_clients",)


def _sanitize_clients(clients: list) -> list:
    if not isinstance(clients, list):
        return []

    seen = set()
    sanitized = []
    for raw in clients:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        dedupe_key = name.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        quota = raw.get("quota")
        try:
            quota = int(quota) if quota not in (None, "") else None
        except (TypeError, ValueError):
            quota = None

        sanitized.append({
            "name": name,
            "regex": str(raw.get("regex") or "").strip(),
            "prompt": str(raw.get("prompt") or ""),
            "quota": quota,
        })

    return sanitized


def _quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'


async def _find_table_by_names(session: AsyncSession, names: tuple[str, ...]) -> Optional[dict]:
    normalized = [name.lower() for name in names]
    result = await session.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND lower(table_name) IN :names
            ORDER BY
              CASE WHEN table_schema = 'public' THEN 0 ELSE 1 END,
              table_schema,
              table_name
            LIMIT 1
            """
        ).bindparams(bindparam("names", expanding=True)),
        {"names": normalized},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _get_table_columns(session: AsyncSession, schema_name: str, table_name: str) -> dict:
    result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = :table_name
            """
        ),
        {"schema_name": schema_name, "table_name": table_name},
    )
    return {str(row.column_name).lower(): str(row.column_name) for row in result}


async def _find_ai_clients_table(session: AsyncSession) -> Optional[dict]:
    table = await _find_table_by_names(session, AI_CLIENT_TABLE_NAMES)
    if table:
        return table
    return await _find_table_by_names(session, AI_CLIENT_FALLBACK_TABLE_NAMES)


async def get_ai_clients_from_table(session: AsyncSession) -> Optional[list]:
    table = await _find_ai_clients_table(session)
    if not table:
        return None

    schema_name = table["table_schema"]
    table_name = table["table_name"]
    columns = await _get_table_columns(session, schema_name, table_name)
    name_col = columns.get("name")
    regex_col = columns.get("regex")
    prompt_col = columns.get("prompt")
    if not name_col or not regex_col or not prompt_col:
        logger.warning("AI clients table %s.%s is missing name/regex/prompt columns.", schema_name, table_name)
        return None

    quota_col = columns.get("quota")
    sort_col = columns.get("sort_order")
    updated_at_col = columns.get("updated_at")
    table_ref = f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"

    select_parts = [
        f"{_quote_identifier(name_col)} AS name",
        f"{_quote_identifier(regex_col)} AS regex",
        f"{_quote_identifier(prompt_col)} AS prompt",
        f"{_quote_identifier(quota_col)} AS quota" if quota_col else "NULL::int AS quota",
    ]
    order_parts = []
    if sort_col:
        order_parts.append(f"{_quote_identifier(sort_col)} ASC")
    if updated_at_col:
        order_parts.append(f"{_quote_identifier(updated_at_col)} DESC")
    order_parts.append(f"{_quote_identifier(name_col)} ASC")

    result = await session.execute(text(f"SELECT {', '.join(select_parts)} FROM {table_ref} ORDER BY {', '.join(order_parts)}"))
    clients = _sanitize_clients([dict(row) for row in result.mappings().all()])
    logger.info("Loaded %s AI clients from %s.%s.", len(clients), schema_name, table_name)
    return clients


async def replace_ai_clients_in_table(session: AsyncSession, clients: list) -> Optional[list]:
    table = await _find_ai_clients_table(session)
    if not table:
        return None

    schema_name = table["table_schema"]
    table_name = table["table_name"]
    columns = await _get_table_columns(session, schema_name, table_name)
    name_col = columns.get("name")
    regex_col = columns.get("regex")
    prompt_col = columns.get("prompt")
    if not name_col or not regex_col or not prompt_col:
        raise ValueError(f"AI clients table {schema_name}.{table_name} is missing name/regex/prompt columns")

    quota_col = columns.get("quota")
    sort_col = columns.get("sort_order")
    updated_at_col = columns.get("updated_at")
    table_ref = f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
    safe_clients = _sanitize_clients(clients)

    await session.execute(text(f"DELETE FROM {table_ref}"))
    for index, client in enumerate(safe_clients):
        insert_cols = [_quote_identifier(name_col), _quote_identifier(regex_col), _quote_identifier(prompt_col)]
        param_names = ["name", "regex", "prompt"]
        params = {
            "name": client["name"],
            "regex": client["regex"],
            "prompt": client["prompt"],
        }

        if quota_col:
            insert_cols.append(_quote_identifier(quota_col))
            param_names.append("quota")
            params["quota"] = client["quota"]
        if sort_col:
            insert_cols.append(_quote_identifier(sort_col))
            param_names.append("sort_order")
            params["sort_order"] = index
        if updated_at_col:
            insert_cols.append(_quote_identifier(updated_at_col))
            param_names.append("updated_at")
            params["updated_at"] = datetime.utcnow()

        values_sql = ", ".join(f":{name}" for name in param_names)
        await session.execute(
            text(f"INSERT INTO {table_ref} ({', '.join(insert_cols)}) VALUES ({values_sql})"),
            params,
        )

    logger.info("Saved %s AI clients to %s.%s.", len(safe_clients), schema_name, table_name)
    return safe_clients


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
            table_clients = await get_ai_clients_from_table(session)

            is_corrupt = not db_val.get("limits")
            needs_clients = table_clients is None and not db_val.get("clients")

            if is_corrupt:
                logger.warning("Auto-Healing: config record is empty/corrupt. Rebuilding with defaults.")
                db_val = {
                    "cronSchedule": "1 0 * * *",
                    "limits": DEFAULT_LIMITS,
                    "clients": table_clients or CLIENTS_SEED,
                    "profiles": [],
                    "yandexFolders": [],
                    "daysToGenerate": 7,
                    "themeAliases": {},
                    "brandQuotas": {},
                }
            elif table_clients is not None:
                db_val["clients"] = table_clients
            elif needs_clients and CLIENTS_SEED:
                logger.info("Auto-Healing: Injecting Seed Clients into existing DB config.")
                db_val["clients"] = CLIENTS_SEED

            if is_corrupt or needs_clients or table_clients is not None:
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
        table_clients = await get_ai_clients_from_table(session)
        if table_clients is not None:
            file_data["clients"] = table_clients

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
            config_value = dict(record.value or {})
            table_clients = await get_ai_clients_from_table(session)
            if table_clients is not None:
                config_value["clients"] = table_clients
            return LegacyConfig(**config_value)
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
        if "clients" in config_data:
            table_clients = await replace_ai_clients_in_table(session, config_data.get("clients") or [])
            if table_clients is not None:
                config_data["clients"] = table_clients

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
        if "clients" in config_data:
            table_clients = await replace_ai_clients_in_table(session, config_data.get("clients") or [])
            if table_clients is not None:
                config_data["clients"] = table_clients
        new_config = PostingSystemConfig(key=CONFIG_KEY, value=config_data)
        session.add(new_config)

    await session.commit()
    logger.info(f"DB Commit Successful. Final Cron: {config_data.get('cronSchedule')}")
