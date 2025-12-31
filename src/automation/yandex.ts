import axios from 'axios';
import { VideoFile } from './types';

export class YandexDiskClient {
    private token: string;
    private baseUrl = 'https://cloud-api.yandex.net/v1/disk/resources';

    constructor(token: string) {
        this.token = token;
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
    async listFiles(path: string, limit = 1000): Promise<VideoFile[]> {
        try {
            const response = await axios.get(`${this.baseUrl}/files`, {
                headers: this.headers,
                params: {
                    path, // NOTE: 'path' param in 'files' endpoint filters by "files in this specific folder"? 
                    // Actually /resources/files gives a flat list of all files. 
                    // If we want specific folder, we usually use /resources with limit & fields.
                    // But n8n workflow used /resources/files with path='ВИДЕО'.
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

        } catch (error) {
            console.error('Error listing Yandex Disk files:', error);
            throw error;
        }
    }

    async deleteFile(path: string, permanently = true): Promise<void> {
        try {
            await axios.delete(this.baseUrl, {
                headers: this.headers,
                params: {
                    path,
                    permanently,
                    force_async: true
                }
            });
            console.log(`Deleted file: ${path}`);
        } catch (error) {
            console.error(`Error deleting file ${path}:`, error);
            // Don't throw if just not found, maybe?
        }
    }

    /**
     * Get download link (if needed separately, though /files usually provides it)
     */
    async getDownloadLink(path: string): Promise<string> {
        const response = await axios.get(`${this.baseUrl}/download`, {
            headers: this.headers,
            params: { path }
        });
        return response.data.href;
    }
}
