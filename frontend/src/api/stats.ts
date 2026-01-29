
import { apiClient } from './client';

export const statsApi = {
    async getGeneral(refresh: boolean = false): Promise<any> {
        const { data } = await apiClient.get('/api/stats', { params: { refresh } });
        return data;
    },

    async getToday(): Promise<any> {
        const { data } = await apiClient.get('/api/stats/today');
        return data;
    },

    async getPublishing(): Promise<any> {
        const { data } = await apiClient.get('/api/stats/publishing');
        return data;
    },

    async getHistory(days: number = 30): Promise<any> {
        const { data } = await apiClient.get('/api/stats/history', { params: { days } });
        return data;
    },

    async getBrandStats(month?: string): Promise<any> {
        const { data } = await apiClient.get('/api/brands/stats', { params: { month } });
        return data;
    }
};
