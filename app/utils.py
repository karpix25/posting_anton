from typing import List
from app.config import settings

def normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace(" ", "").strip()

def extract_brand(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    # User structure: Video / Editor / Category / Brand
    v_idx = -1
    for i, p in enumerate(parts):
        if p.lower() in ["video", "видео"]:
            v_idx = i
            break
    
    brand_raw = None
    if v_idx != -1 and v_idx + 3 < len(parts):
        brand_raw = parts[v_idx + 3].split("*")[0].split("(")[0].strip()
        if "." in brand_raw: brand_raw = None
    
    return normalize(brand_raw) if brand_raw else "unknown"

def extract_author(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    v_idx = -1
    for i, p in enumerate(parts):
        if p.lower() in ["video", "видео"]:
            v_idx = i
            break
    
    if v_idx != -1 and v_idx + 1 < len(parts):
        author = parts[v_idx + 1].split("*")[0].split("(")[0].strip()
        if any(author.lower().endswith(ext) for ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']):
            return "unknown"
        return author
    return "unknown"

def extract_theme(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
    v_idx = -1
    for i, p in enumerate(parts):
        if p.lower() in ["video", "видео"]:
            v_idx = i
            break
    
    theme_raw = None
    if v_idx != -1 and v_idx + 2 < len(parts):
        theme_raw = parts[v_idx + 2].split("*")[0].split("(")[0].strip()
    
    if not theme_raw and len(parts) >= 3:
        theme_raw = parts[-3]
        
    return normalize_theme_key(theme_raw) if theme_raw else "unknown"


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
