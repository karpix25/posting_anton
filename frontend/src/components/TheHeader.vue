
<script setup lang="ts">
import { computed } from 'vue';
import { useConfigStore } from '../stores/configStore';

const configStore = useConfigStore();

// This should come from a status API or SSE
const isOnline = computed(() => true); 

function handleSave() {
    configStore.saveConfig();
}
</script>

<template>
  <div class="max-w-6xl mx-auto mb-8 flex justify-between items-center">
    <h1 class="text-3xl font-bold text-blue-600 flex items-center gap-3">
        🚀 Автоматизация Контента
    </h1>
    <div class="flex items-center gap-4">
        <!-- Real-time Status Indicator -->
        <div class="flex items-center gap-2 px-3 py-1 rounded-full text-sm font-semibold transition-colors"
            :class="isOnline ? 'bg-green-100 text-green-700 border border-green-200' : 'bg-amber-100 text-amber-700 border border-amber-200'">
            <span class="relative flex h-3 w-3">
                <span v-if="isOnline"
                    class="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-3 w-3"
                    :class="isOnline ? 'bg-green-500' : 'bg-amber-500'"></span>
            </span>
            {{ isOnline ? 'Online' : 'Connecting...' }}
        </div>

        <button @click="handleSave" aria-label="Сохранить все изменения конфигурации"
            class="bg-green-500 hover:bg-green-600 text-white px-4 py-2 rounded shadow focus-visible:ring-2 focus-visible:ring-green-300 flex items-center gap-2"
            :disabled="configStore.loading">
            <span v-if="configStore.loading" class="animate-spin">⏳</span>
            Сохранить изменения
        </button>
    </div>
  </div>
</template>
