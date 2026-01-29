
<script setup lang="ts">
import { ref, computed } from 'vue';
import { useConfigStore } from '../stores/configStore';
import { profilesApi } from '../api/profiles';

const configStore = useConfigStore();
const searchQuery = ref('');
const showDisabled = ref(false);
const syncing = ref(false);

const filteredProfiles = computed(() => {
    if (!configStore.config) return [];
    let list = configStore.config.profiles;
    
    if (!showDisabled.value) {
        list = list.filter(p => p.enabled);
    }
    
    if (searchQuery.value) {
        const q = searchQuery.value.toLowerCase();
        list = list.filter(p => p.username.toLowerCase().includes(q) || 
                                (p.theme_key && p.theme_key.toLowerCase().includes(q)));
    }
    
    return list;
});

async function handleSync() {
    syncing.value = true;
    try {
        const res = await profilesApi.sync();
        if (res.success) {
            let msg = `Синхронизация завершена!\nВсего: ${res.stats?.total || 0}`;
            if (res.stats) {
                msg += `\nДобавлено: ${res.stats.added}\nУдалено: ${res.stats.removed}`;
            }
            alert(msg);
            await configStore.loadConfig(); // Reload to see changes
        } else {
            alert('Ошибка: ' + res.error);
        }
    } catch (e: any) {
        alert('Ошибка сети: ' + e.message);
    } finally {
        syncing.value = false;
    }
}

function toggleEnable(username: string, current: boolean) {
    configStore.updateProfile(username, { enabled: !current });
}
</script>

<template>
    <div>
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-2xl font-bold">Профили ({{ filteredProfiles.length }})</h2>
            <button @click="handleSync" :disabled="syncing"
                class="bg-blue-100 text-blue-700 px-4 py-2 rounded hover:bg-blue-200 transition-colors flex items-center gap-2">
                <span v-if="syncing" class="animate-spin">🔄</span>
                <span v-else>🔄</span>
                Синхронизировать (UploadPost)
            </button>
        </div>

        <!-- Filters -->
        <div class="flex gap-4 mb-4 bg-gray-50 p-3 rounded">
            <input v-model="searchQuery" type="text" placeholder="Поиск по имени/теме..." 
                   class="border p-2 rounded flex-1">
            <label class="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" v-model="showDisabled" class="w-4 h-4">
                <span>Показывать выключенные</span>
            </label>
        </div>

        <!-- Table -->
        <div class="bg-white rounded shadow overflow-hidden">
            <table class="w-full text-left border-collapse">
                <thead class="bg-gray-100 text-gray-600 uppercase text-xs">
                    <tr>
                        <th class="p-3 border-b">Статус</th>
                        <th class="p-3 border-b">Аккаунт</th>
                        <th class="p-3 border-b">Тема</th>
                        <th class="p-3 border-b">Платформы</th>
                        <th class="p-3 border-b">Лимиты (Inst/TT/YT)</th>
                    </tr>
                </thead>
                <tbody class="divide-y">
                    <tr v-for="p in filteredProfiles" :key="p.username" class="hover:bg-gray-50 transition-colors"
                        :class="{'opacity-60 bg-gray-50': !p.enabled}">
                        <td class="p-3">
                            <button @click="toggleEnable(p.username, p.enabled)" 
                                class="w-8 h-5 rounded-full relative transition-colors duration-200"
                                :class="p.enabled ? 'bg-green-500' : 'bg-gray-300'">
                                <span class="absolute w-3 h-3 bg-white rounded-full top-1 transition-all duration-200"
                                      :class="p.enabled ? 'left-4' : 'left-1'"></span>
                            </button>
                        </td>
                        <td class="p-3 font-medium">{{ p.username }}</td>
                        <td class="p-3 text-sm">
                            <input type="text" v-model="p.theme_key" @change="configStore.hasUnsavedChanges = true"
                                   class="border rounded px-2 py-1 w-full max-w-[150px] focus:ring-2 focus:ring-blue-100 outline-none" 
                                   placeholder="Без темы">
                        </td>
                        <td class="p-3">
                             <div class="flex gap-1">
                                 <span v-for="plat in p.platforms" :key="plat" 
                                       class="px-1.5 py-0.5 rounded text-xs bg-gray-200 text-gray-700">
                                    {{ plat.substring(0,2).toUpperCase() }}
                                 </span>
                             </div>
                        </td>
                        <td class="p-3 flex gap-2">
                             <input type="number" v-model.number="p.instagramLimit" placeholder="-" 
                                    class="w-12 border rounded px-1 text-center text-sm" @change="configStore.hasUnsavedChanges = true">
                             <input type="number" v-model.number="p.tiktokLimit" placeholder="-"
                                    class="w-12 border rounded px-1 text-center text-sm" @change="configStore.hasUnsavedChanges = true">
                             <input type="number" v-model.number="p.youtubeLimit" placeholder="-"
                                    class="w-12 border rounded px-1 text-center text-sm" @change="configStore.hasUnsavedChanges = true">
                        </td>
                    </tr>
                </tbody>
            </table>
            
            <div v-if="filteredProfiles.length === 0" class="text-center py-8 text-gray-500">
                Ничего не найдено
            </div>
             <div v-if="filteredProfiles.length > 100" class="text-center py-2 text-xs text-gray-400 bg-gray-50 border-t">
                Показано {{ filteredProfiles.length }} из {{ configStore.config?.profiles.length }}
            </div>
        </div>
    </div>
</template>
