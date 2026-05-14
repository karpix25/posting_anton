import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CANONICAL_PLATFORMS = {"instagram", "tiktok", "youtube"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_platform_name(value: str) -> str:
    raw = (value or "").strip().lower()
    compact = raw.replace(" ", "").replace("_", "").replace("-", "")
    if compact in {"instagram", "insta", "ig"}:
        return "instagram"
    if compact in {"tiktok", "tt"}:
        return "tiktok"
    if compact in {"youtube", "yt"}:
        return "youtube"
    return raw


def get_platform_status(account_value: Any) -> str:
    if not account_value:
        return "disconnected"

    if isinstance(account_value, dict):
        explicit_status = str(account_value.get("status", "")).strip().lower()
        if explicit_status in {"connected", "disconnected", "reauth_required"}:
            return explicit_status
        if account_value.get("reauth_required") is True:
            return "reauth_required"
        if account_value.get("connected") is False:
            return "disconnected"
        return "connected"

    return "connected"


def derive_profile_status(platform_statuses: Dict[str, str]) -> str:
    if not platform_statuses:
        return "unknown"
    values = [str(v).lower() for v in platform_statuses.values()]
    if any(v == "reauth_required" for v in values):
        return "reauth_required"
    if any(v == "connected" for v in values):
        return "connected"
    if all(v in {"disconnected", "not_found"} for v in values):
        return "disconnected"
    return "unknown"


def extract_statuses_from_api_profile(api_profile: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    socials = (api_profile or {}).get("social_accounts", {}) or {}
    platform_statuses: Dict[str, str] = {}
    connected_platforms: List[str] = []

    for key, value in socials.items():
        platform = normalize_platform_name(str(key))
        if platform not in CANONICAL_PLATFORMS:
            continue
        status = get_platform_status(value)
        platform_statuses[platform] = status
        if status == "connected":
            connected_platforms.append(platform)

    # deterministic ordering
    connected_platforms = sorted(list(set(connected_platforms)))
    return platform_statuses, connected_platforms


def _ensure_profile_status_defaults(profile: Dict[str, Any]) -> None:
    profile.setdefault("profile_status", "unknown")
    profile.setdefault("platform_statuses", {})
    profile.setdefault("profile_status_updated_at", None)
    profile.setdefault("profile_status_source", None)


def merge_api_profiles_into_config(
    config_data: Dict[str, Any],
    api_profiles: List[Dict[str, Any]],
    status_source: str = "uploadpost_sync",
    upsert_missing_profiles: bool = False,
) -> Dict[str, int]:
    profiles: List[Dict[str, Any]] = list(config_data.get("profiles") or [])
    existing_by_username = {
        str(p.get("username", "")).strip().lower(): p
        for p in profiles
        if str(p.get("username", "")).strip()
    }
    api_seen = set()

    created = 0
    updated = 0
    now = now_iso()

    for api_profile in api_profiles or []:
        username = str(api_profile.get("username", "")).strip()
        if not username:
            continue

        key = username.lower()
        api_seen.add(key)
        platform_statuses, connected_platforms = extract_statuses_from_api_profile(api_profile)
        overall_status = derive_profile_status(platform_statuses)

        existing = existing_by_username.get(key)
        if existing:
            _ensure_profile_status_defaults(existing)
            existing["username"] = username  # keep exact API casing
            # Keep behavior compatible with current system:
            # `platforms` reflects connected platforms from Upload Post.
            existing["platforms"] = connected_platforms
            existing["platform_statuses"] = platform_statuses
            existing["profile_status"] = overall_status
            existing["profile_status_updated_at"] = now
            existing["profile_status_source"] = status_source
            updated += 1
        elif upsert_missing_profiles:
            new_profile = {
                "username": username,
                "theme_key": "",
                "platforms": connected_platforms,
                "enabled": True,
                "platform_statuses": platform_statuses,
                "profile_status": overall_status,
                "profile_status_updated_at": now,
                "profile_status_source": status_source,
            }
            profiles.append(new_profile)
            existing_by_username[key] = new_profile
            created += 1

    # Keep the configured profile list aligned with Upload Post. Category bindings
    # for profiles still returned by the API are preserved above; profiles that
    # disappeared from Upload Post should not keep inflating the UI/profile count.
    removed = 0
    if api_profiles is not None:
        kept_profiles = []
        for profile in profiles:
            key = str(profile.get("username", "")).strip().lower()
            if key in api_seen:
                kept_profiles.append(profile)
            else:
                removed += 1
        profiles = kept_profiles

    config_data["profiles"] = profiles
    return {
        "processed": len(api_profiles or []),
        "created": created,
        "updated": updated,
        "missing_marked": 0,
        "removed": removed,
    }


def apply_webhook_event_to_profile(
    profile: Dict[str, Any],
    event_name: str,
    platform_name: Optional[str],
    status_source: str = "uploadpost_webhook",
) -> bool:
    if not profile:
        return False

    _ensure_profile_status_defaults(profile)
    platform_statuses = dict(profile.get("platform_statuses") or {})
    normalized_platform = normalize_platform_name(platform_name or "")
    if normalized_platform and normalized_platform not in CANONICAL_PLATFORMS:
        normalized_platform = ""

    event = (event_name or "").strip().lower()
    now = now_iso()
    changed = False

    def _set_platform_status(status: str):
        nonlocal changed
        if not normalized_platform:
            return
        if platform_statuses.get(normalized_platform) != status:
            platform_statuses[normalized_platform] = status
            changed = True

    if event.endswith("social_account_connected"):
        _set_platform_status("connected")
    elif event.endswith("social_account_disconnected"):
        _set_platform_status("disconnected")
    elif event.endswith("social_account_reauth_required"):
        _set_platform_status("reauth_required")

    if changed:
        profile["platform_statuses"] = platform_statuses
        profile["platforms"] = sorted([p for p, s in platform_statuses.items() if s == "connected"])
        profile["profile_status"] = derive_profile_status(platform_statuses)
        profile["profile_status_updated_at"] = now
        profile["profile_status_source"] = status_source

    return changed
