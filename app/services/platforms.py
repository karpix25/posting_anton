import httpx
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings
from app.models import PostingHistory
from app.services.yandex import yandex_service

logger = logging.getLogger(__name__)

UPLOAD_POST_API_URL = 'https://api.upload-post.com/api/upload'
USER_PROFILES_API_URL = 'https://api.upload-post.com/api/uploadposts/users'
HISTORY_API_URL = 'https://api.upload-post.com/api/uploadposts/history'
SCHEDULE_API_URL = 'https://api.upload-post.com/api/uploadposts/schedule'

class UploadPostClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {'Authorization': f'Apikey {api_key}'}

    async def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(HISTORY_API_URL, params={'limit': limit}, headers=self.headers)
                data = response.json()
                return data.get('history', [])
            except Exception as e:
                print(f"[UploadPost] Error fetching history: {e}")
                return []

    async def get_profiles(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(USER_PROFILES_API_URL, headers=self.headers)
                data = response.json()
                if data.get('success'):
                    return data.get('profiles', [])
                raise Exception(data.get('message', 'Failed to fetch profiles'))
            except Exception as e:
                print(f"[UploadPost] Error fetching profiles: {e}")
                raise e

    async def get_scheduled_posts(self) -> List[Dict[str, Any]]:
        """Fetch list of pending scheduled posts from Upload Post API."""
        async with httpx.AsyncClient(timeout=120.0) as client:  # 2 minutes for schedule fetching
            try:
                response = await client.get(SCHEDULE_API_URL, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"[UploadPost] Fetched {len(data)} scheduled posts")
                    return data
                else:
                    logger.warning(f"[UploadPost] Schedule fetch returned {response.status_code}")
                    return []
            except Exception as e:
                logger.error(f"[UploadPost] Error fetching scheduled posts: {e}")
                return []

    async def publish(self, profile_username: str, platform: str, video_url: str, 
                      caption: str = "", title: str = "", publish_at: Optional[datetime] = None) -> Dict[str, Any]:
        
        data = {
            'user': profile_username,
            'platform[]': platform,
            'video': video_url,
            'title': title or caption  # Fallback title
        }

        # Add scheduled_date in ISO format
        if publish_at:
            # Format strictly as ISO 8601 with Z (JS style) for API
            # Python's isoformat() uses +00:00 for UTC, which APIs sometimes dislike if they expect JS toISOString() format
            data['scheduled_date'] = publish_at.isoformat().replace('+00:00', 'Z')

        # Platform-specific parameters (matching TS version)
        if platform == 'instagram':
            data['instagram_title'] = title or caption
            data['media_type'] = 'REELS'
        elif platform == 'tiktok':
            data['tiktok_title'] = title or caption
            data['post_mode'] = 'DIRECT_POST'
        elif platform == 'youtube':
            data['youtube_title'] = title or caption[:50]  # YouTube has title length limit
            data['youtube_description'] = caption
            data['categoryId'] = '22'  # People & Blogs
            data['privacyStatus'] = 'public'
        
        print(f"[UploadPost] Publishing for {profile_username} on {platform}...")
        if publish_at:
            print(f"[UploadPost] Scheduled for: {publish_at.isoformat()}")
        
        async with httpx.AsyncClient(timeout=300.0) as client:  # 5 minutes for large video uploads
            try:
                response = await client.post(UPLOAD_POST_API_URL, data=data, headers=self.headers)
                res_data = response.json()
                
                if res_data.get('success'):
                    request_id = res_data.get('request_id') or res_data.get('job_id') or 'sync'
                    print(f"[UploadPost] ✅ Success! Request ID: {request_id}")
                    return res_data
                else:
                    error = res_data.get('message') or res_data.get('error') or 'Unknown error'
                    logger.error(f"[UploadPost] ❌ Failed: {error} | Full response: {res_data}")
                    print(f"[UploadPost] ❌ Failed: {error}")
                    raise Exception(error)
            except httpx.HTTPStatusError as e:
                logger.error(f"[UploadPost] ❌ HTTP Error {e.response.status_code}: {e.response.text}")
                print(f"[UploadPost] ❌ HTTP Error: {e.response.status_code}")
                raise e
            except Exception as e:
                logger.error(f"[UploadPost] ❌ Exception: {type(e).__name__}: {e}")
                print(f"[UploadPost] ❌ Error: {e}")
                raise e


class PlatformManager:
    def __init__(self):
        self.api_key = settings.UPLOAD_POST_API_KEY
        self.client = UploadPostClient(self.api_key)

    async def publish_post(self, profile_username: str, platform: str, video_url: str = None, video_path: str = None,
                           caption: str = "", title: str = "", publish_at: Optional[datetime] = None) -> Dict[str, Any]:
        
        # Support both video_url and video_path for backwards compatibility
        if not video_url:
            video_url = video_path
        
        # Resolve Yandex Disk path to download URL if needed
        if video_url and (video_url.startswith("disk:") or video_url.startswith("/")):
             print(f"[PlatformManager] Fetching download URL for {video_url}...")
             try:
                 # Retry logic for Yandex
                 for attempt in range(3):
                     try:
                         video_url = await yandex_service.get_download_link(video_url)
                         print(f"[PlatformManager] ✅ Got URL: {video_url[:50]}...")
                         break
                     except Exception as e:
                         if attempt == 2: raise e
                         await asyncio.sleep(2 ** attempt)
             except Exception as e:
                 print(f"[PlatformManager] ❌ Failed to get download URL: {e}")
                 return {"success": False, "error": str(e)}

        try:
            result = await self.client.publish(profile_username, platform, video_url, caption, title, publish_at)
            return {"success": True, **result}
        except Exception as e:
            print(f"[PlatformManager] ❌ Failed to publish: {e}")
            return {"success": False, "error": str(e)}

# Singleton
platform_manager = PlatformManager()
upload_post_client = UploadPostClient(settings.UPLOAD_POST_API_KEY)
