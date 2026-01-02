import { VideoFile, SocialProfile, ScheduledPost, AutomationConfig } from './types';

export class ContentScheduler {
    private config: AutomationConfig;
    private usedVideoMd5s: Set<string>;

    constructor(config: AutomationConfig, previouslyUsedMd5s: string[]) {
        this.config = config;
        this.usedVideoMd5s = new Set(previouslyUsedMd5s);
    }

    /**
     * Main scheduling function
     */
    public generateSchedule(
        videos: VideoFile[],
        profiles: SocialProfile[],
        occupiedSlots: Record<string, Date[]> = {} // Map of username -> Date[] of occupied slots
    ): ScheduledPost[] {
        const activeProfiles = profiles.filter(p => p.enabled !== false);
        console.log(`[Scheduler] Active profiles details: ${activeProfiles.map(p => `${p.username} (${p.theme_key})`).join(', ')}`);

        const schedule: ScheduledPost[] = [];
        const videosByTheme = this.groupVideosByTheme(videos);
        const profilePublishCounts: Record<string, { instagram: number; tiktok: number; youtube: number }> = {};
        const profileSlots: Record<string, Date[]> = { ...occupiedSlots }; // Start with occupied slots

        // Initialize counts and slots
        // Initialize slots (counts will be reset daily)
        activeProfiles.forEach(p => {
            if (!profileSlots[p.username]) profileSlots[p.username] = [];
        });

        const startDate = new Date();
        startDate.setHours(8, 0, 0, 0); // Start at 8 AM today (or tomorrow, logic can vary)

        console.log(`[Scheduler] Grouped ${videos.length} videos into themes:`, Object.keys(videosByTheme));

        // Iterate over days
        const days = this.config.daysToGenerate || 7;
        for (let dayIndex = 0; dayIndex < days; dayIndex++) {
            // Reset counts for the new day so limits apply PER DAY
            activeProfiles.forEach(p => {
                profilePublishCounts[p.username] = { instagram: 0, tiktok: 0, youtube: 0 };
            });

            const currentDayStart = new Date(startDate);
            currentDayStart.setDate(startDate.getDate() + dayIndex);

            // FIX: If scheduling for today, ensure we don't pick a time in the past
            const now = new Date();
            if (dayIndex === 0 && currentDayStart < now) {
                // If 8 AM is already past, start scheduling from now + 10 mins
                currentDayStart.setTime(now.getTime() + 10 * 60000);
                console.log(`[Scheduler] Adjusted start time for today to ${currentDayStart.toLocaleTimeString()}`);
            }

            const currentDayEnd = new Date(currentDayStart);
            currentDayEnd.setHours(23, 0, 0, 0); // Keep end at 11 PM

            // Safety: if now is > 23:00, skip today
            if (currentDayStart >= currentDayEnd) {
                console.log(`[Scheduler] Skipping day ${dayIndex} (today) as it's too late.`);
                continue;
            }

            // Shuffle profiles to ensure fairness each day
            const dailyProfiles = this.shuffle([...activeProfiles]);

            // Try to fill slots for each profile
            // We iterate enough times to satisfy the highest limit
            // We iterate enough times to satisfy the highest limit
            let maxLimit = Math.max(
                this.config.limits.instagram,
                this.config.limits.tiktok,
                this.config.limits.youtube,
                1
            );
            // Check properly if any profile has a higher override
            activeProfiles.forEach(p => {
                if (p.limit && p.limit > maxLimit) maxLimit = p.limit;
            });

            const maxPassesPerDay = maxLimit;

            // Track last brand used for each theme (for round-robin selection)
            const lastBrandUsed: Record<string, string> = {};

            for (let pass = 0; pass < maxPassesPerDay; pass++) {
                for (const profile of dailyProfiles) {
                    // Check if ANY platform needs posts
                    const currentCounts = profilePublishCounts[profile.username];

                    // Check if we need posts for any of this profile's platforms
                    let needsPost = false;
                    for (const platform of profile.platforms) {
                        const limitForPl = (profile.limit !== undefined && profile.limit !== null)
                            ? profile.limit
                            : (this.config.limits[platform] || 1);

                        if (currentCounts[platform] < limitForPl) {
                            needsPost = true;
                            break;
                        }
                    }

                    if (!needsPost) {
                        continue;
                    }

                    // Normalize profile theme key to match canonical video groups
                    const canonicalProfileTheme = this.normalizeTheme(profile.theme_key);
                    const themeBrands = videosByTheme[canonicalProfileTheme];

                    if (!themeBrands || Object.keys(themeBrands).length === 0) {
                        if (pass === 0) console.log(`[Scheduler] No videos for theme '${profile.theme_key}' (canonical: '${canonicalProfileTheme}') (profile: ${profile.username}). Available themes: ${Object.keys(videosByTheme).join(', ')}`);
                        continue;
                    }

                    // Get available brands (that still have videos)
                    const availableBrands = Object.keys(themeBrands).filter(
                        brand => themeBrands[brand] && themeBrands[brand].length > 0
                    );

                    if (availableBrands.length === 0) {
                        continue;
                    }

                    // Round-robin brand selection
                    const lastBrand = lastBrandUsed[canonicalProfileTheme];
                    let brandIndex = 0;

                    if (lastBrand) {
                        const lastIndex = availableBrands.indexOf(lastBrand);
                        if (lastIndex !== -1) {
                            brandIndex = (lastIndex + 1) % availableBrands.length;
                        }
                    }

                    const selectedBrand = availableBrands[brandIndex];
                    const brandVideos = themeBrands[selectedBrand];

                    // Find an unused video from this brand
                    let videoForSlot: VideoFile | null = null;
                    for (let i = 0; i < brandVideos.length; i++) {
                        const v = brandVideos[i];
                        const videoId = v.md5 || v.path;
                        if (!this.usedVideoMd5s.has(videoId)) {
                            videoForSlot = v;
                            // Remove from brand queue
                            brandVideos.splice(i, 1);
                            break;
                        }
                    }

                    if (!videoForSlot) {
                        continue;
                    }

                    // Update last brand used for this theme
                    lastBrandUsed[canonicalProfileTheme] = selectedBrand;

                    // Determine effective limit for this profile
                    const pLimit = profile.limit !== undefined && profile.limit !== null && profile.limit >= 0
                        ? profile.limit
                        : Math.max(...Object.values(this.config.limits)); // Fallback to max global limit?
                    // Actually, if we use per-platform global limits:
                    // We need to check per platform.
                    // But if profile.limit is set, it overrides ALL platform limits for that profile (e.g. 5 posts total? or per platform?)
                    // User asked "number of videos", which usually implies "Post X videos" (regardless of platform count).
                    // Current logic counts per platform.
                    // Let's assume profile.limit means "Limit per platform" to be consistent.

                    // Update loop to respect per-profile limit
                    // We need to check if ANY platform still needs posts.
                    let activeParams = false;
                    for (const pl of profile.platforms) {
                        const limitForPl = (profile.limit !== undefined && profile.limit !== null)
                            ? profile.limit
                            : (this.config.limits[pl] || 1);

                        if (currentCounts[pl] < limitForPl) {
                            activeParams = true;
                            break;
                        }
                    }
                    if (!activeParams && pass > 0) continue; // Skip if full (but pass 0 might run once)

                    const videoId = videoForSlot.md5 || videoForSlot.path;

                    // Calculate publish time
                    const baseTime = this.getRandomTimeInWindow(currentDayStart, currentDayEnd);
                    const candidateTime = this.findSafeSlot(profileSlots[profile.username], baseTime, currentDayStart, currentDayEnd);

                    if (!candidateTime) {
                        // Could not find safe slot (e.g. day full)
                        console.log(`[Scheduler] Could not find safe slot for ${profile.username} on day ${dayIndex}. Skipping.`);
                        break; // Stop trying for this profile on this day
                    }

                    this.usedVideoMd5s.add(videoId);
                    let posted = false;

                    // Publish the SAME video to ALL platforms this profile is active on
                    // WITH DELAYS between platforms (2-5 minutes)
                    for (let platformIndex = 0; platformIndex < profile.platforms.length; platformIndex++) {
                        const platform = profile.platforms[platformIndex];
                        const limitForPl = profile.limit || this.config.limits[platform] || 1;

                        if (currentCounts[platform] < limitForPl) {
                            // Calculate publish time with delay for non-first platforms
                            let publishTime = new Date(candidateTime);

                            if (platformIndex > 0) {
                                // Add 2-5 minute delay for subsequent platforms
                                const delayMinutes = Math.floor(Math.random() * 4) + 2; // 2-5 minutes
                                publishTime = new Date(publishTime.getTime() + delayMinutes * 60000);
                            }

                            schedule.push({
                                video: videoForSlot,
                                profile,
                                platform,
                                publish_at: publishTime.toISOString()
                            });
                            currentCounts[platform]++;
                            posted = true;
                            console.log(`[Scheduler] Scheduled ${videoForSlot.name} (brand: ${selectedBrand}) for ${profile.username} on ${platform} at ${publishTime.toISOString()}`);
                        }
                    }

                    if (posted) {
                        // CRITICAL FIX: Update occupied slots so subsequent posts in same run respect this one
                        if (!profileSlots[profile.username]) profileSlots[profile.username] = [];
                        profileSlots[profile.username].push(candidateTime);
                    } else {
                        this.usedVideoMd5s.delete(videoId); // Revert
                    }
                }
            }
        }

        return schedule;
    }

    private groupVideosByTheme(videos: VideoFile[]): Record<string, Record<string, VideoFile[]>> {
        const groups: Record<string, Record<string, VideoFile[]>> = {};

        for (const v of videos) {
            const theme = this.extractTheme(v.path);
            const brand = this.extractBrand(v.path);

            // Initialize theme if needed
            if (!groups[theme]) {
                groups[theme] = {};
            }

            // Initialize brand within theme if needed
            if (!groups[theme][brand]) {
                groups[theme][brand] = [];
            }

            groups[theme][brand].push(v);
        }

        return groups;
    }

    private extractTheme(path: string): string {
        // More robust extraction: scan the whole path for known keywords
        const normalizedPath = this.normalize(path);

        const aliasesMap = this.config.themeAliases || {
            smart: ["smart"],
            toplash: ["toplash"],
            wb: ["wb"],
            pokypki: ["pokypki"],
            synergetic: ["synergetic"]
        };

        // Debug log for the first few checks to realize what's happening
        // (Use a simple counter or random check to avoid spam, or just log once)
        if (Math.random() < 0.005) {
            console.log(`[Scheduler] Debug Match: Path='${path}' Norm='${normalizedPath}' AliasesKeys=${Object.keys(aliasesMap).join(',')}`);
        }



        // We must check aliases in a specific order to avoid partial matches
        // e.g. "pokypki-wb" contains "wb", so if we check "wb" first, it matches wrong.
        // We should sort keys such that specific ones come first? 
        // Or simply ensure "pokypki" is checked before "wb".
        // Let's sort entries by alias length (descending) to ensure "pokypki-wb" (len 10) is checked before "wb" (len 2)

        let allEntries: { key: string, alias: string }[] = [];
        for (const [key, list] of Object.entries(aliasesMap)) {
            for (const alias of list) {
                allEntries.push({ key, alias });
            }
        }

        // Sort by length of alias descending
        allEntries.sort((a, b) => b.alias.length - a.alias.length);

        // Structural extraction: .../VIDEO/Name/Category/...
        // We look for "video" or "видео" segment.
        const parts = path.split('/').filter(p => p.length > 0 && p !== 'disk:');

        let categoryCandidate = '';

        const videoIndex = parts.findIndex(p => {
            const lower = p.toLowerCase();
            return lower === 'video' || lower === 'видео';
        });

        if (videoIndex !== -1 && videoIndex + 2 < parts.length) {
            // Found VIDEO, skip Name, take Category
            categoryCandidate = parts[videoIndex + 2];
        } else if (parts.length >= 2) {
            // Fallback: Parent folder
            categoryCandidate = parts[parts.length - 2];
        }

        if (categoryCandidate) {
            const normCandidate = this.normalize(categoryCandidate);
            // Check aliases
            for (const [key, list] of Object.entries(aliasesMap)) {
                // Normalize list items too
                for (const alias of list) {
                    if (normCandidate.includes(this.normalize(alias))) {
                        return key;
                    }
                }
            }
            // If no alias, return the candidate itself (so it appears in dashboard)
            return normCandidate;
        }

        return 'unknown';
    }

    private normalizeTheme(str: string): string {
        const raw = this.normalize(str);
        // Use config aliases first
        const aliasesMap = this.config.themeAliases || {
            smart: ["smart"],
            toplash: ["toplash", "toplashбьюти", "toplashbeauty", "toplashбюти"],
            wb: ["покупкивб", "wb", "wildberries", "pokypkiwb", "pokypki", "покупки", "pokypki-wb"]
        };

        for (const [canonical, aliases] of Object.entries(aliasesMap)) {
            // Check if raw matches any alias (normalized)
            if (aliases.some(a => this.normalize(a) === raw)) return canonical;
            // Also check if raw is the key itself
            if (this.normalize(canonical) === raw) return canonical;
        }
        return raw;
    }

    private normalize(str: string): string {
        return str.toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]/g, "");
    }

    /**
     * Extract brand from video path
     * Structure: /ВИДЕО/Author/Category/Brand/file.mp4
     *                0      1      2       3      4
     */
    private extractBrand(path: string): string {
        const parts = path.split('/').filter(p => p.length > 0 && p !== 'disk:');

        const videoIndex = parts.findIndex(p => {
            const lower = p.toLowerCase();
            return lower === 'video' || lower === 'видео';
        });

        if (videoIndex !== -1 && videoIndex + 3 < parts.length) {
            // Found VIDEO, skip Author, skip Category, take Brand
            const brandFolder = parts[videoIndex + 3];
            // Remove asterisk and parentheses: "GQbox*" → "gqbox", "Brand (test)" → "brand"
            const cleaned = brandFolder.split('*')[0]
                .replace(/\(.*?\)/g, ' ')
                .trim();
            return this.normalize(cleaned);
        }

        return 'unknown';
    }

    private shuffle<T>(array: T[]): T[] {
        for (let i = array.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [array[i], array[j]] = [array[j], array[i]];
        }
        return array;
    }

    private randomInt(min: number, max: number): number {
        return Math.floor(Math.random() * (max - min + 1)) + min;
    }

    private getRandomTimeInWindow(start: Date, end: Date): Date {
        const t = new Date(start.getTime() + Math.random() * (end.getTime() - start.getTime()));
        return t;
    }

    private findSafeSlot(slots: Date[], desired: Date, dayStart: Date, dayEnd: Date): Date | null {
        // Simplified collision avoidance
        let t = new Date(desired);
        let attempts = 0;

        // If day window is too small (e.g. < 10 mins), just fail
        if (dayEnd.getTime() - dayStart.getTime() < 10 * 60000) return null;

        while (attempts < 15) {
            // Check conflict with 45 min gap
            const conflict = slots && slots.some(s => Math.abs(s.getTime() - t.getTime()) < 45 * 60000);
            if (!conflict) return t;

            // Try different offsets
            t = new Date(t.getTime() + (45 + Math.random() * 60) * 60000); // Add 45-105 mins
            if (t > dayEnd) {
                // Wrap around to start + random offset
                t = new Date(dayStart.getTime() + Math.random() * (dayEnd.getTime() - dayStart.getTime()));
            }
            attempts++;
        }
        return null; // Failed to find slot
    }
}
