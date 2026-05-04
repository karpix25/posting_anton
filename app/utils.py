from typing import List
from app.config import settings

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
                return parts[v_idx + 1].strip()
    except:
            pass
    return "unknown"

def extract_theme(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    try:
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break
        
        if v_idx != -1 and v_idx + 2 < len(parts):
                raw = parts[v_idx + 2].split("(")[0].strip()
                return normalize_theme_key(raw)
    except:
        pass
    return "unknown"

def normalize_theme_key(text: str) -> str:
    raw = normalize(text)
    # Load config dynamically or reuse settings? 
    # Settings has _legacy_config loaded?
    # Ideally should be passed or re-loaded.
    # For utility, we might need access to config.
    # Let's try to access settings._legacy_config if loaded, else load it.
    
    config = settings._legacy_config or settings.load_legacy_config()
    aliases = config.themeAliases or {}
    
    for canonical, list_ in aliases.items():
        if raw == normalize(canonical): return canonical
        for a in list_:
            if normalize(a) == raw: return canonical
    return raw
