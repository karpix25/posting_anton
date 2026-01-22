import asyncio
from app.database import get_session
from app.models import PostingHistory
from sqlalchemy import select, desc

async def inspect_failures():
    print("--- Inspecting Last 10 Failed Posts ---")
    async for session in get_session():
        stmt = select(PostingHistory).where(PostingHistory.status == 'failed').order_by(desc(PostingHistory.posted_at)).limit(10)
        result = await session.execute(stmt)
        posts = result.scalars().all()
        
        for p in posts:
            print(f"ID: {p.id} | Date: {p.posted_at} | Profile: {p.profile_username} | Error: {p.meta.get('error', 'N/A')} | Meta keys: {list(p.meta.keys())}")

if __name__ == "__main__":
    asyncio.run(inspect_failures())
