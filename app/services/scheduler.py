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
                # logger.debug(f"[Scheduler] Skipping {p.username}: No valid platforms configured")
                continue
                
            # Update profile with cleaned platforms (temporary for this run)
            p.platforms = valid_platforms
            active_profiles.append(p)
        
        if not active_profiles:
            logger.warning(f"[Scheduler] No active profiles found! (Total: {len(profiles)}, Disabled: {skipped_reasons['disabled']}, No Platforms: {skipped_reasons['no_platforms']})")
            return []
        
        logger.info(f"[Scheduler] Active profiles: {len(active_profiles)} (Skipped: {skipped_reasons['disabled']} disabled, {skipped_reasons['no_platforms']} no platforms)")
        
        schedule = []
        
        # Helper to extract metadata from path
        # Assuming simple extraction for now, mirroring the complex regex logic from TS if needed
        # We'll implement basic extraction here
        
        videos_by_theme = self.group_videos_by_theme(videos)
        logger.info(f"[Scheduler] Videos grouped by {len(videos_by_theme)} themes: {list(videos_by_theme.keys())}")
        for theme, brands in videos_by_theme.items():
            total = sum(len(v) for v in brands.values())
            logger.info(f"  - Theme '{theme}': {total} videos across {len(brands)} brands: {list(brands.keys())[:10]}")  # Show first 10 brands
        
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
        start_date = now_msk.replace(hour=start_hour, minute=0, second=0, microsecond=0, tzinfo=None)  # Make naive for compatibility
        days_to_generate = self.config.daysToGenerate or 7
        logger.info(f"[Scheduler] Generating posts for {days_to_generate} days starting from {start_date.date()} (Window: {start_hour}:00 - {end_hour}:00)")

        for day_index in range(days_to_generate):
            current_day_start = start_date + timedelta(days=day_index)
            
            # If today, ensuring we don't start in the past
            now_msk_naive = now_msk.replace(tzinfo=None)  # Make naive for comparison
            if day_index == 0 and current_day_start < now_msk_naive:
                current_day_start = now_msk_naive + timedelta(minutes=10)
            
            current_day_end = current_day_start.replace(hour=end_hour, minute=0, second=0, microsecond=0)
            
            if current_day_start >= current_day_end:
                logger.info(f"[Scheduler] Skipping day {day_index} - already past end time (start={current_day_start}, end={current_day_end})")
                continue

            daily_profiles = active_profiles.copy()
            random.shuffle(daily_profiles)
            
            # Track profile publish counts per day (Pre-fill with existing counts if available)
            profile_counts: Dict[str, Dict[str, int]] = {p.username: {pl: 0 for pl in ["instagram", "tiktok", "youtube"]} for p in active_profiles}
            
            date_key = current_day_start.strftime("%Y-%m-%d")
            if existing_counts and date_key in existing_counts:
                logger.info(f"[Scheduler] Found existing posts for {date_key}, syncing counters...")
                for p_user, p_counts in existing_counts[date_key].items():
                    if p_user in profile_counts:
                         for pl, count in p_counts.items():
                             if pl in profile_counts[p_user]:
                                 profile_counts[p_user][pl] = count
                
                # Debug log for first profile
                first_p = list(existing_counts[date_key].keys())[0] if existing_counts[date_key] else None
                if first_p and first_p in profile_counts:
                     logger.info(f"   (Debug) {first_p} starts with: {profile_counts[first_p]}")

            # Determine max iterations
            # We must consider both global limits AND profile-specific overrides
            # Otherwise, if global=10 but profile=20, we stop at 10.
            
            global_max = max(
                self.config.limits.instagram, 
                self.config.limits.tiktok, 
                self.config.limits.youtube
            )
            
            profiles_max = 0
            for p in active_profiles:
                # Check all platform limits for this profile
                p_limits = [
                    p.instagramLimit or 0,
                    p.tiktokLimit or 0,
                    p.youtubeLimit or 0
                ]
                if p.limit: # Backwards comaptibility
                    p_limits.append(p.limit)
                    
                if p_limits:
                    profiles_max = max(profiles_max, max(p_limits))
            
            # The loop must run enough times to satisfy the HIGHEST requirement
            max_limit = max(global_max, profiles_max)
            
            # Safety cap just in case (e.g. 50)
            max_limit = min(max_limit, 50)
            
            logger.info(f"[Scheduler] Max iterations: {max_limit} (Global max: {global_max}, Profiles max: {profiles_max})")
            
            last_brand_used_per_theme: Dict[str, str] = {}
            
            def get_profile_limit(profile: SocialProfile, platform: str) -> int:
                """Get limit for profile+platform with fallback to global config"""
                # Check platform-specific limit
                platform_limit = None
                if platform == 'instagram':
                    platform_limit = profile.instagramLimit
                elif platform == 'tiktok':
                    platform_limit = profile.tiktokLimit
                elif platform == 'youtube':
                    platform_limit = profile.youtubeLimit
                
                # Fallback chain:
                # 1. Platform-specific limit (if set)
                # 2. Deprecated profile.limit (backwards compat)
                # 3. Global config limit
                if platform_limit is not None and platform_limit > 0:
                     return platform_limit
                if profile.limit is not None and profile.limit > 0:
                     return profile.limit
                return getattr(self.config.limits, platform, 1)

            for pass_idx in range(max_limit):
                for profile in daily_profiles:
                    # Check needs
                    needs_post = False
                    for pl in profile.platforms:
                        limit = get_profile_limit(profile, pl)
                        if profile_counts[profile.username].get(pl, 0) < limit:
                            needs_post = True
                            break
                    if not needs_post:
                        continue

                    # Select Video
                    canonical_theme = self.normalize_theme(profile.theme_key)
                    theme_brands = videos_by_theme.get(canonical_theme, {})
                    
                    if not theme_brands:
                        continue

                    available_brands = [b for b, v_list in theme_brands.items() if len(v_list) > 0]
                    if not available_brands:
                         continue
                         
                    # Brand Selection
                    last_brand = last_brand_used_per_theme.get(canonical_theme)
                    selected_brand = await self.select_brand_by_quota(canonical_theme, available_brands, last_brand)
                    last_brand_used_per_theme[canonical_theme] = selected_brand
                    
                    brand_videos = theme_brands[selected_brand]
                    video_for_slot = None
                    
                    # USER REQ: Randomize file selection within brand
                    # brand_videos contains videos from ALL authors for this brand (Aggregated)
                    # We shuffle to mix authors and avoid "clumping" of similar videos from one author
                    available_brand_videos = [v for v in brand_videos]
                    random.shuffle(available_brand_videos)

                    for i, v in enumerate(available_brand_videos):
                        vid_id = v.get("md5") or v.get("path")
                        
                        # Check availability based on config
                        is_available = False
                        if self.config.allowVideoReuse:
                            # Reuse allowed: check if this profile has used it
                            if (vid_id, profile.username) not in self.used_video_profile_pairs:
                                is_available = True
                        else:
                            # Strict global uniqueness
                            if vid_id not in self.used_video_md5s:
                                is_available = True

                        if is_available:
                            video_for_slot = v
                            # DO NOT remove from original list here - wait until slot is confirmed!
                            # This fixes the "video burning" bug.
                            break
                    
                    if not video_for_slot:
                        continue

                    # ---------------------------------------------------------
                    # CRITICAL FIX: "Scheduler Amnesia" Prevention
                    # Check database history if we have EVER posted this video to this user
                    # ---------------------------------------------------------
                    if check_db_duplicates and self.db_session:
                         vid_path = video_for_slot.get("path")
                         # Check strict dupes (same video path + same profile)
                         # We allow re-posting if status was 'failed', but usually cleaner to just skip
                         # if we want strict uniqueness.
                         # Let's check status != 'failed' just in case we want to retry failed ones?
                         # Or just check existence. User wants NO duplicates. So existence is safer.
                         
                         from app.models import PostingHistory
                         stmt_check = select(PostingHistory.id).where(
                             PostingHistory.profile_username == profile.username,
                             PostingHistory.video_path == vid_path,
                             PostingHistory.status != 'failed' # Retry failed allowed? Maybe no.
                             # If user says "10 times 1 video", those were likely 'queued' or 'processing'.
                             # So we must exclude those too. effectively exclude everything except maybe very old failed?
                             # Simplest: Exclude everything.
                         ).limit(1)
                         
                         res_check = await self.db_session.execute(stmt_check)
                         if res_check.scalar_one_or_none():
                             # logger.info(f"[Scheduler] Skipping duplicate: {vid_path} for {profile.username}")
                             continue

                    # Find Time Slot
                    base_time = self.get_random_time_window(current_day_start, current_day_end)
                    candidate_time = self.find_safe_slot(profile_slots[profile.username], base_time, current_day_start, current_day_end)
                    
                    if not candidate_time:
                         # Slot conflict - video is NOT burned, just skipped for this iteration
                         # It remains in available_brand_videos for other profiles (or next pass)
                         continue

                    # SUCCESS - Now mark as used
                    vid_id = video_for_slot.get("md5") or video_for_slot.get("path")
                    
                    if self.config.allowVideoReuse:
                        self.used_video_profile_pairs.add((vid_id, profile.username))
                    else:
                        self.used_video_md5s.add(vid_id)
                        # Remove from the ORIGINAL list to prevent re-picking by others (optimization)
                        if video_for_slot in theme_brands[selected_brand]:
                            theme_brands[selected_brand].remove(video_for_slot)

                    profile_slots[profile.username].append(candidate_time)

                    # Create Schedule Items for each platform
                    for pl_idx, pl in enumerate(profile.platforms):
                        limit = get_profile_limit(profile, pl)  # Use new platform-specific limits
                        if profile_counts[profile.username].get(pl, 0) < limit:
                            publish_time = candidate_time
                            if pl_idx > 0:
                                delay = random.randint(2, 5)
                                publish_time += timedelta(minutes=delay)
                            
                            # Convert candidate_time (naive MSK) to aware MSK, then to UTC
                            aware_msk = candidate_time.replace(tzinfo=MSK)
                            utc_time = aware_msk.astimezone(timezone.utc)
                            
                            schedule.append({
                                "video": video_for_slot,
                                "profile": profile, # Pydantic model
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
                skipped_brands.add(brand)
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
        # Simplified port of extractTheme from main.ts/scheduler.ts
        # Logic: /ВИДЕО/Author/Category/Brand/file.mp4
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
        
        # Strategy 1: Search for known theme aliases in path
        aliases = self.config.themeAliases or {}
        for canonical, list_ in aliases.items():
            # Check canonical first
            norm_canonical = self.normalize(canonical)
            for p in parts:
                if self.normalize(p) == norm_canonical:
                    return canonical
            
            # Check aliases
            for alias in list_:
                norm_alias = self.normalize(alias)
                for p in parts:
                    if self.normalize(p) == norm_alias:
                        return canonical

        # Strategy 2: Positional
        try:
            # Find index of "video"
            v_idx = -1
            for i, p in enumerate(parts):
                if p.lower() in ["video", "видео"]:
                    v_idx = i
                    break
            
            if v_idx != -1 and v_idx + 2 < len(parts):
                 raw = parts[v_idx + 2].split("(")[0].strip() # remove comments like (old)
                 return self.normalize_theme(raw)
        except:
            pass
        return "unknown"

    def extract_brand(self, path: str) -> str:
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
        
        # Strategy 1: Search for known client names/regex matches in the path
        # This is more robust than fixed indices
        for client in self.config.clients:
            normalized_client = self.normalize(client.name)
            
            # Check for exact name match in path parts
            for p in parts:
                if self.normalize(p) == normalized_client:
                    return normalized_client
                    
            # Check regex if available
            import re
            if client.regex:
                try:
                    # USER REQUEST: Check regex against individual PARTS (folders), not the whole path string
                    # This ensures we match a specific folder name, not a substring spanning multiple folders
                    for p in parts:
                        if re.search(client.regex, p, re.IGNORECASE):
                            return normalized_client
                except:
                    pass

        # Strategy 2: Fallback to positional (Index 3 after 'video')
        # /Video/Author/Category/Brand/file
        try:
            v_idx = -1
            for i, p in enumerate(parts):
                if p.lower() in ["video", "видео"]:
                    v_idx = i
                    break
            
            if v_idx != -1 and v_idx + 3 < len(parts):
                 raw = parts[v_idx + 3].split("*")[0].split("(")[0].strip()
                 # If the extracted part looks like a filename (has dot), abort positional
                 if "." in raw:
                     return "unknown"
                 return self.normalize(raw)
        except:
             pass
        return "unknown"

    def normalize(self, text: str) -> str:
        return text.lower().replace("ё", "е").replace(" ", "").strip()

    def normalize_theme(self, text: str) -> str:
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
