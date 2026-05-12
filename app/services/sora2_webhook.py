import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

SORA2_PUBLICATION_WEBHOOK_URL = "https://n8n-sora2-auto.ap2dy7.easypanel.host/api/webhooks/publications"
SORA2_FILENAME_RE = re.compile(r"^p[A-Z0-9]{4,12}_[0-9]{8}_[0-9]{6}_([a-z0-9]{8})\.[A-Za-z0-9]+$")


def extract_sora2_task_id(video_path_or_name: Optional[str]) -> Optional[str]:
    if not video_path_or_name:
        return None

    clean_path = str(video_path_or_name).replace("\\", "/").replace("disk:", "")
    filename = os.path.basename(clean_path.strip("/"))
    match = SORA2_FILENAME_RE.fullmatch(filename)
    return match.group(1) if match else None


def canonical_publication_url(publication_url: Optional[str]) -> Optional[str]:
    if not publication_url:
        return None

    raw = str(publication_url).strip()
    if not raw:
        return None

    try:
        parsed = urlsplit(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw

        hostname = parsed.hostname.lower() if parsed.hostname else ""
        normalized_path = parsed.path.rstrip("/") or parsed.path

        # YouTube watch URLs keep the video id in the query string.
        # Dropping every query parameter turns /watch?v=abc into /watch.
        if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and normalized_path == "/watch":
            video_id = next((value for key, value in parse_qsl(parsed.query) if key == "v" and value), "")
            query = urlencode({"v": video_id}) if video_id else ""
            return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, query, ""))

        if hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
            return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))

        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        return raw


async def send_sora2_publication_webhook(task_id: str, publication_url: str) -> None:
    payload = {
        "taskId": task_id,
        "publicationUrl": canonical_publication_url(publication_url),
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            SORA2_PUBLICATION_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()


async def send_sora2_webhook_for_post(post: Any, publication_url: Optional[str]) -> Dict[str, Any]:
    """
    Send SOra2 publication webhook once for a successfully published post.
    The caller is responsible for committing returned meta updates.
    """
    meta = post.meta.copy() if post.meta else {}

    if meta.get("sora2_webhook_sent_at"):
        return {"sent": False, "reason": "already_sent", "meta": meta}

    task_id = extract_sora2_task_id(post.video_name) or extract_sora2_task_id(post.video_path)
    if not task_id:
        return {"sent": False, "reason": "not_sora2_filename", "meta": meta}

    canonical_url = canonical_publication_url(publication_url)
    if not canonical_url:
        return {"sent": False, "reason": "missing_publication_url", "meta": meta}

    meta["sora2_task_id"] = task_id
    meta["sora2_publication_url"] = canonical_url

    try:
        await send_sora2_publication_webhook(task_id, canonical_url)
        meta["sora2_webhook_sent_at"] = datetime.utcnow().isoformat()
        meta.pop("sora2_webhook_error", None)
        logger.info(f"[SOra2] Webhook sent for post #{post.id}: taskId={task_id}")
        return {"sent": True, "reason": "sent", "meta": meta}
    except Exception as e:
        meta["sora2_webhook_error"] = str(e)
        logger.error(f"[SOra2] Webhook failed for post #{post.id}: {e}")
        return {"sent": False, "reason": "send_failed", "meta": meta}
