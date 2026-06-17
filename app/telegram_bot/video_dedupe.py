from typing import Any


def video_md5(video: dict[str, Any]) -> str:
    return str(video.get("md5") or "").strip()


def video_path(video: dict[str, Any]) -> str:
    return str(video.get("path") or "").strip()


def is_video_used(
    video: dict[str, Any],
    used_paths: set[str],
    used_md5s: set[str],
) -> bool:
    path = video_path(video)
    md5 = video_md5(video)
    return bool(path and path in used_paths) or bool(md5 and md5 in used_md5s)
