
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { systemApi } from '../api/system';

const logs = ref<string[]>([]);
const loading = ref(false);
const autoRefresh = ref(false);
let interval: any = null;

async function fetchLogs() {
    loading.value = true;
    try {
        const res = await systemApi.getLogs(200);
        if (res.success) {
            logs.value = res.logs;
        }
    } finally {
        loading.value = false;
    }
}

function toggleAutoRefresh() {
    autoRefresh.value = !autoRefresh.value;
    if (autoRefresh.value) {
        interval = setInterval(fetchLogs, 5000); // 5 sec
    } else {
        clearInterval(interval);
    }
}

onMounted(() => fetchLogs());

onUnmounted(() => {
    if (interval) clearInterval(interval);
});
</script>

<template>
    <div class="h-full flex flex-col">
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-2xl font-bold">Логи сервера</h2>
            <div class="flex gap-2">
                <button @click="fetchLogs" class="bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded">
                    🔄 Обновить
                </button>
                <button @click="toggleAutoRefresh" 
                        :class="autoRefresh ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'"
                        class="px-3 py-1 rounded border transition-colors">
                    {{ autoRefresh ? 'Auto: ON' : 'Auto: OFF' }}
                </button>
            </div>
        </div>

        <div class="flex-1 bg-gray-900 text-green-400 font-mono text-xs p-4 rounded overflow-auto shadow-inner h-[600px] whitespace-pre-wrap leading-tight">
            <div v-if="logs.length === 0" class="text-gray-500 italic text-center mt-10">Логов нет или не удалось загрузить.</div>
            <div v-for="(line, i) in logs" :key="i">{{ line }}</div>
        </div>
    </div>
</template>
