import json
import os
import asyncio
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from urllib.parse import urlsplit
import pytz
from fastapi import FastAPI, Depends, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings, LegacyConfig
from app.database import get_session, init_db, async_session_maker
from app.models import BrandStats, PostingHistory
from app.services.sora2_webhook import canonical_publication_url, send_sora2_webhook_for_post
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
from app.telegram_bot.service import (
    approve_video_request,
    delete_distribution_rule,
    ensure_telegram_schema,
    get_video_folder_view,
    list_distribution_rules,
    list_pending_approval_requests,
    list_request_users,
    list_video_requests,
    reject_video_request,
    upsert_distribution_rule,
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

from app.services.config_db import migrate_file_to_db, get_db_config, get_yandex_folders, save_db_config

# In-memory caches for expensive Yandex stats scans
stats_cache: Optional[Dict[str, Any]] = None
stats_cache_time: float = 0.0
STATS_CACHE_TTL = 60  # seconds

profile_status_sync_task: Optional[asyncio.Task] = None
telegram_bot_polling_task: Optional[asyncio.Task] = None
telegram_bot_instance: Optional[Any] = None
telegram_dispatcher_instance: Optional[Any] = None
yandex_cache_warmup_task: Optional[asyncio.Task] = None


def _walk_uploadpost_payload(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_uploadpost_payload(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_uploadpost_payload(item)


def _first_uploadpost_value(body: Dict[str, Any], keys: set[str]) -> Optional[str]:
    normalized_keys = {key.lower() for key in keys}
    for source in _walk_uploadpost_payload(body):
        for key, value in source.items():
            if str(key).lower() not in normalized_keys:
                continue
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
    return None


PUBLICATION_URL_KEYS = {
    "url",
    "post_url",
    "postUrl",
    "publication_url",
    "publicationUrl",
    "published_url",
    "publishedUrl",
    "permalink",
    "link",
    "share_url",
    "shareUrl",
}

PREFERRED_PUBLICATION_URL_KEYS = {
    "post_url",
    "posturl",
    "publication_url",
    "publicationurl",
    "published_url",
    "publishedurl",
    "permalink",
    "share_url",
    "shareurl",
}


def _iter_uploadpost_publication_url_candidates(body: Dict[str, Any]):
    normalized_keys = {key.lower() for key in PUBLICATION_URL_KEYS}
    for source in _walk_uploadpost_payload(body):
        for key, value in source.items():
            if str(key).lower() not in normalized_keys:
                continue
            if isinstance(value, (str, int, float)) and str(value).strip():
                yield str(key), str(value).strip()


def _publication_url_score(key: str, value: str, platform: Optional[str]) -> int:
    score = 0
    normalized_key = key.lower()
    if normalized_key in PREFERRED_PUBLICATION_URL_KEYS:
        score += 10

    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        path = parsed.path or ""
        query = parsed.query or ""
    except Exception:
        hostname = ""
        path = ""
        query = ""

    if platform == "youtube":
        if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and path.rstrip("/") == "/watch" and "v=" in query:
            score += 100
        elif hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and path.startswith("/shorts/"):
            score += 95
        elif hostname == "youtu.be" and path.strip("/"):
            score += 90
        elif "youtube" in hostname or hostname == "youtu.be":
            score += 50
    elif platform == "instagram":
        if "instagram.com" in hostname:
            score += 100
    elif platform == "tiktok":
        if "tiktok.com" in hostname:
            score += 100

    return score


def _extract_uploadpost_publication_url(body: Dict[str, Any], platform: Optional[str] = None) -> Optional[str]:
    candidates = list(_iter_uploadpost_publication_url_candidates(body))
    if not candidates:
        return None

    publication_url = max(candidates, key=lambda item: _publication_url_score(item[0], item[1], platform))[1]
    if publication_url:
        return canonical_publication_url(publication_url)
    return None


def _extract_uploadpost_tracking_ids(body: Dict[str, Any]) -> Dict[str, str]:
    tracking_ids: Dict[str, str] = {}
    keys = {
        "job_id",
        "jobId",
        "request_id",
        "requestId",
        "schedule_id",
        "scheduleId",
        "post_id",
        "postId",
        "publication_id",
        "publicationId",
        "upload_id",
        "uploadId",
        "upload_post_id",
        "uploadPostId",
    }
    normalized_keys = {key.lower() for key in keys}
    for source in _walk_uploadpost_payload(body):
        for key, value in source.items():
            if str(key).lower() not in normalized_keys:
                continue
            if isinstance(value, (str, int, float)) and str(value).strip():
                tracking_ids[str(key)] = str(value).strip()
    return tracking_ids


def _is_successful_upload_completed_event(body: Dict[str, Any], event_name: str) -> bool:
    event_compact = event_name.strip().lower().replace("-", "_")
    publication_url = _extract_uploadpost_publication_url(body)
    tracking_ids = _extract_uploadpost_tracking_ids(body)
    if publication_url and tracking_ids:
        return True

    success_events = {
        "upload_completed",
        "publication_completed",
        "post_published",
        "publish_completed",
        "publication_success",
        "upload_post_published",
    }
    if event_compact not in success_events and "publish" not in event_compact:
        return False

    success_value = _first_uploadpost_value(body, {"success", "ok"})
    status_value = str(_first_uploadpost_value(body, {"status", "state"}) or "").strip().lower()

    return str(success_value).lower() == "true" or status_value in {"success", "successful", "completed", "published"}


def _preserve_profile_categories_on_bulk_empty_update(
    incoming_config: Dict[str, Any],
    current_config: Dict[str, Any],
) -> int:
    incoming_profiles = incoming_config.get("profiles")
    current_profiles = current_config.get("profiles") or []
    if not isinstance(incoming_profiles, list) or not incoming_profiles or not current_profiles:
        return 0

    current_by_username = {
        str(profile.get("username", "")).strip().lower(): profile
        for profile in current_profiles
        if str(profile.get("username", "")).strip()
    }
    existing_bound_count = sum(
        1
        for profile in current_by_username.values()
        if str(profile.get("theme_key") or "").strip()
    )
    if existing_bound_count == 0:
        return 0

    incoming_empty_count = sum(
        1
        for profile in incoming_profiles
        if isinstance(profile, dict) and not str(profile.get("theme_key") or "").strip()
    )
    if incoming_empty_count < max(3, len(incoming_profiles) // 2):
        return 0

    restored = 0
    for profile in incoming_profiles:
        if not isinstance(profile, dict):
            continue
        if str(profile.get("theme_key") or "").strip():
            continue
        username_key = str(profile.get("username", "")).strip().lower()
        current_profile = current_by_username.get(username_key)
        current_theme_key = str((current_profile or {}).get("theme_key") or "").strip()
        if current_theme_key:
            profile["theme_key"] = current_theme_key
            restored += 1

    return restored


async def _handle_uploadpost_publication_webhook(body: Dict[str, Any], event_name: str) -> Optional[Dict[str, Any]]:
    if not _is_successful_upload_completed_event(body, event_name):
        return None

    publication_url = _extract_uploadpost_publication_url(body)
    tracking_ids = _extract_uploadpost_tracking_ids(body)
    tracking_values = {value for value in tracking_ids.values() if value}

    if not publication_url:
        logger.warning("[UploadPostWebhook] Successful publication event without publication URL")
        return {"success": True, "updated": False, "reason": "missing_publication_url"}

    if not tracking_values:
        logger.warning("[UploadPostWebhook] Successful publication event without job_id/request_id")
        return {"success": True, "updated": False, "reason": "missing_tracking_id"}

    async with async_session_maker() as session:
        stmt = select(PostingHistory).where(PostingHistory.status.in_(["queued", "processing", "success"]))
        result = await session.execute(stmt)
        posts = result.scalars().all()

        target = None
        for post in posts:
            meta = post.meta or {}
            meta_tracking_values = {
                str(value).strip()
                for key, value in meta.items()
                if key in {"job_id", "request_id", "schedule_id", "post_id", "publication_id", "upload_id", "upload_post_id"}
                and value is not None
                and str(value).strip()
            }
            if meta_tracking_values.intersection(tracking_values):
                target = post
                break

        if not target:
            logger.warning(f"[UploadPostWebhook] No local post found for tracking IDs: {tracking_ids}")
            return {"success": True, "updated": False, "reason": "post_not_found"}

        platform_publication_url = _extract_uploadpost_publication_url(body, target.platform)
        if platform_publication_url:
            publication_url = platform_publication_url

        current_meta = target.meta or {}
        if current_meta.get("sora2_webhook_sent_at"):
            logger.info(f"[UploadPostWebhook] SOra2 webhook already sent for post #{target.id}")
            return {"success": True, "updated": False, "reason": "sora2_already_sent", "post_id": target.id}

        was_success = target.status == "success"
        meta = target.meta.copy() if target.meta else {}
        meta["publication_url"] = publication_url
        meta["uploadpost_publication_webhook"] = body

        target.status = "success"
        target.meta = meta
        session.add(target)
        await session.commit()
        await session.refresh(target)

        sora2_result = await send_sora2_webhook_for_post(target, publication_url)
        target.meta = sora2_result["meta"]
        session.add(target)
        await session.commit()

    logger.info(f"[UploadPostWebhook] Publication URL saved for post #{target.id}: {publication_url}")

    if not was_success:
        try:
            from app.worker import check_cleanup, increment_brand_stats
            await increment_brand_stats(target.video_path)
            await check_cleanup(target.video_path)
        except Exception as e:
            logger.warning(f"[UploadPostWebhook] Post-success side effects failed for post #{target.id}: {e}")

    return {
        "success": True,
        "updated": True,
        "post_id": target.id,
        "sora2": {"sent": sora2_result["sent"], "reason": sora2_result["reason"]},
    }


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


def _yandex_cache_warmup_interval_seconds() -> int:
    raw = os.getenv("YANDEX_CACHE_WARMUP_INTERVAL_SECONDS", "1800")
    try:
        return max(300, int(raw))
    except (TypeError, ValueError):
        return 1800


async def warm_yandex_cache_once():
    """Warm Yandex file caches for dashboard and Telegram views."""
    started = time.monotonic()
    async with async_session_maker() as session:
        folders = await get_yandex_folders(session)
    folders = list(folders or [])
    if not folders:
        logger.info("[YandexWarmup] Skipped: no configured yandexFolders.")
        return

    await yandex_service.refresh_files_cache(
        limit=100000,
        folders=folders,
        cache_scope="default",
    )
    await yandex_service.refresh_files_cache(
        limit=100000,
        folders=folders,
        cache_scope="telegram",
    )
    elapsed = time.monotonic() - started
    logger.info(
        f"[YandexWarmup] Cache refreshed for folders={folders} in {elapsed:.1f}s "
        f"(scopes: default, telegram)."
    )


async def yandex_cache_warmup_loop():
    interval = _yandex_cache_warmup_interval_seconds()
    logger.info(f"[YandexWarmup] Started (interval={interval}s).")
    while True:
        try:
            await warm_yandex_cache_once()
        except Exception as e:
            logger.warning(f"[YandexWarmup] Refresh failed: {e}")
        await asyncio.sleep(interval)


async def start_telegram_bot_polling_if_configured():
    """Run Telegram polling inside the API process for single-service deploys."""
    global telegram_bot_polling_task, telegram_bot_instance, telegram_dispatcher_instance

    if not settings.TELEGRAM_BOT_AUTO_START:
        logger.info("[TelegramBot] Auto-start disabled.")
        return
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.info("[TelegramBot] TELEGRAM_BOT_TOKEN is not configured; skipping polling.")
        return
    if telegram_bot_polling_task and not telegram_bot_polling_task.done():
        logger.info("[TelegramBot] Polling is already running.")
        return

    try:
        from aiogram import Bot, Dispatcher
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from app.telegram_bot.handlers import router as telegram_router

        telegram_bot_instance = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        telegram_dispatcher_instance = Dispatcher()
        telegram_dispatcher_instance.include_router(telegram_router)

        await telegram_bot_instance.delete_webhook(drop_pending_updates=False)
        telegram_bot_polling_task = asyncio.create_task(
            telegram_dispatcher_instance.start_polling(telegram_bot_instance)
        )
        logger.info("[TelegramBot] Polling started inside API process.")
    except Exception as e:
        logger.error(f"[TelegramBot] Failed to start polling: {e}")

# Startup event
@app.on_event("startup")
async def on_startup():
    logger.info("Application starting up...")
    await init_db()
    await migrate_file_to_db()
    
    # Log current schedule state and Force-Check Clients
    try:
        from app.services.config_db import get_ai_clients_from_table, get_db_config, save_db_config
        from app.database import async_session_maker
        from app.seed_data import CLIENTS_SEED
        
        async with async_session_maker() as session:
             await ensure_telegram_schema(session)
             cfg = await get_db_config(session)
             
             # AGGRESSIVE AUTO-SEED
             table_clients = await get_ai_clients_from_table(session)
             if table_clients is None and not cfg.clients and CLIENTS_SEED:
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

             global profile_status_sync_task, yandex_cache_warmup_task
             profile_status_sync_task = asyncio.create_task(profile_status_sync_loop())
             yandex_cache_warmup_task = asyncio.create_task(yandex_cache_warmup_loop())

             await start_telegram_bot_polling_if_configured()
             
    except Exception as e:
        logger.error(f"Startup failed: {e}")


@app.on_event("shutdown")
async def on_shutdown():
    global profile_status_sync_task, yandex_cache_warmup_task, telegram_bot_polling_task, telegram_bot_instance, telegram_dispatcher_instance
    if profile_status_sync_task and not profile_status_sync_task.done():
        profile_status_sync_task.cancel()
    if yandex_cache_warmup_task and not yandex_cache_warmup_task.done():
        yandex_cache_warmup_task.cancel()
    if telegram_dispatcher_instance and telegram_bot_polling_task and not telegram_bot_polling_task.done():
        try:
            await telegram_dispatcher_instance.stop_polling()
        except RuntimeError:
            pass
    if telegram_bot_polling_task and not telegram_bot_polling_task.done():
        telegram_bot_polling_task.cancel()
    if telegram_bot_instance:
        await telegram_bot_instance.session.close()

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}

@app.get("/api/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    config = await get_db_config(session)
    return config.dict()

@app.post("/api/config")
async def update_config(config_data: Dict[str, Any], session: AsyncSession = Depends(get_session)):
    current_config = (await get_db_config(session)).dict()
    restored_categories = _preserve_profile_categories_on_bulk_empty_update(config_data, current_config)
    if restored_categories:
        logger.warning(
            "[Config] Prevented bulk profile category wipe: restored %s theme_key values from DB",
            restored_categories,
        )

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


@app.get("/api/telegram/requests")
async def api_telegram_requests(status: Optional[str] = None, limit: int = 200):
    rows = await list_video_requests(limit=limit, status=status)
    return {"success": True, "items": rows}


@app.get("/api/telegram/requests/pending")
async def api_telegram_pending_requests(limit: int = 200):
    rows = await list_pending_approval_requests(limit=limit)
    return {"success": True, "items": rows}


@app.post("/api/telegram/requests/{request_id}/approve")
async def api_telegram_approve_request(request_id: int, payload: Dict[str, Any] = Body(default={})):
    admin_user_id = int(payload.get("admin_user_id") or 0)
    try:
        result = await approve_video_request(request_id=request_id, admin_user_id=admin_user_id)
    except LookupError as exc:
        code = str(exc)
        if code == "request_not_found":
            raise HTTPException(status_code=404, detail="Request not found")
        if code == "rule_not_found":
            raise HTTPException(status_code=400, detail="No active distribution rule for this user")
        if code == "no_videos_for_rule":
            raise HTTPException(status_code=409, detail="No available videos in assigned folder")
        raise HTTPException(status_code=400, detail=code)
    except ValueError as exc:
        code = str(exc)
        if code == "weekly_limit_exceeded":
            raise HTTPException(status_code=409, detail="Weekly limit reached for this user")
        if code == "rule_folder_empty":
            raise HTTPException(status_code=400, detail="Rule folder is empty")
        if code == "rule_weekly_limit_zero":
            raise HTTPException(status_code=400, detail="Weekly limit is zero")
        raise HTTPException(status_code=400, detail=code)

    row = result.request
    return {
        "success": True,
        "delivered": result.delivered,
        "request": {
            "id": row.id,
            "telegram_user_id": row.telegram_user_id,
            "status": row.status,
            "brand": row.brand,
            "video_name": row.video_name,
            "assigned_folder_prefix": row.assigned_folder_prefix,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        },
    }


@app.post("/api/telegram/requests/{request_id}/reject")
async def api_telegram_reject_request(request_id: int, payload: Dict[str, Any] = Body(default={})):
    admin_user_id = int(payload.get("admin_user_id") or 0)
    reason = str(payload.get("reason") or "").strip()
    ok = await reject_video_request(request_id=request_id, admin_user_id=admin_user_id, reason=reason)
    if not ok:
        raise HTTPException(status_code=400, detail="Request cannot be rejected in current status")
    return {"success": True}


@app.get("/api/telegram/rules")
async def api_telegram_rules(limit: int = 500):
    rules = await list_distribution_rules(limit=limit)
    return {"success": True, "items": rules}


@app.post("/api/telegram/rules")
async def api_telegram_upsert_rule(payload: Dict[str, Any]):
    telegram_user_id = int(payload.get("telegram_user_id") or 0)
    folder_prefix = str(payload.get("folder_prefix") or "").strip()
    weekly_limit = int(payload.get("weekly_limit") or 0)
    is_active = bool(payload.get("is_active", True))
    telegram_username = payload.get("telegram_username")
    telegram_full_name = payload.get("telegram_full_name")
    if telegram_user_id <= 0:
        raise HTTPException(status_code=400, detail="telegram_user_id is required")
    if not folder_prefix:
        raise HTTPException(status_code=400, detail="folder_prefix is required")
    rule = await upsert_distribution_rule(
        telegram_user_id=telegram_user_id,
        folder_prefix=folder_prefix,
        weekly_limit=weekly_limit,
        is_active=is_active,
        telegram_username=telegram_username,
        telegram_full_name=telegram_full_name,
    )
    return {"success": True, "item": rule}


@app.delete("/api/telegram/rules/{rule_id}")
async def api_telegram_delete_rule(rule_id: int):
    ok = await delete_distribution_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"success": True}


@app.get("/api/telegram/users")
async def api_telegram_users(limit: int = 500):
    users = await list_request_users(limit=limit)
    return {"success": True, "items": users}


@app.get("/api/telegram/folders")
async def api_telegram_folders(prefix: str = ""):
    prefix_tuple = tuple(part.strip() for part in prefix.split("/") if part.strip())
    view = await get_video_folder_view(prefix_tuple)
    return {
        "success": True,
        "prefix": list(view.prefix),
        "title": view.title,
        "total_videos": view.total_videos,
        "children": [
            {
                "name": child.name,
                "prefix": list(child.prefix),
                "video_count": child.video_count,
                "child_count": child.child_count,
            }
            for child in view.children
        ],
    }


@app.get("/api/uploadpost/webhook")
@app.head("/api/uploadpost/webhook")
@app.get("/api/webhooks/upload-post")
@app.head("/api/webhooks/upload-post")
async def upload_post_webhook_health(request: Request):
    logger.info(f"[UploadPostWebhook] Health check hit: method={request.method} path={request.url.path}")
    return {"success": True, "status": "ok", "message": "UploadPost webhook is ready"}


@app.post("/api/uploadpost/webhook")
@app.post("/api/webhooks/upload-post")
async def upload_post_webhook(request: Request):
    """
    Receive Upload Post profile connectivity events and patch local profile statuses.
    Supported events:
    - social_account_connected
    - social_account_disconnected
    - social_account_reauth_required
    """
    logger.info(
        f"[UploadPostWebhook] Incoming request: method={request.method} path={request.url.path} "
        f"query={dict(request.query_params)}"
    )
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(
        "[UploadPostWebhook] Payload received: "
        f"event={body.get('event') or body.get('type') or body.get('event_name') or ''} "
        f"username={body.get('username') or body.get('profile_username') or body.get('user') or body.get('account') or ''} "
        f"platform={body.get('platform') or body.get('social_account') or body.get('provider') or ''} "
        f"keys={sorted(list(body.keys()))}"
    )

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

    publication_result = await _handle_uploadpost_publication_webhook(body, event_name)
    if publication_result is not None:
        return publication_result

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
    
    global stats_cache, stats_cache_time
    try:
        now_ts = time.time()
        if not refresh and stats_cache and (now_ts - stats_cache_time) < STATS_CACHE_TTL:
            return stats_cache

        # Load folders from config
        config = await get_db_config(session)
        # Ensure theme alias resolution in utils uses DB config, not stale file config
        settings._legacy_config = config
        all_videos = []
        
        files = await yandex_service.list_files(
            limit=100000,
            force_refresh=refresh,
            folders=config.yandexFolders,
        )
        
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

    schedule_config = config.schedule

    # Default state
    enabled = False
    daily_time = "00:00"
    timezone = "Europe/Moscow"

    if schedule_config:
        enabled = schedule_config.enabled and bool(cron)
        daily_time = schedule_config.dailyRunTime or daily_time
        timezone = schedule_config.timezone or timezone

    # Parse Cron: "min hour * * *"
    # Simple check: does it have 5 parts?
    parts = cron.split(" ")
    if len(parts) >= 5:
        # Check if it looks like a daily schedule: "* * * * *" is not enabled per se, but "M H * * *" is.
        # We assume if it's set, it's enabled.
        if not schedule_config:
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
    timezone_name = payload.get("timezone", "Europe/Moscow")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be a boolean")

    if not isinstance(timezone_name, str):
        raise HTTPException(status_code=400, detail="timezone must be a string")

    try:
        pytz.timezone(timezone_name)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid timezone")
    
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

    data["schedule"] = {
        **(data.get("schedule") or {}),
        "enabled": enabled,
        "timezone": timezone_name,
        "dailyRunTime": daily_time,
    }

    # Persist
    try:
        await save_db_config(session, data, preserve_schedule=False)
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
    """Cancel Upload Post scheduled jobs and delete local queued posts."""
    try:
        from sqlalchemy import delete

        scheduled_posts = await upload_post_client.get_scheduled_posts()
        cancelled_count = 0
        failed_count = 0
        seen_job_ids = set()
        job_ids = []

        for post in scheduled_posts:
            if not isinstance(post, dict):
                continue
            job_id = post.get("job_id") or post.get("jobId") or post.get("id")
            if not job_id or job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            job_ids.append(str(job_id))

        logger.info(f"🗑️ Cleanup: cancelling {len(job_ids)} UploadPost scheduled jobs slowly.")

        for job_id in job_ids:
            if await upload_post_client.cancel_scheduled_post(job_id):
                cancelled_count += 1
            else:
                failed_count += 1

            if (cancelled_count + failed_count) % 10 == 0:
                logger.info(
                    f"🗑️ Cleanup progress: processed={cancelled_count + failed_count}/"
                    f"{len(job_ids)}, cancelled={cancelled_count}, failed={failed_count}"
                )
            await asyncio.sleep(2.0)

        stmt = delete(PostingHistory).where(PostingHistory.status.in_(["queued", "processing"]))
        result = await session.execute(stmt)
        await session.commit()
        
        deleted_count = result.rowcount
        logger.info(
            f"🗑️ Cleanup: cancelled={cancelled_count}, failed_cancel={failed_count}, "
            f"deleted_local={deleted_count}"
        )
        return {
            "success": failed_count == 0,
            "message": (
                f"Отменено в UploadPost: {cancelled_count}. "
                f"Не удалось отменить: {failed_count}. "
                f"Удалено локально: {deleted_count}."
            ),
            "cancelled": cancelled_count,
            "failed_cancel": failed_count,
            "deleted": deleted_count,
        }
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return {"success": False, "message": f"Ошибка: {str(e)}"}

@app.post("/api/run")
async def run_automation(test_mode: bool = False, dry_run: bool = False):
    """Manually trigger the daily schedule generation."""
    from app.worker import generate_daily_schedule
    # Run in background without blocking
    asyncio.create_task(generate_daily_schedule(test_mode=test_mode, dry_run=dry_run))
    if dry_run:
        message = "Dry-run started in background. No DB records or UploadPost requests will be created."
    elif test_mode:
        message = "Test automation started in background."
    else:
        message = "Automation started in background"
    return {"success": True, "message": message, "test_mode": test_mode, "dry_run": dry_run}

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
