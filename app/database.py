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
        # Defensive Migration: Fix system_config schema mismatch without data loss
        try:
            from sqlalchemy import text
            # Check if table exists
            res = await conn.execute(text("SELECT 1 FROM information_schema.tables WHERE table_name = 'system_config'"))
            if res.fetchone():
                # Check for 'key' column
                res_col = await conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'system_config' AND column_name = 'key'"
                ))
                if not res_col.fetchone():
                    # Check for legacy 'id' column to migrate
                    res_id = await conn.execute(text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'system_config' AND column_name = 'id'"
                    ))
                    if res_id.fetchone():
                        print("🛠️ [DB] Migrating legacy 'system_config.id' to 'key'...")
                        await conn.execute(text("ALTER TABLE system_config RENAME COLUMN id TO key"))
                        await conn.execute(text("ALTER TABLE system_config ALTER COLUMN key TYPE VARCHAR"))
                    else:
                        print("🛠️ [DB] Adding missing 'key' column to 'system_config'...")
                        await conn.execute(text("ALTER TABLE system_config ADD COLUMN key VARCHAR"))
                        await conn.execute(text("UPDATE system_config SET key = 'main_config' WHERE key IS NULL"))

                # Check for 'value' column (JSONB) — may be absent if table was created from old schema
                res_val = await conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'system_config' AND column_name = 'value'"
                ))
                if not res_val.fetchone():
                    print("🛠️ [DB] Adding missing 'value' JSONB column to 'system_config'...")
                    await conn.execute(text(
                        "ALTER TABLE system_config ADD COLUMN value JSONB NOT NULL DEFAULT '{}'"
                    ))

                # Check for 'updated_at' column — may also be absent in old schemas
                res_upd = await conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'system_config' AND column_name = 'updated_at'"
                ))
                if not res_upd.fetchone():
                    print("🛠️ [DB] Adding missing 'updated_at' column to 'system_config'...")
                    await conn.execute(text(
                        "ALTER TABLE system_config ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE "
                        "NOT NULL DEFAULT now()"
                    ))

        except Exception as e:
            print(f"⚠️ [DB] Defensive migration error (ignoring): {e}")

        # await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session
