
<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useConfigStore } from '../stores/configStore';
import { statsApi } from '../api/stats'; 
// Note: systemApi import path might be wrong, check export names.
// Correcting import source if needed:
// import { systemApi } from '../api/system'; 
import { formatCron } from '../utils/format';
import { Chart } from 'chart.js/auto';

const configStore = useConfigStore();
const stats = ref<any>(null);
const publishingStats = ref<any>(null);
const loading = ref(false);
const errorList = ref<any[]>([]);
const activityHistory = ref<any[]>([]);
let chartInstance: Chart | null = null;
const chartCanvas = ref<HTMLCanvasElement | null>(null);

const activeProfilesCount = computed(() => {
    return configStore.config?.profiles.filter(p => p.enabled).length || 0;
});

const totalProfilesCount = computed(() => {
    return configStore.config?.profiles.length || 0;
});

// Group errors by message
const groupedErrors = computed(() => {
    if (!errorList.value.length) return [];
    
    const groups: Record<string, { count: number, last_seen: string, profiles: Set<string> }> = {};
    
    for (const err of errorList.value) {
        // Extract meaningful message. 
        // In DB 'status' might happen to be 'failed', and real error is in 'details'?
        // Assuming the API returns objects like { status: 'failed', details: 'Auth Error', ... }
        // If the previous UI showed 'error', check what field it used.
        // Let's rely on 'details' or 'error_message' if present, fallback to status.
        const msg = err.details || err.error_message || err.status || 'Unknown Error';
        
        if (!groups[msg]) {
            groups[msg] = { count: 0, last_seen: err.posted_at, profiles: new Set() };
        }
        groups[msg].count++;
        if (err.posted_at > groups[msg].last_seen) groups[msg].last_seen = err.posted_at;
        if (err.profile_username) groups[msg].profiles.add(err.profile_username);
    }
    
    return Object.entries(groups).map(([msg, data]) => ({
        message: msg,
        count: data.count,
        last_seen: data.last_seen,
        profiles_count: data.profiles.size
    })).sort((a, b) => b.count - a.count);
});

async function loadStats() {
    loading.value = true;
    try {
        // Import API correctly if they are in different files
        const { systemApi: sysApi } = await import('../api/system');

        const [today, pubStats, history, recentErrors] = await Promise.all([
             statsApi.getToday(),
             statsApi.getPublishing(),
             statsApi.getHistory(14), // 2 weeks
             sysApi.getRecentErrors(100)
        ]);
        
        stats.value = today;
        publishingStats.value = pubStats;
        activityHistory.value = history.history || [];
        errorList.value = recentErrors.errors || [];
        
        renderChart();
        
    } catch (e) {
        console.error(e);
    } finally {
        loading.value = false;
    }
}

function renderChart() {
    if (!chartCanvas.value || !activityHistory.value.length) return;
    
    if (chartInstance) chartInstance.destroy();
    
    // Reverse to show oldest to newest
    const data = [...activityHistory.value].reverse();
    
    // Calculate Plan (approximate)
    // "Plan" depends on active profiles * limits. It's static per day usually.
    // We can take it from publishingStats.total_expected_posts for 'today', 
    // but for history we might just plot a line?
    // Or just show Success vs Failed bars.
    
    const labels = data.map(d => d.date.split('-').slice(1).join('.')); // MM.DD
    const successData = data.map(d => d.success);
    const failedData = data.map(d => d.failed);
    
    const ctx = chartCanvas.value.getContext('2d');
    if (!ctx) return;

    chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Успешно',
                    data: successData,
                    backgroundColor: '#4ade80', // green-400
                    borderRadius: 4,
                },
                {
                    label: 'Ошибки',
                    data: failedData,
                    backgroundColor: '#f87171', // red-400
                    borderRadius: 4,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true }
            },
            plugins: {
                legend: { position: 'bottom' }
            }
        }
    });
}

onMounted(() => {
    loadStats();
});
</script>

<template>
    <div>
        <h2 class="text-2xl font-bold mb-4">Дашборд</h2>
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
            
            <!-- Activity Chart -->
            <div class="bg-white border rounded shadow-sm p-4 flex flex-col">
                <h3 class="font-bold mb-4 text-gray-700">Активность публикаций (14 дней)</h3>
                <div class="flex-1 min-h-[300px] relative">
                    <canvas ref="chartCanvas"></canvas>
                </div>
                <div class="mt-4 text-xs text-gray-500 text-center" v-if="publishingStats">
                     Всего постов в базе: {{ publishingStats.total_actual_posts }} 
                     (План на сегодня: ~{{ publishingStats.total_expected_posts }})
                </div>
            </div>

            <!-- Errors (Grouped) -->
            <div class="bg-white border rounded shadow-sm p-4">
                <h3 class="font-bold mb-4 text-red-600 flex justify-between items-center">
                    <span>Топ ошибок</span>
                    <span class="text-xs font-normal text-gray-500 bg-gray-100 px-2 py-1 rounded">Группировка</span>
                </h3>
                
                <div v-if="groupedErrors.length === 0" class="text-gray-500 text-sm italic py-10 text-center">
                    Ошибок нет
                </div>
                
                <ul v-else class="space-y-4 max-h-[400px] overflow-y-auto pr-2">
                    <li v-for="(group, idx) in groupedErrors" :key="idx" class="border-b pb-2 last:border-0 relative">
                        <div class="flex justify-between items-start mb-1">
                            <span class="font-medium text-gray-800 text-sm break-words w-3/4">{{ group.message }}</span>
                            <span class="bg-red-100 text-red-800 text-xs font-bold px-2 py-1 rounded-full">
                                {{ group.count }}
                            </span>
                        </div>
                        <div class="flex justify-between text-xs text-gray-400">
                             <span>Затронуто профилей: {{ group.profiles_count }}</span>
                             <span>Последняя: {{ new Date(group.last_seen).toLocaleTimeString() }}</span>
                        </div>
                    </li>
                </ul>
            </div>
        </div>
    </div>
</template>
