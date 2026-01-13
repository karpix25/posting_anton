import yadisk
import asyncio
from typing import List, Dict, Any, Optional
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class YandexDiskService:
    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.YANDEX_TOKEN
        self.client = yadisk.AsyncClient(token=self.token)

    async def check_token(self) -> bool:
        async with self.client:
            return await self.client.check_token()

    async def list_files(self, limit: int = 100000) -> List[Dict[str, Any]]:
        """
        List video files with robust retry logic matching TypeScript implementation.
        Strategies:
        1. High timeout (120s) via method kwarg
        2. Retry with decreasing limits [limit, 5000, 2000] if timeout occurs.
        """
        limits_to_try = [limit, min(5000, limit), min(2000, limit)]
        # Deduplicate limits
        limits_to_try = sorted(list(set(limits_to_try)), reverse=True)

        async with self.client:
            for attempt, current_limit in enumerate(limits_to_try):
                try:
                    logger.info(f"[Yandex] Fetching files (limit={current_limit}, attempt={attempt+1}/{len(limits_to_try)})...")
                    
                    # fetch generator with per-request timeout
                    items_gen = self.client.get_files(
                        limit=current_limit,
                        media_type='video',
                        fields='items.name,items.path,items.md5,items.size,items.created',
                        timeout=120.0
                    )
                    
                    files = []
                    async for item in items_gen:
                        files.append({
                            "name": item.name,
                            "path": item.path,
                            "url": item.path,
                            "md5": item.md5,
                            "size": item.size,
                            "created": item.created.isoformat() if item.created else None
                        })
                    
                    logger.info(f"[Yandex] Fetched {len(files)} files.")
                    files.sort(key=lambda x: x["name"])
                    return files

                except Exception as e:
                    import httpx
                    last_error = e
                    is_timeout = isinstance(e, (yadisk.exceptions.RequestTimeoutError, httpx.ReadTimeout, httpx.ConnectTimeout))
                    # yadisk wraps exceptions? check string too
                    if "timeout" in str(e).lower() or isinstance(e, httpx.TimeoutException):
                        is_timeout = True
                        
                    if is_timeout and attempt < len(limits_to_try) - 1:
                        logger.warning(f"[Yandex] Timeout with limit {current_limit}. Retrying with lower limit...")
                        await asyncio.sleep(2)
                        continue
                    
                    logger.error(f"[Yandex] Error listing files: {e}")
                    raise e
            
            if last_error:
                raise last_error
            return []

    async def get_download_link(self, path: str) -> str:
        """Get a temporary download link for a file."""
        async with self.client:
            return await self.client.get_download_link(path)

    async def delete_file(self, path: str, permanently: bool = True):
        """Delete a file."""
        async with self.client:
            await self.client.remove(path, permanently=permanently)
            print(f"[Yandex] Deleted file: {path}")

# Singleton-like usage or dependency injection
yandex_service = YandexDiskService()
