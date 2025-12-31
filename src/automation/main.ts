import { YandexDiskClient } from './yandex';
import { ContentScheduler } from './scheduler';
import { ContentGenerator } from './content_generator';
import { PlatformManager } from './platforms';
import { AutomationConfig, ScheduledPost } from './types';
import * as fs from 'fs';
import * as path from 'path';

async function main() {
    // Load Config
    const configPath = path.join(__dirname, '../../config.json');
    if (!fs.existsSync(configPath)) {
        console.error('Config file not found!');
        process.exit(1);
    }
    const config: AutomationConfig = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

    // Inject Env Vars
    config.yandexToken = process.env.YANDEX_TOKEN || '';
    config.daysToGenerate = 7; // Default

    const yandex = new YandexDiskClient(config.yandexToken);
    const usedHashesPath = path.join(__dirname, 'used_hashes.json');
    let usedHashes: string[] = [];
    if (fs.existsSync(usedHashesPath)) {
        usedHashes = JSON.parse(fs.readFileSync(usedHashesPath, 'utf-8'));
    }

    const scheduler = new ContentScheduler(config, usedHashes);
    // Pass config to generator for dynamic prompts
    const generator = new ContentGenerator(process.env.OPENAI_API_KEY || '', config);
    const platformManager = new PlatformManager();

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

                // Generate ID/Caption if not already
                if (!post.caption) {
                    const caption = await generator.generateCaption(post.video.path, post.platform, post.profile.username);
                    post.caption = caption;
                }

                await platformManager.publishPost(post);
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
