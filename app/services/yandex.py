import yadisk
import asyncio
from typing import List, Dict, Any, Optional
from app.config import settings

class YandexDiskService:
    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.YANDEX_TOKEN
        self.client = yadisk.AsyncClient(token=self.token, timeout=60.0)

    async def check_token(self) -> bool:
        async with self.client:
            return await self.client.check_token()

    async def list_files(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """
        List video files from Yandex Disk using the flat listing endpoint.
        Matches the behavior of the TypeScript `listFiles` method.
        """
        async with self.client:
            # yadisk logic to fetch flat list of files
            # get_files(limit=..., media_type='video')
            # It returns a generator, need to iterate
            
            files = []
            try:
                # We need fields: name, path, md5, size, created
                # yadisk automatically parses many fields, but we can rely on default excessive fields or filter if needed?
                # yadisk doesn't strictly support `fields` param in helper wrapper easily without custom loop or **kwargs?
                # Actually get_files supports **kwargs passed to request.
                
                # Fetching
                print(f"[Yandex] Fetching files (limit={limit})...")
                
                # Using a manual request might be safer if we want exact fields to save bandwidth, 
                # but yadisk wrappers are convenient. Let's try wrapper.
                # get_files returns an async generator for Pagination!
                
                # However, the TypeScript code did NOT paginate manually, it just set a high limit (10000).
                # yadisk `get_files` allows `limit` param.
                
                items_gen = self.client.get_files(
                    limit=limit,
                    media_type='video',
                    fields='items.name,items.path,items.md5,items.size,items.created'
                )
                
                # Iterate the generator
                async for item in items_gen:
                    files.append({
                        "name": item.name,
                        "path": item.path,
                        "url": item.path, # As per TS logic
                        "md5": item.md5,
                        "size": item.size,
                        "created": item.created.isoformat() if item.created else None
                    })
                    
                print(f"[Yandex] Fetched {len(files)} files.")
                
                # Sort by name
                files.sort(key=lambda x: x["name"])
                
                return files

            except yadisk.exceptions.RequestError as e:
                print(f"[Yandex] Error listing files: {e}")
                # Retry logic could be implemented here or in the caller / Celery
                raise e

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
