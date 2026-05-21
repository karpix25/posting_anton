import json
import urllib.request
import urllib.error
import sys
import os

# Configuration
DEFAULT_API_URL = "http://posting.focusmarket.su"
DEFAULT_LOCAL_CONFIG_PATH = os.path.join("data", "config.json")

def restore_clients():
    local_config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOCAL_CONFIG_PATH

    print(f"Reading local config from {local_config_path}...")
    try:
        with open(local_config_path, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
            local_clients = local_data.get('clients', [])
            print(f"Found {len(local_clients)} clients in local config.")
    except Exception as e:
        print(f"Error reading local config: {e}")
        return

    if not local_clients:
        print("No clients found to restore.")
        return

    # Prompt for URL
    base_url = input(f"Enter Dashboard URL (default: {DEFAULT_API_URL}): ").strip()
    if not base_url:
        base_url = DEFAULT_API_URL
    
    # Remove trailing slash
    base_url = base_url.rstrip('/')
    api_url = f"{base_url}/api/config"

    print(f"\nTarget API: {api_url}")
    print("Fetching current config...")
    
    try:
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req) as response:
            current_config = json.loads(response.read().decode('utf-8'))
            print("Current config fetched successfully.")
    except urllib.error.URLError as e:
        print(f"❌ Error connecting to API: {e}")
        print("Please check the URL and ensure the server is running.")
        return

    # Merge clients
    print(f"Overwriting {len(current_config.get('clients', []))} remote clients with {len(local_clients)} local clients...")
    current_config['clients'] = local_clients
    
    # Send back
    print("Saving updated config to API...")
    try:
        data = json.dumps(current_config).encode('utf-8')
        req = urllib.request.Request(api_url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req) as response:
            print("✅ Success! Clients restored to database.")
    except urllib.error.URLError as e:
        print(f"❌ Error saving config: {e}")

if __name__ == "__main__":
    restore_clients()
