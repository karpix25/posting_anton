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
    if (viewMode.value === 'category') {
        return statsStore.stats.publishedByCategory?.[key] || 0
    }
    if (viewMode.value === 'author') {
        return statsStore.stats.publishedByAuthor?.[key] || 0
    }
    if (viewMode.value === 'brand') {
        return statsStore.stats.publishedByBrand?.[key] || 0
    }
    return 0 
}

const getAuthorBrands = (authorName) => {
    return statsStore.stats.byAuthorBrand?.[authorName] || {}
}
// ... (skipping unchanged lines)
</script>

<template>
  <!-- ... skipping ... -->
                           <td class="p-3">
                               <div class="flex gap-2 flex-wrap">
                                   <span v-for="profile in getProfilesForCategory(item.name)" :key="profile" class="px-2 py-1 text-xs bg-blue-50 text-blue-700 rounded">
                                       {{ profile }}
                                   </span>
                                   <span v-if="!getProfilesForCategory(item.name).length" class="text-gray-400 text-sm italic">Нет профилей</span>
                               </div>
                           </td>
                           <td class="p-3 text-right font-semibold">{{ item.count }}</td>
                           <td class="p-3 text-right font-bold text-green-600">{{ getPublishedCount(item.name) }}</td>
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
