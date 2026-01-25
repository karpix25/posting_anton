import asyncio
import json
import logging
import httpx

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HISTORY_API_URL = 'https://api.upload-post.com/api/uploadposts/history'

class SimpleUploadPostClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {'Authorization': f'Apikey {api_key}'}

    async def get_history(self, limit: int = 200):
        async with httpx.AsyncClient() as client:
            try:
                print(f"Requesting history from {HISTORY_API_URL}...")
                response = await client.get(HISTORY_API_URL, params={'limit': limit}, headers=self.headers)
                if response.status_code != 200:
                    print(f"Error: API returned {response.status_code}: {response.text}")
                    return []
                    
                data = response.json()
                return data.get('history', [])
            except Exception as e:
                print(f"Exception fetching history: {e}")
                return []

async def check_errors():
    # 1. Load Config
    api_key = ""
    try:
        with open("config.json", "r") as f:
             data = json.load(f)
             # Try to find the key. In config.py it maps to settings.UPLOAD_POST_API_KEY
             # Note: config.json structure usually matches LegacyConfig, but sometimes keys are at root or in env.
             # Let's check where it might be. If not in json, we might need .env
             pass
    except:
        pass
        
    # If not in config.json, try .env or known location. 
    # Actually, config.py says `env_file = ".env"`. 
    # Let's try to read .env file manually.
    try:
        with open(".env", "r") as f:
            for line in f:
                if line.startswith("UPLOAD_POST_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
    except:
        print("Could not read .env file.")

    if not api_key:
        print("API Key not found. Please ensure .env exists with UPLOAD_POST_API_KEY.")
        return

    print(f"Using API Key: {api_key[:5]}...")
    
    client = SimpleUploadPostClient(api_key)
    history = await client.get_history(limit=500) # Fetch more to see patterns
    
    print(f"Fetched {len(history)} items.")
    
    # Filter for errors
    failed_items = [
        h for h in history 
        if h.get('status') == 'error' or 
           h.get('status') == 'failed' or 
           (h.get('status') == 'completed' and not h.get('url')) # sometimes completed but no URL?
    ]
    
    # We mainly care about explicit failures
    explicit_failures = [h for h in history if h.get('status') in ['error', 'failed']]
    
    print(f"Found {len(explicit_failures)} explicit failures in last 500 items.")
    
    # Analyze errors
    error_counts = {}
    for item in explicit_failures:
        msg = item.get('error') or item.get('message') or "Unknown Error"
        # Simplify error message (remove unique IDs if present)
        # e.g. "File timeout" vs "File timeout 123"
        msg_short = msg[:150] 
        error_counts[msg_short] = error_counts.get(msg_short, 0) + 1
        
    print("\n=== TOP ERRORS ===")
    for msg, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"Count: {count}")
        print(f"Error: {msg}")
        print("-" * 30)

if __name__ == "__main__":
    asyncio.run(check_errors())
