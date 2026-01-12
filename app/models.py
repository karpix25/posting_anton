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
