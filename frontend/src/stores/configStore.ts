
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { configApi } from '../api/config';
import type { AppConfig } from '../types';

export const useConfigStore = defineStore('config', () => {
    const config = ref<AppConfig | null>(null);
    const loading = ref(false);
    const error = ref<string | null>(null);
    const hasUnsavedChanges = ref(false);

    const isLoaded = computed(() => !!config.value);

    async function loadConfig() {
        loading.value = true;
        error.value = null;
        try {
            config.value = await configApi.get();
        } catch (err: any) {
            error.value = err.message || 'Failed to load config';
        } finally {
            loading.value = false;
        }
    }

    async function saveConfig() {
        if (!config.value) return;
        loading.value = true;
        try {
            await configApi.update(config.value);
            hasUnsavedChanges.value = false;
            // Optionally reload to ensure sync
        } catch (err: any) {
            error.value = err.message || 'Failed to save config';
        } finally {
            loading.value = false;
        }
    }

    // Profiles helpers
    function updateProfile(username: string, updates: any) {
        if (!config.value) return;
        const p = config.value.profiles.find(p => p.username === username);
        if (p) {
            Object.assign(p, updates);
            hasUnsavedChanges.value = true;
        }
    }

    return {
        config,
        loading,
        error,
        isLoaded,
        hasUnsavedChanges,
        loadConfig,
        saveConfig,
        updateProfile
    };
});
