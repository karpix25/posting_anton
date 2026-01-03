import axios from 'axios';
import * as https from 'https';
import { VideoFile } from './types';

export class YandexDiskClient {
    private token: string;
    private baseUrl = 'https://cloud-api.yandex.net/v1/disk/resources';
    private httpsAgent: https.Agent;

    constructor(token: string) {
        this.token = token;
        // Keep-Alive to prevent connection drops/timeouts in Docker
        this.httpsAgent = new https.Agent({ keepAlive: true });
    }

    private get headers() {
        return {
            Authorization: `OAuth ${this.token}`,
            Accept: 'application/json',
        };
    }

    /**
     * Recursively list files in a directory
     */
    async listFiles(path: string, limit = 10000): Promise<VideoFile[]> {
        const maxRetries = 3;
        let lastError: any;

        // Try with decreasing limits if timeout/error occurs
        const limitsToTry = [limit, Math.min(5000, limit), Math.min(2000, limit)];

        for (let attempt = 0; attempt < maxRetries; attempt++) {
            const currentLimit = limitsToTry[attempt] || 1000;

            try {
                console.log(`[Yandex] Fetching files with pagination (limit per page: ${currentLimit}, attempt: ${attempt + 1}/${maxRetries})...`);

                const allItems: any[] = [];
                let offset = 0;
                let hasMore = true;

                while (hasMore) {
                    const response = await axios.get(`${this.baseUrl}/files`, {
                        headers: this.headers,
                        timeout: 120000, // 120 seconds for large datasets
                        httpsAgent: this.httpsAgent,
                        params: {
                            path,
                            limit: currentLimit,
                            offset: offset,
                            media_type: 'video',
                            // Request only fields we actually need - dramatically reduces response size!
                            fields: 'items.name,items.path,items.md5,items.size,items.created,limit,offset'
                        },
                    });

                    const items = response.data.items || [];
                    allItems.push(...items);

                    console.log(`[Yandex] ✅ Page fetched: ${items.length} files (total so far: ${allItems.length}, offset: ${offset})`);

                    // Check if there are more files
                    if (items.length < currentLimit) {
                        hasMore = false;
                    } else {
                        offset += currentLimit;
                    }
                }

                console.log(`[Yandex] ✅ Total files fetched: ${allItems.length}`);
                if (allItems.length > 0) {
                    console.log(`[Yandex] First file: ${allItems[0].path}`);
                }

                return allItems.map((item: any) => ({
                    name: item.name,
                    path: item.path,
                    url: item.path, // Store path for API calls
                    md5: item.md5,
                    size: item.size,
                    created: item.created,
                }));

            } catch (error: any) {
                lastError = error;
                const isTimeout = error.code === 'ECONNABORTED' || error.message?.includes('timeout');
                const is500Error = error.response?.status === 500;

                if ((isTimeout || is500Error) && attempt < maxRetries - 1) {
                    console.warn(`[Yandex] ⚠️  ${is500Error ? 'Server error (500)' : 'Timeout'} with limit ${currentLimit}, retrying with lower limit...`);
                    await new Promise(resolve => setTimeout(resolve, 3000)); // Wait 3s before retry
                    continue;
                }

                console.error('[Yandex] ❌ Error listing files:', error.message || error);
                if (error.response?.status === 500) {
                    console.error('[Yandex] API returned 500 - try reducing file count or check Yandex Disk status');
                }
                // Log full error if it's a network error
                if (error.code === 'ETIMEDOUT' || error.code === 'ECONNRESET') {
                    console.error('[Yandex] Network error details:', error);
                }
                throw error;
            }
        }
        // This part should ideally not be reached if an error is thrown or items are returned
        // but it's good practice to ensure a throw if all retries fail without returning.
        throw lastError || new Error('[Yandex] Failed to list files after multiple attempts.');
    }

    async deleteFile(path: string, permanently = true): Promise<void> {
        try {
            await axios.delete(this.baseUrl, {
                headers: this.headers,
                timeout: 30000,
                httpsAgent: this.httpsAgent,
                params: {
                    path,
                    permanently,
                    force_async: true
                }
            });
            console.log(`[Yandex] Deleted file: ${path}`);
        } catch (error: any) {
            console.error(`[Yandex] Error deleting file ${path}:`, error.message || error);
        }
    }

    /**
     * Get download link (if needed separately, though /files usually provides it)
     */
    async getDownloadLink(path: string): Promise<string> {
        const response = await axios.get(`${this.baseUrl}/download`, {
            headers: this.headers,
            timeout: 30000,
            httpsAgent: this.httpsAgent,
            params: { path }
        });
        return response.data.href;
    }
}
