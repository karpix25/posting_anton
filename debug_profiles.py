
import asyncio
import logging
import sys
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock settings/env if needed, or just import
sys.path.append(os.getcwd())
from app.config import settings
from app.services.platforms import upload_post_client

async def main():
    try:
        if not settings.UPLOAD_POST_API_KEY:
            print("❌ No API KEY")
            return

        print("Fetching profiles...")
        profiles = await upload_post_client.get_profiles()
        print(f"✅ Got {len(profiles)} profiles")
        
        if profiles:
            p = profiles[0]
            print(f"Sample Profile Keys: {list(p.keys())}")
            if 'social_accounts' in p:
                print(f"Social Accounts: {p['social_accounts']}")
            else:
                print("❌ 'social_accounts' key NOT found in profile object")
                
            # Check a few more if needed
            for i, p in enumerate(profiles[:5]):
                sa = p.get('social_accounts', {})
                print(f"Profile {p.get('username')}: IG={bool(sa.get('instagram'))}, TT={bool(sa.get('tiktok'))}")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
