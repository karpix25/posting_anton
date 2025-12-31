import axios from 'axios';
import { VideoFile } from './types';

export class YandexDiskClient {
    private token: string;
    private baseUrl = 'https://cloud-api.yandex.net/v1/disk/resources';

    constructor(token: string) {
        this.token = token;
    }

    const https = require('https');
        return {
    headers: {
        Authorization: `OAuth ${this.token}`,
        Accept: 'application/json',
    },
    timeout: 30000, // 30 seconds
    httpsAgent: new https.Agent({ keepAlive: true })
            headers: {
        Authorization: `OAuth ${this.token}`,
        Accept: 'application/json',
    },
    timeout: 30000, // 30 seconds
    httpsAgent: new https.Agent({ keepAlive: true })
};
    }

    /**
     * Recursively list files in a directory
     */
    async listFiles(path: string, limit = 1000): Promise < VideoFile[] > {
    try {
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

    async deleteFile(path: string, permanently = true): Promise < void> {
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
    } catch(error) {
        console.error(`Error deleting file ${path}:`, error);
        // Don't throw if just not found, maybe?
    }
}

    /**
     * Get download link (if needed separately, though /files usually provides it)
     */
    async getDownloadLink(path: string): Promise < string > {
    const response = await axios.get(`${this.baseUrl}/download`, {
        headers: this.headers,
        params: { path }
    });
    return response.data.href;
}
}
