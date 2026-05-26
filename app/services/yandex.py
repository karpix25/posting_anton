import yadisk
import asyncio
from typing import List, Dict, Any, Optional
from app.config import settings
import logging
import httpx

logger = logging.getLogger(__name__)

ARCHIVE_PATH_PREFIXES = (
    "disk:/опубликовано",
    "disk:/опубликованно",
)
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "mpg", "mpeg"}

class YandexDiskService:
    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.YANDEX_TOKEN
        self.base_url = "https://cloud-api.yandex.net/v1/disk/resources"
        self._files_cache: Dict[tuple, List[Dict[str, Any]]] = {}
        self._files_cache_lock = asyncio.Lock()
        self._files_cache_refresh_task: Optional[asyncio.Task] = None
        self._directories_cache: Dict[tuple, List[str]] = {}
        self._directories_cache_lock = asyncio.Lock()

    async def check_token(self) -> bool:
        async with yadisk.AsyncClient(token=self.token) as client:
            return await client.check_token()

    async def list_files(
        self,
        limit: int = 100000,
        force_refresh: bool = False,
        folders: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        List video files with robust retry logic matching TypeScript implementation.
        Strategies:
        1. High timeout (600s) per request
        2. Retry with decreasing limits [limit, 5000, 2000] if timeout occurs.
        """
        cache_key = self._files_cache_key(limit, folders)
        if not force_refresh and cache_key in self._files_cache:
            logger.info("[Yandex] Returning cached files for folders=%s.", folders)
            return list(self._files_cache[cache_key])

        async with self._files_cache_lock:
            if not force_refresh and cache_key in self._files_cache:
                logger.info("[Yandex] Returning cached files for folders=%s.", folders)
                return list(self._files_cache[cache_key])

            files = await self._fetch_files(limit=limit, folders=folders)
            self._files_cache[cache_key] = list(files)
            if force_refresh:
                self._directories_cache.clear()
            return files

    async def refresh_files_cache(
        self,
        limit: int = 100000,
        folders: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return await self.list_files(limit=limit, force_refresh=True, folders=folders)

    def invalidate_files_cache(self):
        self._files_cache.clear()
        self._directories_cache.clear()

    def schedule_cached_files_refresh(self, delay_seconds: float = 30.0):
        cache_keys = list(self._files_cache.keys())
        if not cache_keys:
            return
        self.invalidate_files_cache()
        if self._files_cache_refresh_task and not self._files_cache_refresh_task.done():
            self._files_cache_refresh_task.cancel()
        self._files_cache_refresh_task = asyncio.create_task(
            self._refresh_previous_file_cache_keys(cache_keys, delay_seconds)
        )

    async def _refresh_previous_file_cache_keys(self, cache_keys: List[tuple], delay_seconds: float):
        try:
            await asyncio.sleep(delay_seconds)
            for limit, folders_key in cache_keys:
                await self.list_files(limit=limit, force_refresh=True, folders=list(folders_key))
            logger.info("[Yandex] Refreshed %s cached file views after disk changes.", len(cache_keys))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Yandex] Failed to refresh cached file views after disk changes: {e}")

    def _files_cache_key(self, limit: int, folders: Optional[List[str]]) -> tuple:
        folder_key = tuple(sorted(self._normalize_disk_api_path(str(folder)) for folder in (folders or []) if folder))
        return (int(limit), folder_key)

    async def _fetch_files(
        self,
        limit: int,
        folders: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        limits_to_try = [limit, min(5000, limit), min(2000, limit)]
        # Deduplicate limits
        limits_to_try = sorted(list(set(limits_to_try)), reverse=True)

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
                    files = self._filter_files_by_folders(files, folders)
                    files = await self._augment_files_from_folder_tree(files, folders)
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

    def _filter_files_by_folders(
        self,
        files: List[Dict[str, Any]],
        folders: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        if not folders:
            return files

        normalized_folders = [self._normalize_disk_path(folder) for folder in folders if folder]
        folder_matchers = [
            folder.replace("disk:", "").strip("/")
            for folder in normalized_folders
            if folder
        ]
        if not folder_matchers:
            return files

        filtered = []
        skipped_archive = 0
        for file in files:
            path = self._normalize_disk_path(str(file.get("path") or ""))
            path_without_disk = path.replace("disk:", "").strip("/")
            parts = [part for part in path.replace("disk:/", "").split("/") if part]

            if any(path.startswith(prefix) for prefix in ARCHIVE_PATH_PREFIXES):
                skipped_archive += 1
                continue

            for folder in folder_matchers:
                # Match the dashboard logic: tolerate disk:/ prefixes, different
                # path formatting, and nested files under the configured folder.
                if (
                    path_without_disk.startswith(folder)
                    or folder in path_without_disk
                    or folder in parts
                ):
                    filtered.append(file)
                    break

        logger.info(
            f"[Yandex] Filtered files by folders: {len(filtered)}/{len(files)} matched "
            f"(skipped_archive={skipped_archive})."
        )
        return filtered

    async def _augment_files_from_folder_tree(
        self,
        files: List[Dict[str, Any]],
        folders: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        root_folders = []
        for folder in folders or []:
            if not folder:
                continue
            api_path = self._normalize_disk_api_path(str(folder))
            if api_path.startswith("disk:/"):
                root_folders.append(api_path)
            else:
                root_folders.append(f"disk:/{api_path.strip('/')}")

        if not root_folders:
            return files

        existing_paths = {
            self._normalize_disk_path(str(file.get("path") or ""))
            for file in files
            if file.get("path")
        }
        existing_md5s = {
            str(file.get("md5"))
            for file in files
            if file.get("md5")
        }

        extra_files = await self._list_video_files_by_folder_tree(root_folders)
        added = []
        for file in extra_files:
            path_key = self._normalize_disk_path(str(file.get("path") or ""))
            md5 = str(file.get("md5")) if file.get("md5") else ""
            if not path_key or path_key in existing_paths:
                continue
            if md5 and md5 in existing_md5s:
                continue
            existing_paths.add(path_key)
            if md5:
                existing_md5s.add(md5)
            added.append(file)

        if added:
            logger.info(
                f"[Yandex] Folder tree scan added {len(added)} videos missing from global media index "
                f"(tree_total={len(extra_files)})."
            )
            return files + added

        logger.info(f"[Yandex] Folder tree scan found no additional videos (tree_total={len(extra_files)}).")
        return files

    async def _list_video_files_by_folder_tree(
        self,
        root_folders: List[str],
        concurrency: int = 8,
    ) -> List[Dict[str, Any]]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        for folder in root_folders:
            await queue.put(folder)

        headers = {"Authorization": f"OAuth {self.token}"}
        visited = set()
        files: List[Dict[str, Any]] = []
        lock = asyncio.Lock()

        async with httpx.AsyncClient(timeout=120.0) as client:
            async def worker():
                while True:
                    path = await queue.get()
                    try:
                        normalized_path = self._normalize_disk_path(path)
                        async with lock:
                            if normalized_path in visited:
                                continue
                            visited.add(normalized_path)

                        if any(normalized_path.startswith(prefix) for prefix in ARCHIVE_PATH_PREFIXES):
                            continue

                        items = await self._list_resource_items(client, path, headers)
                        for item in items:
                            item_type = item.get("type")
                            item_path = item.get("path")
                            if not item_path:
                                continue
                            if item_type == "dir":
                                await queue.put(str(item_path))
                            elif self._is_video_file_item(item):
                                files.append(self._resource_item_to_file(item))
                    finally:
                        queue.task_done()

            tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
            await queue.join()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"[Yandex] Folder tree scan visited {len(visited)} directories and found {len(files)} videos.")
        return files

    async def _list_resource_items(
        self,
        client: httpx.AsyncClient,
        path: str,
        headers: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        params = {
            "path": path,
            "limit": 10000,
            "fields": "_embedded.items.name,_embedded.items.type,_embedded.items.media_type,"
                      "_embedded.items.path,_embedded.items.md5,_embedded.items.size,_embedded.items.created",
        }
        try:
            resp = await client.get(self.base_url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return (data.get("_embedded") or {}).get("items") or []
        except Exception as e:
            logger.warning(f"[Yandex] Failed to list resource items in {path}: {e}")
            return []

    def _is_video_file_item(self, item: Dict[str, Any]) -> bool:
        if item.get("type") != "file":
            return False
        if item.get("media_type") == "video":
            return True
        name = str(item.get("name") or "").strip().lower()
        if "." not in name:
            return False
        return name.rsplit(".", 1)[-1] in VIDEO_EXTENSIONS

    def _resource_item_to_file(self, item: Dict[str, Any]) -> Dict[str, Any]:
        path = str(item.get("path") or "")
        return {
            "name": str(item.get("name") or path.rsplit("/", 1)[-1]),
            "path": path,
            "url": path,
            "md5": item.get("md5"),
            "size": item.get("size"),
            "created": item.get("created"),
        }

    def _normalize_disk_path(self, path: str) -> str:
        return (
            path.replace("\\", "/")
            .replace("disk;", "disk:")
            .replace("Disk;", "disk:")
            .strip()
            .strip("/")
            .lower()
        )

    def _normalize_disk_api_path(self, path: str) -> str:
        """Normalize user-configured paths without changing case for Yandex resource API."""
        return (
            path.replace("\\", "/")
            .replace("disk;", "disk:")
            .replace("Disk;", "disk:")
            .strip()
            .strip("/")
        )

    async def get_download_link(self, path: str) -> str:
        """Get a temporary download link for a file."""
        async with yadisk.AsyncClient(token=self.token) as client:
            return await client.get_download_link(path)

    async def exists(self, path: str) -> bool:
        """Check whether a Yandex.Disk resource exists."""
        async with yadisk.AsyncClient(token=self.token) as client:
            return await client.exists(path)

    async def delete_file(self, path: str, permanently: bool = True):
        """Delete a file."""
        async with yadisk.AsyncClient(token=self.token) as client:
            await client.remove(path, permanently=permanently)
            print(f"[Yandex] Deleted file: {path}")

    async def move_file(self, source_path: str, dest_folder: str = "disk:/опубликовано"):
        """Move a file to archive folder instead of deleting."""
        import os
        
        async with yadisk.AsyncClient(token=self.token) as client:
            # Ensure destination folder exists
            try:
                await self._ensure_folder(client, dest_folder)
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
            self.schedule_cached_files_refresh()
            return dest_path

    async def _ensure_folder(self, client: yadisk.AsyncClient, folder_path: str):
        normalized = folder_path.replace("\\", "/").strip().strip("/")
        if not normalized.startswith("disk:/"):
            normalized = f"disk:/{normalized}"

        parts = [part for part in normalized.replace("disk:/", "").split("/") if part]
        current = "disk:"
        for part in parts:
            current = f"{current}/{part}"
            if not await client.exists(current):
                await client.mkdir(current)
                logger.info(f"[Yandex] Created folder: {current}")

    async def list_directories(self, path: str, limit: int = 10000) -> List[str]:
        """
        Return direct child directory names for a given Yandex.Disk path.
        Example: path='disk:/ВИДЕО' -> ['Автор 1', 'Автор 2', ...]
        """
        cache_key = (self._normalize_disk_api_path(path), int(limit))
        if cache_key in self._directories_cache:
            return list(self._directories_cache[cache_key])

        async with self._directories_cache_lock:
            if cache_key in self._directories_cache:
                return list(self._directories_cache[cache_key])

            dirs = await self._fetch_directories(path, limit)
            self._directories_cache[cache_key] = list(dirs)
            return dirs

    async def _fetch_directories(self, path: str, limit: int = 10000) -> List[str]:
        url = self.base_url
        params = {
            "path": path,
            "limit": limit,
            "fields": "_embedded.items.name,_embedded.items.type"
        }
        headers = {"Authorization": f"OAuth {self.token}"}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            items = (data.get("_embedded") or {}).get("items") or []
            dirs = sorted(
                [str(i.get("name", "")).strip() for i in items if i.get("type") == "dir" and i.get("name")]
            )
            logger.info(f"[Yandex] Found {len(dirs)} directories in {path}.")
            return dirs
        except Exception as e:
            logger.warning(f"[Yandex] Failed to list directories in {path}: {e}")
            return []

# Singleton-like usage or dependency injection
yandex_service = YandexDiskService()
