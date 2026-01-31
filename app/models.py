from typing import Optional, Dict, Any
from datetime import datetime
from sqlmodel import SQLModel, Field, JSON
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

class PostingHistory(SQLModel, table=True):
    __tablename__ = "posting_history"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    posted_at: datetime = Field(default_factory=datetime.utcnow)
    profile_username: str = Field(index=True)
    platform: str
    video_path: Optional[str] = None
    video_name: Optional[str] = None
    author: Optional[str] = Field(index=True)
    status: str = Field(default="success") # success, failed
    # Use JSONB for meta to match existing schema
    meta: Optional[Dict[str, Any]] = Field(default={}, sa_column=Column(JSONB))

class BrandStats(SQLModel, table=True):
    __tablename__ = "brand_stats"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    category: str = Field(index=True)
    brand: str = Field(index=True)
    month: str = Field(index=True) # YYYY-MM
    published_count: int = Field(default=0)
    quota: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Composite unique constraint is not directly supported in SQLModel simple syntax yet via Field kwargs easily for composite,
    # but we can add __table_args__.
    # However, since the table ALREADY exists in the correct schema, we just need the model to match.
    # If we were relying on create_all to enforce it, we'd add it.
    # __table_args__ = (UniqueConstraint("category", "brand", "month"),)
    # __table_args__ = (UniqueConstraint("category", "brand", "month"),)

class SystemConfig(SQLModel, table=True):
    __tablename__ = "system_config"
    key: str = Field(primary_key=True)
    value: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class DailyAnalytics(SQLModel, table=True):
    __tablename__ = "daily_analytics"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    date: datetime = Field(default_factory=datetime.utcnow, index=True) # Normalized to YYYY-MM-DD
    profile_username: str = Field(index=True)
    platform: str = Field(index=True) # instagram, tiktok, youtube
    
    # Metrics
    followers: int = Field(default=0)
    reach: int = Field(default=0) # Or views for TikTok/YouTube
    engagement_rate: float = Field(default=0.0)
    
    # Store raw API response just in case
    raw_data: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SocialProfile(SQLModel, table=True):
    __tablename__ = "social_profiles_db"  # suffix _db to avoid conflict with Pydantic model
    
    username: str = Field(primary_key=True)
    theme_key: Optional[str] = Field(default=None, index=True)
    enabled: bool = Field(default=True)
    
    # Stores ["instagram", "tiktok"] as JSON
    platforms: Dict[str, Any] = Field(default=[], sa_column=Column(JSONB)) 
    
    # Limits
    instagram_limit: Optional[int] = Field(default=None)
    tiktok_limit: Optional[int] = Field(default=None)
    youtube_limit: Optional[int] = Field(default=None)
    
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class AIClient(SQLModel, table=True):
    __tablename__ = "ai_clients_db"
    
    name: str = Field(primary_key=True)
    prompt: Optional[str] = None
    regex: Optional[str] = None
    
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class AppSettings(SQLModel, table=True):
    __tablename__ = "app_settings"
    
    id: int = Field(default=1, primary_key=True) # Singleton
    
    cron_schedule: Optional[str] = Field(default="1 0 * * *")
    days_to_generate: int = Field(default=1)
    
    # Array of strings
    yandex_folders: Dict[str, Any] = Field(default=[], sa_column=Column(JSONB))
    
    # Global limits: {"instagram": 10, ...}
    global_limits: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    # Structure features
    theme_aliases: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    brand_quotas: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    # Schedule Meta
    schedule_enabled: bool = Field(default=False)
    schedule_timezone: str = Field(default="Europe/Moscow")
    schedule_time: str = Field(default="00:00")
    
    # Cached Stats (to avoid rescanning and persistance)
    cached_yandex_stats: Dict[str, Any] = Field(default={}, sa_column=Column(JSONB))
    
    updated_at: datetime = Field(default_factory=datetime.utcnow)
