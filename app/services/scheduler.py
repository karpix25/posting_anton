import random
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings, LegacyConfig, SocialProfile
from app.models import BrandStats

logger = logging.getLogger(__name__)

class ContentScheduler:
    def __init__(self, config: LegacyConfig, db_session: Optional[AsyncSession] = None):
        self.config = config
        self.db_session = db_session
        self.used_video_md5s: Set[str] = set()

    async def generate_schedule(self, videos: List[Dict[str, Any]], 
                                profiles: List[SocialProfile], 
                                occupied_slots: Dict[str, List[datetime]]) -> List[Dict[str, Any]]:
        # Filter profiles: enabled AND has connected platforms
        active_profiles = [p for p in profiles if p.enabled and p.platforms and len(p.platforms) > 0]
        
        if not active_profiles:
            logger.warning("[Scheduler] No active profiles with connected platforms found!")
            return []
        
        logger.info(f"[Scheduler] Active profiles with platforms: {len(active_profiles)}/{len(profiles)}")
        
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

        start_date = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
        days_to_generate = self.config.daysToGenerate or 7
        logger.info(f"[Scheduler] Generating posts for {days_to_generate} days starting from {start_date.date()}")

        for day_index in range(days_to_generate):
            current_day_start = start_date + timedelta(days=day_index)
            
            # If today, ensuring we don't start in the past
            now = datetime.now()
            if day_index == 0 and current_day_start < now:
                current_day_start = now + timedelta(minutes=10)
            
            current_day_end = current_day_start.replace(hour=23, minute=0, second=0, microsecond=0)
            
            if current_day_start >= current_day_end:
                logger.info(f"[Scheduler] Skipping day {day_index} - already past end time (start={current_day_start}, end={current_day_end})")
                continue

            daily_profiles = active_profiles.copy()
            random.shuffle(daily_profiles)
            
            # Track profile publish counts per day
            profile_counts: Dict[str, Dict[str, int]] = {p.username: {pl: 0 for pl in ["instagram", "tiktok", "youtube"]} for p in active_profiles}

            # Determine max iterations = max of global limits
            # Profile-specific limits are checked individually in the loop
            max_limit = max(
                self.config.limits.instagram, 
                self.config.limits.tiktok, 
                self.config.limits.youtube
            )
            logger.info(f"[Scheduler] Max iterations: {max_limit} (from global limits: IG={self.config.limits.instagram}, TT={self.config.limits.tiktok}, YT={self.config.limits.youtube})")
            
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
                if platform_limit is not None:
                    return platform_limit
                if profile.limit is not None:
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
                    available_brand_videos = [v for v in brand_videos]
                    random.shuffle(available_brand_videos)

                    for i, v in enumerate(available_brand_videos):
                        vid_id = v.get("md5") or v.get("path")
                        if vid_id not in self.used_video_md5s:
                            video_for_slot = v
                            # Remove from the ORIGINAL list to avoid re-picking
                            # We need to find index in original list or just remove by value logic
                            # Since dicts are by ref, we can traverse original and remove
                            
                            # Simplest: remove from theme_brands[selected_brand] by reference
                            if v in theme_brands[selected_brand]:
                                theme_brands[selected_brand].remove(v)
                                
                            self.used_video_md5s.add(vid_id)
                            break
                    
                    if not video_for_slot:
                        continue
                        
                    # Find Time Slot
                    base_time = self.get_random_time_window(current_day_start, current_day_end)
                    candidate_time = self.find_safe_slot(profile_slots[profile.username], base_time, current_day_start, current_day_end)
                    
                    if not candidate_time:
                        continue

                    profile_slots[profile.username].append(candidate_time)

                    # Create Schedule Items for each platform
                    for pl_idx, pl in enumerate(profile.platforms):
                        limit = get_profile_limit(profile, pl)  # Use new platform-specific limits
                        if profile_counts[profile.username].get(pl, 0) < limit:
                            publish_time = candidate_time
                            if pl_idx > 0:
                                delay = random.randint(2, 5)
                                publish_time += timedelta(minutes=delay)
                            
                            schedule.append({
                                "video": video_for_slot,
                                "profile": profile, # Pydantic model
                                "platform": pl,
                                "publish_at": publish_time.isoformat()
                            })
                            profile_counts[profile.username][pl] += 1

        return schedule

    def group_videos_by_theme(self, videos: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        groups = {}
        for v in videos:
            theme = self.extract_theme(v["path"])
            brand = self.extract_brand(v["path"])
            
            if theme not in groups: groups[theme] = {}
            if brand not in groups[theme]: groups[theme][brand] = []
            
            groups[theme][brand].append(v)
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
        try:
            v_idx = -1
            for i, p in enumerate(parts):
                if p.lower() in ["video", "видео"]:
                    v_idx = i
                    break
            
            if v_idx != -1 and v_idx + 3 < len(parts):
                 raw = parts[v_idx + 3].split("*")[0].split("(")[0].strip()
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
