
<script setup lang="ts">
import { ref, computed } from 'vue';
import { useConfigStore } from '../stores/configStore';
import { systemApi } from '../api/system';

const configStore = useConfigStore();
const cleaning = ref(false);

const foldersText = computed({
    get: () => configStore.config?.yandexFolders.join('\n') || '',
    set: (val) => {
        if (configStore.config) {
            configStore.config.yandexFolders = val.split('\n').map(s => s.trim()).filter(s => s);
            configStore.hasUnsavedChanges = true;
        }
    }
});

async function handleCleanup() {
    if (!confirm('Вы уверены? Это удалит все запланированные (queued) посты.')) return;
    cleaning.value = true;
    try {
        const res = await systemApi.cleanup();
        alert(res.message);
    } catch (e: any) {
        alert('Ошибка: ' + e.message);
    } finally {
        cleaning.value = false;
    }
}
</script>

<template>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8" v-if="configStore.config">
        
        <!-- General Settings -->
        <div class="space-y-6">
            <section class="bg-white p-6 rounded shadow border">
                <h3 class="font-bold text-lg mb-4">📂 Папки Яндекс.Диска</h3>
                <p class="text-sm text-gray-500 mb-2">Каждая папка с новой строки. Начинайте с <code>disk:/</code></p>
                <textarea v-model="foldersText" rows="6" 
                    class="w-full border rounded p-2 font-mono text-sm bg-gray-50 focus:ring-2 focus:ring-blue-100 outline-none"></textarea>
            </section>

             <section class="bg-white p-6 rounded shadow border">
                <h3 class="font-bold text-lg mb-4">⏰ Расписание (Cron)</h3>
                <div class="flex gap-4 items-center">
                    <input type="text" v-model="configStore.config.cronSchedule" 
                          @change="configStore.hasUnsavedChanges = true"
                          class="border rounded px-3 py-2 flex-1 font-mono" placeholder="MM HH * * *">
                </div>
                 <p class="text-xs text-gray-400 mt-2">Формат: Минуты Часы * * * (Москва UTC+3)</p>
            </section>
        </div>

        <!-- Global Limits & Defaults -->
        <div class="space-y-6">
            <section class="bg-white p-6 rounded shadow border">
                <h3 class="font-bold text-lg mb-4">🌐 Глобальные Лимиты</h3>
                 <p class="text-xs text-gray-400 mb-4">Применяются, если у профиля стоит 0</p>
                
                <div class="grid grid-cols-3 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">Instagram</label>
                        <input type="number" v-model="configStore.config.limits.instagram" 
                               @change="configStore.hasUnsavedChanges = true"
                               class="w-full border rounded p-2">
                    </div>
                     <div>
                        <label class="block text-sm font-medium mb-1">TikTok</label>
                        <input type="number" v-model="configStore.config.limits.tiktok" 
                               @change="configStore.hasUnsavedChanges = true"
                               class="w-full border rounded p-2">
                    </div>
                     <div>
                        <label class="block text-sm font-medium mb-1">YouTube</label>
                        <input type="number" v-model="configStore.config.limits.youtube" 
                               @change="configStore.hasUnsavedChanges = true"
                               class="w-full border rounded p-2">
                    </div>
                </div>
                 <div class="mt-4">
                    <label class="block text-sm font-medium mb-1">Дней генерировать</label>
                    <input type="number" v-model="configStore.config.daysToGenerate" 
                           @change="configStore.hasUnsavedChanges = true"
                           class="w-full border rounded p-2 max-w-[100px]">
                </div>
            </section>

             <section class="bg-white p-6 rounded shadow border border-red-100">
                <h3 class="font-bold text-lg mb-4 text-red-600">Опасная зона</h3>
                <button @click="handleCleanup" 
                        class="w-full border border-red-300 text-red-700 hover:bg-red-50 px-4 py-2 rounded transition-colors flex justify-center items-center gap-2">
                     <span v-if="cleaning" class="animate-spin">⏳</span>
                    🗑️ Очистить очередь (Failed/Queued)
                </button>
            </section>
        </div>
    </div>
</template>
