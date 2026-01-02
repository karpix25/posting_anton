
import { PlatformManager } from '../automation/platforms';
import * as dotenv from 'dotenv';
import * as path from 'path';

// Load environment variables manually since we are outside main
dotenv.config({ path: path.join(__dirname, '../.env') });

async function clearSchedule() {
    console.log('[Cleanup] Starting cleaning process...');

    if (!process.env.UPLOAD_POST_API_KEY) {
        console.error('[Cleanup] Error: UPLOAD_POST_API_KEY not found in .env');
        process.exit(1);
    }

    const platformManager = new PlatformManager();

    console.log('[Cleanup] Fetching scheduled posts...');
    const posts = await platformManager.getScheduledPosts();

    if (!posts || posts.length === 0) {
        console.log('[Cleanup] No scheduled posts found. Exiting.');
        return;
    }

    console.log(`[Cleanup] Found ${posts.length} scheduled posts. Deleting...`);

    let deletedCount = 0;
    for (const post of posts) {
        // Assuming post object has 'id' or 'job_id'
        const jobId = post.id || post.job_id;
        if (!jobId) {
            console.warn(`[Cleanup] Post object has no ID:`, post);
            continue;
        }

        const success = await platformManager.cancelPost(jobId);
        if (success) deletedCount++;
        // Small delay to avoid rate limits
        await new Promise(r => setTimeout(r, 200));
    }

    console.log(`[Cleanup] Done. Deleted ${deletedCount} of ${posts.length} posts.`);
}

clearSchedule().catch(err => {
    console.error('[Cleanup] Unhandled error:', err);
});
