
import { apiClient } from './client';

export const systemApi = {
    async cleanup(): Promise<any> {
        const { data } = await apiClient.post('/api/cleanup');
        return data;
    },

    async runAutomation(): Promise<any> {
        const { data } = await apiClient.post('/api/run');
        return data;
    },

    async getLogs(lines: number = 100): Promise<any> {
        const { data } = await apiClient.get('/api/logs', { params: { lines } });
        return data;
    },

    async getGroupedErrors(): Promise<any> {
        const { data } = await apiClient.get('/api/errors/grouped');
        return data;
    },

    async getRecentErrors(limit: number = 50): Promise<any> {
        const { data } = await apiClient.get('/api/errors/recent', { params: { limit } });
        return data;
    }
};
