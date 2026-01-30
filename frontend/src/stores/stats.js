import { defineStore } from 'pinia'
import axios from 'axios'

export const useStatsStore = defineStore('stats', {
    state: () => ({
        stats: {
            totalVideos: 0,
            publishedCount: 0,
            byCategory: {},
            byAuthor: {},
            byBrand: {},
            // ... other structures if needed
        },
        todayStats: {
            date: '--',
            time_msk: '--:--',
            success_count: 0,
            failed_count: 0,
            queued_count: 0,
            profiles_count: 0
        },
        publishingStats: null, // { total_profiles, total_expected_posts, ... }
        history: [],
        brandStats: {}, // used for platform cards
        analytics: {
            platforms: {} // { instagram: { followers: 0, ... }, ... }
        },
        errors: [],
        apiStatus: {
            telegram: false,
            upload: false,
            gemini: false
        },
        loading: false,
        dateFrom: '',
        dateTo: ''
    }),

    actions: {
        async loadTodayStats() {
            try {
                const res = await axios.get('/api/stats/today')
                if (res.data) this.todayStats = res.data
            } catch (e) {
                console.error('Failed to load today stats:', e)
            }
        },

        async loadPublishingStats() {
            try {
                let url = '/api/stats/publishing'
                const params = new URLSearchParams()
                if (this.dateFrom) params.append('date_from', this.dateFrom)
                if (this.dateTo) params.append('date_to', this.dateTo)

                const res = await axios.get(url, { params })
                if (res.data.success) {
                    this.publishingStats = res.data
                }
            } catch (e) {
                console.error('Failed to load publishing stats:', e)
            }
        },

        async loadHistory(days = 30) {
            try {
                const res = await axios.get('/api/stats/history', { params: { days } })
                if (res.data.success) {
                    this.history = res.data.history
                }
            } catch (e) {
                console.error('Failed to load history:', e)
            }
        },

        async loadBrandStats() {
            try {
                // Default to current month if not specified
                const res = await axios.get('/api/brands/stats')
                if (res.data.success) {
                    this.brandStats = res.data.stats || {}
                }
            } catch (e) {
                console.error('Failed to load brand stats:', e)
            }
        },

        async checkHealth() {
            try {
                const res = await axios.get('/api/health')
                if (res.data) this.apiStatus = res.data
            } catch (e) {
                console.error('Health check failed', e)
            }
        },

        async fetchGlobalAnalytics() {
            try {
                const res = await axios.get('/api/analytics/global')
                if (res.data.success) {
                    this.analytics = res.data
                }
            } catch (e) { console.error(e) }
        },

        async fetchErrors() {
            try {
                const res = await axios.get('/api/errors/recent')
                if (res.data.success) {
                    this.errors = res.data.errors
                }
            } catch (e) {
                console.error('Failed to fetch errors:', e)
            }
        },

        async loadYandexStats(refresh = false) {
            this.loading = true
            try {
                // This endpoint returns generic stats including totalVideos and byCategory from Yandex scan
                // Added timeout of 60 seconds to prevent hanging indefinitely
                const res = await axios.get('/api/stats', {
                    params: { refresh },
                    timeout: 60000
                })
                if (res.data) {
                    // Update relevant stats
                    this.stats.totalVideos = res.data.totalVideos || 0
                    this.stats.byCategory = res.data.byCategory || {}
                    // We might merge other fields if needed
                }
            } catch (e) {
                console.error('Failed to load Yandex stats:', e)
            } finally {
                this.loading = false
            }
        }
    }
})
