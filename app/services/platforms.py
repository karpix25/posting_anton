import httpx
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings
from app.models import PostingHistory
from app.services.yandex import yandex_service

UPLOAD_POST_API_URL = 'https://api.upload-post.com/api/upload'
USER_PROFILES_API_URL = 'https://api.upload-post.com/api/uploadposts/users'
HISTORY_API_URL = 'https://api.upload-post.com/api/uploadposts/history'

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

    async def publish(self, profile_username: str, platform: str, video_url: str, 
                      caption: str = "", title: str = "", publish_at: Optional[datetime] = None) -> Dict[str, Any]:
        
        data = {
            'user': profile_username,
            'platform[]': platform,
            'video': video_url,
            'title': caption # Default title mapping
        }

        if publish_at:
            data['scheduled_date'] = publish_at.isoformat()

        # Platform specific logic
        if platform == 'instagram':
            data['instagram_title'] = caption
            data['media_type'] = 'REELS'
        elif platform == 'tiktok':
            data['tiktok_title'] = caption
            data['post_mode'] = 'DIRECT_POST'
        elif platform == 'youtube':
            data['youtube_title'] = title or caption[:50]
            data['youtube_description'] = caption
            data['categoryId'] = '22'
            data['privacyStatus'] = 'public'
        
        print(f"[UploadPost] Publishing for {profile_username} on {platform}...")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                # Use data= for form-encoded (multipart not needed if sending string URL? TS used FormData which implies multipart)
                # TS used `new FormData()`. So we should ideally use data=data which httpx sends as application/x-www-form-urlencoded
                # OR files=... for multipart.
                # However, sending URL is usually form-data fields.
                # httpx `data` sends form-encoded. `files` sends multipart.
                # If the API expects multipart, we might need to be careful.
                # "form-data" library in node creates multipart.
                # So let's force multipart-like behavior?
                # Actually, standard is usually just data fields for text.
                # Let's try standard data first. If it fails, Convert to multipart dict.
                
                response = await client.post(UPLOAD_POST_API_URL, data=data, headers=self.headers)
                res_data = response.json()
                
                if res_data.get('success'):
                    print(f"[UploadPost] Success! Request ID: {res_data.get('request_id', 'sync')}")
                    return res_data
                else:
                    raise Exception(res_data.get('message', 'Unknown error'))
            except Exception as e:
                print(f"[UploadPost] Error: {e}")
                raise e


class PlatformManager:
    def __init__(self):
        self.api_key = settings.UPLOAD_POST_API_KEY
        self.client = UploadPostClient(self.api_key)

    async def publish_post(self, profile_username: str, platform: str, video_path: str, 
                           caption: str = "", title: str = "", publish_at: Optional[datetime] = None) -> bool:
        
        video_url = video_path
        
        # Resolve Yandex Disk path to download URL
        # "disk:/..." or starts with "/" if we treat it as remote path (which we do if we are Yandex hosted)
        # The scheduler passes `video.path` which is "disk:/Folder/..."
        
        if video_path.startswith("disk:") or video_path.startswith("/"):
             # Normalize for yandex check?
             # Yandex service handles paths.
             print(f"[PlatformManager] Fetching download URL for {video_path}...")
             try:
                 # Retry logic handled by yandex service or here?
                 # Let's do simple retry here
                 for attempt in range(3):
                     try:
                         video_url = await yandex_service.get_download_link(video_path)
                         print(f"[PlatformManager] Got URL: {video_url[:50]}...")
                         break
                     except Exception as e:
                         if attempt == 2: raise e
                         await asyncio.sleep(2 ** attempt)
             except Exception as e:
                 print(f"[PlatformManager] Failed to get download URL: {e}")
                 return False

        try:
            await self.client.publish(profile_username, platform, video_url, caption, title, publish_at)
            return True
        except Exception as e:
            print(f"[PlatformManager] Failed to publish: {e}")
            return False

# Singleton
platform_manager = PlatformManager()
