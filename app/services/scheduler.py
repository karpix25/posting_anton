import random
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Set, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings, LegacyConfig, SocialProfile
from app.models import BrandStats

logger = logging.getLogger(__name__)

# Moscow timezone (UTC+3)
MSK = timezone(timedelta(hours=3))

from app.utils import extract_theme, extract_brand, extract_brand_with_regex, normalize_theme_key

def has_ai_client(clients: list, brand_name: str) -> bool:
    """Check if brand has a matching AI client (by name or regex)."""
    import re
    normalized_brand = brand_name.lower().replace(" ", "").replace("-", "")
    
    for client in clients:
        # Method 1: Exact name match (normalized)
        client_normalized = client.name.lower().replace(" ", "").replace("-", "")
        if client_normalized == normalized_brand:
            return True
        
        # Method 2: Regex match
        if client.regex:
            try:
                if re.search(client.regex, brand_name, re.IGNORECASE):
                    return True
            except re.error:
                pass  # Invalid regex, skip
    
    return False

class ContentScheduler:
    def __init__(self, config: LegacyConfig, db_session: Optional[AsyncSession] = None):
        self.config = config
        self.db_session = db_session
        self.used_video_md5s: Set[str] = set()
        self.used_video_profile_pairs: Set[tuple] = set()

    async def generate_schedule(self, videos: List[Dict[str, Any]], 
                                profiles: List[SocialProfile], 
                                occupied_slots: Dict[str, List[datetime]],
                                existing_counts: Optional[Dict[str, Dict[str, Dict[str, int]]]] = None,
                                check_db_duplicates: bool = True,
                                force_limit: Optional[int] = None) -> List[Dict[str, Any]]:
        # 1. Filter active profiles (Enabled + Has at least one valid platform)
        active_profiles = []
        skipped_reasons = {"disabled": 0, "no_platforms": 0}
        
        for p in profiles:
            if not p.enabled:
                skipped_reasons["disabled"] += 1
                continue
            

                
            # Filter empty strings or nulls from platforms
            valid_platforms = [pl for pl in (p.platforms or []) if pl and pl.strip()]
            
            if not valid_platforms:
                skipped_reasons["no_platforms"] += 1
                continue
                
            # Update profile with cleaned platforms
            p.platforms = valid_platforms
            active_profiles.append(p)
        
        if not active_profiles:
            logger.warning(f"[Scheduler] No active profiles found!")
            return []
        
        # ---------------------------------------------------------
        # OPTIMIZATION: Pre-load History into Memory
        # ---------------------------------------------------------
        posted_history_set = set() # Set[(username, path)]
        if check_db_duplicates and self.db_session:
            from app.models import PostingHistory
            logger.info(f"[Scheduler] Pre-loading history for {len(active_profiles)} profiles...")
            
            usernames = [p.username for p in active_profiles]
            stmt = select(PostingHistory.profile_username, PostingHistory.video_path).where(
                PostingHistory.profile_username.in_(usernames),
                PostingHistory.status != 'failed'
            )
            res = await self.db_session.execute(stmt)
            for row in res.all():
                posted_history_set.add((row[0], row[1]))
            
            logger.info(f"[Scheduler] Loaded {len(posted_history_set)} history records into memory.")
        # ---------------------------------------------------------

        logger.info(f"[Scheduler] Active profiles: {len(active_profiles)} (Skipped: {skipped_reasons['disabled']} disabled)")
        
        schedule = []
        
        # Compile Regexes for Brand Extraction
        import re
        client_regexes = []
        if hasattr(self.config, "clients") and self.config.clients:
            for c in self.config.clients:
                if c.regex:
                    try:
                         client_regexes.append((c.name, re.compile(c.regex, re.IGNORECASE)))
                    except re.error:
                         pass

        videos_by_theme = self.group_videos_by_theme(videos, client_regexes)
        
        profile_slots: Dict[str, List[datetime]] = occupied_slots.copy()
        for p in active_profiles:
            if p.username not in profile_slots:
                profile_slots[p.username] = []

        # Get scheduling window from config
        start_hour = 8
        end_hour = 23
        if self.config.schedule:
            start_hour = self.config.schedule.start_hour
            end_hour = self.config.schedule.end_hour
            
        now_msk = datetime.now(MSK)
        start_date = now_msk.replace(hour=start_hour, minute=0, second=0, microsecond=0, tzinfo=None)
        days_to_generate = self.config.daysToGenerate or 1
        
        logger.info(f"[Scheduler] Window: {start_hour}:00 - {end_hour}:00, Days: {days_to_generate}")

        for day_index in range(days_to_generate):
            current_day_start = start_date + timedelta(days=day_index)
            now_msk_naive = now_msk.replace(tzinfo=None)
            if day_index == 0 and current_day_start < now_msk_naive:
                current_day_start = now_msk_naive + timedelta(minutes=10)
            
            current_day_end = current_day_start.replace(hour=end_hour, minute=0, second=0, microsecond=0)
            if current_day_start >= current_day_end:
                continue

            daily_profiles = active_profiles.copy()
            random.shuffle(daily_profiles)
            
            profile_counts: Dict[str, Dict[str, int]] = {p.username: {pl: 0 for pl in ["instagram", "tiktok", "youtube"]} for p in active_profiles}
            
            date_key = current_day_start.strftime("%Y-%m-%d")
            if existing_counts and date_key in existing_counts:
                for p_user, p_counts in existing_counts[date_key].items():
                    if p_user in profile_counts:
                         for pl, count in p_counts.items():
                             if pl in profile_counts[p_user]:
                                 profile_counts[p_user][pl] = count

            # Determine max iterations
            global_max = max(self.config.limits.instagram, self.config.limits.tiktok, self.config.limits.youtube)
            profiles_max = 0
            for p in active_profiles:
                p_limits = [p.instagramLimit or 0, p.tiktokLimit or 0, p.youtubeLimit or 0]
                if p.limit: p_limits.append(p.limit)
                if p_limits: profiles_max = max(profiles_max, max(p_limits))
            
            max_limit = min(max(global_max, profiles_max), 50)
            logger.info(f"[Scheduler] Day {day_index}: Iterating {max_limit} passes for {len(daily_profiles)} profiles")
            
            last_brand_used_per_theme: Dict[str, str] = {}
            
            def get_profile_limit(profile: SocialProfile, platform: str) -> int:
                if force_limit is not None:
                    return force_limit
                    
                if platform == 'instagram': platform_limit = profile.instagramLimit
                elif platform == 'tiktok': platform_limit = profile.tiktokLimit
                elif platform == 'youtube': platform_limit = profile.youtubeLimit
                else: platform_limit = None
                
                # Allow 0 to override global limit (0 = disabled for this profile)
                if platform_limit is not None: return platform_limit
                
                # Check legacy 'limit' field (also allow 0)
                if profile.limit is not None: return profile.limit
                
                # Fallback to global limit
                return getattr(self.config.limits, platform, 1)

            # Track planned counts dynamically for this run
            planned_counts = {} # brand -> count

            # Fixed: Process profiles SEQUENTIALLY (fill one profile's quota, then move to next)
            # This avoids round-robin overhead and ensures we don't skip profiles just because pass count is exhausted.
            for profile in daily_profiles:
                # 1. Determine Quota for this profile
                needed_posts = {}
                total_needed = 0
                
                for pl in profile.platforms:
                    limit = get_profile_limit(profile, pl)
                    current_count = profile_counts[profile.username].get(pl, 0)
                    if current_count < limit:
                        needed = limit - current_count
                        needed_posts[pl] = needed
                        total_needed += needed
                
                if total_needed == 0:
                    continue
                    
                # 2. Try to fill quota
                # We loop until we satisfy needs or run out of content/slots for this profile
                attempts = 0
                max_profile_attempts = total_needed * 5 # Safety break
                
                while total_needed > 0 and attempts < max_profile_attempts:
                    attempts += 1
                    
                    # Select platform that needs posts
                    target_pl = None
                    for pl, needed in needed_posts.items():
                        if needed > 0:
                            target_pl = pl
                            break
                    
                    if not target_pl:
                         break # Should not happen if total_needed > 0

                    # Select Video
                    canonical_theme = self.normalize_theme_key(profile.theme_key)
                    theme_brands = videos_by_theme.get(canonical_theme, {})
                    if not theme_brands:
                        break # No videos for this theme
                    
                    available_brands = [b for b, v_list in theme_brands.items() if len(v_list) > 0]
                    if not available_brands:
                         break # No brands available
                         
                    selected_brand = await self.select_brand_by_quota(
                        canonical_theme, 
                        available_brands, 
                        last_brand_used_per_theme.get(canonical_theme),
                        planned_counts
                    )
                    last_brand_used_per_theme[canonical_theme] = selected_brand
                    planned_counts[selected_brand] = planned_counts.get(selected_brand, 0) + 1
                    
                    brand_videos = theme_brands[selected_brand]
                    video_for_slot = None
                    
                    # USER REQ: Randomize and avoid clumps
                    available_brand_videos = [v for v in brand_videos]
                    random.shuffle(available_brand_videos)

                    for v in available_brand_videos:
                        vid_id = v.get("md5") or v.get("path")
                        vid_path = v.get("path")
                        
                        # 1. Check uniqueness (MD5/Path)
                        is_unique = False
                        if self.config.allowVideoReuse:
                            if (vid_id, profile.username) not in self.used_video_profile_pairs:
                                is_unique = True
                        else:
                            if vid_id not in self.used_video_md5s:
                                is_unique = True
                        
                        if not is_unique:
                            continue

                        # 2. Check Database History (NOW O(1) from Memory)
                        if check_db_duplicates:
                            if (profile.username, vid_path) in posted_history_set:
                                continue # NEXT video for this profile

                        video_for_slot = v
                        break
                    
                    if not video_for_slot:
                        # If we couldn't find a video for this brand, maybe try next iteration (which will pick another brand)
                        # But we should be careful not to infinite loop.
                        # For now, just continue loop, attempts will break us out if we are stuck.
                        continue

                    # Find Time Slot
                    base_time = self.get_random_time_window(current_day_start, current_day_end)
                    candidate_time = self.find_safe_slot(profile_slots[profile.username], base_time, current_day_start, current_day_end)
                    
                    if not candidate_time:
                         # Slot conflict, retry loop
                         continue

                    # SUCCESS - Mark as used
                    vid_id = video_for_slot.get("md5") or video_for_slot.get("path")
                    if self.config.allowVideoReuse:
                        self.used_video_profile_pairs.add((vid_id, profile.username))
                    else:
                        self.used_video_md5s.add(vid_id)
                        if video_for_slot in theme_brands[selected_brand]:
                            theme_brands[selected_brand].remove(video_for_slot)

                    profile_slots[profile.username].append(candidate_time)

                    # Create Schedule Items - JUST FOR ONE PLATFORM or ALL?
                    # Original logic: added for ALL platforms if needed.
                    # Current logic: We are iterating needed_posts.
                    # Requirement: Usually we post same video to all needed platforms at once.
                    
                    added_platforms = []
                    # Add for MAIN target platform
                    publish_time = candidate_time
                    
                    # Also try to add for OTHER platforms if they need posts too
                    # This simulates "Post same video to Reels, TikTok, Shorts simultanously"
                    platforms_to_post = [target_pl]
                    for other_pl in needed_posts:
                        if other_pl != target_pl and needed_posts[other_pl] > 0:
                            platforms_to_post.append(other_pl)
                            
                    base_aware_msk = candidate_time.replace(tzinfo=MSK)
                    
                    for i, pl in enumerate(platforms_to_post):
                         # Jitter for other platforms
                         pl_time = base_aware_msk
                         if i > 0:
                             pl_time += timedelta(minutes=random.randint(2, 5))
                         
                         utc_time = pl_time.astimezone(timezone.utc)
                         
                         schedule.append({
                            "video": video_for_slot,
                            "profile": profile,
                            "platform": pl,
                            "publish_at": utc_time.isoformat()
                         })
                         
                         profile_counts[profile.username][pl] += 1
                         needed_posts[pl] -= 1
                         total_needed -= 1
                         added_platforms.append(pl)
                    
                    # If we successfully added posts, we continue to next iteration of while loop
                    # until quota is 0.

        return schedule

            
    def group_videos_by_theme(self, videos: List[Dict[str, Any]], client_regexes: List[Any]) -> Dict[str, Dict[str, List[Any]]]:
        groups = {}
        skipped_brands = set()

        for v in videos:
            path = v.get("path")
            if not path:
                continue

            theme = self.extract_theme(path)
            brand = self.extract_brand(path)
            
            # STRICT MODE: Only allow brands that exist in AI Clients config
            try:
                # Ensure clients list exists
                clients_list = getattr(self.config, "clients", []) or []
                if not has_ai_client(clients_list, brand):
                    skipped_brands.add(brand)
                    continue
            except Exception as e:
                # Log error but don't crash
                logger.error(f"[Scheduler] Filtering Error for brand '{brand}': {e}")
                # Fallback: Allow if error? Or Skip? 
                # Better to skip to avoid spam if broken.
                skipped_brands.add(brand)
                continue
            
            if theme not in groups:
                groups[theme] = {}
            if brand not in groups[theme]:
                groups[theme][brand] = []

            groups[theme][brand].append(v)
        
        # Debug: Log author distribution per brand
        for theme, brands in groups.items():
            for br, vids in brands.items():
                authors = set()
                for v in vids:
                    # Extract author (index 1 after video)
                    # /ВИДЕО/Author/Category/Brand/file.mp4
                    parts = [p for p in v["path"].replace("\\", "/").split("/") if p and p != "disk:"]
                    try:
                        v_idx = -1
                        for i, p in enumerate(parts):
                            if p.lower() in ["video", "видео"]:
                                v_idx = i
                                break
                        if v_idx != -1 and v_idx + 1 < len(parts):
                            authors.add(parts[v_idx + 1])
                    except:
                         pass
                logger.info(f"[Scheduler] Theme '{theme}' / Brand '{br}': {len(vids)} videos from {len(authors)} authors: {list(authors)}")

        if skipped_brands:
            logger.info(f"[Scheduler] Skipped {len(skipped_brands)} brands without AI client: {list(skipped_brands)[:10]}...")
        
        return groups

    async def select_brand_by_quota(self, category: str, available_brands: List[str], last_brand: Optional[str], planned_counts: Dict[str, int]) -> str:
        # Check Quotas from DB
        if not self.db_session:
            return self.round_robin(available_brands, last_brand)
            
        current_month = datetime.now().strftime("%Y-%m")
        
        result = await self.db_session.execute(
            select(BrandStats).where(
                BrandStats.category == category,
                BrandStats.month == current_month
            )
        )
        stats_rows = result.scalars().all()
        stats_map = {s.brand: s for s in stats_rows}
        
        quotas = self.config.brandQuotas.get(category, {})
        
        weights = {}
        for brand in available_brands:
            quota = quotas.get(brand, 0)
            published = stats_map[brand].published_count if brand in stats_map else 0
            
            # Key fix: subtract what we have ALREADY planned in this run
            already_planned = planned_counts.get(brand, 0)
            
            weights[brand] = max(0, quota - published - already_planned)
            
        # Sort by weight desc (Highest need first)
        sorted_brands = sorted(available_brands, key=lambda b: weights.get(b, 0), reverse=True)
        
        # If we have a clear winner with need > 0
        if weights.get(sorted_brands[0], 0) > 0:
            # If top 2 have same weight, try to rotate if possible to avoid clumps
            if len(sorted_brands) > 1 and weights[sorted_brands[0]] == weights[sorted_brands[1]]:
                 if last_brand == sorted_brands[0]:
                     return sorted_brands[1]
            return sorted_brands[0]
            
        return self.round_robin(available_brands, last_brand)

    def round_robin(self, brands: List[str], last: Optional[str]) -> str:
        if not last or last not in brands:
            return brands[0]
        idx = brands.index(last)
        return brands[(idx + 1) % len(brands)]

    def extract_theme(self, path: str) -> str:
        # User structure: Video / Editor / Category / Brand
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
        
        # 1. Identify "Category Folder" by position
        theme_part = None
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break
        
        if v_idx != -1 and v_idx + 2 < len(parts):
             theme_part = parts[v_idx + 2].split("(")[0].strip()
        


        if not theme_part:
            return "unknown"

        # 2. Normalize and check Aliases strictly on this folder name
        raw_normalized = self.normalize(theme_part)
        aliases = self.config.themeAliases or {}
        
        for canonical, list_ in aliases.items():
            if raw_normalized == self.normalize(canonical): 
                return canonical
            for a in list_:
                if self.normalize(a) == raw_normalized: 
                    return canonical
        
        return self.normalize_theme_key(theme_part)


    def extract_brand(self, path: str) -> str:
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
        
        # 1. Anchor by "Video"/"Видео" folder
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break
        
        # If anchor found, we only look at specific positions relative to it
        # Structure A: Video/Author/Category/Brand/... (Brand is at v_idx + 3)
        # Structure B: Video/Author/Brand/...          (Brand is at v_idx + 2)
        
        candidate_parts = []
        if v_idx != -1:
            # Check 3rd folder first (most specific)
            if v_idx + 3 < len(parts):
                candidate_parts.append(parts[v_idx + 3])
            # Check 2nd folder next (fallback)
            if v_idx + 2 < len(parts):
                candidate_parts.append(parts[v_idx + 2])
        


        # 2. Match candidates against known Clients
        # We prioritize the structure-based candidates
        
        for candidate in candidate_parts:
            # Clean candidate
            clean_candidate = candidate.split("*")[0].split("(")[0].strip()
            if "." in clean_candidate: # Skip files
                continue
                
            normalized_candidate = self.normalize(clean_candidate)
            
            for client in self.config.clients:
                # Check 1: Exact Name Match
                if self.normalize(client.name) == normalized_candidate:
                    return self.normalize(client.name)
                
                # Check 2: Regex Match
                if client.regex:
                    import re
                    try:
                        if re.search(client.regex, clean_candidate, re.IGNORECASE):
                            return self.normalize(client.name)
                    except:
                        pass
        
        # 3. If no client matched, return the most likely brand folder (prefer 3rd level, then 2nd)
        # warning: this might return "Category" name if no brand matched, 
        # but that's better than returning a subfolder file name.
        if candidate_parts:
             return self.normalize(candidate_parts[0].split("*")[0].split("(")[0].strip())
             
        return "unknown"

    def normalize(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return str(text).lower().replace("ё", "е").replace(" ", "").strip()

    def normalize_theme_key(self, text: str) -> str:
        raw = self.normalize(text)
        aliases = self.config.themeAliases or {}
        for canonical, list_ in aliases.items():
            if raw == self.normalize(canonical): return canonical
            for a in list_:
                if self.normalize(a) == raw: return canonical
        return raw

    def get_random_time_window(self, start: datetime, end: datetime) -> datetime:
        total_seconds = int((end - start).total_seconds())
        if total_seconds <= 0: return start
        random_seconds = random.randint(0, total_seconds)
        return start + timedelta(seconds=random_seconds)

    def find_safe_slot(self, slots: List[datetime], desired: datetime, day_start: datetime, day_end: datetime) -> Optional[datetime]:
        candidate = desired
        # Use configured interval or default to 45 mins
        min_gap_seconds = (self.config.minIntervalMinutes or 45) * 60
        
        for _ in range(15):
             conflict = False
             for s in slots:
                 if abs((s - candidate).total_seconds()) < min_gap_seconds:
                     conflict = True
                     break
             if not conflict:
                 return candidate
             
             # Retry with offset (at least min_interval, up to min_interval + 60 mins)
             min_minutes = self.config.minIntervalMinutes or 45
             candidate += timedelta(minutes=random.randint(min_minutes, min_minutes + 60))
             
             if candidate > day_end:
                 candidate = self.get_random_time_window(day_start, day_end)
        return None
