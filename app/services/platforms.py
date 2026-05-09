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

INSTAGRAM_TITLE_MAX_CHARS = 1870
TIKTOK_TITLE_MAX_CHARS = 1870
YOUTUBE_TITLE_MAX_CHARS = 85
YOUTUBE_DESCRIPTION_MAX_CHARS = 4250


def _normalize_text(value: str, max_chars: int) -> str:
    text = " ".join((value or "").strip().split())
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text

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
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(USER_PROFILES_API_URL, headers=self.headers)
                data = response.json()
                if data.get('success'):
                    return data.get('profiles', [])
                raise Exception(data.get('message', f'Failed to fetch profiles (HTTP {response.status_code})'))
            except Exception as e:
                last_error = e
                logger.warning(f"[UploadPost] get_profiles attempt {attempt}/3 failed: {e}")
                if attempt < 3:
                    await asyncio.sleep(2 ** (attempt - 1))
        print(f"[UploadPost] Error fetching profiles: {last_error}")
        raise last_error if last_error else Exception("Failed to fetch profiles")

    async def get_scheduled_posts(self) -> List[Dict[str, Any]]:
        """Fetch list of pending scheduled posts from Upload Post API."""
        async with httpx.AsyncClient(timeout=600.0) as client:  # 10 minutes for schedule fetching
            try:
                response = await client.get(SCHEDULE_API_URL, headers=self.headers)
                if response.status_code == 200:
                    data = response.json()
                    # User docs said array, but debug shows {'scheduled_posts': []}
                    # Handle both cases for robustness
                    if isinstance(data, dict):
                        posts = data.get('scheduled_posts', [])
                    elif isinstance(data, list):
                        posts = data
                    else:
                        posts = []
                        
                    logger.info(f"[UploadPost] Fetched {len(posts)} scheduled posts")
                    return posts
                else:
                    logger.warning(f"[UploadPost] Schedule fetch returned {response.status_code}")
                    return []
            except Exception as e:
                logger.error(f"[UploadPost] Error fetching scheduled posts: {e}")
                return []

    async def cancel_scheduled_post(self, job_id: str, max_attempts: int = 4) -> bool:
        """Cancel a pending scheduled post in Upload Post by job ID."""
        if not job_id:
            return False

        url = f"{SCHEDULE_API_URL}/{job_id}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.delete(url, headers=self.headers)
                    data = response.json() if response.content else {}
                    if response.status_code < 400 and data.get("success", True):
                        logger.info(f"[UploadPost] Cancelled scheduled job {job_id}")
                        return True

                    if response.status_code == 429 and attempt < max_attempts:
                        retry_after = response.headers.get("retry-after")
                        try:
                            wait_seconds = float(retry_after) if retry_after else 10.0 * attempt
                        except ValueError:
                            wait_seconds = 10.0 * attempt
                        logger.warning(
                            f"[UploadPost] Rate limited cancelling job {job_id}; "
                            f"retrying in {wait_seconds:.1f}s (attempt {attempt}/{max_attempts})"
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                    logger.warning(f"[UploadPost] Failed to cancel job {job_id}: {response.status_code} {data}")
                    return False
                except Exception as e:
                    if attempt < max_attempts:
                        wait_seconds = 5.0 * attempt
                        logger.warning(
                            f"[UploadPost] Error cancelling job {job_id}: {e}; "
                            f"retrying in {wait_seconds:.1f}s (attempt {attempt}/{max_attempts})"
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    logger.error(f"[UploadPost] Error cancelling job {job_id}: {e}")
                    return False

        return False

    async def get_analytics(self, profile_username: str, platforms: List[str]) -> Dict[str, Any]:
        """Fetch analytics for specific profile and platforms."""
        url = f"https://api.upload-post.com/api/analytics/{profile_username}"
        params = {'platforms': ','.join(platforms)}
        
        async with httpx.AsyncClient(timeout=200.0) as client:
            try:
                response = await client.get(url, params=params, headers=self.headers)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"[Analytics] Failed to fetch for {profile_username}: {response.status_code} {response.text}")
                    return {}
            except Exception as e:
                logger.error(f"[Analytics] Error fetching for {profile_username}: {e}")
                return {}

    async def publish(self, profile_username: str, platform: str, video_url: str, 
                      caption: str = "", title: str = "", publish_at: Optional[datetime] = None) -> Dict[str, Any]:
        is_youtube = platform == 'youtube'
        resolved_title = (title or "").strip()
        resolved_caption = (caption or "").strip()
        fallback_title = (resolved_title or resolved_caption).strip()

        data = {
            'user': profile_username,
            'platform[]': platform,
            'video': video_url,
            'async_upload': 'true'  # Keep lowercase string form-data value expected by API
        }

        # Upload Post docs: global `title` is a fallback and required for YouTube.
        # For IG/TikTok we send only platform-specific title to avoid text duplication.
        if is_youtube and fallback_title:
            data['title'] = _normalize_text(fallback_title, YOUTUBE_TITLE_MAX_CHARS)

        # Add scheduled_date in ISO format
        if publish_at:
            # Format strictly as ISO 8601 with Z (JS style) for API
            # Python's isoformat() uses +00:00 for UTC, which APIs sometimes dislike if they expect JS toISOString() format
            data['scheduled_date'] = publish_at.isoformat().replace('+00:00', 'Z')

        # Platform-specific parameters (matching TS version)
        if platform == 'instagram':
            # Docs: Instagram uses `instagram_title`; global `description` is ignored.
            instagram_text = _normalize_text(resolved_caption or fallback_title, INSTAGRAM_TITLE_MAX_CHARS)
            if instagram_text:
                data['instagram_title'] = instagram_text
            data['media_type'] = 'REELS'
        elif platform == 'tiktok':
            # Docs: TikTok uses `tiktok_title`; global `description` is ignored.
            tiktok_text = _normalize_text(resolved_caption or fallback_title, TIKTOK_TITLE_MAX_CHARS)
            if tiktok_text:
                data['tiktok_title'] = tiktok_text
            data['post_mode'] = 'DIRECT_POST'
        elif platform == 'youtube':
            yt_title = _normalize_text(fallback_title, YOUTUBE_TITLE_MAX_CHARS)
            data['youtube_title'] = yt_title
            yt_description = _normalize_text(resolved_caption or fallback_title, YOUTUBE_DESCRIPTION_MAX_CHARS)
            data['youtube_description'] = yt_description
            # Upload Post docs: YouTube description can also come from global `description`.
            data['description'] = yt_description
            data['categoryId'] = '22'  # People & Blogs
            data['privacyStatus'] = 'public'
        
        print(f"[UploadPost] Publishing for {profile_username} on {platform}...")
        if publish_at:
            print(f"[UploadPost] Scheduled for: {publish_at.isoformat()}")
        
        # Increased timeout to 600s (10 mins) as API might be extremely busy
        async with httpx.AsyncClient(timeout=600.0) as client:
            try:
                response = await client.post(UPLOAD_POST_API_URL, data=data, headers=self.headers)
                res_data = response.json()
                
                if res_data.get('success'):
                    # Extract request_id (async) or job_id (scheduled) for status tracking
                    request_id = res_data.get('request_id')
                    job_id = res_data.get('job_id')
                    tracking_id = request_id or job_id or 'unknown'
                    
                    # Check if it's scheduled (202) or async (200)
                    is_scheduled = response.status_code == 202 or job_id is not None
                    
                    if is_scheduled:
                        logger.info(f"[UploadPost] ✅ Post scheduled! Job ID: {job_id}")
                        print(f"[UploadPost] ✅ Post scheduled! Job ID: {job_id}")
                    else:
                        logger.info(f"[UploadPost] ✅ Async upload started! Request ID: {request_id}")
                        print(f"[UploadPost] ✅ Async upload started! Request ID: {request_id}")
                    
                    # Return with tracking info
                    return {
                        'success': True,
                        'request_id': request_id,
                        'job_id': job_id,
                        'async': True,
                        'scheduled': is_scheduled
                    }
                else:
                    # Improved error extraction - try multiple fields
                    error = (res_data.get('message') or 
                            res_data.get('error') or 
                            res_data.get('errors') or
                            str(res_data))
                    
                    logger.error(f"[UploadPost] ❌ API Error: {error}")
                    logger.error(f"[UploadPost] Full response: {res_data}")
                    print(f"[UploadPost] ❌ Failed: {error}")
                    raise Exception(error)
            except httpx.ReadTimeout as e:
                error_msg = f"ReadTimeout after 200s - API not responding"
                logger.error(f"[UploadPost] ❌ {error_msg}")
                print(f"[UploadPost] ❌ {error_msg}")
                raise Exception(error_msg)
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                logger.error(f"[UploadPost] ❌ {error_msg}")
                print(f"[UploadPost] ❌ HTTP Error: {e.response.status_code}")
                raise Exception(error_msg)
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

        # Append filename as hashtag to caption if available
        if video_path:
            import os
            import re
            try:
                # 1. Get clean filename
                clean_path = video_path.replace("disk:", "").strip("/")
                filename = os.path.basename(clean_path)
                
                # 2. Strip extension
                name_without_ext = os.path.splitext(filename)[0]
                
                # 3. Sanitize for Hashtag (Alphanumeric + Underscores, no spaces)
                # Replace spaces and non-alphanumeric chars with empty string or underscore
                # Usually hashtags shouldn't have weird chars. specific replacements:
                # Spaces -> remove or underscore? User said "video.mp4" -> "#video".
                # Let's strip spaces to be safe for hashtag continuity
                hashtag_body = re.sub(r'[^\w]', '', name_without_ext) 
                
                # Should not be empty
                if hashtag_body:
                    hashtag = f"#{hashtag_body}"
                    
                    if caption:
                        caption = f"{caption}\n\n{hashtag}"
                    else:
                        caption = hashtag
                        
                    logger.info(f"[PlatformManager] Appended hashtag to caption: {hashtag}")
            except Exception as e:
                logger.warning(f"[PlatformManager] Failed to append filename hashtag: {e}")

        try:
            result = await self.client.publish(profile_username, platform, video_url, caption, title, publish_at)
            # ✅ Return original inputs along with final processed caption for DB matching
            return {
                "success": True, 
                "final_caption": caption, 
                "final_title": title,
                **result
            }
        except Exception as e:
            print(f"[PlatformManager] ❌ Failed to publish: {e}")
            return {"success": False, "error": str(e)}

# Singleton
platform_manager = PlatformManager()
upload_post_client = UploadPostClient(settings.UPLOAD_POST_API_KEY)
