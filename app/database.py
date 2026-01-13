from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from app.config import settings

# Create Async Engine
# Note: config.settings.DATABASE_URL usually comes in as "postgres://..." but SQLAlchemy needs "postgresql+asyncpg://..."
# We handle this replacement to be safe if user provides the old format
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Fix for EasyPanel/Heroku injecting 'sslmode' which asyncpg doesn't support in kwargs
if "?" in db_url:
    # simple brute force remove
    db_url = db_url.replace("?sslmode=disable", "").replace("&sslmode=disable", "")
    db_url = db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    db_url = db_url.replace("?sslmode=prefer", "").replace("&sslmode=prefer", "")

    db_url = db_url.replace("?sslmode=prefer", "").replace("&sslmode=prefer", "")

engine = create_async_engine(db_url, echo=False, future=True)

async_session_maker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def init_db():
    async with engine.begin() as conn:
        # await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session
