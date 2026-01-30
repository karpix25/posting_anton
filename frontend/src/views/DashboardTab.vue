<script setup>
import { onMounted, computed, ref } from 'vue'
import { useConfigStore } from '../stores/config'
import { useStatsStore } from '../stores/stats'
import { Clock, Users, HardDrive, Play, FlaskConical, Trash2 } from 'lucide-vue-next'
import axios from 'axios'

const configStore = useConfigStore()
const statsStore = useStatsStore()

const isRunning = ref(false)

const formatCron = (cron) => {
  // Simple cron formatter (placeholder)
  if (!cron) return 'Не задано'
  return cron
}

onMounted(() => {
  statsStore.loadTodayStats()
  statsStore.loadPublishingStats()
  statsStore.loadHistory()
  statsStore.loadBrandStats()
  statsStore.fetchGlobalAnalytics()
  statsStore.loadYandexStats(false)
  statsStore.checkHealth()
  statsStore.fetchErrors()
})

const refreshStats = () => {
    statsStore.loadTodayStats()
    statsStore.loadPublishingStats()
    statsStore.loadHistory()
    statsStore.loadBrandStats()
    statsStore.fetchGlobalAnalytics()
}

const triggerRun = async (testMode = false) => {
    const modeText = testMode ? '🧪 ТЕСТОВОМ режиме (по 1 посту на платформу)' : '🚀 ПОЛНОМ цикле'
    if (!confirm(`Запустить автоматизацию в ${modeText}?`)) return

    isRunning.value = true
    try {
        await axios.post('/api/schedule/run', { run_for_today_only: testMode })
        alert('✅ Автоматизация запущена! Проверьте логи для отслеживания прогресса.')
    } catch (e) {
        console.error(e)
        alert('❌ Ошибка запуска: ' + (e.response?.data?.detail || e.message))
    } finally {
        isRunning.value = false
    }
}

const triggerCleanup = async () => {
    if (!confirm('🗑️ Вы уверены? Это удалит ВСЕ запланированные, но еще не опубликованные посты.')) return

    isRunning.value = true
    try {
        const res = await axios.post('/api/cleanup')
        alert('✅ ' + (res.data.message || 'Очередь очищена'))
    } catch (e) {
        console.error(e)
        alert('❌ Ошибка очистки: ' + (e.response?.data?.detail || e.message))
    } finally {
        isRunning.value = false
    }
}
</script>

<template>
  <div class="space-y-8">
    <!-- Status Section -->
    <div>
      <h2 class="text-2xl font-bold mb-4">Статус</h2>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <!-- Next Run -->
        <div class="bg-blue-50 p-4 rounded-lg border border-blue-100 flex items-center justify-between">
          <div>
            <div class="text-sm text-gray-500">Следующий запуск</div>
            <div class="text-lg font-mono font-medium">{{ formatCron(configStore.config.cronSchedule) }}</div>
          </div>
          <Clock class="text-blue-300 w-8 h-8" />
        </div>

        <!-- Active Profiles -->
        <div class="bg-purple-50 p-4 rounded-lg border border-purple-100 flex items-center justify-between">
          <div>
            <div class="text-sm text-gray-500">Активные профили</div>
            <div class="text-lg font-bold">{{ configStore.config.profiles?.filter(p => p.enabled !== false).length || 0 }}</div>
          </div>
          <Users class="text-purple-300 w-8 h-8" />
        </div>

        <!-- Videos on Disk -->
         <div class="bg-indigo-50 p-4 rounded-lg border border-indigo-100 flex flex-col justify-between">
            <div class="flex justify-between items-start">
               <div>
                  <div class="text-sm text-gray-500">Видео на диске</div>
                  <div class="text-lg font-bold">{{ statsStore.stats.totalVideos || 0 }}</div>
               </div>
               <HardDrive class="text-indigo-300 w-8 h-8" />
            </div>
             <button @click="statsStore.loadYandexStats(true)" class="mt-2 text-xs bg-indigo-200 hover:bg-indigo-300 px-2 py-1 rounded text-indigo-800 self-start transition">
                  Синхронизировать
             </button>
         </div>
      </div>

      <!-- Actions Bar -->
      <div class="flex flex-wrap gap-4">
        <button 
            @click="triggerRun(false)" 
            :disabled="isRunning"
            class="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-6 py-3 rounded-lg text-lg font-semibold shadow-sm flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
        >
            <Play class="w-5 h-5" />
            Запустить Сейчас
        </button>
        
        <button 
            @click="triggerRun(true)" 
            :disabled="isRunning"
            class="bg-amber-500 hover:bg-amber-600 text-white px-6 py-3 rounded-lg text-lg font-semibold shadow-sm flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            title="Запустить только по 1 посту на платформу"
        >
            <FlaskConical class="w-5 h-5" />
            Тест (1 пост)
        </button>
        
        <button 
            @click="triggerCleanup" 
            :disabled="isRunning"
            class="bg-red-100 hover:bg-red-200 text-red-700 border border-red-300 px-6 py-3 rounded-lg text-lg font-semibold shadow-sm flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            title="Удалить запланированные посты"
        >
            <Trash2 class="w-5 h-5" />
            Сброс
        </button>
      </div>
    </div>
    
     <!-- General Publishing Stats (with Date Filter) -->
    <div class="bg-gradient-to-br from-indigo-50 to-purple-50 border border-indigo-200 rounded-xl p-6 shadow-sm">
      <div class="flex justify-between items-center mb-4">
        <h3 class="text-lg font-bold text-indigo-900">📊 Общая статистика публикаций</h3>
        <div class="flex items-center gap-2">
            <div class="flex items-center gap-1 text-sm text-indigo-800">
               <span class="text-xs">С:</span>
               <input type="date" v-model="statsStore.dateFrom" class="p-1 rounded border border-indigo-200 text-xs text-indigo-800 bg-white/50">
               <span class="text-xs">По:</span>
               <input type="date" v-model="statsStore.dateTo" class="p-1 rounded border border-indigo-200 text-xs text-indigo-800 bg-white/50">
            </div>
            <button @click="refreshStats" class="text-xs bg-indigo-200 hover:bg-indigo-300 px-3 py-1 rounded text-indigo-800 transition">
               🔄 Обновить
            </button>
        </div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
         <!-- Total Profiles -->
         <div class="bg-white/60 p-3 rounded border border-indigo-100">
            <div class="text-xs text-indigo-600 uppercase font-bold tracking-wider">Профили</div>
            <div class="text-2xl font-bold text-indigo-900 mt-1">{{ statsStore.publishingStats?.total_profiles || 0 }}</div>
            <div class="text-xs text-indigo-400">Активных: {{ statsStore.publishingStats?.active_profiles || 0 }}</div>
         </div>
         <!-- Expected -->
         <div class="bg-white/60 p-3 rounded border border-indigo-100">
            <div class="text-xs text-indigo-600 uppercase font-bold tracking-wider">План постов</div>
            <div class="text-2xl font-bold text-indigo-900 mt-1">{{ statsStore.publishingStats?.total_expected_posts || 0 }}</div>
         </div>
         <!-- Actual -->
         <div class="bg-white/60 p-3 rounded border border-indigo-100">
             <div class="text-xs text-indigo-600 uppercase font-bold tracking-wider">Создано</div>
             <div class="text-2xl font-bold text-indigo-900 mt-1">{{ statsStore.publishingStats?.total_actual_posts || 0 }}</div>
         </div>
         <!-- Success Rate -->
         <div class="bg-white/60 p-3 rounded border border-indigo-100">
             <div class="text-xs text-indigo-600 uppercase font-bold tracking-wider">Успешность</div>
             <div class="text-2xl font-bold text-indigo-900 mt-1">{{ statsStore.publishingStats?.avg_success_rate || 0 }}%</div>
         </div>
      </div>
      
       <!-- Detailed Stats Grid -->
       <div class="grid grid-cols-3 gap-6">
           <div class="space-y-2">
              <div class="text-xs font-bold text-gray-500 uppercase">По статусу</div>
               <div v-for="(count, status) in statsStore.publishingStats?.posts_by_status" :key="status" class="flex justify-between text-sm">
                   <span class="capitalize" :class="{
                       'text-green-600': status === 'success',
                       'text-red-600': status === 'failed',
                       'text-blue-600': status === 'queued'
                   }">{{ status }}</span>
                   <span class="font-bold">{{ count }}</span>
               </div>
           </div>
           
           <div class="space-y-2">
              <div class="text-xs font-bold text-gray-500 uppercase">По платформам</div>
               <div v-for="(count, platform) in statsStore.publishingStats?.posts_by_platform" :key="platform" class="flex justify-between text-sm">
                   <span class="capitalize">{{ platform }}</span>
                   <span class="font-bold">{{ count }}</span>
               </div>
           </div>
       </div>
    </div>
    
    <!-- Platform Analytics Cards (Placeholder for brevity, can expand) -->
    <!-- You can add Instagram/TikTok/YouTube cards here mirroring index.html lines 1000+ -->

    <!-- Daily History Table -->
    <div>
        <h3 class="text-xl font-bold mb-4">История Публикаций (30 дней)</h3>
        <div class="bg-white border rounded-lg overflow-hidden shadow-sm">
            <table class="w-full text-sm text-left">
                <thead class="bg-gray-50 text-gray-500 uppercase font-bold text-xs">
                    <tr>
                        <th class="px-6 py-3">Дата</th>
                        <th class="px-6 py-3 text-center text-green-700">Опубликовано</th>
                        <th class="px-6 py-3 text-center text-red-700">Ошибки</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                    <tr v-for="day in statsStore.history" :key="day.date" class="hover:bg-gray-50">
                        <td class="px-6 py-4 font-medium text-gray-900">{{ day.date }}</td>
                         <td class="px-6 py-4 text-center">
                            <span class="bg-green-100 text-green-800 px-3 py-1 rounded-full font-bold">{{ day.success || 0 }}</span>
                        </td>
                        <td class="px-6 py-4 text-center">
                            <span v-if="day.failed > 0" class="bg-red-100 text-red-800 px-3 py-1 rounded-full font-bold">{{ day.failed }}</span>
                            <span v-else class="text-gray-300">-</span>
                        </td>
                    </tr>
                     <tr v-if="statsStore.history.length === 0">
                      <td colspan="3" class="px-6 py-8 text-center text-gray-500">
                        Нет данных за последние 30 дней
                      </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
  </div>
</template>
