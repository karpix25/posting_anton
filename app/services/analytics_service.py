import logging
import asyncio
from datetime import datetime, date
from typing import List, Dict, Any
from sqlalchemy import select, func, delete, and_
from app.database import get_session
from app.models import DailyAnalytics, SystemConfig
from app.services.config_db import get_db_config
from app.services.platforms import platform_manager

logger = logging.getLogger(__name__)

class AnalyticsService:
    async def fetch_and_save_daily_stats(self):
        """
        Iterates through all active profiles, fetches analytics from UploadPost API,
        and saves them to the 'daily_analytics' table.
        """
        logger.info("[Analytics] Starting daily stats update...")
        
        # 1. Get active profiles from config
        config = None
        async for session in get_session():
            config = await get_db_config(session)
            break
            
        if not config or not config.profiles:
            logger.warning("[Analytics] No profiles configured.")
            return

        active_profiles = [p for p in config.profiles if p.enabled]
        logger.info(f"[Analytics] Found {len(active_profiles)} active profiles.")
        
        current_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 2. Fetch data for each profile
        results = []
        for profile in active_profiles:
            username = profile.username
            platforms = profile.platforms or ['instagram'] # Default list if empty
            
            # Filter supported analytics platforms
            supported = ['instagram', 'tiktok', 'youtube']
            target_platforms = [p for p in platforms if p in supported]
            
            if not target_platforms:
                continue
                
            logger.info(f"[Analytics] Fetching for {username} ({target_platforms})...")
            
            try:
                data = await platform_manager.client.get_analytics(username, target_platforms)
                
                # Check response structure
                # Expected: { "instagram": { "followers": 123, ... }, ... }
                
                for platform_name in target_platforms:
                    if platform_name in data:
                        p_data = data[platform_name]
                        
                        # Extract metrics based on platform
                        followers = 0
                        reach = 0
                        
                        if platform_name == 'instagram':
                            followers = p_data.get('followers', 0)
                            reach = p_data.get('reach', 0) # or impressions
                        elif platform_name == 'tiktok':
                            followers = p_data.get('followersBase', 0) or p_data.get('followers', 0)
                            reach = p_data.get('views', 0) # TikTok usually gives video views
                        elif platform_name == 'youtube':
                            followers = p_data.get('subscribers', 0)
                            reach = p_data.get('views', 0)
                            
                        results.append({
                            "date": current_date,
                            "profile_username": username,
                            "platform": platform_name,
                            "followers": followers,
                            "reach": reach,
                            "raw_data": p_data
                        })
                
                # Rate limit protection
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"[Analytics] Failed to process {username}: {e}")

        # 3. Save to DB (Upsert logic: delete today's then insert)
        if results:
            async for session in get_session():
                # Delete existing records for today to avoid duplicates
                try:
                    stmt = delete(DailyAnalytics).where(DailyAnalytics.date == current_date)
                    await session.execute(stmt)
                    
                    for r in results:
                        stat = DailyAnalytics(**r)
                        session.add(stat)
                    
                    await session.commit()
                    logger.info(f"[Analytics] Successfully saved {len(results)} records for {current_date.date()}")
                except Exception as e:
                    logger.error(f"[Analytics] DB Error: {e}")
                    await session.rollback()

    async def get_aggregated_stats(self) -> Dict[str, Any]:
        """
        Returns aggregated stats for the most recent available date.
        Grouped by platform.
        """
        async for session in get_session():
            # Get latest date
            stmt = select(func.max(DailyAnalytics.date))
            result = await session.execute(stmt)
            latest_date = result.scalar()
            
            if not latest_date:
                return {}
                
            # Query sums grouped by platform
            stmt = select(
                DailyAnalytics.platform,
                func.sum(DailyAnalytics.followers).label('total_followers'),
                func.sum(DailyAnalytics.reach).label('total_reach'),
                func.count(DailyAnalytics.id).label('profile_count')
            ).where(
                DailyAnalytics.date == latest_date
            ).group_by(DailyAnalytics.platform)
            
            result = await session.execute(stmt)
            rows = result.all()
            
            agg = {}
            for row in rows:
                agg[row.platform] = {
                    "followers": row.total_followers,
                    "reach": row.total_reach,
                    "profiles": row.profile_count
                }
            
            return {
                "date": latest_date.isoformat(),
                "platforms": agg
            }
        return {}

analytics_service = AnalyticsService()
