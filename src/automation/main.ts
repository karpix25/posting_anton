import { YandexDiskClient } from './yandex';
import { ContentScheduler } from './scheduler';
import { ContentGenerator } from './content_generator';
import { PlatformManager } from './platforms';
import { AutomationConfig } from './types';
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
    for (const post of schedule) {
        try {
            console.log(`Processing post for ${post.profile.username} on ${post.platform}...`);

            const caption = await generator.generateCaption(post.video.path, post.platform);
            post.caption = caption;
            post.hashtags = [];

            await platformManager.publishPost(post);

            usedHashes.push(post.video.md5 || post.video.path);
            fs.writeFileSync(usedHashesPath, JSON.stringify(usedHashes));

        } catch (error) {
            console.error(`Failed to process post:`, error);
        }
    }
}

if (require.main === module) {
    main().catch(console.error);
}
