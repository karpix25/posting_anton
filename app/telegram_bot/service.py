import asyncio
import html
import logging
import os
import random
import re
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ClientConfig
from app.config import settings
from app.database import async_session_maker
from app.models import TelegramDistributionRule, TelegramVideoRequest
from app.services.config_db import get_db_config, get_yandex_folders
from app.services.content_generator import content_generator
from app.services.scheduler import ContentScheduler
from app.services.yandex import yandex_service
from app.utils import extract_author

logger = logging.getLogger(__name__)

TELEGRAM_BOT_FILE_LIMIT_BYTES = 50 * 1024 * 1024
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SENT = "sent"
STATUS_REPORTED = "reported"
STATUS_ARCHIVED = "archived"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"

REPORTABLE_STATUSES = (STATUS_SENT, STATUS_REPORTED)
RESERVED_STATUSES = (STATUS_APPROVED, STATUS_SENT, STATUS_REPORTED, STATUS_ARCHIVED)
OPEN_REQUEST_STATUSES = (STATUS_PENDING_APPROVAL, STATUS_APPROVED, STATUS_SENT)
ACTIVE_VIDEO_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_video_requests_active_video_path
ON telegram_video_requests(video_path)
WHERE status IN ('approved', 'sent', 'reported', 'archived')
  AND video_path NOT LIKE 'pending://%'
"""
TELEGRAM_SCHEMA_MIGRATION_SQL = [
    """
    ALTER TABLE telegram_video_requests
      ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP NULL
    """,
    """
    ALTER TABLE telegram_video_requests
      ADD COLUMN IF NOT EXISTS approved_by BIGINT NULL
    """,
    """
    ALTER TABLE telegram_video_requests
      ADD COLUMN IF NOT EXISTS week_key VARCHAR(16) NULL
    """,
    """
    ALTER TABLE telegram_video_requests
      ADD COLUMN IF NOT EXISTS assigned_folder_prefix TEXT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telegram_video_requests_week_key
    ON telegram_video_requests(week_key)
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_distribution_rules (
      id SERIAL PRIMARY KEY,
      telegram_user_id BIGINT NOT NULL,
      telegram_username VARCHAR(255),
      telegram_full_name VARCHAR(255),
      folder_prefix TEXT NOT NULL,
      weekly_limit INTEGER NOT NULL DEFAULT 1,
      is_active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMP NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_telegram_distribution_rules_user
    ON telegram_distribution_rules(telegram_user_id)
    """,
]
YOUTUBE_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[^\s]+$",
    re.IGNORECASE,
)
FOLDER_INVENTORY_TTL_SECONDS = 30 * 60
_folder_inventory_cache: dict[tuple[str, ...], tuple[float, list[dict]]] = {}
_folder_inventory_lock = None
_schema_ready = False
_schema_lock = None


@dataclass
class PreparedVideo:
    request: TelegramVideoRequest
    download_link: str
    size: Optional[int] = None

    @property
    def should_send_as_link(self) -> bool:
        return self.size is not None and self.size > TELEGRAM_BOT_FILE_LIMIT_BYTES


@dataclass(frozen=True)
class FolderOption:
    name: str
    prefix: tuple[str, ...]
    video_count: int
    child_count: int


@dataclass(frozen=True)
class FolderView:
    prefix: tuple[str, ...]
    title: str
    total_videos: int
    children: list[FolderOption]


@dataclass(frozen=True)
class ApprovalResult:
    request: TelegramVideoRequest
    delivered: bool


def _normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е").replace(" ", "").replace("-", "").strip()


def _strip_youtube_template_tokens(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?is)\[/?YT_(?:TITLE|DESCRIPTION)\]", " ", text)
    text = re.sub(r"(?i)/?YT_(?:TITLE|DESCRIPTION)\b", " ", text)
    text = re.sub(r"(?im)^\s*(?:YT_TITLE|YT_DESCRIPTION)\s*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    hashtag_match = re.search(r"(?m)(^|\s)(#\S+)", text)
    if hashtag_match:
        hashtag_start = hashtag_match.start(2)
        body = text[:hashtag_start].strip()
        hashtags = text[hashtag_start:].strip()
        if body and hashtags:
            text = f"{body}\n\n{hashtags}"

    return text


def _short_youtube_title_from_text(text: str) -> str:
    clean = re.sub(r"#\S+", "", _strip_youtube_template_tokens(text))
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return "Видео Shorts"
    title = " ".join(clean.split()[:7]).strip(" .,!?:;\"'")
    return title[:97].rstrip() + "..." if len(title) > 100 else title


def parse_youtube_text(generated: str) -> tuple[str, str]:
    text = (generated or "").strip()
    if not text:
        return "Видео Shorts", ""

    tagged = re.search(
        r"(?is)\[YT_TITLE\]\s*(.*?)\s*\[/YT_TITLE\]\s*\[YT_DESCRIPTION\]\s*(.*?)\s*\[/YT_DESCRIPTION\]",
        text,
    )
    if tagged:
        title = _strip_youtube_template_tokens(tagged.group(1))
        description = _strip_youtube_template_tokens(tagged.group(2))
        return title or _short_youtube_title_from_text(description), description

    if "$$$" in text:
        parts = [part.strip() for part in text.split("$$$") if part.strip()]
        title = _strip_youtube_template_tokens(parts[0]) if parts else ""
        description = _strip_youtube_template_tokens("\n\n".join(parts[1:])) if len(parts) > 1 else ""
        return title or _short_youtube_title_from_text(description), description or text

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        title = _strip_youtube_template_tokens(lines[0])
        description = _strip_youtube_template_tokens("\n".join(lines[1:]))
        return title or _short_youtube_title_from_text(description), description

    description = _strip_youtube_template_tokens(text)
    return _short_youtube_title_from_text(description), description


def is_youtube_url(text: str) -> bool:
    return bool(YOUTUBE_URL_RE.match((text or "").strip()))


def _client_matches_extracted_brand(client: ClientConfig, brand_name: str) -> bool:
    normalized_brand = _normalize(brand_name)
    normalized_client = _normalize(client.name)
    if normalized_client == normalized_brand:
        return True

    if client.regex:
        try:
            if re.fullmatch(client.regex, brand_name, re.IGNORECASE):
                return True
            if re.fullmatch(_normalize(client.regex), normalized_brand, re.IGNORECASE):
                return True
        except re.error:
            logger.warning("Invalid AI client regex for %s: %s", client.name, client.regex)

    return False


def _client_matches_video_path(client: ClientConfig, video_path: str) -> bool:
    if client.regex:
        try:
            if re.search(client.regex, video_path, re.IGNORECASE):
                return True
        except re.error:
            logger.warning("Invalid AI client regex for %s: %s", client.name, client.regex)

    return _normalize(client.name) in _normalize(video_path)


def _find_client_for_video(
    clients: list[ClientConfig],
    scheduler: ContentScheduler,
    video: dict,
) -> Optional[ClientConfig]:
    video_path = str(video.get("path") or "")
    extracted_brand = scheduler.extract_brand(video_path)
    for client in clients:
        if _client_matches_extracted_brand(client, extracted_brand):
            return client
    for client in clients:
        if _client_matches_video_path(client, video_path):
            return client
    return None


async def list_brands() -> list[str]:
    async with async_session_maker() as session:
        config = await get_db_config(session)
    return [client.name for client in config.clients if client.name]


async def build_video_inventory_text() -> str:
    async with async_session_maker() as session:
        config = await get_db_config(session)

    videos = await yandex_service.list_files(
        limit=100000,
        force_refresh=True,
        folders=config.yandexFolders,
        cache_scope="telegram",
    )
    scheduler = ContentScheduler(config)
    tree = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for video in videos:
        path = str(video.get("path") or "")
        category, brand, product = _extract_inventory_parts(path, scheduler)
        if category == "unknown" or brand == "unknown":
            continue
        tree[category][brand][product] += 1

    if not tree:
        return "Видео по структуре категорий не найдены."

    lines = [f"Видео на диске: {len(videos)}", ""]
    for category in sorted(tree):
        lines.append(category)
        for brand in sorted(tree[category]):
            brand_total = sum(tree[category][brand].values())
            lines.append(f"  {brand} — {brand_total} видео")
            for product, count in sorted(tree[category][brand].items()):
                if product == ROOT_PRODUCT_GROUP_LABEL:
                    lines.append(f"    Без продуктовой папки — {count} видео")
                else:
                    lines.append(f"    {product} — {count} видео")
        lines.append("")

    return "\n".join(lines).strip()


async def get_video_folder_view(prefix: tuple[str, ...] = ()) -> FolderView:
    async with async_session_maker() as session:
        folders = await get_yandex_folders(session)

    videos = await _get_cached_folder_inventory(folders)
    prefix = tuple(prefix)
    child_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"videos": 0, "children": 0})
    child_names: dict[str, str] = {}
    total_videos = 0

    for video in videos:
        segments = _extract_navigation_segments(str(video.get("path") or ""))
        if not _segments_match_prefix(segments, prefix):
            continue
        total_videos += 1
        if len(segments) <= len(prefix):
            continue
        child_name = segments[len(prefix)]
        child_key = _normalize(child_name)
        child_names.setdefault(child_key, child_name)
        child_stats[child_key]["videos"] += 1
        if len(segments) > len(prefix) + 1:
            child_stats[child_key]["children"] += 1

    children = [
        FolderOption(
            name=child_names[key],
            prefix=prefix + (child_names[key],),
            video_count=stats["videos"],
            child_count=stats["children"],
        )
        for key, stats in child_stats.items()
    ]
    children.sort(key=lambda item: item.name.casefold())

    title = "Выберите папку после автора" if not prefix else " / ".join(prefix)
    return FolderView(prefix=prefix, title=title, total_videos=total_videos, children=children)


async def _get_cached_folder_inventory(folders: list[str] | None) -> list[dict]:
    global _folder_inventory_lock
    if _folder_inventory_lock is None:
        _folder_inventory_lock = asyncio.Lock()

    key = tuple(sorted(str(folder) for folder in (folders or []) if folder))
    now = time.monotonic()
    cached = _folder_inventory_cache.get(key)
    if cached and now - cached[0] < FOLDER_INVENTORY_TTL_SECONDS:
        logger.info("[TelegramFolders] Cache hit (fresh): key=%s, age=%.1fs", key, now - cached[0])
        return cached[1]

    lock_wait_started = time.monotonic()
    try:
        await asyncio.wait_for(_folder_inventory_lock.acquire(), timeout=7.0)
    except asyncio.TimeoutError:
        stale = _folder_inventory_cache.get(key)
        if stale:
            stale_age = time.monotonic() - stale[0]
            logger.warning(
                "[TelegramFolders] Cache lock busy >7s; returning stale cache (age=%.1fs) for key=%s",
                stale_age,
                key,
            )
            return stale[1]

        logger.warning(
            "[TelegramFolders] Cache lock busy >7s and no stale cache; waiting for active refresh key=%s",
            key,
        )
        async with _folder_inventory_lock:
            pass
        async with _folder_inventory_lock:
            # The first waiter has finished refresh by now; try cached value first.
            now = time.monotonic()
            cached = _folder_inventory_cache.get(key)
            if cached and now - cached[0] < FOLDER_INVENTORY_TTL_SECONDS:
                logger.info("[TelegramFolders] Cache became available after waiting: key=%s", key)
                return cached[1]

            videos = await yandex_service.list_files(
                limit=100000,
                force_refresh=False,
                folders=list(key),
                cache_scope="telegram",
            )
            _folder_inventory_cache[key] = (time.monotonic(), videos)
            logger.info("[TelegramFolders] Cache rebuilt after long wait: key=%s, videos=%s", key, len(videos))
            return videos

    try:
        waited = time.monotonic() - lock_wait_started
        if waited > 0.2:
            logger.info("[TelegramFolders] Waited %.2fs for cache lock (key=%s)", waited, key)

        now = time.monotonic()
        cached = _folder_inventory_cache.get(key)
        if cached and now - cached[0] < FOLDER_INVENTORY_TTL_SECONDS:
            logger.info("[TelegramFolders] Cache hit after lock: key=%s, age=%.1fs", key, now - cached[0])
            return cached[1]

        logger.info("[TelegramFolders] Cache miss; requesting Yandex list_files (key=%s)", key)
        videos = await yandex_service.list_files(
            limit=100000,
            force_refresh=False,
            folders=list(key),
            cache_scope="telegram",
        )
        _folder_inventory_cache[key] = (time.monotonic(), videos)
        logger.info("[TelegramFolders] Cache updated from Yandex (key=%s, videos=%s)", key, len(videos))
        return videos
    finally:
        if _folder_inventory_lock.locked():
            _folder_inventory_lock.release()


ROOT_PRODUCT_GROUP_LABEL = "_root"


def _extract_navigation_segments(path: str) -> tuple[str, ...]:
    parts = [part for part in path.replace("\\", "/").split("/") if part and part != "disk:"]
    try:
        video_idx = next(i for i, part in enumerate(parts) if part.lower() in {"video", "видео"})
    except StopIteration:
        return ()

    folders_after_author = parts[video_idx + 2 :]
    if not folders_after_author:
        return ()
    if folders_after_author and _looks_like_video_file(folders_after_author[-1]):
        folders_after_author = folders_after_author[:-1]
    return tuple(part.strip() for part in folders_after_author if part.strip())


def _segments_match_prefix(segments: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if len(segments) < len(prefix):
        return False
    return all(_normalize(left) == _normalize(right) for left, right in zip(segments, prefix))


def _looks_like_video_file(segment: str) -> bool:
    value = (segment or "").strip().lower()
    if "." not in value:
        return False
    return value.rsplit(".", 1)[-1] in {"mp4", "mov", "avi", "mkv", "webm", "m4v"}


def _extract_inventory_parts(path: str, scheduler: ContentScheduler) -> tuple[str, str, str]:
    parts = [part for part in path.replace("\\", "/").split("/") if part and part != "disk:"]
    try:
        video_idx = next(i for i, part in enumerate(parts) if part.lower() in {"video", "видео"})
    except StopIteration:
        return "unknown", "unknown", ROOT_PRODUCT_GROUP_LABEL

    category = parts[video_idx + 2].split("(")[0].strip() if video_idx + 2 < len(parts) else "unknown"
    brand = parts[video_idx + 3].split("*")[0].split("(")[0].strip() if video_idx + 3 < len(parts) else "unknown"
    product = ROOT_PRODUCT_GROUP_LABEL
    if video_idx + 4 < len(parts):
        candidate = parts[video_idx + 4].strip()
        if candidate and not scheduler.looks_like_video_file(candidate):
            product = candidate

    return category or "unknown", brand or "unknown", product


def _build_pending_placeholder_path(telegram_user_id: int) -> str:
    nonce = f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    return f"pending://{telegram_user_id}/{nonce}"


def _current_day_key(dt: Optional[datetime] = None) -> str:
    value = dt or datetime.utcnow()
    return value.strftime("%Y-%m-%d")


def _parse_folder_prefix_text(value: Optional[str]) -> tuple[str, ...]:
    text_value = str(value or "").strip()
    if not text_value:
        return ()
    return tuple(part.strip() for part in text_value.replace("\\", "/").split("/") if part.strip())


def _format_folder_prefix(prefix: tuple[str, ...]) -> str:
    return " / ".join(prefix)


async def ensure_telegram_schema(session: AsyncSession):
    global _schema_ready, _schema_lock
    if _schema_ready:
        return
    if _schema_lock is None:
        _schema_lock = asyncio.Lock()

    async with _schema_lock:
        if _schema_ready:
            return
        for sql in TELEGRAM_SCHEMA_MIGRATION_SQL:
            await session.execute(text(sql))
        await session.execute(text("DROP INDEX IF EXISTS uq_telegram_video_requests_active_video_path"))
        await session.execute(text(ACTIVE_VIDEO_UNIQUE_INDEX_SQL))
        await session.commit()
        _schema_ready = True


async def submit_video_request(
    telegram_user_id: int,
    telegram_username: Optional[str],
    telegram_full_name: Optional[str],
) -> TelegramVideoRequest:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        existing = await _get_open_request_for_update(session, telegram_user_id)
        if existing:
            raise RuntimeError("already_open_request")

        request = TelegramVideoRequest(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_full_name=telegram_full_name,
            brand="pending",
            video_path=_build_pending_placeholder_path(telegram_user_id),
            video_name="pending.mp4",
            status=STATUS_PENDING_APPROVAL,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)
        logger.info(
            "[TelegramRequest] Created request id=%s user_id=%s username=%s status=%s",
            request.id,
            telegram_user_id,
            telegram_username or "",
            request.status,
        )
        return request


async def list_pending_approval_requests(limit: int = 200) -> list[dict]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = (
            select(TelegramVideoRequest)
            .where(TelegramVideoRequest.status == STATUS_PENDING_APPROVAL)
            .order_by(TelegramVideoRequest.requested_at.asc())
            .limit(max(1, min(limit, 1000)))
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "telegram_user_id": row.telegram_user_id,
                "telegram_username": row.telegram_username,
                "telegram_full_name": row.telegram_full_name,
                "status": row.status,
                "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            }
            for row in rows
        ]


async def list_video_requests(limit: int = 200, status: Optional[str] = None) -> list[dict]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = select(TelegramVideoRequest)
        if status:
            stmt = stmt.where(TelegramVideoRequest.status == status)
        stmt = stmt.order_by(TelegramVideoRequest.requested_at.desc()).limit(max(1, min(limit, 2000)))
        result = await session.execute(stmt)
        rows = result.scalars().all()
        logger.info(
            "[TelegramRequest] list_video_requests returned=%s limit=%s status=%s",
            len(rows),
            limit,
            status or "all",
        )
        return [
            {
                "id": row.id,
                "telegram_user_id": row.telegram_user_id,
                "telegram_username": row.telegram_username,
                "telegram_full_name": row.telegram_full_name,
                "status": row.status,
                "brand": row.brand,
                "video_name": row.video_name,
                "assigned_folder_prefix": row.assigned_folder_prefix,
                "error_message": row.error_message,
                "requested_at": row.requested_at.isoformat() if row.requested_at else None,
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "reported_at": row.reported_at.isoformat() if row.reported_at else None,
            }
            for row in rows
        ]


async def list_distribution_rules(limit: int = 500) -> list[dict]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = (
            select(TelegramDistributionRule)
            .order_by(TelegramDistributionRule.is_active.desc(), TelegramDistributionRule.telegram_user_id.asc())
            .limit(max(1, min(limit, 2000)))
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "telegram_user_id": row.telegram_user_id,
                "telegram_username": row.telegram_username,
                "telegram_full_name": row.telegram_full_name,
                "folder_prefix": row.folder_prefix,
                "weekly_limit": row.weekly_limit,
                "is_active": row.is_active,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]


async def list_request_users(limit: int = 500) -> list[dict]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = (
            select(
                TelegramVideoRequest.telegram_user_id,
                func.max(TelegramVideoRequest.telegram_username).label("telegram_username"),
                func.max(TelegramVideoRequest.telegram_full_name).label("telegram_full_name"),
                func.count(TelegramVideoRequest.id).label("requests_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (TelegramVideoRequest.status.in_((STATUS_SENT, STATUS_REPORTED, STATUS_ARCHIVED)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("received_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (TelegramVideoRequest.status.in_((STATUS_REPORTED, STATUS_ARCHIVED)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("reported_total"),
            )
            .group_by(TelegramVideoRequest.telegram_user_id)
            .order_by(
                text("received_total DESC"),
                text("reported_total DESC"),
                func.count(TelegramVideoRequest.id).desc(),
                TelegramVideoRequest.telegram_user_id.asc(),
            )
            .limit(max(1, min(limit, 5000)))
        )
        result = await session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]


async def upsert_distribution_rule(
    telegram_user_id: int,
    folder_prefix: str,
    weekly_limit: int,
    is_active: bool = True,
    telegram_username: Optional[str] = None,
    telegram_full_name: Optional[str] = None,
) -> dict:
    weekly_limit = max(int(weekly_limit or 0), 0)
    folder_prefix = str(folder_prefix or "").strip()
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = (
            select(TelegramDistributionRule)
            .where(TelegramDistributionRule.telegram_user_id == telegram_user_id)
            .order_by(TelegramDistributionRule.id.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        rule = result.scalar_one_or_none()
        now = datetime.utcnow()
        if not rule:
            rule = TelegramDistributionRule(
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
                telegram_full_name=telegram_full_name,
                folder_prefix=folder_prefix,
                weekly_limit=weekly_limit,
                is_active=bool(is_active),
                created_at=now,
                updated_at=now,
            )
        else:
            rule.folder_prefix = folder_prefix
            rule.weekly_limit = weekly_limit
            rule.is_active = bool(is_active)
            if telegram_username:
                rule.telegram_username = telegram_username
            if telegram_full_name:
                rule.telegram_full_name = telegram_full_name
            rule.updated_at = now
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return {
            "id": rule.id,
            "telegram_user_id": rule.telegram_user_id,
            "telegram_username": rule.telegram_username,
            "telegram_full_name": rule.telegram_full_name,
            "folder_prefix": rule.folder_prefix,
            "weekly_limit": rule.weekly_limit,
            "is_active": rule.is_active,
        }


async def delete_distribution_rule(rule_id: int) -> bool:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = select(TelegramDistributionRule).where(TelegramDistributionRule.id == rule_id).limit(1)
        result = await session.execute(stmt)
        rule = result.scalar_one_or_none()
        if not rule:
            return False
        await session.delete(rule)
        await session.commit()
        return True


async def approve_video_request(request_id: int, admin_user_id: int) -> ApprovalResult:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = select(TelegramVideoRequest).where(TelegramVideoRequest.id == request_id).limit(1)
        result = await session.execute(stmt)
        request = result.scalar_one_or_none()
        if not request:
            raise LookupError("request_not_found")
        if request.status == STATUS_REJECTED:
            raise ValueError("request_rejected")
        if request.status in {STATUS_SENT, STATUS_REPORTED, STATUS_ARCHIVED}:
            return ApprovalResult(request=request, delivered=True)

        rule = await _get_active_distribution_rule(session, request.telegram_user_id)
        if not rule:
            raise LookupError("rule_not_found")
        folder_prefix = _parse_folder_prefix_text(rule.folder_prefix)
        if not folder_prefix:
            raise ValueError("rule_folder_empty")
        if rule.weekly_limit <= 0:
            raise ValueError("rule_daily_limit_zero")

        day_key = _current_day_key()
        daily_count_stmt = select(func.count(TelegramVideoRequest.id)).where(
            TelegramVideoRequest.telegram_user_id == request.telegram_user_id,
            TelegramVideoRequest.week_key == day_key,
            TelegramVideoRequest.status.in_(RESERVED_STATUSES),
        )
        daily_count_result = await session.execute(daily_count_stmt)
        daily_count = int(daily_count_result.scalar() or 0)
        if daily_count >= int(rule.weekly_limit):
            raise ValueError("daily_limit_exceeded")

        assigned = await _assign_video_to_request(session, request, folder_prefix, day_key, admin_user_id)
        if not assigned:
            raise LookupError("no_videos_for_rule")

    delivered = await deliver_approved_request_to_user(request_id)
    async with async_session_maker() as session:
        stmt = select(TelegramVideoRequest).where(TelegramVideoRequest.id == request_id).limit(1)
        result = await session.execute(stmt)
        refreshed = result.scalar_one()
    return ApprovalResult(request=refreshed, delivered=delivered)


async def reject_video_request(request_id: int, admin_user_id: int, reason: Optional[str] = None) -> bool:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = select(TelegramVideoRequest).where(TelegramVideoRequest.id == request_id).limit(1)
        result = await session.execute(stmt)
        request = result.scalar_one_or_none()
        if not request:
            return False
        if request.status in {STATUS_SENT, STATUS_REPORTED, STATUS_ARCHIVED}:
            return False

        request.status = STATUS_REJECTED
        request.approved_by = admin_user_id
        request.approved_at = datetime.utcnow()
        request.error_message = (reason or "").strip()[:500] or None
        session.add(request)
        await session.commit()
        return True


async def deliver_approved_request_to_user(request_id: int) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("[TelegramApproval] Bot token is not configured; cannot deliver request=%s", request_id)
        return False

    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = select(TelegramVideoRequest).where(TelegramVideoRequest.id == request_id).limit(1)
        result = await session.execute(stmt)
        request = result.scalar_one_or_none()
        if not request:
            return False
        if request.status == STATUS_SENT:
            return True
        if request.status != STATUS_APPROVED:
            return False

        download_link = await yandex_service.get_download_link(request.video_path)

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    temp_path: Optional[str] = None
    try:
        await bot.send_message(
            request.telegram_user_id,
            "✅ Заявка одобрена. Отправляю ролик и описание.",
        )
        try:
            temp_path = await download_video_to_temp(download_link, request.video_name or "video.mp4")
            await bot.send_document(
                request.telegram_user_id,
                FSInputFile(temp_path, filename=(request.video_name or "video.mp4")),
                caption=(request.video_name or "video")[:1024],
            )
        except Exception as exc:
            logger.warning("[TelegramApproval] send_document failed, fallback to link for request=%s: %s", request_id, exc)
            await bot.send_message(
                request.telegram_user_id,
                f"Не удалось отправить файл как документ, даю прямую ссылку:\n{download_link}",
            )

        await bot.send_message(
            request.telegram_user_id,
            "Заголовок для YouTube:\n" + html.escape(request.youtube_title or ""),
            parse_mode="HTML",
        )
        await bot.send_message(
            request.telegram_user_id,
            "<b>Описание для YouTube:</b>\n<pre>" + html.escape(request.youtube_description or "") + "</pre>",
            parse_mode="HTML",
        )
        await bot.send_message(
            request.telegram_user_id,
            "После публикации отправьте сюда ссылку на YouTube.",
        )
    except TelegramBadRequest as exc:
        logger.exception("[TelegramApproval] Telegram send failed for request=%s", request_id)
        async with async_session_maker() as session:
            stmt = select(TelegramVideoRequest).where(TelegramVideoRequest.id == request_id).limit(1)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                row.error_message = f"Telegram delivery failed: {exc}"
                session.add(row)
                await session.commit()
        return False
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        await bot.session.close()

    async with async_session_maker() as session:
        stmt = select(TelegramVideoRequest).where(TelegramVideoRequest.id == request_id).limit(1)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.status = STATUS_SENT
            row.error_message = None
            session.add(row)
            await session.commit()
    return True


async def _get_active_distribution_rule(
    session: AsyncSession,
    telegram_user_id: int,
) -> Optional[TelegramDistributionRule]:
    stmt = (
        select(TelegramDistributionRule)
        .where(
            TelegramDistributionRule.telegram_user_id == telegram_user_id,
            TelegramDistributionRule.is_active.is_(True),
        )
        .order_by(TelegramDistributionRule.updated_at.desc(), TelegramDistributionRule.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _assign_video_to_request(
    session: AsyncSession,
    request: TelegramVideoRequest,
    folder_prefix: tuple[str, ...],
    day_key: str,
    admin_user_id: int,
) -> bool:
    config = await get_db_config(session)
    used_result = await session.execute(
        select(TelegramVideoRequest.video_path).where(TelegramVideoRequest.status.in_(RESERVED_STATUSES))
    )
    used_paths = {path for path in used_result.scalars().all() if path}
    videos = await yandex_service.list_files(
        limit=100000,
        force_refresh=False,
        folders=config.yandexFolders,
        cache_scope="telegram",
    )
    scheduler = ContentScheduler(config)
    candidates = [
        (video, matched_client)
        for video in videos
        if video.get("path")
        and video["path"] not in used_paths
        and _segments_match_prefix(_extract_navigation_segments(str(video["path"])), folder_prefix)
        for matched_client in [_find_client_for_video(config.clients, scheduler, video)]
        if matched_client
    ]
    if not candidates:
        return False

    random.shuffle(candidates)
    for video, client in candidates:
        request.brand = client.name
        request.video_path = str(video["path"])
        request.video_name = str(video.get("name") or request.video_path.rsplit("/", 1)[-1])
        request.assigned_folder_prefix = _format_folder_prefix(folder_prefix)
        request.week_key = day_key
        request.approved_by = admin_user_id
        request.approved_at = datetime.utcnow()
        request.status = STATUS_APPROVED
        session.add(request)
        try:
            await session.commit()
            await session.refresh(request)
            author_name = extract_author(request.video_path)
            generated = await content_generator.generate_caption(
                request.video_path,
                "youtube",
                client,
                author_name if author_name != "unknown" else None,
            )
            title, description = parse_youtube_text(generated or "")
            request.youtube_title = title
            request.youtube_description = description or f"{title}\n\n#shorts"
            session.add(request)
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            logger.info("[TelegramApproval] Video already reserved concurrently, trying next candidate")
            continue
        except Exception as exc:
            await session.rollback()
            logger.exception("[TelegramApproval] Failed to generate caption for request=%s", request.id)
            request.error_message = f"Caption generation failed: {exc}"
            request.status = STATUS_FAILED
            session.add(request)
            await session.commit()
            return False
    return False


async def get_pending_request(telegram_user_id: int) -> Optional[TelegramVideoRequest]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = (
            select(TelegramVideoRequest)
            .where(
                TelegramVideoRequest.telegram_user_id == telegram_user_id,
                TelegramVideoRequest.status.in_(REPORTABLE_STATUSES),
                TelegramVideoRequest.published_url.is_(None),
            )
            .order_by(TelegramVideoRequest.requested_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_open_request(telegram_user_id: int) -> Optional[TelegramVideoRequest]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        return await _get_open_request_for_update(session, telegram_user_id)


async def cancel_pending_request(telegram_user_id: int) -> bool:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        request = await _get_open_request_for_update(session, telegram_user_id)
        if not request:
            return False
        request.status = STATUS_CANCELLED
        session.add(request)
        await session.commit()
        return True


async def prepare_random_video(
    brand: str,
    telegram_user_id: int,
    telegram_username: Optional[str],
    telegram_full_name: Optional[str],
) -> PreparedVideo:
    return await _prepare_random_video(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        telegram_full_name=telegram_full_name,
        brand=brand,
        folder_prefix=None,
    )


async def prepare_random_video_from_folder(
    folder_prefix: tuple[str, ...],
    telegram_user_id: int,
    telegram_username: Optional[str],
    telegram_full_name: Optional[str],
) -> PreparedVideo:
    return await _prepare_random_video(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        telegram_full_name=telegram_full_name,
        brand=None,
        folder_prefix=folder_prefix,
    )


async def _prepare_random_video(
    *,
    telegram_user_id: int,
    telegram_username: Optional[str],
    telegram_full_name: Optional[str],
    brand: Optional[str],
    folder_prefix: Optional[tuple[str, ...]],
) -> PreparedVideo:
    selected_video = None
    selected_client = None
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        await ensure_active_video_unique_index(session)
        config = await get_db_config(session)
        client = next((item for item in config.clients if item.name == brand), None) if brand else None
        if brand and not client:
            raise ValueError(f"Бренд не найден: {brand}")

        pending = await _get_pending_request_for_update(session, telegram_user_id)
        if pending:
            raise RuntimeError("pending_report")

        used_result = await session.execute(
            select(TelegramVideoRequest.video_path).where(
                TelegramVideoRequest.status.in_(RESERVED_STATUSES)
            )
        )
        used_paths = {path for path in used_result.scalars().all() if path}

        videos = await yandex_service.list_files(
            limit=100000,
            force_refresh=True,
            folders=config.yandexFolders,
            cache_scope="telegram",
        )
        logger.info(
            "[TelegramBot] Yandex scan returned %s videos for folders=%s before brand filter.",
            len(videos),
            config.yandexFolders,
        )
        scheduler = ContentScheduler(config)
        if folder_prefix is not None:
            candidates = [
                (video, matched_client)
                for video in videos
                if video.get("path")
                and video["path"] not in used_paths
                and _segments_match_prefix(_extract_navigation_segments(str(video["path"])), folder_prefix)
                for matched_client in [_find_client_for_video(config.clients, scheduler, video)]
                if matched_client
            ]
            logger.info(
                "[TelegramBot] Folder '%s' matched %s candidate videos.",
                " / ".join(folder_prefix),
                len(candidates),
            )
        else:
            candidates = [
                (video, client) for video in videos
                if video.get("path")
                and video["path"] not in used_paths
                and client
                and _client_matches_extracted_brand(client, scheduler.extract_brand(str(video["path"])))
            ]
        logger.info(
            "[TelegramBot] Brand '%s' matched %s candidate videos after scheduler-style brand extraction.",
            client.name if client else "folder",
            len(candidates),
        )
        if not candidates and client:
            candidates = [
                (video, client) for video in videos
                if video.get("path")
                and video["path"] not in used_paths
                and _client_matches_video_path(client, str(video["path"]))
            ]
            logger.info(
                "[TelegramBot] Brand '%s' matched %s candidate videos after full-path fallback.",
                client.name,
                len(candidates),
            )
        if not candidates:
            raise LookupError("no_videos")

        random.shuffle(candidates)
        request = None
        for video, candidate_client in candidates:
            request = await _try_reserve_video(
                session=session,
                video=video,
                client=candidate_client,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
                telegram_full_name=telegram_full_name,
            )
            if request:
                selected_video = video
                selected_client = candidate_client
                break

        if not request or not selected_video or not selected_client:
            raise LookupError("no_videos")

        video_path = str(selected_video["path"])
        author_name = extract_author(video_path)
        generated = await content_generator.generate_caption(
            video_path,
            "youtube",
            selected_client,
            author_name if author_name != "unknown" else None,
        )
        title, description = parse_youtube_text(generated or "")
        if not description:
            description = f"{title}\n\n#shorts"

        request.youtube_title = title
        request.youtube_description = description
        session.add(request)
        await session.commit()
        await session.refresh(request)

    download_link = await yandex_service.get_download_link(video_path)
    size = selected_video.get("size") if selected_video else None
    return PreparedVideo(
        request=request,
        download_link=download_link,
        size=int(size) if size is not None else None,
    )


async def ensure_active_video_unique_index(session: AsyncSession):
    await session.execute(text(ACTIVE_VIDEO_UNIQUE_INDEX_SQL))
    await session.commit()


async def _try_reserve_video(
    session: AsyncSession,
    video: dict,
    client: ClientConfig,
    telegram_user_id: int,
    telegram_username: Optional[str],
    telegram_full_name: Optional[str],
) -> Optional[TelegramVideoRequest]:
    video_path = str(video["path"])
    request = TelegramVideoRequest(
        telegram_user_id=telegram_user_id,
        telegram_username=telegram_username,
        telegram_full_name=telegram_full_name,
        brand=client.name,
        video_path=video_path,
        video_name=str(video.get("name") or video_path.rsplit("/", 1)[-1]),
        status=STATUS_SENT,
    )
    session.add(request)
    try:
        await session.commit()
        await session.refresh(request)
        return request
    except IntegrityError:
        await session.rollback()
        logger.info("[TelegramBot] Video was reserved concurrently, trying another: %s", video_path)
        return None


async def download_video_to_temp(download_link: str, video_name: str) -> str:
    suffix = os.path.splitext(video_name or "")[1] or ".mp4"
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path = temp.name
    temp.close()

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
            async with client.stream("GET", download_link) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as file:
                    async for chunk in response.aiter_bytes():
                        file.write(chunk)
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


async def accept_publication_report(
    telegram_user_id: int,
    published_url: str,
) -> TelegramVideoRequest:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        request = await _get_pending_request_for_update(session, telegram_user_id)
        if not request:
            raise LookupError("no_pending")

        request.published_url = published_url.strip()
        request.reported_at = datetime.utcnow()
        request.status = STATUS_REPORTED
        session.add(request)
        await session.commit()
        await session.refresh(request)

        dest_folder = f"disk:/опубликовано/{request.brand}/{datetime.utcnow().strftime('%Y-%m-%d')}"
        try:
            archive_path = await yandex_service.move_file(request.video_path, dest_folder)
            request.archive_path = archive_path
            request.archived_at = datetime.utcnow()
            request.status = STATUS_ARCHIVED
            session.add(request)
            await session.commit()
            await session.refresh(request)
        except Exception as exc:
            request.error_message = f"Archive failed: {exc}"
            session.add(request)
            await session.commit()
            logger.exception("Failed to archive Telegram-requested video %s", request.video_path)

        return request


async def user_report(telegram_user_id: int) -> dict:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = select(
            func.max(TelegramVideoRequest.telegram_username).label("telegram_username"),
            func.max(TelegramVideoRequest.telegram_full_name).label("telegram_full_name"),
            func.count(TelegramVideoRequest.id).label("requested"),
            func.coalesce(
                func.sum(
                    case(
                        (TelegramVideoRequest.status.in_((STATUS_REPORTED, STATUS_ARCHIVED)), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("reported"),
        ).where(TelegramVideoRequest.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        row = result.mappings().one()
        return {
            "telegram_user_id": telegram_user_id,
            "telegram_username": row["telegram_username"],
            "telegram_full_name": row["telegram_full_name"],
            "requested": int(row["requested"]),
            "reported": int(row["reported"]),
        }


async def admin_report() -> list[dict]:
    async with async_session_maker() as session:
        await ensure_telegram_schema(session)
        stmt = (
            select(
                TelegramVideoRequest.telegram_user_id,
                TelegramVideoRequest.telegram_username,
                TelegramVideoRequest.telegram_full_name,
                func.count(TelegramVideoRequest.id).label("requested"),
                func.coalesce(
                    func.sum(
                        case(
                            (TelegramVideoRequest.status.in_((STATUS_REPORTED, STATUS_ARCHIVED)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("reported"),
            )
            .group_by(
                TelegramVideoRequest.telegram_user_id,
                TelegramVideoRequest.telegram_username,
                TelegramVideoRequest.telegram_full_name,
            )
            .order_by(func.count(TelegramVideoRequest.id).desc())
        )
        result = await session.execute(stmt)
        return [dict(row) for row in result.mappings().all()]


async def _get_pending_request_for_update(
    session: AsyncSession,
    telegram_user_id: int,
) -> Optional[TelegramVideoRequest]:
    stmt = (
        select(TelegramVideoRequest)
        .where(
            TelegramVideoRequest.telegram_user_id == telegram_user_id,
            TelegramVideoRequest.status.in_(REPORTABLE_STATUSES),
            TelegramVideoRequest.published_url.is_(None),
        )
        .order_by(TelegramVideoRequest.requested_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _get_open_request_for_update(
    session: AsyncSession,
    telegram_user_id: int,
) -> Optional[TelegramVideoRequest]:
    stmt = (
        select(TelegramVideoRequest)
        .where(
            TelegramVideoRequest.telegram_user_id == telegram_user_id,
            TelegramVideoRequest.status.in_(OPEN_REQUEST_STATUSES),
            TelegramVideoRequest.published_url.is_(None),
        )
        .order_by(TelegramVideoRequest.requested_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
