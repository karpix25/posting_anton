import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ClientConfig
from app.models import TelegramVideoRequest
from app.services.scheduler import ContentScheduler
from app.services.yandex import yandex_service

logger = logging.getLogger(__name__)

RESERVED_VIDEO_STATUSES = ("approved", "sent", "reported", "archived")


@dataclass(frozen=True)
class FolderCandidateStats:
    total_videos: int
    reserved_videos: int
    unmatched_client_videos: int
    available_videos: int


def normalize_match_text(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", (text or "").lower().replace("ё", "е")).strip()


def extract_navigation_segments(path: str) -> tuple[str, ...]:
    parts = [part for part in path.replace("\\", "/").split("/") if part and part != "disk:"]
    try:
        video_idx = next(i for i, part in enumerate(parts) if part.lower() in {"video", "видео"})
    except StopIteration:
        return ()

    folders_after_author = parts[video_idx + 2 :]
    if not folders_after_author:
        return ()
    if folders_after_author and looks_like_video_file(folders_after_author[-1]):
        folders_after_author = folders_after_author[:-1]
    return tuple(part.strip() for part in folders_after_author if part.strip())


def segments_match_prefix(segments: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if len(segments) < len(prefix):
        return False
    return all(normalize_match_text(left) == normalize_match_text(right) for left, right in zip(segments, prefix))


def looks_like_video_file(segment: str) -> bool:
    value = (segment or "").strip().lower()
    if "." not in value:
        return False
    return value.rsplit(".", 1)[-1] in {"mp4", "mov", "avi", "mkv", "webm", "m4v"}


def find_client_for_video(
    clients: Sequence[ClientConfig],
    scheduler: ContentScheduler,
    video: dict[str, Any],
) -> Optional[ClientConfig]:
    video_path = str(video.get("path") or "")
    extracted_brand = scheduler.extract_brand(video_path)
    for client in clients:
        if client_matches_extracted_brand(client, extracted_brand):
            return client
    for client in clients:
        if client_matches_video_path(client, video_path):
            return client
    return None


def client_matches_extracted_brand(client: ClientConfig, brand_name: str) -> bool:
    normalized_brand = normalize_match_text(brand_name)
    normalized_client = normalize_match_text(client.name)
    if normalized_client == normalized_brand:
        return True

    if client.regex:
        try:
            if re.fullmatch(client.regex, brand_name, re.IGNORECASE):
                return True
            if re.fullmatch(normalize_match_text(client.regex), normalized_brand, re.IGNORECASE):
                return True
        except re.error:
            logger.warning("Invalid AI client regex for %s: %s", client.name, client.regex)

    return False


def client_matches_video_path(client: ClientConfig, video_path: str) -> bool:
    if client.regex:
        try:
            if re.search(client.regex, video_path, re.IGNORECASE):
                return True
        except re.error:
            logger.warning("Invalid AI client regex for %s: %s", client.name, client.regex)

    return normalize_match_text(client.name) in normalize_match_text(video_path)


async def load_folder_video_candidates(
    session: AsyncSession,
    config: Any,
    folder_prefix: tuple[str, ...],
    *,
    force_refresh: bool = False,
) -> tuple[list[tuple[dict[str, Any], ClientConfig]], FolderCandidateStats]:
    used_result = await session.execute(
        select(TelegramVideoRequest.video_path).where(TelegramVideoRequest.status.in_(RESERVED_VIDEO_STATUSES))
    )
    used_paths = {path for path in used_result.scalars().all() if path}
    videos = await yandex_service.list_files(
        limit=100000,
        force_refresh=force_refresh,
        folders=config.yandexFolders,
        cache_scope="telegram",
    )
    scheduler = ContentScheduler(config)
    candidates: list[tuple[dict[str, Any], ClientConfig]] = []
    total_videos = 0
    reserved_videos = 0
    unmatched_client_videos = 0

    for video in videos:
        path = str(video.get("path") or "")
        if not path or not segments_match_prefix(extract_navigation_segments(path), folder_prefix):
            continue

        total_videos += 1
        if path in used_paths:
            reserved_videos += 1
            continue

        client = find_client_for_video(config.clients, scheduler, video)
        if not client:
            unmatched_client_videos += 1
            continue

        candidates.append((video, client))

    stats = FolderCandidateStats(
        total_videos=total_videos,
        reserved_videos=reserved_videos,
        unmatched_client_videos=unmatched_client_videos,
        available_videos=len(candidates),
    )
    return candidates, stats
