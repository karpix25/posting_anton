import asyncio
from sqlalchemy import text
from app.database import engine

async def migrate():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS cached_yandex_stats JSONB DEFAULT '{}'"))
            print("✅ Added 'cached_yandex_stats' column to 'app_settings'")
            
            await conn.execute(text("ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS min_interval_minutes INTEGER DEFAULT 45"))
            print("✅ Added 'min_interval_minutes' column to 'app_settings'")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
