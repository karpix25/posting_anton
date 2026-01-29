<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const logs = ref([])
const logsLoading = ref(false)
const logsSuccess = ref(true)
const logsMessage = ref('')
const logsAutoRefresh = ref(false)
let refreshInterval = null

const loadLogs = async () => {
    logsLoading.value = true
    try {
        const res = await fetch('/api/logs')
        if (res.ok) {
            const data = await res.json()
            if (data.success) {
                logs.value = data.logs
                logsSuccess.value = true
            } else {
                logsSuccess.value = false
                logsMessage.value = data.message || 'Ошибка загрузки логов'
            }
        } else {
            logsSuccess.value = false
            logsMessage.value = `HTTP Error: ${res.status}`
        }
    } catch (e) {
        logsSuccess.value = false
        logsMessage.value = e.message
    } finally {
        logsLoading.value = false
    }
}

const toggleAutoRefresh = () => {
    logsAutoRefresh.value = !logsAutoRefresh.value
    if (logsAutoRefresh.value) {
        loadLogs()
        refreshInterval = setInterval(loadLogs, 5000)
    } else {
        if (refreshInterval) clearInterval(refreshInterval)
        refreshInterval = null
    }
}

onMounted(() => {
    loadLogs()
})

onUnmounted(() => {
    if (refreshInterval) clearInterval(refreshInterval)
})
</script>

<template>
  <div>
      <div class="flex justify-between items-center mb-4">
          <h2 class="text-2xl font-bold">Системные логи</h2>
          <div class="flex items-center gap-4">
              <label class="flex items-center cursor-pointer text-sm">
                  <input type="checkbox" :checked="logsAutoRefresh" @change="toggleAutoRefresh" class="mr-2">
                  Автообновление (5с)
              </label>
              <button @click="loadLogs" class="text-sm bg-blue-500 text-white px-3 py-1 rounded hover:bg-blue-600">
                  🔄 Обновить
              </button>
          </div>
      </div>
      
      <div v-if="logsLoading && logs.length === 0" class="text-center text-gray-500 py-8">Загрузка логов...</div>
      
      <div v-else-if="!logsSuccess" class="bg-yellow-50 border border-yellow-200 rounded p-4 mb-4">
           <p class="text-yellow-800">⚠️ {{ logsMessage }}</p>
           <p class="text-sm text-yellow-700 mt-2">Используйте логи Docker/EasyPanel для просмотра вывода приложения.</p>
      </div>
      
      <div v-else class="bg-gray-900 text-green-400 p-4 rounded font-mono text-xs overflow-auto h-[600px]">
          <div v-for="(line, idx) in logs" :key="idx" class="hover:bg-gray-800 whitespace-pre-wrap font-mono">{{ line }}</div>
          <div v-if="logs.length === 0" class="text-gray-500 text-center py-4">Логов пока нет</div>
      </div>
  </div>
</template>
