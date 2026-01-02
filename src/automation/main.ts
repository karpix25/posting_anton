
import { YandexDiskClient } from './yandex';
import { DatabaseService } from './db';
import { ContentScheduler } from './scheduler';
import { ContentGenerator } from './content_generator';
import { PlatformManager } from './platforms';
import { AutomationConfig, ScheduledPost } from './types';
import { StatsManager } from './stats';
import * as fs from 'fs';
import * as path from 'path';

async function main() {
    // Load Config
    // Load Config
    // const configPath = path.join(__dirname, '../../config.json');
    const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, '../../data');

    // Ensure data dir exists
    if (!fs.existsSync(DATA_DIR)) {
        fs.mkdirSync(DATA_DIR, { recursive: true });
    }

    const configPath = path.join(DATA_DIR, 'config.json');

    if (!fs.existsSync(configPath)) {
        console.error(`Config file not found at ${configPath}!`);
        // Fallback or exit? If server created it, it should exist.
        // But if running standalone CLI, maybe not.
        process.exit(1);
    }
    const config: AutomationConfig = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

    // DB Init
    const fullDbUrl = 'postgres://admin:admin@tools_postgres:5432/postgres?sslmode=disable';
    const dbUrl = process.env.DATABASE_URL || fullDbUrl;
    const db = new DatabaseService(dbUrl);
    await db.init();

    // Inject Env Vars
    config.yandexToken = process.env.YANDEX_TOKEN || '';

    // Override limits if FORCE_LIMITS is set (Test Mode)
    if (process.env.FORCE_LIMITS) {
        const limit = parseInt(process.env.FORCE_LIMITS, 10);
        console.log(`[Main] Test Mode Active: Overriding all limits to ${limit}`);
        config.limits = {
            instagram: limit,
            tiktok: limit,
            youtube: limit
        };
        config.daysToGenerate = 1; // Only generate for today
    } else {
        // Default
        config.daysToGenerate = config.daysToGenerate || 1; // Default to 1 day if not set
    }

    // Debug: Log custom limits
    const profilesWithLimits = config.profiles.filter(p => p.limit !== undefined && p.limit !== null);
    if (profilesWithLimits.length > 0) {
        console.log(`[Main] Found ${profilesWithLimits.length} profiles with custom limits:`);
        profilesWithLimits.forEach(p => console.log(`  - ${p.username}: ${p.limit}`));
    } else {
        console.log('[Main] No profiles have custom limits set (using global defaults).');
    }

    const usedHashesPath = path.join(DATA_DIR, 'used_hashes.json');
    let usedHashes: string[] = [];
    if (fs.existsSync(usedHashesPath)) {
        usedHashes = JSON.parse(fs.readFileSync(usedHashesPath, 'utf-8'));
    }

    const scheduler = new ContentScheduler(config, usedHashes);
    // Pass config to generator for dynamic prompts
    const generator = new ContentGenerator(process.env.OPENAI_API_KEY || '', config);
    const yandex = new YandexDiskClient(config.yandexToken);
    const platformManager = new PlatformManager(yandex); // Pass yandex client for download URLs
    const statsManager = new StatsManager(DATA_DIR);

    // Auto-Sync Profiles from API
    try {
        if (process.env.UPLOAD_POST_API_KEY) {
            console.log('Syncing profiles from API...');
            const apiProfiles = await platformManager.getProfiles();
            if (apiProfiles && apiProfiles.length > 0) {
                let addedCount = 0;
                // Merge logic similar to UI
                apiProfiles.forEach((apiProfile: any) => {
                    // Check existing based on username AND platform to avoid duplicates on multi-platform
                    // Actually config structure is simple: { username, platform, theme_key }
                    // API returns user + social_accounts map.

                    // We simplistically add generic "instagram" if not exists, user can adjust? 
                    // Or better, we trust the sync logic. 
                    // Let's just match by username for now to match UI logic I wrote earlier.
                    const exists = config.profiles.find(p => p.username === apiProfile.username);
                    if (!exists) {
                        // Default to instagram if new
                        // Auto-detect theme from username
                        let theme = apiProfile.theme_key || '';
                        if (!theme && apiProfile.username) {
                            const name = apiProfile.username.toLowerCase();
                            const aliasesMap = config.themeAliases || {};
                            // Try to match against config aliases first
                            for (const [canonical, aliases] of Object.entries(aliasesMap)) {
                                if (aliases.some(alias => name.includes(alias))) {
                                    theme = canonical;
                                    break;
                                }
                            }
                        }

                        const detectedPlatforms = Object.entries(apiProfile.social_accounts || {})
                            .filter(([_, val]) => !!val) // Filter out empty credentials
                            .map(([key]) => key as 'instagram' | 'tiktok' | 'youtube');

                        config.profiles.push({
                            username: apiProfile.username,
                            theme_key: theme || apiProfile.username.toLowerCase(),
                            platforms: detectedPlatforms.length > 0 ? detectedPlatforms : ['instagram'],
                            enabled: true, // New profiles enabled by default
                            last_posted: {}
                        });
                        addedCount++;
                    } else {
                        // UPDATE existing profile: update platforms, preserve user edits
                        const detectedPlatforms = Object.entries(apiProfile.social_accounts || {})
                            .filter(([_, val]) => !!val) // Filter out empty credentials
                            .map(([key]) => key as 'instagram' | 'tiktok' | 'youtube');

                        if (JSON.stringify(exists.platforms) !== JSON.stringify(detectedPlatforms)) {
                            exists.platforms = detectedPlatforms;
                            addedCount++; // Count as change to trigger save
                        }

                        // Self-heal theme if missing
                        if (!exists.theme_key) {
                            let theme = '';
                            if (apiProfile.username) {
                                const name = apiProfile.username.toLowerCase();
                                const aliasesMap = config.themeAliases || {};

                                // Try to match against config aliases
                                for (const [canonical, aliases] of Object.entries(aliasesMap)) {
                                    if (aliases.some(alias => name.includes(alias))) {
                                        theme = canonical;
                                        break;
                                    }
                                }
                            }
                            if (theme) {
                                console.log(`[Main] Heal: Auto-detected theme '${theme}' for existing profile '${exists.username}'`);
                                exists.theme_key = theme;
                                addedCount++; // Count as update/change to trigger save
                            }
                        }
                        // DON'T touch: enabled (preserve user's deactivation)
                    }
                });

                if (addedCount > 0) {
                    console.log(`Synced ${addedCount} new profiles.`);
                    // Save updated config back to disk so UI sees it
                    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
                } else {
                    console.log('Profiles up to date.');
                }
            }
        }
    } catch (e) {
        console.warn('Failed to auto-sync profiles:', e);
    }


    console.log(`1. Listing videos from concurrently: ${config.yandexFolders.join(', ')}...`);
    // Parallelize folder scanning
    const folderPromises = config.yandexFolders.map(async (folder) => {
        try {
            return await yandex.listFiles(folder);
        } catch (e) {
            console.error(`Failed to list folder ${folder}:`, e);
            return [];
        }
    });

    const videosResults = await Promise.all(folderPromises);
    const allVideos = videosResults.flat();
    console.log(`Found ${allVideos.length} videos.`);

    console.log('2. Generating schedule...');
    // Fetch history for collision check
    let occupiedSlots: Record<string, Date[]> = {};
    try {
        console.log('Fetching upload history to avoid collisions...');
        const history = await platformManager.getHistory();
        history.forEach((item: any) => {
            if (item.upload_timestamp && item.profile_username) {
                const date = new Date(item.upload_timestamp);
                if (!occupiedSlots[item.profile_username]) {
                    occupiedSlots[item.profile_username] = [];
                }
                occupiedSlots[item.profile_username].push(date);
            }
        });
        console.log('Occupied slots loaded.');
    } catch (e) {
        console.warn('Failed to load history, proceeding without collision check:', e);
    }

    // Use profiles from config
    const schedule = scheduler.generateSchedule(allVideos, config.profiles, occupiedSlots);
    console.log(`Scheduled ${schedule.length} posts total (across ${config.daysToGenerate} days).`);

    // Filter for posts due "now" or "today"
    // Since this script is a "Runner", it should probably only run tasks for the current execution window.
    // If the user manually runs it, then we likely want to catch up on anything scheduled for "today".
    // "Today" means the calendar day of execution? 
    // Yes. If it's 23:59, we run posts for 23:59. If 00:01, we run posts for the NEW day.

    // We only process posts that are SCHEDULED for SAME CALENDAR DAY as `now`.
    const now = new Date();

    const immediatePosts = schedule.filter(p => {
        const scheduledTime = new Date(p.publish_at);

        // Strict Calendar Day check
        const isSameDay = scheduledTime.getDate() === now.getDate() &&
            scheduledTime.getMonth() === now.getMonth() &&
            scheduledTime.getFullYear() === now.getFullYear();

        // Also allow missed past posts if they are recent (e.g. earlier today)
        // But NOT future days.
        // Wait, if scheduledTime is yesterday and we missed it? 
        // Logic: if diff < 24h AND time < now? 
        // Let's stick to "Is Same Day" to avoid confusion. Better to skip old posts than spam.
        // Or if the script runs daily, it should cover today.

        return isSameDay;
    });

    console.log(`Processing ${immediatePosts.length} posts scheduled for TODAY (${now.toLocaleDateString()}).`);

    if (process.argv.includes('--dry-run')) {
        console.log('Dry run completed. Schedule sample:', JSON.stringify(immediatePosts.slice(0, 3), null, 2));
        return;
    }

    console.log('3. Processing posts...');

    // Group posts by video path
    const postsByVideo = new Map<string, ScheduledPost[]>();
    for (const post of immediatePosts) {
        const key = post.video.path;
        if (!postsByVideo.has(key)) postsByVideo.set(key, []);
        postsByVideo.get(key)!.push(post);
    }

    console.log(`Processing ${postsByVideo.size} unique videos across platforms...`);

    // Helper to extract author from path (folder after 'ВИДЕО')
    function getAuthorFromPath(path: string): string {
        const normalized = path.replace(/\\/g, '/');
        const parts = normalized.split('/');
        const idx = parts.findIndex(p => p.toLowerCase() === 'видео' || p.toLowerCase() === 'video');
        if (idx !== -1 && idx + 1 < parts.length) {
            return parts[idx + 1];
        }
        return '';
    }

    // Process videos concurrently (limit concurrency to avoid overload)
    const MAX_CONCURRENT_VIDEOS = 2; // Process 2 videos at a time
    const videoEntries = Array.from(postsByVideo.entries());

    // Chunk array helper
    const chunks = [];
    for (let i = 0; i < videoEntries.length; i += MAX_CONCURRENT_VIDEOS) {
        chunks.push(videoEntries.slice(i, i + MAX_CONCURRENT_VIDEOS));
    }

    for (const chunk of chunks) {
        await Promise.all(chunk.map(async ([videoPath, posts]) => {
            let allSuccess = true;
            const videoName = posts[0].video.name;
            const authorName = getAuthorFromPath(videoPath);

            console.log(`\n--- Processing Video: ${videoName} (Author: ${authorName || 'Unknown'}) ---`);

            // 1. Generate Caption (Once per video)
            let baseCaption = '';
            let baseTitle = '';

            if (config.clients && config.clients.length > 0) {
                try {
                    console.log(`[Main] Generating caption for ${videoName}...`);
                    // We use the first post's platform for generation context, but use generic logic usually
                    const rawText = await generator.generateCaption(videoPath, posts[0].platform, authorName);

                    // Parse if needed (assumes YouTube format logic applies globally or handled per platform below)
                    // Actually, let's keep it simple: 
                    baseCaption = rawText.trim();
                } catch (e) {
                    console.error(`[Main] Failed to generate caption for ${videoName}:`, e);
                    baseCaption = `${videoName} #shorts #video`;
                }
            } else {
                baseCaption = `${videoName} #shorts #video`;
                baseTitle = videoName;
            }

            // 2. Publish to all platforms in parallel
            const publishPromises = posts.map(async (post) => {
                try {
                    console.log(`[${post.profile.username}] Publishing to ${post.platform}...`);

                    // Apply caption logic specific to platform
                    if (post.platform === 'youtube') {
                        const parts = baseCaption.split('$$$');
                        if (parts.length > 1) {
                            post.title = parts[0].trim();
                            post.caption = parts[1].trim();
                        } else {
                            post.caption = baseCaption;
                            post.title = baseCaption.substring(0, 50) + '...';
                        }
                    } else {
                        post.caption = baseCaption;
                        post.title = '';
                    }

                    await platformManager.publishPost(post);
                    console.log(`✅ Published to ${post.platform}`);

                    // Log success and stats
                    await db.logPost(post, 'success');
                    statsManager.incrementPublished(post.platform);
                    return true;
                } catch (error: any) {
                    const msg = error.response?.data?.message || error.message;
                    console.error(`[Main] Failed to publish to ${post.platform} for ${post.profile.username}: ${msg}`);
                    await db.logPost(post, 'failed', msg);
                    return false;
                }
            });

            const results = await Promise.all(publishPromises);
            allSuccess = results.every(res => res === true);

            // 3. Cleanup logic
            if (allSuccess) {
                console.log(`[Cleanup] All platforms published successfully. Deleting ${videoName} from source...`);
                try {
                    await yandex.deleteFile(videoPath);
                    const hash = posts[0].video.md5 || posts[0].video.path;
                    usedHashes.push(hash);
                    // Append synchronously to be safe or use lock? 
                    // Main loop is sequential on chunks, so usedHashes access is safe-ish if we don't write file concurrency
                    // But we are in a Promise.all chunk.
                    // Let's just write file at the very end or use sync read/write.
                    // For safety in concurrency, we might want to write immediately but strictly.
                    // Or just push to memory and write once? 
                    // Better to write immediately if process crashes.
                    // Ensure usedHashes update is atomic-ish.

                    // Note: fs.writeFileSync is synchronous blocking, so it's safe.
                    fs.writeFileSync(usedHashesPath, JSON.stringify(usedHashes));
                    console.log(`🗑️ Deleted and hash saved.`);
                    statsManager.incrementDeleted();
                } catch (e) {
                    console.error(`[Cleanup] Failed to delete file:`, e);
                }
            } else {
                console.warn(`[Cleanup] Skipping deletion for ${videoName} because some posts failed.`);
            }
        }));
    }
    // At end of script
    await db.close();
}

if (require.main === module) {
    main().catch(console.error);
}
