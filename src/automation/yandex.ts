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
    async listFiles(path: string, limit = 100000): Promise<VideoFile[]> {
        const maxRetries = 2;
        let lastError: any;

        // Try with decreasing limits if timeout occurs
        const limitsToTry = [limit, Math.min(50000, limit), Math.min(20000, limit)];

        for (let attempt = 0; attempt < maxRetries; attempt++) {
            const currentLimit = limitsToTry[attempt] || 10000;

            try {
                console.log(`[Yandex] Fetching files (limit: ${currentLimit}, attempt: ${attempt + 1}/${maxRetries})...`);

                const response = await axios.get(`${this.baseUrl}/files`, {
                    headers: this.headers,
                    timeout: 120000, // 120 seconds for large datasets
                    httpsAgent: this.httpsAgent,
                    params: {
                        path,
                        limit: currentLimit,
                        media_type: 'video',
                        // Request only fields we actually need - dramatically reduces response size!
                        // NOTE: API /files не возвращает прямые ссылки (file). Нужно делать отдельный запрос /download
                        fields: 'items.name,items.path,items.md5,items.size,items.created,limit,offset'
                    },
                });

                // Transform response to our interface
                console.log(`[Yandex] ✅ API Response Status: ${response.status}`);
                console.log(`[Yandex] Items fetched: ${(response.data.items || []).length}`);

                if (response.data.items && response.data.items.length > 0) {
                    console.log(`[Yandex] First file: ${response.data.items[0].path}`);
                }

                const items = response.data.items || [];
                return items.map((item: any) => ({
                    name: decodeURIComponent(item.name || ''),
                    path: decodeURIComponent(item.path || ''),
                    url: item.path, // Store original path for API calls
                    md5: item.md5,
                    size: item.size,
                    created: item.created,
                }));

            } catch (error: any) {
                lastError = error;
                const isTimeout = error.code === 'ECONNABORTED' || error.message?.includes('timeout');

                if (isTimeout && attempt < maxRetries - 1) {
                    console.warn(`[Yandex] ⚠️  Timeout with limit ${currentLimit}, retrying with lower limit...`);
                    await new Promise(resolve => setTimeout(resolve, 2000)); // Wait 2s before retry
                    continue;
                }

                console.error('[Yandex] ❌ Error listing files:', error.message || error);
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
