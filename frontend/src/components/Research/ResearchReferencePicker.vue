<template>
  <div class="reference-picker">
    <el-select
      :model-value="modelValue.map(key)"
      multiple
      filterable
      :disabled="disabled"
      :loading="loading"
      placeholder="选择引用资料"
      @change="select"
    >
      <el-option-group v-for="group in groups" :key="group.label" :label="group.label">
        <el-option
          v-for="item in group.items"
          :key="key(item)"
          :value="key(item)"
          :disabled="
            item.available === false && !modelValue.some(value => key(value) === key(item))
          "
          :label="`${group.label} · ${item.label || item.source_id}${item.available === false ? '（来源暂不可用）' : ''}`"
        />
      </el-option-group>
    </el-select>
    <el-button v-if="failed" :icon="Refresh" text :disabled="disabled" @click="load"
      >重试引用资料</el-button
    >
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  stockResearchApi,
  type ResearchReference,
  type ResearchReferenceCandidate
} from '@/api/stockResearch'

const props = defineProps<{
  modelValue: ResearchReference[]
  securityId?: string | null
  securityIds?: string[]
  decisionId?: string
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: ResearchReference[]] }>()
const loading = ref(false)
const failed = ref(false)
const candidates = ref<ResearchReferenceCandidate[]>([])
let request = 0
function key(item: ResearchReference) {
  return JSON.stringify([
    item.kind,
    item.source_id,
    item.account_type || null,
    item.source_date || null
  ])
}
function groupLabel(item: ResearchReference) {
  if (item.account_type === 'real' || item.kind === 'real_trade') return '真实账户'
  if (item.account_type === 'paper' || item.kind === 'paper_trade') return '模拟账户'
  return item.kind === 'decision' ? '历史决策' : '分析报告'
}
const options = computed(() => {
  const values = new Map<string, ResearchReference & { available?: boolean }>()
  for (const item of props.modelValue) values.set(key(item), { ...item, available: false })
  for (const item of candidates.value) values.set(key(item), item)
  return [...values.values()]
})
const groups = computed(() =>
  ['真实账户', '模拟账户', '历史决策', '分析报告']
    .map(label => ({
      label,
      items: options.value.filter(item => groupLabel(item) === label)
    }))
    .filter(group => group.items.length)
)
function select(keys: string[]) {
  emit(
    'update:modelValue',
    options.value
      .filter(item => keys.includes(key(item)))
      .map(({ kind, source_id, account_type, source_date, label }) => ({
        kind,
        source_id,
        account_type,
        source_date,
        label
      }))
  )
}
async function load() {
  const current = ++request
  loading.value = true
  failed.value = false
  const ids = [
    ...new Set([props.securityId, ...(props.securityIds || [])].filter(Boolean))
  ] as string[]
  const requests = ids.map(security_id => stockResearchApi.listReferences({ security_id }))
  if (props.decisionId) requests.push(stockResearchApi.recommendTradeLinks(props.decisionId))
  const results = await Promise.allSettled(requests)
  if (current !== request) return
  candidates.value = results.flatMap(result =>
    result.status === 'fulfilled' ? result.value.data : []
  )
  failed.value = results.some(result => result.status === 'rejected')
  loading.value = false
}
watch(() => [props.securityId, props.securityIds, props.decisionId], load, {
  immediate: true,
  deep: true
})
</script>

<style scoped>
.reference-picker,
.reference-picker .el-select {
  width: 100%;
  min-width: 0;
}
</style>
