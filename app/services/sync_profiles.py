
import logging
from datetime import datetime
from sqlalchemy import select, delete
from app.database import async_session_maker
from app.models import SocialProfile
from app.services.platforms import upload_post_client

logger = logging.getLogger(__name__)

async def sync_profiles_service():
    """
    Synchronizes local RElational DB profiles with the external API.
    Refuses to delete all profiles if API returns 0 (safety mechanism).
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

    # 1. Filter & Normalize from API
    # Only keep profiles with at least one connected social account
    valid_lives = {}
    
    for p in live_profiles:
        raw_uname = p.get('username')
        if not raw_uname: continue
        
        # Check connected accounts
        socials = p.get('social_accounts', {})
        active_platforms = []
        
        # UploadPost returns dict: {"instagram": {...}, "tiktok": ""}
        # If value is truthy (dict with data), it's connected.
        for platform, details in socials.items():
            if details: 
                active_platforms.append(platform)
        
        if not active_platforms:
            # Skip profile with no connected accounts
            continue
            
        uname_key = raw_uname.lower().strip()
        valid_lives[uname_key] = {
            "username": raw_uname, # Preserve case from API
            "platforms": active_platforms
        }

    if not valid_lives:
        logger.warning("🛑 [Sync] No profiles with active social accounts found! Aborting.")
        # Raise exception or just return? If user really deleted all connections, we should theoretically sync that.
        # But '0' is suspicious. Let's block.
        raise Exception("No active profiles found (with connected social accounts).")

    logger.info(f"✅ [Sync] Found {len(valid_lives)} valid profiles (with social accounts).")

    stats = {"total": 0, "removed": 0, "added": 0, "params_updated": 0}

    async with async_session_maker() as session:
        # 2. Fetch Existing DB Profiles
        stmt = select(SocialProfile)
        result = await session.execute(stmt)
        db_profiles = result.scalars().all()
        db_map = {p.username.lower().strip(): p for p in db_profiles}
        
        logger.info(f"📋 [Sync] Found {len(db_profiles)} profiles in Local DB.")
        
        # 3. Sync Logic
        
        # A. Update or Add
        for key, api_data in valid_lives.items():
            correct_uname = api_data["username"]
            detected_platforms = api_data["platforms"]
            
            if key in db_map:
                # Update existing
                p = db_map[key]
                changed = False
                
                # Check casing change (unlikely but possible)
                if p.username != correct_uname:
                    # PK change is hard. SQLModel doesn't support PK update easily without cascade.
                    # Usually we treat as same user. We can just ignore casing display for now or recreate?
                    # Let's assume username is stable enough.
                    pass 
                
                # Update platforms list if simplified
                # We merge detected platforms with existing ones? 
                # Or overwrite? User might have unchecked some in our UI?
                # "Synced" implies source of truth is API.
                # Let's overwrite platforms list with what is actually connected.
                current_platforms = p.platforms or []
                # Sort for comparison
                if sorted(current_platforms) != sorted(detected_platforms):
                   p.platforms = detected_platforms
                   changed = True
                   stats['params_updated'] += 1
                
                # Re-enable if it was disabled? No, respect user choice.
                # Update timestamp
                if changed:
                    p.updated_at = datetime.utcnow()
                
            else:
                # Add New
                new_p = SocialProfile(
                    username=correct_uname,
                    theme_key=None, # User must set theme
                    enabled=True,
                    platforms=detected_platforms,
                    updated_at=datetime.utcnow()
                )
                session.add(new_p)
                stats['added'] += 1
        
        # B. Remove Missing (Dead Souls or Disconnected)
        for key, p in db_map.items():
            if key not in valid_lives:
                await session.delete(p)
                stats['removed'] += 1

        await session.commit()
    
    stats['total'] = len(valid_lives)
    logger.info(f"✅ [Sync] Complete. Total: {stats['total']}, Added: {stats['added']}, Removed: {stats['removed']}")
    return stats
