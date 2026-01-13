import os
import json
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from pydantic_settings import BaseSettings

class SocialProfile(BaseModel):
    username: str
    theme_key: str
    platforms: List[str]
    enabled: bool = True
    limit: Optional[int] = None
    last_posted: Optional[Dict[str, str]] = {}

class ClientConfig(BaseModel):
    name: str
    regex: str
    prompt: str
    quota: Optional[int] = None

class GlobalLimits(BaseModel):
    instagram: int
    tiktok: int
    youtube: int

class LegacyConfig(BaseModel):
    cronSchedule: Optional[str] = None
    yandexFolders: List[str] = []
    daysToGenerate: int = 7
    themeAliases: Dict[str, List[str]] = {}
    brandQuotas: Dict[str, Dict[str, int]] = {}
    limits: GlobalLimits
    profiles: List[SocialProfile] = []
    clients: List[ClientConfig] = []
    schedule: Optional[Dict[str, Any]] = None

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://admin:admin@tools_postgres:5432/postgres"
    YANDEX_TOKEN: str = ""
    OPENAI_API_KEY: str = ""
    UPLOAD_POST_API_KEY: str = ""
    DATA_DIR: str = "/data"
    REDIS_URL: str = "redis://tools_redis:6379/0"
    
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
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self._legacy_config = LegacyConfig(**data)
            return self._legacy_config

    class Config:
        env_file = ".env"

settings = Settings()
