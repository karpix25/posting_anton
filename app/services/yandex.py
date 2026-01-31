import yadisk
import asyncio
from typing import List, Dict, Any, Optional
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class YandexDiskService:
    CACHE_TTL = 900  # 15 minutes

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.YANDEX_TOKEN
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0

    async def check_token(self) -> bool:
        async with yadisk.AsyncClient(token=self.token) as client:
            return await client.check_token()

    async def list_files(self, limit: int = 100000, force_refresh: bool = False, 
                   folders: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        List video files with in-memory caching and optional path filtering.
        """
        import time
        now = time.time()
        
        # Check cache
        if not force_refresh and self._cache and (now - self._cache_time < self.CACHE_TTL):
            age = int(now - self._cache_time)
            logger.debug(f"[Yandex] Returning cached file list ({len(self._cache)} files, age: {age}s)")
            return self._cache

        # User requested fallback steps: 100k -> 80k -> 60k
        # We start with the requested 'limit', then add fallbacks.
        # Ensure we don't try limits higher than the initial request if it was small.
        possible_limits = [limit]
        if limit >= 80000:
            possible_limits.append(80000)
        if limit >= 60000:
            possible_limits.append(60000)
            
        limits_to_try = sorted(list(set(possible_limits)), reverse=True)

        # Create fresh client for this request to avoid "client closed" errors
        async with yadisk.AsyncClient(token=self.token) as client:
            for attempt, current_limit in enumerate(limits_to_try):
                try:
                    logger.info(f"[Yandex] Fetching files (limit={current_limit}, attempt={attempt+1}/{len(limits_to_try)})...")
                    
                    # fetch generator with per-request timeout
                    items_gen = client.get_files(
                        limit=current_limit,
                        media_type='video',
                        fields='items.name,items.path,items.md5,items.size,items.created',
                        timeout=600.0
                    )
                    
                    files = []
                    logger.info("[Yandex] Starting stream processing...")
                    processed_count = 0
                    
                    async for item in items_gen:
                        processed_count += 1
                        path = item.path
                        
                        # Apply early path filtering if folders provided
                        if folders:
                            # Normalize path for comparison (remove disk:, strip slashes, lowercase)
                            path_norm = path.replace("disk:", "").strip("/").lower()
                            match = False
                            for f in folders:
                                # Normalize folder for comparison
                                f_norm = f.replace("disk:", "").strip("/").lower()
                                
                                # 1. Prefix Match (Standard for paths)
                                if path_norm.startswith(f_norm):
                                    match = True
                                    break
                                    
                                # 2. Segment Match (For Client Names auto-added to list)
                                # If filter is a simple name (no slashes), check if it exists as a folder segment
                                if "/" not in f_norm:
                                    # fast check substring first
                                    if f_norm in path_norm:
                                        # Strict segment check to avoid partial matches (e.g. 'box' in 'dropbox')
                                        segments = path_norm.split("/")
                                        if f_norm in segments:
                                            match = True
                                            break
                            if not match:
                                continue

                        files.append({
                            "name": item.name,
                            "path": path,
                            "url": path,
                            "md5": item.md5,
                            "size": item.size,
                            "created": item.created.isoformat() if item.created else None
                        })
                        
                        if len(files) % 5000 == 0 and len(files) > 0:
                            logger.info(f"[Yandex] ... matched {len(files)} files (scanned {processed_count})")
                    
                    logger.info(f"[Yandex] Finished fetch: Scanned {processed_count} files, Matched {len(files)} after filtering.")
                    
                    if files:
                        logger.info(f"[Yandex] Sorting {len(files)} files...")
                        files.sort(key=lambda x: x["name"])
                        logger.info("[Yandex] Sort complete.")
                    
                    # Update Cache
                    self._cache = files
                    self._cache_time = time.time()
                    
                    return files

                except Exception as e:
                    import httpx
                    last_error = e
                    is_retryable = isinstance(e, (yadisk.exceptions.RequestTimeoutError, yadisk.exceptions.InternalServerError, httpx.ReadTimeout, httpx.ConnectTimeout))
                    # yadisk wraps exceptions? check string too
                    if "timeout" in str(e).lower() or "internalservererror" in str(e).lower() or isinstance(e, httpx.TimeoutException):
                        is_retryable = True
                        
                    if is_retryable and attempt < len(limits_to_try) - 1:
                        logger.warning(f"[Yandex] Retryable error ({type(e).__name__}) with limit {current_limit}. Retrying with lower limit...")
                        await asyncio.sleep(2)
                        continue
                    
                    logger.error(f"[Yandex] Error listing files: {e}")
                    raise e
            
            if last_error:
                raise last_error
            return []

    async def get_download_link(self, path: str) -> str:
        """Get a temporary download link for a file."""
        async with yadisk.AsyncClient(token=self.token) as client:
            return await client.get_download_link(path)

    async def delete_file(self, path: str, permanently: bool = True):
        """Delete a file."""
        async with yadisk.AsyncClient(token=self.token) as client:
            await client.remove(path, permanently=permanently)
            print(f"[Yandex] Deleted file: {path}")

    async def exists(self, path: str) -> bool:
        """Check if file or folder exists on Yandex Disk."""
        try:
            async with yadisk.AsyncClient(token=self.token) as client:
                return await client.exists(path)
        except Exception as e:
            logger.error(f"[Yandex] Error checking existence of {path}: {e}")
            return False

    async def move_file(self, source_path: str, dest_folder: str = "disk:/опубликовано"):
        """Move a file to archive folder instead of deleting."""
        import os
        
        async with yadisk.AsyncClient(token=self.token) as client:
            # Ensure destination folder exists
            try:
                if not await client.exists(dest_folder):
                    await client.mkdir(dest_folder)
                    logger.info(f"[Yandex] Created archive folder: {dest_folder}")
            except Exception as e:
                logger.warning(f"[Yandex] Could not create/check folder {dest_folder}: {e}")
            
            # Get filename from path
            filename = os.path.basename(source_path)
            dest_path = f"{dest_folder}/{filename}"
            
            # Handle duplicate filenames by adding timestamp
            try:
                if await client.exists(dest_path):
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    name, ext = os.path.splitext(filename)
                    dest_path = f"{dest_folder}/{name}_{timestamp}{ext}"
            except Exception:
                pass  # If check fails, just try to move
            
            # Move file
            await client.move(source_path, dest_path, overwrite=True)
            logger.info(f"[Yandex] Moved file: {source_path} -> {dest_path}")
            return dest_path

# Singleton-like usage or dependency injection
yandex_service = YandexDiskService()
