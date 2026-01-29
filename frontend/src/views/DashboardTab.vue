
<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useConfigStore } from '../stores/configStore';
import { statsApi } from '../api/stats';
import { systemApi } from '../api/system'; 
// Note: systemApi import path might be wrong, check export names
import { formatCron } from '../utils/format';

const configStore = useConfigStore();
const stats = ref<any>(null);
const loading = ref(false);
const errorList = ref<any[]>([]);

const activeProfilesCount = computed(() => {
    return configStore.config?.profiles.filter(p => p.enabled).length || 0;
});

const totalProfilesCount = computed(() => {
    return configStore.config?.profiles.length || 0;
});

async function loadStats() {
    loading.value = true;
    try {
        const [today, errors] = await Promise.all([
             statsApi.getToday(),
             systemApi.getRecentErrors(10)
        ]);
        stats.value = today;
        errorList.value = errors.errors || [];
    } catch (e) {
        console.error(e);
    } finally {
        loading.value = false;
    }
}

onMounted(() => {
    loadStats();
});
</script>

<template>
    <div>
        <h2 class="text-2xl font-bold mb-4">Статус</h2>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
            <div class="bg-blue-50 p-4 rounded border border-blue-100">
                <div class="text-sm text-gray-500">Следующий запуск</div>
                <div class="text-lg font-mono">{{ formatCron(configStore.config?.cronSchedule || '') }}</div>
            </div>
            <div class="bg-purple-50 p-4 rounded border border-purple-100">
                <div class="text-sm text-gray-500">Активные профили</div>
                <div class="text-2xl font-bold text-purple-600">
                    {{ activeProfilesCount }} / {{ totalProfilesCount }}
                </div>
            </div>
            <div class="bg-green-50 p-4 rounded border border-green-100" v-if="stats">
                <div class="text-sm text-gray-500">Сегодня опубликовано</div>
                <div class="text-2xl font-bold text-green-600">
                    {{ stats.success_count }} <span class="text-sm font-normal text-gray-400">/ {{ stats.failed_count }} ошибок</span>
                </div>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <!-- Errors -->
            <div class="bg-white border rounded shadow-sm p-4">
                <h3 class="font-bold mb-4 text-red-600">Последние ошибки</h3>
                <div v-if="errorList.length === 0" class="text-gray-500 text-sm italic">Ошибок нет</div>
                <ul v-else class="space-y-2 max-h-64 overflow-y-auto">
                    <li v-for="err in errorList" :key="err.id" class="text-sm border-b pb-2 last:border-0">
                        <div class="flex justify-between text-xs text-gray-400 mb-1">
                            <span>{{ new Date(err.posted_at).toLocaleString() }}</span>
                            <span>{{ err.profile_username }}</span>
                        </div>
                        <div class="text-gray-700 break-words">{{ err.status }}</div>
                    </li>
                </ul>
            </div>

            <!-- Chart placeholder -->
            <div class="bg-white border rounded shadow-sm p-4 flex items-center justify-center bg-gray-50">
                <span class="text-gray-400">График активности (в разработке)</span>
            </div>
        </div>
    </div>
</template>
