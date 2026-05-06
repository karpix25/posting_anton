from typing import Dict, List, Optional
from app.config import settings

VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "mpg", "mpeg"}


def _looks_like_video_file(segment: str) -> bool:
    value = (segment or "").strip().lower()
    if "." not in value:
        return False
    ext = value.rsplit(".", 1)[-1]
    return ext in VIDEO_EXTENSIONS


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace(" ", "").strip()

def extract_brand(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    try:
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break
        
        if v_idx != -1 and v_idx + 3 < len(parts):
                raw = parts[v_idx + 3].split("*")[0].split("(")[0].strip()
                # Brand must be a folder segment, not a video file at this level.
                if _looks_like_video_file(raw):
                    return "unknown"
                return normalize(raw)
    except:
            pass
    return "unknown"

def extract_author(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    try:
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break
        
        if v_idx != -1 and v_idx + 1 < len(parts):
                raw = parts[v_idx + 1].strip()
                # Author must be a folder segment, not a video file at this level.
                if _looks_like_video_file(raw):
                    return "unknown"
                return raw
    except:
            pass
    return "unknown"

def extract_theme(path: str, aliases_map: Optional[Dict[str, List[str]]] = None, use_aliases: bool = True) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    try:
        raw = ""
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break

        # Primary structure: /VIDEO/Author/Category/Brand/file
        if v_idx != -1 and v_idx + 2 < len(parts):
            raw = parts[v_idx + 2].split("(")[0].strip()
        # Fallback: parent folder if VIDEO marker is absent
        elif len(parts) >= 2:
            raw = parts[-2].split("(")[0].strip()

        if not raw:
            return "unknown"

        # Avoid treating file names as categories
        if _looks_like_video_file(raw):
            return "unknown"

        if use_aliases:
            return normalize_theme_key(raw, aliases_map)
        return raw
    except:
        pass
    return "unknown"

def normalize_theme_key(text: str, aliases_override: Optional[Dict[str, List[str]]] = None) -> str:
    raw = normalize(text)
    # Load config dynamically or reuse settings? 
    # Settings has _legacy_config loaded?
    # Ideally should be passed or re-loaded.
    # For utility, we might need access to config.
    # Let's try to access settings._legacy_config if loaded, else load it.
    
    config = settings._legacy_config
    if config is None:
        config = settings.load_legacy_config()
        settings._legacy_config = config
    aliases = aliases_override if aliases_override is not None else (config.themeAliases or {})
    
    for canonical, list_ in aliases.items():
        if raw == normalize(canonical): return canonical
        for a in list_:
            if normalize(a) == raw: return canonical
    return raw
