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
                                check_db_duplicates: bool = True) -> List[Dict[str, Any]]:
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
        videos_by_theme = self.group_videos_by_theme(videos)
        
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
                if platform == 'instagram': platform_limit = profile.instagramLimit
                elif platform == 'tiktok': platform_limit = profile.tiktokLimit
                elif platform == 'youtube': platform_limit = profile.youtubeLimit
                else: platform_limit = None
                
                if platform_limit is not None and platform_limit > 0: return platform_limit
                if profile.limit is not None and profile.limit > 0: return profile.limit
                return getattr(self.config.limits, platform, 1)

            # Fixed: Use while loop with safety cap to ensure targets are met
            target_limit = max(global_max, profiles_max)
            max_passes = min(max(target_limit * 3, 50), 100)
            
            logger.info(f"[Scheduler] Day {day_index}: Target {target_limit} posts/profile. Max passes: {max_passes}")

            pass_idx = 0
            while pass_idx < max_passes:
                pass_idx += 1
                
                # Check if we still have work to do
                pending_profiles = 0
                for p in daily_profiles:
                     for pl in p.platforms:
                         if profile_counts[p.username].get(pl, 0) < get_profile_limit(p, pl):
                             pending_profiles += 1
                             break
                
                if pending_profiles == 0:
                     logger.info(f"[Scheduler] All profiles satisfied after {pass_idx} passes.")
                     break

                for profile in daily_profiles:
                    # Check needs
                    needs_post = False
                    for pl in profile.platforms:
                        if profile_counts[profile.username].get(pl, 0) < get_profile_limit(profile, pl):
                            needs_post = True
                            break
                    if not needs_post:
                        continue

                    # Select Video
                    canonical_theme = self.normalize_theme_key(profile.theme_key)
                    theme_brands = videos_by_theme.get(canonical_theme, {})
                    if not theme_brands:
                        continue

                    available_brands = [b for b, v_list in theme_brands.items() if len(v_list) > 0]
                    if not available_brands:
                         continue
                         
                    selected_brand = await self.select_brand_by_quota(canonical_theme, available_brands, last_brand_used_per_theme.get(canonical_theme))
                    last_brand_used_per_theme[canonical_theme] = selected_brand
                    
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
                        continue

                    # Find Time Slot
                    base_time = self.get_random_time_window(current_day_start, current_day_end)
                    candidate_time = self.find_safe_slot(profile_slots[profile.username], base_time, current_day_start, current_day_end)
                    
                    if not candidate_time:
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

                    # Create Schedule Items
                    for pl_idx, pl in enumerate(profile.platforms):
                        if profile_counts[profile.username].get(pl, 0) < get_profile_limit(profile, pl):
                            publish_time = candidate_time
                            if pl_idx > 0:
                                publish_time += timedelta(minutes=random.randint(2, 5))
                            
                            aware_msk = candidate_time.replace(tzinfo=MSK)
                            utc_time = aware_msk.astimezone(timezone.utc)
                            
                            schedule.append({
                                "video": video_for_slot,
                                "profile": profile,
                                "platform": pl,
                                "publish_at": utc_time.isoformat()
                            })
                            profile_counts[profile.username][pl] += 1

        return schedule

    def group_videos_by_theme(self, videos: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        groups = {}
        skipped_brands = set()
        
        for v in videos:
            theme = self.extract_theme(v["path"])
            brand = self.extract_brand(v["path"])
            
            # Skip brands without AI client configured
            if not has_ai_client(self.config.clients, brand):
                if brand not in skipped_brands:
                    # Log ONCE per brand to avoid flooding
                    skipped_brands.add(brand)
                    logger.warning(f"[Scheduler] ⚠️ SKIPPING videos for brand '{brand}' (category '{theme}') - No AI Client configured matching this name/regex!")
                    # Debug: List available clients
                    available_clients = [c.name for c in self.config.clients]
                    logger.debug(f"[Scheduler] Available AI Clients: {available_clients}")
                continue
            
            if theme not in groups: groups[theme] = {}
            if brand not in groups[theme]: groups[theme][brand] = []
            
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

    async def select_brand_by_quota(self, category: str, available_brands: List[str], last_brand: Optional[str]) -> str:
        # Check Quotas from DB
        if not self.db_session:
            return self.round_robin(available_brands, last_brand)
            
        current_month = datetime.now().strftime("%Y-%m")
        # Query DB for stats
        # For performance, we should load all stats for the category once outside the loop?
        # But here we do query per selection. It's fine for batch job.
        
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
            weights[brand] = max(0, quota - published)
            
        # Sort by weight desc
        sorted_brands = sorted(available_brands, key=lambda b: weights.get(b, 0), reverse=True)
        
        if weights.get(sorted_brands[0], 0) > 0:
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
        
        # Fallback to parent of brand if positional video anchor not found
        if not theme_part and len(parts) >= 3:
            theme_part = parts[-3]

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
        
        # 1. Identify the specific "Brand Folder" part based on structure
        brand_part = None
        
        # Strategy A: Anchor by "Video"/"Видео" (User structure: Video/Editor/Category/Brand)
        v_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in ["video", "видео"]:
                v_idx = i
                break
        
        if v_idx != -1 and v_idx + 3 < len(parts):
             # Extract raw folder name, stripping casual comments like "Brand (comment)"
             brand_part = parts[v_idx + 3].split("*")[0].split("(")[0].strip()
             # Sanity check: if it looks like a filename, abort
             if "." in brand_part:
                 brand_part = None

        # Strategy B: Fallback to Parent Folder (parts[-2])
        # Only use if Strategy A failed
        if not brand_part and len(parts) >= 2:
            brand_part = parts[-2]

        if not brand_part:
            return "unknown"

        # 2. Match the identifying part against known Clients
        normalized_part = self.normalize(brand_part)
        
        for client in self.config.clients:
            # Check 1: Exact Name Match
            if self.normalize(client.name) == normalized_part:
                return self.normalize(client.name)
            
            # Check 2: Regex Match (Strictly on the folder name)
            if client.regex:
                import re
                try:
                    if re.search(client.regex, brand_part, re.IGNORECASE):
                        return self.normalize(client.name)
                except:
                    pass
        
        # 3. If no client matched, return the raw normalized part as the brand key
        return normalized_part

    def normalize(self, text: str) -> str:
        return text.lower().replace("ё", "е").replace(" ", "").strip()

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
        for _ in range(15):
             conflict = False
             for s in slots:
                 if abs((s - candidate).total_seconds()) < 45 * 60:
                     conflict = True
                     break
             if not conflict:
                 return candidate
             
             # Retry with offset
             candidate += timedelta(minutes=random.randint(45, 105))
             if candidate > day_end:
                 candidate = self.get_random_time_window(day_start, day_end)
        return None
