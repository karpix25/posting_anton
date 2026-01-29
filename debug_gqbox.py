
from app.services.scheduler import ContentScheduler
from app.config import LegacyConfig, ClientConfig

# Mock classes
class MockConfig:
    clients = [
        ClientConfig(name="GQbox", regex="gqbox", prompt="...")
    ]

scheduler = ContentScheduler(MockConfig())

# Test paths
paths = [
    "disk:/ВИДЕО/SomeAuthor/GQbox/file.mp4",
    "disk:/ВИДЕО/SomeAuthor/Category/GQbox/file.mp4",
    "disk:/ВИДЕО/GQbox/file.mp4",
    "disk:/ВИДЕО/SomeAuthor/GQbox/Subfolder/file.mp4" 
]

print("Testing Extraction Logic:")
for p in paths:
    brand = scheduler.extract_brand(p)
    print(f"Path: {p}")
    print(f" -> Extracted Brand: '{brand}'")
    
    # Check if 'has_ai_client' would pass
    from app.services.scheduler import has_ai_client
    has = has_ai_client(MockConfig.clients, brand)
    print(f" -> Has AI Client? {has}")
    print("-" * 20)
