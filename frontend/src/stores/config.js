import { defineStore } from 'pinia'
import axios from 'axios'

export const useConfigStore = defineStore('config', {
    state: () => ({
        config: {
            cronSchedule: '',
            daysToGenerate: 1,
            yandexFolders: [],
            limits: { instagram: 0, tiktok: 0, youtube: 0 },
            profiles: [],
            clients: []
        },
        loading: false,
        error: null
    }),

    actions: {
        async fetchConfig() {
            this.loading = true
            try {
                const response = await axios.get('/api/config')
                if (response.data.success) {
                    this.config = {
                        ...this.config,
                        ...response.data.config,
                        limits: response.data.config.limits || { instagram: 0, tiktok: 0, youtube: 0 },
                        profiles: response.data.config.profiles || [],
                        clients: response.data.config.clients || []
                    }
                }
            } catch (err) {
                this.error = 'Failed to load config'
                console.error(err)
            } finally {
                this.loading = false
            }
        },

        async saveConfig(newConfig) {
            try {
                // GUARD: Prevent DOM Events from being treated as config objects
                if (newConfig && (newConfig instanceof Event || typeof newConfig.preventDefault === 'function' || newConfig.target)) {
                    console.warn('saveConfig called with Event object, ignoring argument.')
                    newConfig = null
                }

                // Use provided config or current state
                const configToSave = newConfig || this.config
                const response = await axios.post('/api/config', configToSave)
                if (response.data.success) {
                    // Only update local state if a NEW config object was provided
                    if (newConfig) {
                        this.config = newConfig
                    }
                    return true
                }
                return false
            } catch (err) {
                console.error('Failed to save config:', err)
                return false
            }
        }
    }
})
