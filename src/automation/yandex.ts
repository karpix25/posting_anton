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
        try {
            const response = await axios.get(`${this.baseUrl}/files`, {
                headers: this.headers,
                timeout: 30000, // 30 seconds timeout
                httpsAgent: this.httpsAgent,
                params: {
                    path, // effectively ignored by /files endpoint but good to keep if they change API
                    limit,
                    preview_size: 'XXXL',
                    media_type: 'video'
                },
            });

            // Transform response to our interface
            const items = response.data.items || [];
            return items.map((item: any) => ({
                name: item.name,
                path: item.path,
                url: item.file || item.preview, // Prefer direct file link
                md5: item.md5,
                size: item.size,
                created: item.created,
            }));

        } catch (error: any) {
            console.error('[Yandex] Error listing files:', error.message || error);
            // Log full error if it's a network error
            if (error.code === 'ETIMEDOUT' || error.code === 'ECONNRESET') {
                console.error('[Yandex] Network error details:', error);
            }
            throw error;
        }
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
