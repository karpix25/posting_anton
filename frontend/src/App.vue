<script setup>
import { ref, onMounted, computed, watch, defineAsyncComponent } from 'vue'
import { useConfigStore } from './stores/config'
import { useRealtimeStore } from './stores/realtime'
import { Store, Users, Folder, FileText, BarChart2 } from 'lucide-vue-next'

// Components
const DashboardTab = defineAsyncComponent(() => import('./views/DashboardTab.vue'))
const ProfilesTab = defineAsyncComponent(() => import('./views/ProfilesTab.vue'))
const StatsTab = defineAsyncComponent(() => import('./views/StatsTab.vue'))
const SettingsTab = defineAsyncComponent(() => import('./views/SettingsTab.vue'))
const ClientsTab = defineAsyncComponent(() => import('./views/ClientsTab.vue'))
const LogsTab = defineAsyncComponent(() => import('./views/LogsTab.vue'))

const configStore = useConfigStore()
const realtimeStore = useRealtimeStore()

const tabs = [
  { id: 'dashboard', name: 'Дашборд', icon: Store },
  { id: 'stats', name: 'Аналитика', icon: BarChart2 },
  { id: 'settings', name: 'Настройки', icon: FileText },
  { id: 'profiles', name: 'Профили', icon: Users },
  { id: 'clients', name: 'AI Клиенты', icon: Users },
  { id: 'logs', name: '📋 Логи', icon: FileText }
]

const currentTab = ref('dashboard')

onMounted(async () => {
  await configStore.fetchConfig()
  realtimeStore.connect()
})

const saveConfig = async () => {
  const success = await configStore.saveConfig()
  if (success) {
    alert('Настройки сохранены!')
  } else {
    alert('Ошибка при сохранении!')
  }
}
</script>

<template>
  <div class="min-h-screen bg-gray-100 text-gray-800 p-8 font-sans">
    <!-- Header -->
    <div class="max-w-6xl mx-auto mb-8 flex justify-between items-center">
      <h1 class="text-3xl font-bold text-blue-600 flex items-center gap-3">
        🚀 Автоматизация Контента
      </h1>
      <div class="flex items-center gap-4">
        <!-- Real-time Status -->
        <div class="flex items-center gap-2 px-3 py-1 rounded-full text-sm font-semibold transition-colors"
          :class="realtimeStore.connected ? 'bg-green-100 text-green-700 border border-green-200' : 'bg-amber-100 text-amber-700 border border-amber-200'">
          <span class="relative flex h-3 w-3">
             <span v-if="realtimeStore.connected" class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
             <span class="relative inline-flex rounded-full h-3 w-3" :class="realtimeStore.connected ? 'bg-green-500' : 'bg-amber-500'"></span>
          </span>
          {{ realtimeStore.connected ? 'Online' : 'Connecting...' }}
        </div>

        <button @click="saveConfig" class="bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded shadow transition">
          Сохранить изменения
        </button>
      </div>
    </div>

    <!-- Main Content -->
    <div class="max-w-6xl mx-auto bg-white rounded-lg shadow-lg overflow-hidden flex min-h-[600px]">
      <!-- Sidebar -->
      <div class="w-64 bg-gray-50 border-r p-4 space-y-2">
        <button v-for="tab in tabs" :key="tab.id" @click="currentTab = tab.id"
          class="w-full text-left px-4 py-2 rounded flex items-center gap-3 transition-colors"
          :class="currentTab === tab.id ? 'bg-blue-100 text-blue-700 font-semibold' : 'hover:bg-gray-200 text-gray-600'">
          <component :is="tab.icon" class="w-5 h-5" />
          {{ tab.name }}
        </button>
      </div>

      <!-- Tab Content Area -->
      <div class="flex-1 p-8 relative">
        <div v-if="configStore.loading" class="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
           <div class="text-blue-600 font-semibold">Загрузка настроек...</div>
        </div>

        <div v-else>
           <h2 class="text-2xl font-bold mb-6 capitalize">{{ tabs.find(t => t.id === currentTab)?.name }}</h2>
           
           <div v-if="currentTab === 'dashboard'">
              <DashboardTab />
           </div>
           
           <div v-else-if="currentTab === 'profiles'">
              <ProfilesTab />
           </div>

           <div v-else-if="currentTab === 'stats'">
              <StatsTab />
           </div>
           
           <div v-else-if="currentTab === 'settings'">
              <SettingsTab />
           </div>
           
           <div v-else-if="currentTab === 'clients'">
              <ClientsTab />
           </div>

           <div v-else-if="currentTab === 'logs'">
              <LogsTab />
           </div>
        </div>
      </div>
    </div>
  </div>
</template>
