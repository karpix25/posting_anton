
import { apiClient } from './client';
import type { AppConfig, ScheduleConfig } from '../types';

export const configApi = {
    async get(): Promise<AppConfig> {
        const { data } = await apiClient.get<AppConfig>('/api/config');
        return data;
    },

    async update(config: AppConfig): Promise<{ success: boolean; message: string }> {
        const { data } = await apiClient.post('/api/config', config);
        return data;
    },

    async restoreDefaults(): Promise<{ success: boolean; message: string }> {
        const { data } = await apiClient.post('/api/config/restore-defaults');
        return data;
    },

    async getSchedule(): Promise<ScheduleConfig> {
        const { data } = await apiClient.get<ScheduleConfig>('/api/schedule');
        return data;
    },

    async updateSchedule(schedule: Partial<ScheduleConfig>): Promise<{ success: boolean }> {
        const { data } = await apiClient.post('/api/schedule', schedule);
        return data;
    }
};
