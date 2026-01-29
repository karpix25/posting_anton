<script setup>
import { ref } from 'vue'
import { useConfigStore } from '../stores/config'
import axios from 'axios'

const configStore = useConfigStore()
const config = configStore.config

const foldersInput = ref(config.yandexFolders ? config.yandexFolders.join(', ') : '')

// Schedule Model (if separate from config, check index.html. usually part of config or separate endpoint)
// index.html: schedule: { enabled: false ... } in data.
// loadSchedule() calls /api/schedule
const schedule = ref({
    enabled: false,
    timezone: 'Europe/Moscow',
    dailyRunTime: '00:01'
})

const loadSchedule = async () => {
    try {
        const res = await axios.get('/api/schedule')
        if (res.data) schedule.value = res.data
    } catch (e) { console.error(e) }
}
loadSchedule()

const saveSchedule = async () => {
    try {
        await axios.post('/api/schedule', schedule.value)
        alert('Расписание сохранено')
    } catch (e) {
        alert('Ошибка при сохранении расписания')
    }
}

const updateFolders = () => {
    config.yandexFolders = foldersInput.value.split(',').map(s => s.trim()).filter(Boolean)
}
</script>

<template>
  <div>
       <h2 class="text-2xl font-bold mb-4">Общие настройки</h2>
       
       <!-- Schedule -->
       <div class="mb-6 p-4 bg-blue-50 border border-blue-200 rounded">
           <h3 class="text-lg font-bold mb-3">⏰ Расписание автоматизации</h3>
           <div class="mb-4">
               <label class="flex items-center cursor-pointer">
                   <input type="checkbox" v-model="schedule.enabled" class="mr-2 w-5 h-5">
                   <span class="font-semibold">Включить автоматический запуск</span>
               </label>
           </div>
           
           <div class="grid grid-cols-2 gap-4">
               <div>
                   <label class="block mb-2 font-semibold">Время запуска</label>
                   <input type="time" v-model="schedule.dailyRunTime" class="w-full border p-2 rounded" :disabled="!schedule.enabled">
               </div>
               <div>
                   <label class="block mb-2 font-semibold">Часовой пояс</label>
                   <select v-model="schedule.timezone" class="w-full border p-2 rounded" :disabled="!schedule.enabled">
                       <option value="Europe/Moscow">Москва (МСК, UTC+3)</option>
                       <option value="Europe/Kiev">Киев (UTC+2)</option>
                       <option value="Asia/Yekaterinburg">Екатеринбург (UTC+5)</option>
                       <option value="UTC">UTC (UTC+0)</option>
                   </select>
               </div>
           </div>
           
           <div class="mt-4 flex justify-between items-center">
                <button @click="saveSchedule" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm font-semibold">💾 Сохранить расписание</button>
           </div>
       </div>
       
       <!-- Config Fields -->
        <div class="mb-4">
            <label class="block mb-2 font-semibold">Папки Яндекс Диска (через запятую)</label>
            <input v-model="foldersInput" @input="updateFolders" class="w-full border p-2 rounded">
        </div>
        
         <div class="mb-4">
            <label class="block mb-2 font-semibold">На сколько дней планировать вперед?</label>
            <input type="number" min="1" max="30" v-model.number="config.daysToGenerate" class="w-full border p-2 rounded">
         </div>
         
         <h3 class="text-xl font-bold mt-6 mb-4">Лимиты платформ (глобальные)</h3>
         <div class="grid grid-cols-3 gap-4">
              <div>
                 <label>Instagram</label>
                 <input type="number" v-model="config.limits.instagram" class="w-full border p-2 rounded">
              </div>
              <div>
                 <label>TikTok</label>
                 <input type="number" v-model="config.limits.tiktok" class="w-full border p-2 rounded">
              </div>
              <div>
                 <label>YouTube</label>
                 <input type="number" v-model="config.limits.youtube" class="w-full border p-2 rounded">
              </div>
         </div>
         
         <div class="mt-8">
             <button @click="configStore.saveConfig()" class="bg-green-600 text-white px-6 py-2 rounded font-bold hover:bg-green-700 w-full">Сохранить глобальные настройки</button>
         </div>
  </div>
</template>
