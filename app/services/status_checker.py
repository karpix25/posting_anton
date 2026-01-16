import httpx
import logging
from typing import Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

STATUS_API_URL = 'https://api.upload-post.com/api/uploadposts/status'

class UploadStatusChecker:
    """Service for checking async upload status from UploadPost API."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {'Authorization': f'Apikey {api_key}'}
    
    
    async def check_status(self, request_id: Optional[str] = None, 
                            job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check upload status for async uploads or scheduled posts.
        
        Args:
            request_id: For async uploads (async_upload=true)
            job_id: For scheduled posts (scheduled_date set)
            
        Returns:
            {
                "status": "pending" | "in_progress" | "completed",
                "completed": int,
                "total": int,
                "results": [{"platform": str, "success": bool, "message": str}]
            }
        """
        if not request_id and not job_id:
            raise ValueError("Either request_id or job_id must be provided")
        
        params = {}
        if request_id:
            params['request_id'] = request_id
        if job_id:
            params['job_id'] = job_id
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    STATUS_API_URL,
                    params=params,
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"[StatusChecker] Status for {request_id or job_id}: {data.get('status')}")
                    return data
                elif response.status_code == 400:
                    logger.warning(f"[StatusChecker] Bad request: {response.text}")
                    return {'status': 'error', 'error': 'Bad request'}
                elif response.status_code == 401:
                    logger.error(f"[StatusChecker] Unauthorized - check API key")
                    return {'status': 'error', 'error': 'Unauthorized'}
                else:
                    logger.error(f"[StatusChecker] HTTP {response.status_code}: {response.text}")
                    return {'status': 'error', 'error': f'HTTP {response.status_code}'}
                    
            except Exception as e:
                logger.error(f"[StatusChecker] Exception: {type(e).__name__}: {e}")
                return {'status': 'error', 'error': str(e)}

# Singleton
upload_status_checker = UploadStatusChecker(settings.UPLOAD_POST_API_KEY)
