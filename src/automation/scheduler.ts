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
        const schedule: ScheduledPost[] = [];
        const videosByTheme = this.groupVideosByTheme(videos);
        const profilePublishCounts: Record<string, { instagram: number; tiktok: number; youtube: number }> = {};
        const profileSlots: Record<string, Date[]> = { ...occupiedSlots }; // Start with occupied slots

        // Initialize counts and slots
        profiles.forEach(p => {
            profilePublishCounts[p.username] = { instagram: 0, tiktok: 0, youtube: 0 };
            if (!profileSlots[p.username]) profileSlots[p.username] = [];
        });

        const startDate = new Date();
        startDate.setHours(8, 0, 0, 0); // Start at 8 AM today (or tomorrow, logic can vary)

        console.log(`[Scheduler] Grouped ${videos.length} videos into themes:`, Object.keys(videosByTheme));

        // Iterate over days
        const days = this.config.daysToGenerate || 7;
        for (let dayIndex = 0; dayIndex < days; dayIndex++) {
            const currentDayStart = new Date(startDate);
            currentDayStart.setDate(startDate.getDate() + dayIndex);

            const currentDayEnd = new Date(currentDayStart);
            currentDayEnd.setHours(23, 0, 0, 0);

            // Shuffle profiles to ensure fairness each day
            const dailyProfiles = this.shuffle([...profiles]);

            // Try to fill slots for each profile
            const maxPassesPerDay = 5;

            for (let pass = 0; pass < maxPassesPerDay; pass++) {
                for (const profile of dailyProfiles) {
                    // Check limits (Global/Batch limits)
                    const currentCounts = profilePublishCounts[profile.username];
                    const needsPost =
                        currentCounts.instagram < this.config.limits.instagram ||
                        currentCounts.tiktok < this.config.limits.tiktok ||
                        currentCounts.youtube < this.config.limits.youtube;

                    if (!needsPost) {
                        // console.log(`[Scheduler] SKIP: ${profile.username} reached limits.`);
                        continue;
                    }

                    // Find matching videos
                    const themeVideos = videosByTheme[profile.theme_key] || [];
                    if (themeVideos.length === 0) {
                        if (dayIndex === 0 && pass === 0) console.log(`[Scheduler] WARN: No videos found for theme '${profile.theme_key}' (Profile: ${profile.username})`);
                        continue;
                    }

                    this.shuffle(themeVideos); // Shuffle again to pick random

                    let videoForSlot: VideoFile | null = null;
                    for (const v of themeVideos) {
                        const videoId = v.md5 || v.path;
                        if (!this.usedVideoMd5s.has(videoId)) {
                            videoForSlot = v;
                            break;
                        }
                    }

                    if (!videoForSlot) {
                        // console.log(`[Scheduler] SKIP: All ${themeVideos.length} videos for '${profile.theme_key}' are already used.`);
                        continue;
                    }

                    const videoId = videoForSlot.md5 || videoForSlot.path;

                    // Schedule
                    const baseTime = this.getRandomTimeInWindow(currentDayStart, currentDayEnd);
                    const candidateTime = this.findSafeSlot(profileSlots[profile.username], baseTime, currentDayStart, currentDayEnd);

                    this.usedVideoMd5s.add(videoId);
                    let posted = false;

                    // Distribute to platforms
                    (['instagram', 'tiktok', 'youtube'] as const).forEach(platform => {
                        if (currentCounts[platform] < this.config.limits[platform]) {
                            schedule.push({
                                video: videoForSlot!,
                                profile,
                                platform,
                                publish_at: candidateTime.toISOString()
                            });
                            currentCounts[platform]++;
                            posted = true;
                            candidateTime.setMinutes(candidateTime.getMinutes() + this.randomInt(2, 5));
                        }
                    });

                    if (posted) {
                        profileSlots[profile.username].push(candidateTime);
                        console.log(`[Scheduler] Scheduled ${videoForSlot.name} for ${profile.username} (${profile.theme_key})`);
                    } else {
                        this.usedVideoMd5s.delete(videoId); // Revert
                    }
                }
            }
        }

        return schedule;
    }

    private groupVideosByTheme(videos: VideoFile[]): Record<string, VideoFile[]> {
        const groups: Record<string, VideoFile[]> = {};
        for (const v of videos) {
            // Logic to extract theme from path (as per n8n helpers)
            const theme = this.extractTheme(v.path);
            if (!groups[theme]) groups[theme] = [];
            groups[theme].push(v);
        }
        return groups;
    }

    private extractTheme(path: string): string {
        // More robust extraction: scan the whole path for known keywords
        const normalizedPath = this.normalize(path);

        const THEME_ALIASES: Record<string, string[]> = {
            smart: ["smart"],
            toplash: ["toplash", "toplashбьюти", "toplashbeauty", "toplashбюти"],
            wb: ["покупкивб", "wb", "wildberries", "pokypkiwb", "pokypki", "покупки", "pokypki-wb"],
            synergetic: ["synergetic", "синергетик"],
            beauty: ["beauty", "бьюти"]
        };

        // Check if any alias exists in the path
        for (const [canonical, aliases] of Object.entries(THEME_ALIASES)) {
            for (const alias of aliases) {
                if (normalizedPath.includes(alias)) {
                    // Found a match!
                    return canonical;
                }
            }
        }

        // Fallback: if no keyword found, try the old parts[3] method but carefully
        const parts = path.split('/');
        // If path is disk:/ВИДЕО/Blogger/Theme/... -> parts[3] is Theme
        if (parts.length >= 4) {
            return this.normalizeTheme(parts[3]);
        }

        return 'unknown';
    }

    private normalizeTheme(str: string): string {
        const raw = this.normalize(str);
        // Aliases from legacy system
        const THEME_ALIASES: Record<string, string[]> = {
            smart: ["smart"],
            toplash: ["toplash", "toplashбьюти", "toplashbeauty", "toplashбюти"],
            wb: ["покупкивб", "wb", "wildberries", "pokypkiwb", "pokypki", "покупки", "pokypki-wb"]
        };

        for (const [canonical, aliases] of Object.entries(THEME_ALIASES)) {
            if (aliases.includes(raw)) return canonical;
        }
        return raw;
    }

    private normalize(str: string): string {
        return str.toLowerCase().replace(/ё/g, "е").replace(/[^a-zа-я0-9]/g, "");
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

    private findSafeSlot(slots: Date[], desired: Date, dayStart: Date, dayEnd: Date): Date {
        // Simplified collision avoidance
        let t = new Date(desired);
        let attempts = 0;
        while (attempts < 10) {
            const conflict = slots.some(s => Math.abs(s.getTime() - t.getTime()) < 45 * 60000); // 45 min gap
            if (!conflict) return t;
            t = new Date(t.getTime() + 60 * 60000); // Add hour
            if (t > dayEnd) t = dayStart; // Wrap around
            attempts++;
        }
        return t;
    }
}
