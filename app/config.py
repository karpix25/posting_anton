import os
import json
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings

class SocialProfile(BaseModel):
    username: str
    theme_key: Optional[str] = None
    platforms: List[str]
    enabled: bool = True
    
    # Per-platform limits (posts/day for each platform)
    instagramLimit: Optional[int] = None
    tiktokLimit: Optional[int] = None
    youtubeLimit: Optional[int] = None
    
    # Deprecated: use platform-specific limits instead
    # Kept for backwards compatibility
    limit: Optional[int] = None
    last_posted: Optional[Dict[str, str]] = {}

    @field_validator('instagramLimit', 'tiktokLimit', 'youtubeLimit', 'limit', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        if v == "":
            return None
        return v

class ClientConfig(BaseModel):
    name: str
    regex: str
    prompt: str
    quota: Optional[int] = None

class GlobalLimits(BaseModel):
    instagram: int
    tiktok: int
    youtube: int

class ScheduleConfig(BaseModel):
    enabled: bool = True
    timezone: str = "Europe/Moscow"
    dailyRunTime: str = "00:01"
    start_hour: int = 8
    end_hour: int = 23

class LegacyConfig(BaseModel):
    cronSchedule: Optional[str] = None
    yandexFolders: List[str] = []
    daysToGenerate: int = 7
    themeAliases: Dict[str, List[str]] = {}
    brandQuotas: Dict[str, Dict[str, Optional[int]]] = {}
    limits: GlobalLimits
    profiles: List[SocialProfile] = []
    clients: List[ClientConfig] = []
    schedule: Optional[ScheduleConfig] = None
    allowVideoReuse: bool = False
    minIntervalMinutes: int = 45
    cached_yandex_stats: Dict[str, Any] = {}

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://admin:admin@tools_postgres:5432/postgres"
    YANDEX_TOKEN: str = ""
    OPENAI_API_KEY: str = ""
    UPLOAD_POST_API_KEY: str = ""
    DATA_DIR: str = "/data"
    REDIS_URL: str = "redis://tools_redis:6379/0"
    ALLOWED_ORIGINS: str = "*"
    
    # Internal state for legacy config
    _legacy_config: Optional[LegacyConfig] = None

    def get_config_path(self) -> str:
        return os.path.join(self.DATA_DIR, "config.json")

    def load_legacy_config(self) -> LegacyConfig:
        path = self.get_config_path()
        if not os.path.exists(path):
            # Return default if not found
            return LegacyConfig(
                limits=GlobalLimits(instagram=10, tiktok=10, youtube=2)
            )
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # Auto-repair: Ensure cronSchedule exists
                changed = False
                if "cronSchedule" not in data:
                     data["cronSchedule"] = "1 0 * * *"
                     changed = True
                     
                # Save back if repaired
                if changed:
                    try:
                        with open(path, "w", encoding="utf-8") as f_out:
                             json.dump(data, f_out, indent=2, ensure_ascii=False)
                    except:
                        pass
    
                return LegacyConfig(**data)
        except Exception as e:
            # Fallback to default if file is corrupted
            print(f"Error loading legacy config: {e}. Returning default.")
            return LegacyConfig(
                limits=GlobalLimits(instagram=10, tiktok=10, youtube=2)
            )

    class Config:
        env_file = ".env"
        from dotenv import load_dotenv
        load_dotenv()

settings = Settings()
