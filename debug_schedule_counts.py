
import asyncio
import os
from collections import Counter
from app.services.platforms import upload_post_client
from app.config import settings

async def main():
    try:
        print("Fetching scheduled posts from API...")
        # Ensure API key is set (it should be from .env now)
        if not settings.UPLOAD_POST_API_KEY:
            print("❌ UPLOAD_POST_API_KEY is missing in settings!")
            return

        scheduled_posts = await upload_post_client.get_scheduled_posts()
        print(f"Total scheduled posts result type: {type(scheduled_posts)}")
        if isinstance(scheduled_posts, list) and len(scheduled_posts) > 0:
             print(f"First item type: {type(scheduled_posts[0])}")
             print(f"First item: {scheduled_posts[0]}")
             
        if isinstance(scheduled_posts, dict):
             print(f"It is a dict! Keys: {scheduled_posts.keys()}")
             # Maybe the list is under a key?
             if 'items' in scheduled_posts: scheduled_posts = scheduled_posts['items']
             elif 'data' in scheduled_posts: scheduled_posts = scheduled_posts['data']
             elif 'schedule' in scheduled_posts: scheduled_posts = scheduled_posts['schedule']
             else:
                 print("Cannot find list in dict.")
                 scheduled_posts = []

        print(f"Total scheduled posts count: {len(scheduled_posts)}")
        
        # Aggregate by username key
        counts = Counter()
        for post in scheduled_posts:
            if isinstance(post, str):
                print(f"Unexpected string item: {post}")
                continue
            uname = post.get('profile_username')
            if uname:
                counts[uname] += 1
                
        print("\n--- Scheduled Posts Count per Profile ---")
        if not counts:
            print("No posts found.")
        
        # Sort by count
        sorted_counts = counts.most_common()
        
        # Show stats
        print(f"Profiles with scheduled posts: {len(sorted_counts)}")
        print(f"Max posts for a profile: {sorted_counts[0][1] if sorted_counts else 0}")
        print(f"Min posts for a profile: {sorted_counts[-1][1] if sorted_counts else 0}")
        
        print("\n--- Detailed Counts (Top 50) ---")
        for uname, count in sorted_counts[:50]:
            print(f"{uname}: {count}")
            
        # Check specific profiles user mentioned if any (e.g. general check)
        
        # Also let's check current date distribution to see if they are mostly for today or scattered
        date_counts = Counter()
        for post in scheduled_posts:
            d = post.get('scheduled_date', '').split('T')[0]
            date_counts[d] += 1
            
        print("\n--- Date Distribution ---")
        for d, c in date_counts.most_common():
            print(f"{d}: {c}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
