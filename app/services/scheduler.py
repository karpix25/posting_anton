import random
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Set, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings, LegacyConfig, SocialProfile
from app.models import BrandStats

logger = logging.getLogger(__name__)

# Moscow timezone (UTC+3)
MSK = timezone(timedelta(hours=3))
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "mpg", "mpeg"}
ROOT_PRODUCT_GROUP = "_root"

def _normalize_client_key(value: str) -> str:
    return (value or "").lower().replace(" ", "").replace("-", "")


def _compact_match_key(value: str) -> str:
    import re
    return re.sub(r"[\W_]+", "", (value or "").lower())


def has_ai_client(clients: list, brand_name: str, video_path: str = "") -> bool:
    """Check if brand has a matching AI client (by name or regex)."""
    import re
    normalized_brand = _normalize_client_key(brand_name)
    compact_brand = _compact_match_key(brand_name)
    compact_path = _compact_match_key(video_path)
    path_parts = [
        _normalize_client_key(part)
        for part in (video_path or "").replace("\\", "/").split("/")
        if part and part != "disk:"
    ]
    
    # Match with the same broad rules used later by worker.find_ai_client().
    sorted_clients = sorted(clients, key=lambda c: len(c.regex) if c.regex else 0, reverse=True)

    for client in sorted_clients:
        # Method 1: Exact name match (normalized)
        client_normalized = _normalize_client_key(client.name)
        client_compact = _compact_match_key(client.name)
        if client_normalized == normalized_brand:
            return True
        if client_compact and client_compact == compact_brand:
            return True

        # Method 2: Exact folder match anywhere in the Yandex path.
        if client_normalized in path_parts:
            return True
        if client_compact and client_compact in compact_path:
            return True
        
        # Method 3: Regex match against both extracted brand and full path.
        if client.regex:
            regex_compact = _compact_match_key(client.regex)
            if regex_compact and (regex_compact in compact_brand or regex_compact in compact_path):
                return True
            try:
                if re.search(client.regex, brand_name, re.IGNORECASE) or re.search(client.regex, video_path or "", re.IGNORECASE):
                    return True
            except re.error:
                pass  # Invalid regex, skip
    
    return False

class ContentScheduler:
    def __init__(self, config: LegacyConfig, db_session: Optional[AsyncSession] = None):
        self.config = config
        self.db_session = db_session
        self.used_video_md5s: Set[str] = set()

    async def generate_schedule(self, videos: List[Dict[str, Any]],
                                profiles: List[SocialProfile],
                                occupied_slots: Dict[str, List[datetime]],
                                existing_counts: Optional[Dict[str, Dict[str, Dict[str, int]]]] = None,
                                force_limit: Optional[int] = None) -> List[Dict[str, Any]]:
        # Filter profiles: enabled AND has connected platforms
        active_profiles = [p for p in profiles if p.enabled and p.platforms and len(p.platforms) > 0]
        
        if not active_profiles:
            logger.warning("[Scheduler] No active profiles with connected platforms found!")
            return []
        
        logger.info(f"[Scheduler] Active profiles with platforms: {len(active_profiles)}/{len(profiles)}")
        
        schedule = []
        scheduled_profile_counts: Counter[str] = Counter()
        
        # Helper to extract metadata from path
        # Assuming simple extraction for now, mirroring the complex regex logic from TS if needed
        # We'll implement basic extraction here
        
        videos_by_theme = self.group_videos_by_theme(videos)
        logger.info(f"[Scheduler] Videos grouped by {len(videos_by_theme)} themes: {list(videos_by_theme.keys())}")
        for theme, brands in videos_by_theme.items():
            total = sum(len(v) for v in brands.values())
            logger.info(f"  - Theme '{theme}': {total} videos across {len(brands)} brands: {list(brands.keys())[:10]}")  # Show first 10 brands

        profile_theme_counts = Counter(self.normalize_theme(p.theme_key) for p in active_profiles)
        unmatched_profiles = [
            {
                "username": p.username,
                "theme_key": p.theme_key,
                "normalized_theme": self.normalize_theme(p.theme_key),
                "platforms": p.platforms,
            }
            for p in active_profiles
            if self.normalize_theme(p.theme_key) not in videos_by_theme
        ]
        logger.info(f"[Scheduler] Active profile themes requested: {dict(profile_theme_counts.most_common())}")
        if unmatched_profiles:
            logger.warning(
                f"[Scheduler] Profiles with no matching Yandex theme "
                f"({len(unmatched_profiles)}): {unmatched_profiles}"
            )
            logger.warning(f"[Scheduler] Available Yandex themes: {list(videos_by_theme.keys())}")
        
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
            
            # Track profile publish counts per day
            date_key = current_day_start.strftime("%Y-%m-%d")
            day_existing_counts = (existing_counts or {}).get(date_key, {})
            profile_counts: Dict[str, Dict[str, int]] = {}
            for p in active_profiles:
                profile_counts[p.username] = {pl: 0 for pl in ["instagram", "tiktok", "youtube"]}
                existing_for_profile = day_existing_counts.get(p.username, {})
                for pl in ["instagram", "tiktok", "youtube"]:
                    profile_counts[p.username][pl] = int(existing_for_profile.get(pl, 0) or 0)

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
                if force_limit is not None:
                    return force_limit
                if platform_limit is not None:
                    return platform_limit
                if profile.limit is not None:
                    return profile.limit
                return getattr(self.config.limits, platform, 1)

            # Determine max iterations.
            # IMPORTANT: must include profile-level limits, otherwise profile=2 + global=1 yields only 1 pass.
            max_limit = max(
                self.config.limits.instagram,
                self.config.limits.tiktok,
                self.config.limits.youtube,
                1
            )
            for p in active_profiles:
                for pl in p.platforms:
                    p_limit = get_profile_limit(p, pl)
                    if p_limit > max_limit:
                        max_limit = p_limit

            logger.info(
                f"[Scheduler] Max iterations: {max_limit} "
                f"(global: IG={self.config.limits.instagram}, TT={self.config.limits.tiktok}, YT={self.config.limits.youtube}; "
                f"profile overrides included)"
            )
            
            last_brand_used_per_theme: Dict[str, str] = {}
            last_product_used_per_brand: Dict[str, str] = {}

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
                    product_rr_key = f"{canonical_theme}:{selected_brand}"
                    video_for_slot, selected_product = self.select_video_by_product_round_robin(
                        brand_videos,
                        last_product_used_per_brand.get(product_rr_key),
                    )

                    if video_for_slot and selected_product:
                        last_product_used_per_brand[product_rr_key] = selected_product
                    
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
                            scheduled_profile_counts[profile.username] += 1
                            profile_counts[profile.username][pl] += 1

        zero_scheduled_profiles = []
        for profile in active_profiles:
            if scheduled_profile_counts.get(profile.username, 0) > 0:
                continue

            normalized_theme = self.normalize_theme(profile.theme_key)
            theme_brands = videos_by_theme.get(normalized_theme, {})
            zero_scheduled_profiles.append({
                "username": profile.username,
                "theme_key": profile.theme_key,
                "normalized_theme": normalized_theme,
                "platforms": profile.platforms,
                "theme_found": normalized_theme in videos_by_theme,
                "available_brands": list(theme_brands.keys()),
                "available_video_count": sum(len(videos) for videos in theme_brands.values()),
            })

        if zero_scheduled_profiles:
            logger.warning(
                f"[Scheduler] Active profiles that received 0 scheduled posts "
                f"({len(zero_scheduled_profiles)}): {zero_scheduled_profiles}"
            )

        return schedule

    def group_videos_by_theme(self, videos: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        groups = {}
        skipped_brands = set()
        
        for v in videos:
            theme = self.extract_theme(v["path"])
            brand = self.extract_brand(v["path"])
            
            # Skip brands without AI client configured
            if not has_ai_client(self.config.clients, brand, v["path"]):
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
                product_groups = self.group_brand_videos_by_product(vids)
                if len(product_groups) > 1:
                    product_counts = {name: len(group) for name, group in product_groups.items()}
                    logger.info(f"[Scheduler] Product folders for {theme}/{br}: {product_counts}")

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

    def select_video_by_product_round_robin(
        self,
        brand_videos: List[Dict[str, Any]],
        last_product: Optional[str],
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        product_groups = self.group_brand_videos_by_product(brand_videos)
        available_products = [
            product
            for product, videos in product_groups.items()
            if any((v.get("md5") or v.get("path")) not in self.used_video_md5s for v in videos)
        ]

        if not available_products:
            return None, None

        selected_product = self.round_robin(available_products, last_product)
        available_videos = [
            v
            for v in product_groups[selected_product]
            if (v.get("md5") or v.get("path")) not in self.used_video_md5s
        ]
        random.shuffle(available_videos)

        if not available_videos:
            return None, None

        selected_video = available_videos[0]
        if selected_video in brand_videos:
            brand_videos.remove(selected_video)
        self.used_video_md5s.add(selected_video.get("md5") or selected_video.get("path"))

        return selected_video, selected_product

    def group_brand_videos_by_product(
        self,
        brand_videos: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for video in brand_videos:
            product = self.extract_product_group(video.get("path") or "")
            if product not in groups:
                groups[product] = []
            groups[product].append(video)
        return groups

    def extract_product_group(self, path: str) -> str:
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "disk:"]
        try:
            v_idx = -1
            for i, p in enumerate(parts):
                if p.lower() in ["video", "видео"]:
                    v_idx = i
                    break

            # /ВИДЕО/Author/Category/Brand/file.mp4 -> _root
            # /ВИДЕО/Author/Category/Brand/Product/file.mp4 -> Product
            product_idx = v_idx + 4
            if v_idx != -1 and product_idx < len(parts):
                product = parts[product_idx].strip()
                if product and not self.looks_like_video_file(product):
                    return product
        except:
            pass
        return ROOT_PRODUCT_GROUP

    def looks_like_video_file(self, segment: str) -> bool:
        value = (segment or "").strip().lower()
        if "." not in value:
            return False
        ext = value.rsplit(".", 1)[-1]
        return ext in VIDEO_EXTENSIONS

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
            elif len(parts) >= 2:
                 raw = parts[-2].split("(")[0].strip()
                 if not self.looks_like_video_file(raw):
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
                    if re.search(client.regex, path, re.IGNORECASE):
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
