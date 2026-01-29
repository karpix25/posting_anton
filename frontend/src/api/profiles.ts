
import { apiClient } from './client';

export const profilesApi = {
    async sync(): Promise<{ success: boolean; stats?: any; error?: string }> {
        const { data } = await apiClient.post('/api/profiles/sync');
        return data;
    },

    // Direct profile updates if implemented in backend, 
    // otherwise we update via configApi.update() whole object.
    // The task mentions "/api/profiles/*" optmization might be done.
    // For now we rely on Config Sync.
};
