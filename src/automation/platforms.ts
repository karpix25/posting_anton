import axios from 'axios';
import { ScheduledPost } from './types';
import FormData from 'form-data';
import { YandexDiskClient } from './yandex';

const UPLOAD_POST_API_URL = 'https://api.upload-post.com/api/upload';
const USER_PROFILES_API_URL = 'https://api.upload-post.com/api/uploadposts/users';
const HISTORY_API_URL = 'https://api.upload-post.com/api/uploadposts/history';
const INSTAGRAM_TITLE_MAX_CHARS = 1870;
const TIKTOK_TITLE_MAX_CHARS = 1870;
const YOUTUBE_TITLE_MAX_CHARS = 85;
const YOUTUBE_DESCRIPTION_MAX_CHARS = 4250;

function normalizeText(value: string, maxChars: number): string {
    // Preserve newlines, collapse only multiple spaces/tabs
    const text = (value || '').replace(/[ \t]+/g, ' ').trim();
    if (text.length <= maxChars) return text;
    return text.substring(0, maxChars - 3) + '...';
}

export class UploadPostClient {
    private apiKey: string;

    constructor(apiKey: string) {
        this.apiKey = apiKey;
    }

    async getHistory(limit: number = 100): Promise<any[]> {
        try {
            // TODO: Pagination if needed, for now just fetch recent 100
            const response = await axios.get(HISTORY_API_URL, {
                params: { limit },
                headers: { 'Authorization': `Apikey ${this.apiKey}` }
            });
            return response.data.history || [];
        } catch (error: any) {
            console.error(`[UploadPost] Error fetching history:`, error.response?.data || error.message);
            return []; // Return empty on error to not block flow, or throw? Better safe default empty?
        }
    }

    async getProfiles(): Promise<any[]> {
        try {
            const response = await axios.get(USER_PROFILES_API_URL, {
                headers: {
                    'Authorization': `Apikey ${this.apiKey}`
                }
            });

            if (response.data.success) {
                return response.data.profiles;
            } else {
                throw new Error(response.data.message || 'Failed to fetch profiles');
            }
        } catch (error: any) {
            console.error(`[UploadPost] Error fetching profiles:`, error.response?.data || error.message);
            throw error;
        }
    }

    async getScheduledPosts(): Promise<any[]> {
        try {
            const response = await axios.get('https://api.upload-post.com/api/uploadposts/schedule', {
                headers: { 'Authorization': `Apikey ${this.apiKey}` }
            });

            // Debug: Log the actual response structure
            console.log('[UploadPost] GET /schedule response:', JSON.stringify(response.data, null, 2));

            // Try different possible response formats
            if (Array.isArray(response.data)) {
                console.log(`[UploadPost] Response is array with ${response.data.length} items`);
                return response.data;
            }

            if (response.data.scheduled_posts) {
                console.log(`[UploadPost] Found scheduled_posts array with ${response.data.scheduled_posts.length} items`);
                return response.data.scheduled_posts;
            }

            if (response.data.schedule) {
                console.log(`[UploadPost] Found schedule array with ${response.data.schedule.length} items`);
                return response.data.schedule;
            }

            console.warn('[UploadPost] Unknown response format, returning empty array');
            return [];
        } catch (error: any) {
            console.error(`[UploadPost] Error fetching schedule:`, error.response?.data || error.message);
            return [];
        }
    }

    async cancelPost(jobId: string): Promise<boolean> {
        try {
            const response = await axios.delete(`https://api.upload-post.com/api/uploadposts/schedule/${jobId}`, {
                headers: { 'Authorization': `Apikey ${this.apiKey}` }
            });
            if (response.data.success) {
                console.log(`[UploadPost] Cancelled job ${jobId}`);
                return true;
            }
            return false;
        } catch (error: any) {
            console.error(`[UploadPost] Failed to cancel job ${jobId}:`, error.response?.data || error.message);
            return false;
        }
    }

    async publish(post: ScheduledPost): Promise<string> {
        const form = new FormData();
        const isYoutube = post.platform === 'youtube';
        const resolvedTitle = (post.title || '').trim();
        const resolvedCaption = (post.caption || '').trim();
        const fallbackTitle = (resolvedTitle || resolvedCaption).trim();

        // Common parameters
        form.append('user', post.profile.username);
        form.append('platform[]', post.platform);
        form.append('video', post.video.url); // Sending URL directly
        // Upload Post docs: global `title` is fallback and required for YouTube.
        // For IG/TikTok we rely on platform-specific fields to avoid text duplication.
        if (isYoutube && fallbackTitle) {
            form.append('title', normalizeText(fallbackTitle, YOUTUBE_TITLE_MAX_CHARS));
        }

        if (post.publish_at) {
            // Ensure ISO format as per docs
            form.append('scheduled_date', new Date(post.publish_at).toISOString());
        }

        // Platform-specific titles/params per Upload Post docs
        console.log(`[UploadPost] Preparing payload for ${post.platform}. Final Caption Sample: "${resolvedCaption.substring(0, 80).replace(/\n/g, '\\n')}${resolvedCaption.length > 80 ? '...' : ''}"`);

        switch (post.platform) {
            case 'instagram':
                form.append('instagram_title', normalizeText(resolvedCaption || fallbackTitle, INSTAGRAM_TITLE_MAX_CHARS));
                form.append('media_type', 'REELS');
                break;
            case 'tiktok':
                form.append('tiktok_title', normalizeText(resolvedCaption || fallbackTitle, TIKTOK_TITLE_MAX_CHARS));
                form.append('post_mode', 'DIRECT_POST');
                break;
            case 'youtube':
                form.append('youtube_title', normalizeText(fallbackTitle, YOUTUBE_TITLE_MAX_CHARS));
                form.append('youtube_description', normalizeText(resolvedCaption || fallbackTitle, YOUTUBE_DESCRIPTION_MAX_CHARS));
                // Note: We avoid sending global 'description' and 'title' alongside 
                // platform-specific ones to prevent duplication in some API versions.
                form.append('categoryId', '22');
                form.append('privacyStatus', 'public');
                break;
        }

        try {
            console.log(`[UploadPost] Sending request for ${post.profile.username} on ${post.platform}...`);
            const response = await axios.post(UPLOAD_POST_API_URL, form, {
                headers: {
                    'Authorization': `Apikey ${this.apiKey}`,
                    ...form.getHeaders()
                }
            });

            if (response.data.success) {
                console.log(`[UploadPost] Success! Request ID: ${response.data.request_id || 'sync'}`);
                return JSON.stringify(response.data);
            } else {
                throw new Error(response.data.message || 'Unknown error');
            }
        } catch (error: any) {
            console.error(`[UploadPost] Error:`, error.response?.data || error.message);
            throw error;
        }
    }
}

export class PlatformManager {
    private client: UploadPostClient;
    private yandexClient?: YandexDiskClient;

    constructor(yandexClient?: YandexDiskClient) {
        // We expect the key to be in env, loaded by main.ts or config
        const apiKey = process.env.UPLOAD_POST_API_KEY || '';
        if (!apiKey) {
            console.warn('WARNING: UPLOAD_POST_API_KEY is missing!');
        }
        this.client = new UploadPostClient(apiKey);
        this.yandexClient = yandexClient;
    }

    async publishPost(post: ScheduledPost): Promise<void> {
        // If video.url is a Yandex path (starts with "disk:/"), fetch real download URL
        if (post.video.url.startsWith('disk:/') && this.yandexClient) {
            console.log(`[PlatformManager] Fetching download URL for ${post.video.path}...`);

            // Retry logic for network failures
            const maxRetries = 3;
            let lastError: any;

            for (let attempt = 1; attempt <= maxRetries; attempt++) {
                try {
                    const downloadUrl = await this.yandexClient.getDownloadLink(post.video.path);
                    post.video.url = downloadUrl;
                    console.log(`[PlatformManager] ✅ Got download URL (${downloadUrl.substring(0, 50)}...)`);
                    break; // Success, exit retry loop
                } catch (error: any) {
                    lastError = error;
                    const isNetworkError = error.code === 'ETIMEDOUT' || error.code === 'ENETUNREACH' || error.code === 'ECONNRESET';

                    if (attempt < maxRetries && isNetworkError) {
                        const waitMs = Math.min(1000 * Math.pow(2, attempt - 1), 5000); // Exponential backoff, max 5s
                        console.warn(`[PlatformManager] ⚠️ Network error (attempt ${attempt}/${maxRetries}), retrying in ${waitMs}ms...`);
                        await new Promise(resolve => setTimeout(resolve, waitMs));
                    } else {
                        console.error(`[PlatformManager] ❌ Failed to get download URL (attempt ${attempt}/${maxRetries}):`, error.message || error);
                        if (attempt === maxRetries) {
                            throw new Error(`Cannot publish: failed to get download URL for ${post.video.path} after ${maxRetries} attempts`);
                        }
                    }
                }
            }
        }

        await this.client.publish(post);
    }

    async getHistory(): Promise<any[]> {
        return this.client.getHistory();
    }

    async getProfiles(): Promise<any[]> {
        return this.client.getProfiles();
    }

    async getScheduledPosts(): Promise<any[]> {
        return this.client.getScheduledPosts();
    }

    async cancelPost(jobId: string): Promise<boolean> {
        return this.client.cancelPost(jobId);
    }
}
