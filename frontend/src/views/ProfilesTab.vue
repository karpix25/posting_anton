<script setup>
import { ref, computed, onMounted } from 'vue'
import { useConfigStore } from '../stores/config'
import axios from 'axios'

import { useStatsStore } from '../stores/stats'

const configStore = useConfigStore()
const statsStore = useStatsStore()

// UI State
const showActiveProfiles = ref(true)
const showDisabledProfiles = ref(false)
const expandedGroups = ref({})
const expandedGroupsDisabled = ref({})
const selectedProfiles = ref([])
const selectedDisabledProfiles = ref([])

// Bulk Action Models
const bulkThemeKey = ref('')
const bulkInstagramLimit = ref('')
const bulkTikTokLimit = ref('')
const bulkYoutubeLimit = ref('')

const bulkThemeKeyDisabled = ref('')
const bulkInstagramLimitDisabled = ref('')
const bulkTikTokLimitDisabled = ref('')
const bulkYoutubeLimitDisabled = ref('')

const availableThemes = computed(() => {
    const configThemes = configStore.config.themeAliases ? Object.keys(configStore.config.themeAliases) : []
    const yandexThemes = statsStore.stats.byCategory ? Object.keys(statsStore.stats.byCategory) : []
    
    // Merge and deduplicate
    const combined = new Set([...configThemes, ...yandexThemes])
    return Array.from(combined).sort()
})

// Grouping Logic
const groupProfiles = (profiles) => {
    const groups = {}
    profiles.forEach(p => {
        const key = (p.theme_key || 'Без темы').toLowerCase().trim()
        const displayKey = key.charAt(0).toUpperCase() + key.slice(1)
        if (!groups[displayKey]) groups[displayKey] = []
        groups[displayKey].push(p)
    })
    return Object.keys(groups).sort().reduce((acc, key) => {
        acc[key] = groups[key]
        return acc
    }, {})
}

const activeProfiles = computed(() => configStore.config.profiles?.filter(p => p.enabled !== false) || [])
const disabledProfiles = computed(() => configStore.config.profiles?.filter(p => p.enabled === false) || [])

const groupedActiveProfiles = computed(() => groupProfiles(activeProfiles.value))
const groupedDisabledProfiles = computed(() => groupProfiles(disabledProfiles.value))

// Helper to save config and refresh stats immediately
const updateConfigAndStats = async () => {
    await configStore.saveConfig()
    // Soft refresh of stats (no Yandex scan) to update categories/counts
    await statsStore.loadYandexStats(false) 
}

// Actions
const toggleGroup = (group, isDisabled = false) => {
    const target = isDisabled ? expandedGroupsDisabled.value : expandedGroups.value
    target[group] = !target[group]
}

const expandAllGroups = (expand, isDisabled = false) => {
    const target = isDisabled ? expandedGroupsDisabled : expandedGroups
    const groups = isDisabled ? groupedDisabledProfiles.value : groupedActiveProfiles.value
    const newVal = {}
    Object.keys(groups).forEach(k => newVal[k] = expand)
    target.value = newVal
}

const toggleProfileStatus = async (profile) => {
    profile.enabled = !profile.enabled
    await updateConfigAndStats()
    selectedProfiles.value = selectedProfiles.value.filter(u => u !== profile.username)
    selectedDisabledProfiles.value = selectedDisabledProfiles.value.filter(u => u !== profile.username)
}

const syncProfiles = async () => {
    if(!confirm('Синхронизировать профили с UploadPost?')) return;
    try {
        await axios.post('/api/profiles/sync')
        await configStore.fetchConfig()
        alert('Синхронизация завершена')
    } catch (e) {
        alert('Ошибка синхронизации')
    }
}

const fullResync = async () => {
    if (!confirm("ВНИМАНИЕ! \nЭто удалит все текущие настройки. Вы уверены?")) return;
    configStore.config.profiles = []
    await syncProfiles()
    await updateConfigAndStats()
}

const addProfile = async () => {
    const username = prompt("Введите username профиля:")
    if (!username) return
    configStore.config.profiles.push({
        username,
        enabled: true,
        platforms: ['instagram', 'tiktok', 'youtube'],
        instagramLimit: 0,
        tiktokLimit: 0,
        youtubeLimit: 0
    })
    await updateConfigAndStats()
}

// Bulk Actions
const applyBulkCategory = async (isDisabled = false) => {
    const list = isDisabled ? selectedDisabledProfiles.value : selectedProfiles.value
    const key = isDisabled ? bulkThemeKeyDisabled.value : bulkThemeKey.value
    if (!key || list.length === 0) return
    
    configStore.config.profiles.forEach(p => {
        if (list.includes(p.username)) p.theme_key = key
    })
    await updateConfigAndStats()

    // Clear selection
    if (isDisabled) selectedDisabledProfiles.value = []
    else selectedProfiles.value = []
}

const applyBulkLimits = async (isDisabled = false) => {
    const list = isDisabled ? selectedDisabledProfiles.value : selectedProfiles.value
    const ig = isDisabled ? bulkInstagramLimitDisabled.value : bulkInstagramLimit.value
    const tt = isDisabled ? bulkTikTokLimitDisabled.value : bulkTikTokLimit.value
    const yt = isDisabled ? bulkYoutubeLimitDisabled.value : bulkYoutubeLimit.value

    if (list.length === 0) return

    configStore.config.profiles.forEach(p => {
        if (list.includes(p.username)) {
            if (ig !== '') p.instagramLimit = ig
            if (tt !== '') p.tiktokLimit = tt
            if (yt !== '') p.youtubeLimit = yt
        }
    })
    await updateConfigAndStats()

    // Clear selection
    if (isDisabled) selectedDisabledProfiles.value = []
    else selectedProfiles.value = []
}

// Shift+Click Selection Logic
const lastSelectedUsername = ref(null)

const getFlatProfiles = (groupedProfiles) => {
    // Reconstruct the visual order of profiles based on sorted group keys
    return Object.keys(groupedProfiles).reduce((acc, groupKey) => {
        return acc.concat(groupedProfiles[groupKey])
    }, [])
}

const handleProfileSelection = (event, profile, isDisabled = false) => {
    const targetList = isDisabled ? selectedDisabledProfiles : selectedProfiles
    const currentUsername = profile.username
    
    // Standard Toggle happens via v-model, we just track logic here
    // But for Shift+Click we need to intervene
    
    if (event.shiftKey && lastSelectedUsername.value) {
        const flatList = getFlatProfiles(isDisabled ? groupedDisabledProfiles.value : groupedActiveProfiles.value)
        const lastIdx = flatList.findIndex(p => p.username === lastSelectedUsername.value)
        const currIdx = flatList.findIndex(p => p.username === currentUsername)
        
        if (lastIdx !== -1 && currIdx !== -1) {
            const start = Math.min(lastIdx, currIdx)
            const end = Math.max(lastIdx, currIdx)
            const rangeProfiles = flatList.slice(start, end + 1)
            
            // Determine target state based on the checkbox state AFTER the click (which triggered this)
            // Actually, with v-model, the value in 'selectedProfiles' might already be updated or not?
            // Safer to check the checked property of the input
            const isChecked = event.target.checked
            
            const rangeUsernames = rangeProfiles.map(p => p.username)
            
            if (isChecked) {
                // Add all in range
                // Use Set to avoid duplicates
                const newSet = new Set([...targetList.value, ...rangeUsernames])
                targetList.value = Array.from(newSet)
            } else {
                // Remove all in range
                targetList.value = targetList.value.filter(u => !rangeUsernames.includes(u))
            }
        }
    }
    
    lastSelectedUsername.value = currentUsername
}

onMounted(() => {
    // Ensure we have stats for categories
    if (!statsStore.stats.totalVideos) {
        statsStore.loadYandexStats(false)
    }
})
</script>

<template>
  <div>
    <!-- Header -->
    <div class="flex justify-between items-center mb-4">
        <h2 class="text-2xl font-bold">Социальные профили</h2>
        <div class="space-x-2">
            <button @click="syncProfiles" class="text-sm bg-blue-100 text-blue-700 px-3 py-1 rounded hover:bg-blue-200">🔄 Синхронизировать</button>
             <button @click="fullResync" class="text-sm bg-red-100 text-red-700 px-3 py-1 rounded hover:bg-red-200 border border-red-300">⚠️ Полный Сброс</button>
            <button @click="addProfile" class="text-sm bg-gray-200 px-3 py-1 rounded hover:bg-gray-300">+ Добавить</button>
        </div>
    </div>

    <!-- Active Profiles -->
    <div v-if="activeProfiles.length > 0">
        <div class="flex justify-between items-center mb-3 p-3 bg-gray-50 rounded">
            <button @click="showActiveProfiles = !showActiveProfiles" class="flex-1 text-left text-lg font-semibold flex items-center gap-2">
                 <span>🟢 Активные Профили ({{ activeProfiles.length }})</span>
                 <span class="text-xl">{{ showActiveProfiles ? '▼' : '▶' }}</span>
            </button>
             <div class="flex gap-2 text-sm" v-if="showActiveProfiles">
                  <button @click="expandAllGroups(true)" class="text-blue-600 hover:underline">Развернуть все</button>
                  <span class="text-gray-300">|</span>
                  <button @click="expandAllGroups(false)" class="text-blue-600 hover:underline">Свернуть все</button>
             </div>
        </div>
        
        <div v-if="showActiveProfiles">
            <!-- BULK BAR -->
            <div v-if="selectedProfiles.length > 0" class="sticky top-0 z-10 bg-blue-600 text-white p-3 rounded shadow-lg mb-4 flex flex-wrap gap-4 justify-between items-center">
                 <div class="font-bold">Выбрано: {{ selectedProfiles.length }}</div>
                 <div class="flex gap-4">
                     <div class="flex items-center gap-2">
                         <span class="text-sm">Категория:</span>
                         <select v-model="bulkThemeKey" class="text-black text-sm p-1 rounded">
                             <option disabled value="">Выбрать...</option>
                             <option v-for="theme in availableThemes" :key="theme" :value="theme">{{ theme }}</option>
                         </select>
                         <button @click="applyBulkCategory(false)" class="bg-white text-blue-600 px-2 rounded font-bold text-sm">OK</button>
                     </div>
                     <div class="flex items-center gap-2 pl-4 border-l border-blue-400">
                         <span class="text-sm">Лимиты (IG/TT/YT):</span>
                         <input type="number" v-model.number="bulkInstagramLimit" class="w-10 text-black text-center p-1 rounded text-sm" placeholder="-">
                         <input type="number" v-model.number="bulkTikTokLimit" class="w-10 text-black text-center p-1 rounded text-sm" placeholder="-">
                         <input type="number" v-model.number="bulkYoutubeLimit" class="w-10 text-black text-center p-1 rounded text-sm" placeholder="-">
                         <button @click="applyBulkLimits(false)" class="bg-white text-blue-600 px-2 rounded font-bold text-sm">OK</button>
                     </div>
                 </div>
            </div>

            <!-- Groups -->
            <div v-for="(profiles, group) in groupedActiveProfiles" :key="group" class="mb-4 border border-gray-200 rounded overflow-hidden">
                <div class="bg-gray-100 p-3 flex justify-between items-center cursor-pointer hover:bg-gray-200" @click="toggleGroup(group)">
                    <div class="font-bold">{{ group }} ({{ profiles.length }})</div>
                    <div>{{ expandedGroups[group] ? '▼' : '▶' }}</div>
                </div>
                
                <div v-show="expandedGroups[group]" class="bg-white">
                    <table class="w-full text-sm">
                        <thead class="bg-gray-50 text-xs uppercase text-gray-500">
                             <tr>
                                 <th class="p-3 w-8"></th>
                                 <th class="p-3 text-left">Профиль</th>
                                 <th class="p-3 text-left">Категория</th>
                                 <th class="p-3 text-center">IG</th>
                                 <th class="p-3 text-center">TT</th>
                                 <th class="p-3 text-center">YT</th>
                                 <th class="p-3 text-right">Статус</th>
                             </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">
                            <tr v-for="profile in profiles" :key="profile.username" class="hover:bg-blue-50">
                                <td class="p-3 text-center">
                                    <input type="checkbox" :value="profile.username" v-model="selectedProfiles" @click="handleProfileSelection($event, profile, false)">
                                </td>
                                <td class="p-3">
                                    <div class="font-medium text-gray-900">{{ profile.username }}</div>
                                    <div class="flex gap-1 mt-1 flex-wrap">
                                        <span v-if="profile.platforms?.includes('instagram')" class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-pink-100 text-pink-700 border border-pink-200">Instagram</span>
                                        <span v-if="profile.platforms?.includes('tiktok')" class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-100 text-gray-800 border border-gray-200">TikTok</span>
                                        <span v-if="profile.platforms?.includes('youtube')" class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-700 border border-red-200">YouTube</span>
                                    </div>
                                </td>
                                <td class="p-3">
                                    <select v-model="profile.theme_key" @change="updateConfigAndStats" class="border rounded p-1 text-xs w-32">
                                        <option value="">Без темы</option>
                                        <option v-for="theme in availableThemes" :key="theme" :value="theme">{{ theme }}</option>
                                    </select>
                                </td>
                                <td class="p-3 text-center">
                                    <input type="number" v-model.number="profile.instagramLimit" @change="updateConfigAndStats" class="w-12 border rounded text-center p-1">
                                </td>
                                 <td class="p-3 text-center">
                                    <input type="number" v-model.number="profile.tiktokLimit" @change="updateConfigAndStats" class="w-12 border rounded text-center p-1">
                                </td>
                                 <td class="p-3 text-center">
                                    <input type="number" v-model.number="profile.youtubeLimit" @change="updateConfigAndStats" class="w-12 border rounded text-center p-1">
                                </td>
                                <td class="p-3 text-right">
                                    <button @click="toggleProfileStatus(profile)" class="text-green-600 hover:text-green-800 font-bold text-xs bg-green-100 px-2 py-1 rounded">
                                        Активен
                                    </button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
  </div>
</template>
