import logging
import os
import random
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ClientConfig
from app.database import async_session_maker
from app.models import TelegramVideoRequest
from app.services.config_db import get_db_config
from app.services.content_generator import content_generator
from app.services.yandex import yandex_service
from app.utils import extract_author

logger = logging.getLogger(__name__)

TELEGRAM_BOT_FILE_LIMIT_BYTES = 50 * 1024 * 1024
REPORTABLE_STATUSES = ("sent", "reported")
RESERVED_STATUSES = ("sent", "reported", "archived")
YOUTUBE_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[^\s]+$",
    re.IGNORECASE,
)


@dataclass
class PreparedVideo:
    request: TelegramVideoRequest
    download_link: str
    size: Optional[int] = None

    @property
    def should_send_as_link(self) -> bool:
        return self.size is not None and self.size > TELEGRAM_BOT_FILE_LIMIT_BYTES


def _normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е").replace(" ", "").replace("-", "").strip()


def _strip_youtube_template_tokens(text: str) -> str:
    text = text or ""
    text = re.sub(r"(?is)\[/?YT_(?:TITLE|DESCRIPTION)\]", " ", text)
    text = re.sub(r"(?i)/?YT_(?:TITLE|DESCRIPTION)\b", " ", text)
    text = re.sub(r"(?im)^\s*(?:YT_TITLE|YT_DESCRIPTION)\s*$", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
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


def _client_matches_video(client: ClientConfig, video_path: str) -> bool:
    if client.regex:
        try:
            if re.search(client.regex, video_path, re.IGNORECASE):
                return True
        except re.error:
            logger.warning("Invalid AI client regex for %s: %s", client.name, client.regex)

    normalized_name = _normalize(client.name)
    return normalized_name and normalized_name in _normalize(video_path)


async def list_brands() -> list[str]:
    async with async_session_maker() as session:
        config = await get_db_config(session)
    return [client.name for client in config.clients if client.name]


async def get_pending_request(telegram_user_id: int) -> Optional[TelegramVideoRequest]:
    async with async_session_maker() as session:
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


async def cancel_pending_request(telegram_user_id: int) -> bool:
    async with async_session_maker() as session:
        request = await _get_pending_request_for_update(session, telegram_user_id)
        if not request:
            return False
        request.status = "cancelled"
        session.add(request)
        await session.commit()
        return True


async def prepare_random_video(
    brand: str,
    telegram_user_id: int,
    telegram_username: Optional[str],
    telegram_full_name: Optional[str],
) -> PreparedVideo:
    async with async_session_maker() as session:
        config = await get_db_config(session)
        client = next((item for item in config.clients if item.name == brand), None)
        if not client:
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
        )
        candidates = [
            video for video in videos
            if video.get("path")
            and video["path"] not in used_paths
            and _client_matches_video(client, str(video["path"]))
        ]
        if not candidates:
            raise LookupError("no_videos")

        video = random.choice(candidates)
        video_path = str(video["path"])
        author_name = extract_author(video_path)
        generated = await content_generator.generate_caption(
            video_path,
            "youtube",
            client,
            author_name if author_name != "unknown" else None,
        )
        title, description = parse_youtube_text(generated or "")
        if not description:
            description = f"{title}\n\n#shorts"

        request = TelegramVideoRequest(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_full_name=telegram_full_name,
            brand=client.name,
            video_path=video_path,
            video_name=str(video.get("name") or video_path.rsplit("/", 1)[-1]),
            youtube_title=title,
            youtube_description=description,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

    download_link = await yandex_service.get_download_link(video_path)
    size = video.get("size")
    return PreparedVideo(
        request=request,
        download_link=download_link,
        size=int(size) if size is not None else None,
    )


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
        request = await _get_pending_request_for_update(session, telegram_user_id)
        if not request:
            raise LookupError("no_pending")

        request.published_url = published_url.strip()
        request.reported_at = datetime.utcnow()
        request.status = "reported"
        session.add(request)
        await session.commit()
        await session.refresh(request)

        dest_folder = f"disk:/опубликовано/{request.brand}/{datetime.utcnow().strftime('%Y-%m-%d')}"
        try:
            archive_path = await yandex_service.move_file(request.video_path, dest_folder)
            request.archive_path = archive_path
            request.archived_at = datetime.utcnow()
            request.status = "archived"
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
        stmt = select(
            func.count(TelegramVideoRequest.id).label("requested"),
            func.coalesce(
                func.sum(
                    case(
                        (TelegramVideoRequest.status.in_(("reported", "archived")), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("reported"),
        ).where(TelegramVideoRequest.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        row = result.mappings().one()
        return {"requested": int(row["requested"]), "reported": int(row["reported"])}


async def admin_report() -> list[dict]:
    async with async_session_maker() as session:
        stmt = (
            select(
                TelegramVideoRequest.telegram_user_id,
                TelegramVideoRequest.telegram_username,
                TelegramVideoRequest.telegram_full_name,
                func.count(TelegramVideoRequest.id).label("requested"),
                func.coalesce(
                    func.sum(
                        case(
                            (TelegramVideoRequest.status.in_(("reported", "archived")), 1),
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
