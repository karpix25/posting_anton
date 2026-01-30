<script setup>
import { computed } from 'vue'
import { useConfigStore } from '../stores/config'
import { useStatsStore } from '../stores/stats'

const configStore = useConfigStore()
const statsStore = useStatsStore()

const getClientPublished = (client) => {
    try {
        if (!client || !client.name) return 0
        
        // Try precise match via brandStats (Category:Brand key)
        if (statsStore.brandStats && Object.keys(statsStore.brandStats).length > 0) {
             const categoryMatch = (client.regex || '').match(/([a-zA-Zа-яА-Я0-9]+)/)
             const category = categoryMatch ? categoryMatch[1].toLowerCase() : 'unknown'
             const brandName = client.name.toLowerCase().replace(/[^a-zа-я0-9]/g, '')
             const key = `${category}:${brandName}`
             
             if (statsStore.brandStats[key]) {
                 return statsStore.brandStats[key].published_count || 0
             }
        }
        
        // Fallback to global scan stats
        if (statsStore.stats && statsStore.stats.byBrand) {
            const key = client.name.toLowerCase().trim()
            return statsStore.stats.byBrand[key] || 0
        }
        
        return 0
    } catch (e) {
        console.warn('Error in getClientPublished', e)
        return 0
    }
}

const getClientRemaining = (client) => {
    try {
        const published = getClientPublished(client)
        return Math.max(0, (client.quota || 0) - published)
    } catch (e) { return 0 }
}

const getClientProgress = (client) => {
    try {
        if (!client.quota) return 0
        const published = getClientPublished(client)
        return Math.min(100, Math.round((published / client.quota) * 100))
    } catch (e) { return 0 }
}

import { onMounted } from 'vue'
onMounted(() => {
    statsStore.loadBrandStats()
})

const addClient = async () => {
    if (!configStore.config.clients) configStore.config.clients = []
    configStore.config.clients.push({
        name: `New Client ${new Date().toLocaleTimeString()}`,
        regex: '.*',
        quota: 30,
        prompt: ''
    })
    await configStore.saveConfig()
}

const removeClient = async (index) => {
    if(!confirm('Удалить клиента?')) return
    configStore.config.clients.splice(index, 1)
    await configStore.saveConfig()
}
</script>

<template>
  <div>
       <div class="flex justify-between items-center mb-4">
           <h2 class="text-2xl font-bold">Клиенты и Промты (AI)</h2>
           <button @click="addClient" class="text-sm bg-gray-200 px-3 py-1 rounded hover:bg-gray-300 transition-colors">+ Добавить клиента</button>
       </div>
       
       <div v-for="(client, idx) in configStore.config.clients" :key="idx" class="border p-4 rounded mb-4 bg-gray-50 hover:bg-white hover:shadow-sm transition-all">
           <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-2">
               <div>
                   <label class="text-sm text-gray-500 block mb-1">Имя клиента (бренд)</label>
                   <input v-model="client.name" @change="configStore.saveConfig" class="w-full border p-2 rounded">
               </div>
                <div>
                   <label class="text-sm text-gray-500 block mb-1">Regex папки</label>
                   <input v-model="client.regex" @change="configStore.saveConfig" class="w-full border p-2 rounded font-mono text-sm">
               </div>
                <div>
                   <label class="text-sm text-gray-500 block mb-1">Квота (видео/месяц)</label>
                   <input v-model.number="client.quota" @change="configStore.saveConfig" type="number" min="0" class="w-full border p-2 rounded">
                   
                   <div v-if="client.quota" class="mt-2">
                       <div class="text-xs text-gray-600 mb-1 flex justify-between">
                           <span><span class="font-semibold">{{ getClientPublished(client) }}</span> / {{ client.quota }}</span>
                           <span class="text-green-600">Осталось: {{ getClientRemaining(client) }}</span>
                       </div>
                       <div class="w-full bg-gray-200 rounded-full h-2 overflow-hidden">
                           <div class="bg-blue-500 h-full transition-all" :style="{ width: getClientProgress(client) + '%' }"></div>
                       </div>
                   </div>
               </div>
           </div>
           
           <div class="mt-2">
                <label class="text-sm text-gray-500 block mb-1">AI Системный промт</label>
                <textarea v-model="client.prompt" @change="configStore.saveConfig" class="w-full border p-2 rounded h-24 font-mono text-sm"></textarea>
           </div>
           
           <div class="text-right mt-2">
               <button @click="removeClient(idx)" class="text-red-500 text-sm hover:underline hover:text-red-700">Удалить клиента</button>
           </div>
       </div>
       
       <div v-if="!configStore.config.clients?.length" class="text-center text-gray-400 py-8 border border-dashed rounded">
           Клиентов пока нет. Добавьте первого клиента.
       </div>
  </div>
</template>
