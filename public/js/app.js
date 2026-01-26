const { createApp } = Vue;

createApp({
    data() {
        return {
            currentTab: 'dashboard',
            tabs: [
                { id: 'dashboard', name: 'Дашборд' },
                { id: 'stats', name: 'Аналитика' },
                { id: 'settings', name: 'Настройки' },
                { id: 'profiles', name: 'Профили' },
                { id: 'clients', name: 'AI Клиенты' },
                { id: 'logs', name: '📋 Логи' }
            ],
            config: {
                cronSchedule: '',
                daysToGenerate: 1, // Default for safety
                yandexFolders: [],
                limits: { instagram: 0, tiktok: 0, youtube: 0 },
                profiles: [],
                clients: []
            },
            stats: {
                totalVideos: 0,
                publishedCount: 0,
                byCategory: {},
                byAuthor: {},
                byBrand: {},
                profilesByCategory: {},
                profilesByAuthor: {},
                profilesByBrand: {}
            },
            brandStats: {},
            currentMonth: new Date().toISOString().substring(0, 7),
            statsSummary: null,
            todayStats: {
                date: '--',
                time_msk: '--:--',
                success_count: 0,
                failed_count: 0,
                queued_count: 0,
                profiles_count: 0
            },
            loading: true,
            statsLoading: false,
            statsViewMode: 'category', // 'category' or 'author'
            foldersInput: '',
            newThemeKey: '',
            showActiveProfiles: true,    // Collapsible - active by default
            showDisabledProfiles: false,  // Collapsible - collapsed by default
            expandedGroups: {}, // For accordion view
            expandedGroupsDisabled: {}, // For disabled profiles accordion
            selectedProfiles: [], // List of selected usernames for bulk actions
            logs: [],
            logsLoading: false,
            logsSuccess: true,
            logsMessage: '',
            logsAutoRefresh: false,
            logsRefreshInterval: null,
            selectedDisabledProfiles: [], // List of selected disabled profiles for bulk actions
            lastSelected: null, // For Shift+Click
            lastSelectedDisabled: null, // For Shift+Click in disabled profiles
            bulkThemeKey: '', // Model for bulk edit input
            bulkThemeKeyDisabled: '', // Model for bulk edit input (disabled profiles)
            schedule: {
                enabled: false,
                timezone: 'Europe/Moscow',
                dailyRunTime: '00:01'
            }
        }
    },
    async mounted() {
        await this.loadConfig();
        this.loadSchedule();
        this.loadStats();
        this.loadTodayStats();
        this.fetchStatsSummary();
        this.loadBrandStats();

        // Auto-refresh today stats every 60 seconds
        setInterval(() => this.loadTodayStats(), 60000);
    },
    computed: {
        currentStatsList() {
            let source = {};
            if (this.statsViewMode === 'author') source = this.stats.byAuthor || {};
            else if (this.statsViewMode === 'brand') source = this.stats.byBrand || {};
            else source = this.stats.byCategory || {};

            return Object.entries(source)
                .map(([name, count]) => ({ name, count }))
                .filter(item => item.name !== 'unknown') // Exclude files outside hierarchy
                .sort((a, b) => a.name.localeCompare(b.name));
        },
        statsHeader() {
            if (this.statsViewMode === 'category') return 'Категория';
            if (this.statsViewMode === 'brand') return 'Бренд';
            return 'Автор';
        },
        activeProfiles() {
            if (!this.config.profiles) return [];
            return this.config.profiles.filter(p => p.enabled !== false);
        },
        disabledProfiles() {
            if (!this.config.profiles) return [];
            return this.config.profiles.filter(p => p.enabled === false);
        },
        groupedActiveProfiles() {
            const groups = {};
            this.activeProfiles.forEach(p => {
                // Create a consistent key
                const key = (p.theme_key || 'Без темы').toLowerCase().trim();
                // Capitalize for display
                const displayKey = key.charAt(0).toUpperCase() + key.slice(1);

                if (!groups[displayKey]) groups[displayKey] = [];
                groups[displayKey].push(p);
            });

            // Sort keys alphabetically
            return Object.keys(groups).sort().reduce((acc, key) => {
                acc[key] = groups[key];
                return acc;
            }, {});
        },
        groupedDisabledProfiles() {
            const groups = {};
            this.disabledProfiles.forEach(p => {
                const key = (p.theme_key || 'Без темы').toLowerCase().trim();
                const displayKey = key.charAt(0).toUpperCase() + key.slice(1);
                if (!groups[displayKey]) groups[displayKey] = [];
                groups[displayKey].push(p);
            });
            return Object.keys(groups).sort().reduce((acc, key) => {
                acc[key] = groups[key];
                return acc;
            }, {});
        },
        availableThemes() {
            // Source strictly from config.themeAliases (which is auto-populated from Yandex Stats)
            if (this.config && this.config.themeAliases) {
                return Object.keys(this.config.themeAliases).sort();
            }
            return [];
        }
    },
    methods: {
        getProfilesForCategory(categoryName) {
            // Get profiles based on current view mode
            const key = categoryName.toLowerCase().trim();

            if (this.statsViewMode === 'author') {
                return this.stats.profilesByAuthor?.[key] || [];
            } else if (this.statsViewMode === 'brand') {
                return this.stats.profilesByBrand?.[key] || [];
            } else {
                // category mode
                return this.stats.profilesByCategory?.[key] || [];
            }
        },
        getPublishedCount(name) {
            // Try to find matching brand stat
            // name is the Brand Name or Category Name from Yandex scan
            const cleanName = name.toLowerCase().replace(" ", "");

            // Search in this.brandStats (which is Keyed by "category:brand")
            // If we are in 'brand' mode, we need to sum up all stats where brand == name
            // If we are in 'category' mode, we sum up all stats where category == name

            let total = 0;
            Object.entries(this.brandStats).forEach(([key, val]) => {
                const [cat, br] = key.split(':');
                if (this.statsViewMode === 'brand') {
                    if (br === cleanName) total += val.published_count || 0;
                } else if (this.statsViewMode === 'category') {
                    if (cat === cleanName) total += val.published_count || 0;
                }
            });
            return total > 0 ? total : '-';
        },
        async loadTodayStats() {
            try {
                const res = await fetch('/api/stats/today');
                if (res.ok) {
                    this.todayStats = await res.json();
                }
            } catch (e) {
                console.error('Failed to load today stats:', e);
            }
        },
        async loadStats() {
            this.statsLoading = true;
            try {
                const res = await fetch('/api/stats');
                if (res.ok) {
                    this.stats = await res.json();

                    // Auto-persist found folders as themes
                    let changed = false;
                    if (!this.config.themeAliases) this.config.themeAliases = {};

                    const foundFolders = Object.keys(this.stats.byCategory || {});
                    foundFolders.forEach(folder => {
                        // Use exact folder name as key
                        // If not exists, add it.
                        // We don't delete old ones automatically to preserve history? 
                        // User said "upsert or add new". Cleaning old ones isn't explicitly requested but safe to keep.
                        if (!this.config.themeAliases[folder]) {
                            this.config.themeAliases[folder] = [folder]; // Default alias is itself
                            changed = true;
                        }
                    });

                    if (changed) {
                        this.saveConfig();
                        // No alert needed, just silent save
                    }
                }
            } catch (e) {
                console.error('Failed to load stats', e);
            } finally {
                this.statsLoading = false;
            }
        },
        async fetchStatsSummary() {
            try {
                const res = await fetch('/api/stats/summary');
                const data = await res.json();
                if (data.success) {
                    this.statsSummary = data.stats;
                }
            } catch (error) {
                console.error('Failed to fetch stats summary:', error);
            }
        },
        async loadConfig() {
            this.loading = true;
            try {
                const res = await fetch('/api/config');
                if (!res.ok) {
                    throw new Error(`Server returned ${res.status}`);
                }
                const data = await res.json();
                // Ensure structure and merge with defaults
                this.config = {
                    profiles: [],
                    clients: [],
                    limits: { instagram: 10, tiktok: 10, youtube: 2 },
                    yandexFolders: [],
                    ...this.config, // Keep existing if any? No, we want server data.
                    ...data // Overwrite with server data
                };

                // Explicitly ensure deep objects if missing from server data
                if (!this.config.profiles) this.config.profiles = [];
                if (!this.config.clients) this.config.clients = [];
                if (!this.config.limits) this.config.limits = { instagram: 10, tiktok: 10, youtube: 2 };

                this.foldersInput = (this.config.yandexFolders || []).join(', ');
            } catch (e) {
                console.error('[UI] Failed to load config:', e);
                // alert('Failed to load config (using defaults)');
                // Keep default config structure
            } finally {
                this.loading = false;
            }
        },
        async saveConfig() {
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.config)
                });
                alert('Сохранено успешно!');
            } catch (e) {
                alert('Ошибка сохранения');
            }
        },
        async loadSchedule() {
            try {
                const res = await fetch('/api/schedule');
                if (res.ok) {
                    const data = await res.json();
                    this.schedule = {
                        enabled: data.enabled || false,
                        timezone: data.timezone || 'Europe/Moscow',
                        dailyRunTime: data.dailyRunTime || '00:01'
                    };
                }
            } catch (e) {
                console.error('[UI] Failed to load schedule:', e);
            }
        },
        async saveSchedule() {
            try {
                const res = await fetch('/api/schedule', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.schedule)
                });

                if (res.ok) {
                    alert('✅ Расписание сохранено успешно!');
                    // Reload full config to update local cronSchedule state
                    await this.loadConfig();
                } else {
                    const error = await res.json();
                    alert(`❌ Ошибка: ${error.error || 'Не удалось сохранить'}`);
                }
            } catch (e) {
                console.error('[UI] Failed to save schedule:', e);
                alert('❌ Ошибка сохранения расписания');
            }
        },
        async triggerRun(testMode = false) {
            // Ensure current state is saved before running
            await this.saveConfig();

            if (!confirm(testMode ? 'Запустить в ТЕСТОВОМ режиме (по 1 посту на платформу)?' : 'Запустить ПОЛНЫЙ цикл автоматизации?')) return;
            try {
                await fetch('/api/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ testMode })
                });
                alert('Автоматизация запущена в фоне. Проверьте логи сервера.');
            } catch (e) {
                alert('Не удалось запустить процесс');
            }
        },
        async syncProfiles() {
            console.log('[UI] Sync button clicked');
            try {
                const res = await fetch('/api/profiles/sync');
                const data = await res.json();
                console.log('[UI] Sync response:', data);

                if (data.success && data.profiles) {
                    let addedCount = 0;

                    data.profiles.forEach(apiProfile => {
                        // Find existing profile by username
                        const existingProfile = this.config.profiles.find(p => p.username === apiProfile.username);

                        // Get platforms from API (keys of social_accounts where value is not empty)
                        const platforms = Object.entries(apiProfile.social_accounts || {})
                            .filter(([_, value]) => !!value) // Filter out empty strings/nulls
                            .map(([key]) => key);

                        if (platforms.length === 0) platforms.push('instagram'); // Default fallback

                        if (existingProfile) {
                            // Update existing profile's platforms if needed (merge or overwrite)
                            // We overwrite to ensure it reflects current state
                            existingProfile.platforms = platforms;
                        } else {
                            // Add new profile
                            this.config.profiles.push({
                                username: apiProfile.username,
                                theme_key: '',
                                platforms: platforms,
                                enabled: true
                            });
                            addedCount++;
                        }
                    });

                    alert(`Синхронизация завершена! Добавлено новых: ${addedCount}. Обновлены платформы у существующих.`);
                    this.saveConfig();
                } else {
                    console.error('[UI] Sync failed:', data.error);
                    alert('Ошибка синхронизации: ' + (data.error || 'Неизвестная ошибка'));
                }
            } catch (e) {
                console.error('[UI] Fetch error:', e);
                alert('Не удалось синхронизировать профили (см. консоль)');
            }
        },
        async fullResync() {
            if (!confirm("ВНИМАНИЕ! \nЭто удалит все текущие настройки категорий (Smart, Toplash и др.) для профилей.\nСписок профилей будет загружен начисто из UploadPost.\n\nВы уверены?")) return;

            this.config.profiles = []; // Clear all local profiles
            await this.syncProfiles(); // Fetch fresh
            this.saveConfig(); // Save the new clean state
        },
        updateFolders() {
            this.config.yandexFolders = this.foldersInput.split(',').map(s => s.trim()).filter(Boolean);
        },
        addProfile() {
            this.config.profiles.push({
                username: '',
                theme_key: '',
                platforms: [] // Start with empty array, user will check boxes
            });
        },
        toggleProfileEnabled(profile) {
            // Toggle between enabled=true and enabled=false
            profile.enabled = profile.enabled === false ? true : false;
            this.saveConfig();
        },
        removeProfile(idx) {
            this.config.profiles.splice(idx, 1);
        },
        toggleBulkEnabled(enable) {
            this.selectedProfiles.forEach(username => {
                const profile = this.config.profiles.find(p => p.username === username);
                if (profile) {
                    profile.enabled = enable;
                }
            });
            this.saveConfig();
            this.selectedProfiles = [];
            // No alert, just UI update
        },
        addClient() {
            this.config.clients.push({ name: 'New Client', regex: '', prompt: '' });
        },
        removeClient(idx) {
            this.config.clients.splice(idx, 1);
        },
        async loadBrandStats() {
            try {
                const res = await fetch(`/api/brands/stats?month=${this.currentMonth}`);
                if (res.ok) {
                    const data = await res.json();
                    this.brandStats = data.stats || {};
                }
            } catch (e) {
                console.error('[UI] Failed to load brand stats:', e);
            }
        },
        getClientPublished(client) {
            if (!client.name || !client.regex) return 0;
            const categoryMatch = client.regex.match(/([a-zA-Zа-яА-Я0-9]+)/);
            const category = categoryMatch ? categoryMatch[1].toLowerCase() : 'unknown';
            const brandName = client.name.toLowerCase().replace(/[^a-zа-я0-9]/g, '');
            const key = `${category}:${brandName}`;
            return this.brandStats[key]?.published_count || 0;
        },
        getClientRemaining(client) {
            const quota = client.quota || 0;
            const published = this.getClientPublished(client);
            return Math.max(0, quota - published);
        },
        getClientProgress(client) {
            const quota = client.quota || 0;
            if (quota === 0) return 0;
            const published = this.getClientPublished(client);
            return Math.min(100, Math.round((published / quota) * 100));
        },
        toggleGroupDisabled(theme) {
            this.expandedGroupsDisabled[theme] = !this.expandedGroupsDisabled[theme];
        },
        toggleGroupSelectionDisabled(profiles, checked) {
            profiles.forEach(p => {
                const idx = this.selectedDisabledProfiles.indexOf(p.username);
                if (checked && idx === -1) {
                    this.selectedDisabledProfiles.push(p.username);
                } else if (!checked && idx !== -1) {
                    this.selectedDisabledProfiles.splice(idx, 1);
                }
            });
        },
        handleProfileSelectDisabled(event, profile, groupProfiles) {
            const username = profile.username;
            const idx = this.selectedDisabledProfiles.indexOf(username);

            if (event.shiftKey && this.lastSelectedDisabled) {
                const lastIdx = groupProfiles.findIndex(p => p.username === this.lastSelectedDisabled);
                const currentIdx = groupProfiles.findIndex(p => p.username === username);
                const [start, end] = lastIdx < currentIdx ? [lastIdx, currentIdx] : [currentIdx, lastIdx];

                for (let i = start; i <= end; i++) {
                    const u = groupProfiles[i].username;
                    if (!this.selectedDisabledProfiles.includes(u)) {
                        this.selectedDisabledProfiles.push(u);
                    }
                }
            } else {
                if (idx === -1) {
                    this.selectedDisabledProfiles.push(username);
                } else {
                    this.selectedDisabledProfiles.splice(idx, 1);
                }
            }
            this.lastSelectedDisabled = username;
        },
        async applyBulkCategoryDisabled() {
            if (!this.bulkThemeKeyDisabled) return;
            this.selectedDisabledProfiles.forEach(username => {
                const profile = this.config.profiles.find(p => p.username === username);
                if (profile) {
                    profile.theme_key = this.bulkThemeKeyDisabled;
                }
            });
            await this.saveConfig();
            this.bulkThemeKeyDisabled = '';
        },
        async toggleBulkEnabledDisabled(enabled) {
            this.selectedDisabledProfiles.forEach(username => {
                const profile = this.config.profiles.find(p => p.username === username);
                if (profile) {
                    profile.enabled = enabled;
                }
            });
            await this.saveConfig();
        },
        updateAliases(key, value) {
            const arr = value.split(',').map(s => s.trim()).filter(s => s.length > 0);
            this.config.themeAliases[key] = arr;
        },
        addThemeKey() {
            if (!this.newThemeKey) return;
            const key = this.newThemeKey.toLowerCase().trim();
            if (this.config.themeAliases[key]) {
                alert('Группа уже существует!');
                return;
            }
            this.config.themeAliases[key] = [key];
            this.newThemeKey = '';
        },
        deleteThemeKey(key) {
            if (confirm(`Удалить группу '${key}'?`)) {
                delete this.config.themeAliases[key];
            }
        },
        deleteTheme(key) {
            // Alias for deleteThemeKey for backward compatibility if template calls it
            this.deleteThemeKey(key);
        },
        toggleGroup(key) {
            this.expandedGroups[key] = !this.expandedGroups[key];
        },
        expandAllGroups(expand) {
            const keys = Object.keys(this.groupedActiveProfiles);
            keys.forEach(key => {
                this.expandedGroups[key] = expand;
            });
        },
        handleProfileSelect(event, profile, groupProfiles) {
            const username = profile.username;
            const isSelected = this.selectedProfiles.includes(username);
            // Standard checkbox behavior toggles value. 
            // But since we use @click, we need to know what the NEW state will be.
            // If it was selected, it will become unselected.
            // Note: browser checkbox toggles visually on click immediately.
            // We just need to sync our array.

            let targetState = !isSelected;

            // Use event.shiftKey to detect range selection
            if (event.shiftKey && this.lastSelected) {
                const lastIdx = groupProfiles.findIndex(p => p.username === this.lastSelected);
                const currIdx = groupProfiles.findIndex(p => p.username === username);

                if (lastIdx !== -1 && currIdx !== -1) {
                    const start = Math.min(lastIdx, currIdx);
                    const end = Math.max(lastIdx, currIdx);

                    const range = groupProfiles.slice(start, end + 1);

                    range.forEach(p => {
                        const idx = this.selectedProfiles.indexOf(p.username);
                        if (targetState && idx === -1) {
                            this.selectedProfiles.push(p.username);
                        } else if (!targetState && idx !== -1) {
                            this.selectedProfiles.splice(idx, 1);
                        }
                    });
                } else {
                    this.toggleSelectionSingle(username, targetState);
                }
            } else {
                this.toggleSelectionSingle(username, targetState);
            }

            this.lastSelected = username;
        },
        toggleSelectionSingle(username, state) {
            const idx = this.selectedProfiles.indexOf(username);
            if (state && idx === -1) {
                this.selectedProfiles.push(username);
            } else if (!state && idx !== -1) {
                this.selectedProfiles.splice(idx, 1);
            }
        },
        toggleGroupSelection(profiles, checked) {
            if (checked) {
                // Add all profiles in group to selection
                profiles.forEach(p => {
                    if (!this.selectedProfiles.includes(p.username)) {
                        this.selectedProfiles.push(p.username);
                    }
                });
            } else {
                // Remove all profiles in group from selection
                profiles.forEach(p => {
                    const idx = this.selectedProfiles.indexOf(p.username);
                    if (idx > -1) this.selectedProfiles.splice(idx, 1);
                });
            }
        },
        triggerCleanup() {
            console.log('[Frontend] Cleanup button clicked');
            if (!confirm('Вы уверены? Это удалит ВСЕ запланированные, но еще не опубликованные посты. Нажмите ОК для очистки.')) {
                console.log('[Frontend] Cleanup cancelled by user');
                return;
            }

            console.log('[Frontend] Sending cleanup request to POST /api/cleanup...');
            fetch('/api/cleanup', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    console.log('[Frontend] Cleanup response:', data);
                    alert(data.message || 'Очистка запущена!');
                })
                .catch(e => {
                    console.error('[Frontend] Cleanup error:', e);
                    alert('Ошибка запуска очистки');
                });
        },
        formatCron(cron) {
            if (!cron) return 'Отключено';
            try {
                const parts = cron.split(' ');
                if (parts.length >= 2) {
                    const m = parts[0].padStart(2, '0');
                    const h = parts[1].padStart(2, '0');
                    return `${h}:${m}`;
                }
            } catch (e) { }
            return cron;
        },
        applyBulkCategory() {
            if (!this.bulkThemeKey) return alert('Введите название категории!');
            if (this.selectedProfiles.length === 0) return alert('Выберите профили!');

            const category = this.bulkThemeKey.toLowerCase().trim();

            let count = 0;
            this.config.profiles.forEach(p => {
                if (this.selectedProfiles.includes(p.username)) {
                    p.theme_key = category;
                    count++;
                }
            });

            this.saveConfig();
            this.selectedProfiles = []; // Clear selection
            this.bulkThemeKey = '';
            alert(`Успешно обновлено профилей: ${count}`);
        },
        async loadLogs() {
            this.logsLoading = true;
            try {
                const res = await fetch('/api/logs?lines=200');
                const data = await res.json();

                this.logsSuccess = data.success || false;
                this.logsMessage = data.message || '';
                this.logs = data.logs || [];
            } catch (e) {
                this.logsSuccess = false;
                this.logsMessage = e.message;
                this.logs = [];
            } finally {
                this.logsLoading = false;
            }
        }
    },
    watch: {
        logsAutoRefresh(newVal) {
            if (newVal) {
                // Start auto-refresh
                this.logsRefreshInterval = setInterval(() => {
                    if (this.currentTab === 'logs') {
                        this.loadLogs();
                    }
                }, 3000);
            } else {
                // Stop auto-refresh
                if (this.logsRefreshInterval) {
                    clearInterval(this.logsRefreshInterval);
                    this.logsRefreshInterval = null;
                }
            }
        },
        currentTab(newTab) {
            // Load logs when entering logs tab
            if (newTab === 'logs' && this.logs.length === 0) {
                this.loadLogs();
            }
        }
    }
}).mount('#app');
