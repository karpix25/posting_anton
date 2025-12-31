import { YandexDiskClient } from './yandex';
import { ContentScheduler } from './scheduler';
import { ContentGenerator } from './content_generator';
import { PlatformManager } from './platforms';
import { AutomationConfig, ScheduledPost } from './types';
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
        config.daysToGenerate = 7; // Default
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
    const platformManager = new PlatformManager();

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

                        config.profiles.push({
                            username: apiProfile.username,
                            platform: 'instagram',
                            theme_key: theme
                        });
                        addedCount++;
                    } else {
                        // Profile exists, but check if theme is missing and try to self-heal
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


    console.log(`1. Listing videos from: ${config.yandexFolders.join(', ')}...`);
    // Flatten multiple folders if needed, or just take the first one for now as per scheduler logic
    // The original n8n code took specific paths. We'll iterate.
    let allVideos: any[] = [];
    for (const folder of config.yandexFolders) {
        try {
            const videos = await yandex.listFiles(folder);
            allVideos = allVideos.concat(videos);
        } catch (e) {
            console.error(`Failed to list folder ${folder}:`, e);
            // Don't crash entire process if one folder fails
        }
    }
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
                // Only care about future or recent collisions? 
                // If we schedule for today/tomorrow, we care about those days.
                // Let's just add all valid dates, scheduler handles window checking.
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
    console.log(`Scheduled ${schedule.length} posts.`);

    if (process.argv.includes('--dry-run')) {
        console.log('Dry run completed. Schedule sample:', JSON.stringify(schedule.slice(0, 3), null, 2));
        return;
    }

    console.log('3. Processing posts...');

    // Group posts by video path to handle "1 video -> multiple platforms -> delete" logic
    const postsByVideo = new Map<string, ScheduledPost[]>();
    for (const post of schedule) {
        const key = post.video.path;
        if (!postsByVideo.has(key)) postsByVideo.set(key, []);
        postsByVideo.get(key)!.push(post);
    }

    console.log(`Processing ${postsByVideo.size} unique videos across platforms...`);

    for (const [videoPath, posts] of postsByVideo) {
        let allSuccess = true;
        const videoName = posts[0].video.name;
        console.log(`\n--- Processing Video: ${videoName} ---`);

        // Generate caption once per video (optimization) or per platform?
        // User prompts might be platform specific? 
        // Current content_generator takes platform argument. 
        // But usually the prompt is the same "text for Reels". 
        // Let's keep it per-post to be safe with existing logic, or optimize if needed.
        // Actually, prompts in config are per "Client" (Folder), not Platform. 
        // The prompt text usually says "generate text". 
        // Let's generate once and reuse? 
        // The prompt says "Output only 1 text". 
        // If we reuse, it's consistent. If we regenerate, it might vary slightly.
        // Let's regenerate for now to match n8n logic which likely ran parallel branches or sequential nodes.

        for (const post of posts) {
            try {
                console.log(`[${post.profile.username}] Publishing to ${post.platform}...`);

                if (config.clients && config.clients.length > 0) {
                    console.log(`[Main] Generating caption for ${post.video.name} (Profile: ${post.profile.username})...`);
                    const rawText = await generator.generateCaption(post.video.path, post.platform, post.profile.username);

                    if (post.platform === 'youtube') {
                        // Parse Title $$$ Caption format
                        const parts = rawText.split('$$$');
                        if (parts.length > 1) {
                            post.title = parts[0].trim();
                            post.caption = parts[1].trim();
                        } else {
                            post.caption = rawText.trim();
                            post.title = post.caption.substring(0, 50) + '...';
                        }
                    } else {
                        // Instagram / TikTok: Raw text is the caption
                        post.caption = rawText.trim();
                        post.title = ''; // No separate title needed
                    }
                } else {
                    post.caption = `${post.video.name} #shorts #video`;
                    post.title = post.video.name;
                }

                try {
                    const result = await platformManager.publishPost(post);
                    // process response
                } catch (error: any) {
                    // If 400 (Bad Request), it might be "No TikTok account", etc.
                    // We should just log and continue, not crash.
                    const msg = error.response?.data?.message || error.message;
                    console.error(`[Main] Failed to publish to ${post.platform} for ${post.profile.username}: ${msg}`);
                    continue; // Skip this post
                }
                console.log(`✅ Published to ${post.platform}`);

            } catch (error) {
                console.error(`❌ Failed to process ${post.platform}:`, error);
                allSuccess = false;
            }
        }

        if (allSuccess) {
            console.log(`[Cleanup] All platforms published successfully. Deleting ${videoName} from source...`);
            try {
                await yandex.deleteFile(videoPath);

                // Mark as used (redundant if deleted? but good for history)
                // Actually if deleted, we can't accidentally pick it again from Yandex.
                // But keeping hash ensures if it's re-uploaded, we know?
                // Logic:
                const hash = posts[0].video.md5 || posts[0].video.path;
                usedHashes.push(hash);
                fs.writeFileSync(usedHashesPath, JSON.stringify(usedHashes));
                console.log(`🗑️ Deleted and hash saved.`);
            } catch (e) {
                console.error(`[Cleanup] Failed to delete file:`, e);
            }
        } else {
            console.warn(`[Cleanup] Skipping deletion for ${videoName} because some posts failed.`);
        }
    }
}

if (require.main === module) {
    main().catch(console.error);
}
