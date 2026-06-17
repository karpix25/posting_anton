import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PostingHistory

RECENT_REPOST_GUARD_DAYS = 21
RECENT_REPOST_GUARD_STATUSES = ("success", "processing")


@dataclass(frozen=True)
class RepostGuard:
    paths: frozenset[str]
    md5s: frozenset[str]
    file_names: frozenset[str]

    @property
    def size(self) -> int:
        return len(self.paths) + len(self.md5s) + len(self.file_names)

    def blocks(self, video: dict[str, Any]) -> bool:
        identity = video_identity(video)
        return bool(
            identity.path and identity.path in self.paths
            or identity.md5 and identity.md5 in self.md5s
            or identity.file_name and identity.file_name in self.file_names
        )


@dataclass(frozen=True)
class VideoIdentity:
    path: str
    md5: str
    file_name: str


def video_identity(video: dict[str, Any]) -> VideoIdentity:
    path = str(video.get("path") or "").strip()
    md5 = str(video.get("md5") or "").strip()
    name = str(video.get("name") or "").strip()
    return VideoIdentity(
        path=path,
        md5=md5,
        file_name=_normalize_file_name(name or path),
    )


def filter_recent_reposts(videos: Iterable[dict[str, Any]], guard: RepostGuard) -> tuple[list[dict[str, Any]], int]:
    video_list = list(videos)
    kept = [video for video in video_list if not guard.blocks(video)]
    removed = len(video_list) - len(kept)
    return kept, removed


async def load_recent_repost_guard(
    session: AsyncSession,
    *,
    now: Optional[datetime] = None,
    days: int = RECENT_REPOST_GUARD_DAYS,
) -> RepostGuard:
    cutoff = (now or datetime.utcnow()) - timedelta(days=max(int(days or 1), 1))
    stmt = select(PostingHistory.video_path, PostingHistory.video_name, PostingHistory.meta).where(
        PostingHistory.status.in_(RECENT_REPOST_GUARD_STATUSES),
        PostingHistory.posted_at >= cutoff,
    )
    result = await session.execute(stmt)

    paths: set[str] = set()
    md5s: set[str] = set()
    file_names: set[str] = set()

    for path, name, meta in result.all():
        path_value = str(path or "").strip()
        name_value = str(name or "").strip()
        meta_value = meta if isinstance(meta, dict) else {}
        md5_value = str(meta_value.get("video_md5") or "").strip()

        if path_value:
            paths.add(path_value)
        if md5_value:
            md5s.add(md5_value)
        file_name = _normalize_file_name(name_value or path_value)
        if file_name:
            file_names.add(file_name)

    return RepostGuard(frozenset(paths), frozenset(md5s), frozenset(file_names))


def _normalize_file_name(value: str) -> str:
    file_name = os.path.basename(str(value or "").replace("\\", "/")).strip().lower()
    return file_name
