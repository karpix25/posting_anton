
import logging
from sqlalchemy import select
from app.database import async_session_maker
from app.models import SystemConfig
from app.services.platforms import upload_post_client
from app.services.config_db import CONFIG_KEY

logger = logging.getLogger(__name__)

async def sync_profiles_service():
    """
    Synchronizes local DB profiles with the external API.
    Refuses to delete all profiles if API returns 0 (safety mechanism).
    Returns a dict with statistics: { "total": int, "removed": int, "added": int, "params_updated": int }
    """
    logger.info("📡 [Sync] Fetching LIVE profiles from API...")
    try:
        live_profiles = await upload_post_client.get_profiles()
    except Exception as e:
        logger.error(f"❌ [Sync] Failed to fetch from API: {e}")
        raise Exception(f"API Error: {e}")

    if not live_profiles:
        logger.error("🛑 [Sync] API returned 0 profiles! Aborting sync to prevent data loss.")
        raise Exception("API returned 0 profiles. Sync aborted for safety.")

    # Normalize live usernames map: lowercase -> full profile dict
    live_map = {p.get('social_username', '').lower(): p for p in live_profiles if p.get('social_username')}
    logger.info(f"✅ [Sync] Found {len(live_map)} live profiles in API.")

    stats = {"total": 0, "removed": 0, "added": 0, "params_updated": 0}

    async with async_session_maker() as session:
        logger.info("💾 [Sync] Fetching System Config from DB...")
        stmt = select(SystemConfig).where(SystemConfig.key == CONFIG_KEY)
        result = await session.execute(stmt)
        config_record = result.scalar_one_or_none()

        if not config_record:
            raise Exception("No system configuration found in DB.")

        config_data = config_record.value
        db_profiles = config_data.get('profiles', [])
        
        logger.info(f"📋 [Sync] Found {len(db_profiles)} profiles in DB config.")
        
        new_db_profiles = []
        existing_usernames = set()

        # 1. Retain only valid profiles (Remove Dead Souls)
        for p in db_profiles:
            uname = p.get('username', '')
            uname_lower = uname.lower()
            
            if uname_lower in live_map:
                # Update casing to match API exactly
                api_p = live_map[uname_lower]
                correct_uname = api_p.get('social_username')
                
                if p['username'] != correct_uname:
                    p['username'] = correct_uname
                    stats['params_updated'] += 1
                
                new_db_profiles.append(p)
                existing_usernames.add(uname_lower)
            else:
                stats['removed'] += 1
                if stats['removed'] <= 10:
                    logger.info(f"   🗑️ removing dead soul: {uname}")

        # 2. Add New Profiles (from API that are missing in DB)
        # Note: We initialize them with default enabled=True and global limits
        for uname_lower, api_p in live_map.items():
            if uname_lower not in existing_usernames:
                correct_uname = api_p.get('social_username')
                new_profile = {
                    "username": correct_uname,
                    "enabled": True,
                    "instagramLimit": 0,
                    "tiktokLimit": 0,
                    "youtubeLimit": 0,
                    "proxy": None,
                    "platforms": ["instagram"] # Default, usage main_config default if preferable
                }
                new_db_profiles.append(new_profile)
                stats['added'] += 1
                if stats['added'] <= 10:
                    logger.info(f"   ✨ adding new profile: {correct_uname}")

        stats['total'] = len(new_db_profiles)

        if stats['removed'] == 0 and stats['added'] == 0 and stats['params_updated'] == 0:
            logger.info("✨ [Sync] Database is already in sync!")
        else:
            # Update Config
            config_data['profiles'] = new_db_profiles
            config_record.value = config_data
            session.add(config_record)
            await session.commit()
            logger.info(f"✅ [Sync] Completed. New count: {len(new_db_profiles)}")

    return stats
