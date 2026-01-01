import axios from 'axios';
import { ScheduledPost } from './types';
import FormData from 'form-data';
import { YandexDiskClient } from './yandex';

const UPLOAD_POST_API_URL = 'https://api.upload-post.com/api/upload';
const USER_PROFILES_API_URL = 'https://api.upload-post.com/api/uploadposts/users';
const HISTORY_API_URL = 'https://api.upload-post.com/api/uploadposts/history';

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

    async publish(post: ScheduledPost): Promise<string> {
        const form = new FormData();

        // Common parameters
        form.append('user', post.profile.username);
        form.append('platform[]', post.platform);
        form.append('video', post.video.url); // Sending URL directly
        form.append('title', post.caption || ''); // Default title

        if (post.publish_at) {
            // Ensure ISO format as per docs
            form.append('scheduled_date', new Date(post.publish_at).toISOString());
        }

        // Platform-specific titles/params default to the main title/caption relative to the docs
        // Docs say: "[platform]_title will override the main title for that platform"
        // We map our 'caption' to the specific title fields just in case

        switch (post.platform) {
            case 'instagram':
                form.append('instagram_title', post.title || post.caption || '');
                form.append('media_type', 'REELS');
                break;
            case 'tiktok':
                form.append('tiktok_title', post.title || post.caption || '');
                form.append('post_mode', 'DIRECT_POST');
                break;
            case 'youtube':
                form.append('youtube_title', post.title || post.caption || ''); // YouTube title
                form.append('youtube_description', post.caption || ''); // YouTube description
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
            try {
                const downloadUrl = await this.yandexClient.getDownloadLink(post.video.path);
                post.video.url = downloadUrl;
                console.log(`[PlatformManager] ✅ Got download URL (${downloadUrl.substring(0, 50)}...)`);
            } catch (error) {
                console.error(`[PlatformManager] ❌ Failed to get download URL:`, error);
                throw new Error(`Cannot publish: failed to get download URL for ${post.video.path}`);
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
}
