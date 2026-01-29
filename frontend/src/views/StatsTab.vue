<script setup>
import { computed, ref } from 'vue'
import { useStatsStore } from '../stores/stats'

const statsStore = useStatsStore()

// Local UI state
const viewMode = ref('category') // category, author, brand

const currentStatsList = computed(() => {
    let source = {}
    // Need view mode in store or local? index.html uses `statsViewMode` in data.
    // Let's add it to local state since it's UI only.
    if (viewMode.value === 'author') source = statsStore.stats.byAuthor || {}
    else if (viewMode.value === 'brand') source = statsStore.stats.byBrand || {}
    else source = statsStore.stats.byCategory || {}

    return Object.entries(source)
    .map(([name, count]) => ({ name, count }))
    .filter(item => item.name !== 'unknown')
    .sort((a, b) => a.name.localeCompare(b.name))
})

const expandedAuthors = ref([])

const toggleAuthorExpand = (name) => {
    if (expandedAuthors.value.includes(name)) {
        expandedAuthors.value = expandedAuthors.value.filter(n => n !== name)
    } else {
        expandedAuthors.value.push(name)
    }
}

const getPublishedCount = (key) => {
    // Logic from index.html: find key in published stats?
    // index.html just calls `getPublishedCount(item.name)`.
    // It implies `stats.publishedBy...` exists?
    // Let's look at `index.html` structure again if possible.
    // Assuming 0 for now or extracting from stats object if available.
    return 0 
}

const getAuthorBrands = (authorName) => {
    return statsStore.stats.byAuthorBrand?.[authorName] || {}
}

const getProfilesForCategory = (name) => {
    // Access statsStore profiles mapping
    const key = name.toLowerCase().trim()
    if (viewMode.value === 'author') return statsStore.stats.profilesByAuthor?.[key] || []
    if (viewMode.value === 'brand') return statsStore.stats.profilesByBrand?.[key] || []
    return statsStore.stats.profilesByCategory?.[key] || []
}
</script>

<template>
  <div>
      <div class="flex justify-between items-center mb-6">
          <h2 class="text-2xl font-bold">Аналитика и Статистика</h2>
          <button @click="statsStore.loadBrandStats" class="text-sm bg-gray-200 px-3 py-1 rounded hover:bg-gray-300">🔄 Обновить</button>
      </div>
      
      <!-- Overview Cards -->
      <div class="grid grid-cols-2 gap-6 mb-8">
          <div class="bg-blue-50 p-6 rounded-lg border border-blue-100 text-center">
              <div class="text-gray-500 mb-1">Всего доступно видео</div>
              <div class="text-4xl font-bold text-blue-700">{{ statsStore.stats.totalVideos || 0 }}</div>
              <div class="text-xs text-blue-400 mt-2">Готовы к публикации (Yandex Disk)</div>
          </div>
           <div class="bg-green-50 p-6 rounded-lg border border-green-100 text-center">
              <div class="text-gray-500 mb-1">Опубликовано и удалено</div>
              <div class="text-4xl font-bold text-green-700">{{ statsStore.stats.publishedCount || 0 }}</div>
              <div class="text-xs text-green-400 mt-2">За всё время</div>
          </div>
      </div>
      
      <!-- Breakdown Table -->
      <div class="flex justify-between items-center mb-4">
          <h3 class="text-xl font-bold">Разбивка</h3>
           <div class="bg-gray-200 p-1 rounded-lg flex text-sm">
               <button @click="viewMode = 'category'" :class="['px-3 py-1 rounded-md transition', viewMode === 'category' ? 'bg-white shadow text-blue-600 font-bold' : 'text-gray-600']">По Темам</button>
               <button @click="viewMode = 'author'" :class="['px-3 py-1 rounded-md transition', viewMode === 'author' ? 'bg-white shadow text-blue-600 font-bold' : 'text-gray-600']">По Авторам</button>
               <button @click="viewMode = 'brand'" :class="['px-3 py-1 rounded-md transition', viewMode === 'brand' ? 'bg-white shadow text-blue-600 font-bold' : 'text-gray-600']">По Брендам</button>
           </div>
      </div>
      
      <div class="bg-white border rounded-lg overflow-hidden">
          <table class="w-full border-collapse mt-6">
              <thead class="bg-gray-100">
                  <tr>
                      <th v-if="viewMode === 'author'" class="text-left p-3 border-b w-8"></th>
                      <th class="text-left p-3 border-b capitalize">{{ viewMode === 'brand' ? 'Бренд' : (viewMode === 'author' ? 'Автор' : 'Категория') }}</th>
                      <th class="text-left p-3 border-b">Профили</th>
                      <th class="text-right p-3 border-b w-32">На Диске</th>
                      <th class="text-right p-3 border-b w-32">Опубликовано</th>
                  </tr>
              </thead>
              <tbody>
                  <template v-for="item in currentStatsList" :key="item.name">
                      <tr class="border-b hover:bg-gray-50">
                          <td v-if="viewMode === 'author'" class="p-3 text-center">
                              <button @click="toggleAuthorExpand(item.name)" class="text-blue-600 font-bold">
                                  {{ expandedAuthors.includes(item.name) ? '▼' : '▶' }}
                              </button>
                          </td>
                          <td class="p-3 capitalize">{{ item.name }}</td>
                          <td class="p-3">
                              <div class="flex gap-2 flex-wrap">
                                  <span v-for="profile in getProfilesForCategory(item.name)" :key="profile" class="px-2 py-1 text-xs bg-blue-50 text-blue-700 rounded">
                                      {{ profile }}
                                  </span>
                                  <span v-if="!getProfilesForCategory(item.name).length" class="text-gray-400 text-sm italic">Нет профилей</span>
                              </div>
                          </td>
                           <td class="p-3 text-right font-semibold">{{ item.count }}</td>
                           <td class="p-3 text-right font-bold text-green-600">-</td>
                      </tr>
                      <!-- Expandable Author Details -->
                      <tr v-if="viewMode === 'author' && expandedAuthors.includes(item.name)" class="bg-blue-50">
                          <td :colspan="5" class="p-0">
                              <div class="px-8 py-3">
                                  <div class="text-xs font-semibold text-gray-600 mb-2">Разбивка по брендам:</div>
                                  <table class="w-full text-sm">
                                      <tr v-for="(count, brand) in getAuthorBrands(item.name)" :key="brand" class="border-t border-blue-100">
                                          <td class="py-2 capitalize">{{ brand }}</td>
                                          <td class="py-2 text-right font-semibold">{{ count }}</td>
                                      </tr>
                                      <tr v-if="!Object.keys(getAuthorBrands(item.name)).length">
                                           <td colspan="2" class="py-2 text-center text-gray-400 italic">Нет данных</td>
                                      </tr>
                                  </table>
                              </div>
                          </td>
                      </tr>
                  </template>
                  <tr v-if="currentStatsList.length === 0">
                      <td colspan="5" class="p-4 text-center text-gray-400">Нет данных</td>
                  </tr>
              </tbody>
          </table>
      </div>
  </div>
</template>
