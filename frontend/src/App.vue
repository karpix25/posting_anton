
<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useConfigStore } from './stores/configStore';
import TheHeader from './components/TheHeader.vue';

// Tabs
// We will define these components in next steps via separate files
import DashboardTab from './views/DashboardTab.vue'; 
import ProfilesTab from './views/ProfilesTab.vue'; 
import SettingsTab from './views/SettingsTab.vue'; 
import LogsTab from './views/LogsTab.vue';

const configStore = useConfigStore();
const currentTab = ref('dashboard');

const tabs = [
    { id: 'dashboard', name: '📊 Статус', component: DashboardTab },
    { id: 'profiles', name: '👥 Профили', component: ProfilesTab },
    { id: 'activity', name: '⚡ Активность', component: null }, // To implement later
    { id: 'settings', name: '⚙️ Настройки', component: SettingsTab },
    { id: 'logs', name: '📝 Логи', component: LogsTab },
];

onMounted(() => {
    configStore.loadConfig();
});
</script>

<template>
<div class="min-h-screen p-8 bg-gray-100 text-gray-800">
    <TheHeader />

    <!-- Main Content -->
    <div class="max-w-6xl mx-auto bg-white rounded-lg shadow-lg overflow-hidden flex min-h-[600px]">

        <!-- Sidebar / Tabs -->
        <div class="w-64 bg-gray-50 border-r p-4 space-y-2">
            <button v-for="tab in tabs" :key="tab.id" @click="currentTab = tab.id"
                :class="['w-full text-left px-4 py-2 rounded flex items-center gap-2 transition-colors', 
                         currentTab === tab.id ? 'bg-blue-100 text-blue-700 font-semibold shadow-sm' : 'hover:bg-gray-200 text-gray-600']">
                <span>{{ tab.name.split(' ')[0] }}</span> 
                <span>{{ tab.name.split(' ').slice(1).join(' ') }}</span>
            </button>
        </div>

        <!-- Tab Content -->
        <div class="flex-1 p-8 overflow-y-auto max-h-[calc(100vh-200px)]">
            <div v-if="configStore.loading && !configStore.config" class="text-center text-gray-500 py-20 flex flex-col items-center">
                <span class="text-4xl mb-4 animate-bounce">⏳</span>
                Загрузка настроек...
            </div>
            
            <div v-else-if="configStore.error" class="text-center text-red-500 py-20">
                <h3 class="text-xl font-bold mb-2">Ошибка загрузки</h3>
                <p>{{ configStore.error }}</p>
                <button @click="configStore.loadConfig()" class="mt-4 bg-blue-500 text-white px-4 py-2 rounded">Повторить</button>
            </div>

            <div v-else class="h-full">
                <!-- Using dynamic component -->
                 <component :is="tabs.find(t => t.id === currentTab)?.component" v-if="tabs.find(t => t.id === currentTab)?.component" />
                 <div v-else class="text-center text-gray-400 py-10">В разработке...</div>
            </div>
        </div>
    </div>
</div>
</template>
