<template>
  <div class="entry-list" v-loading="loading">
    <div class="list-toolbar">
      <el-select
        :model-value="status"
        :disabled="disabled"
        aria-label="记录状态"
        @change="$emit('status', $event)"
      >
        <el-option label="全部状态" value="" />
        <el-option label="草稿" value="draft" />
        <el-option label="已确认" value="confirmed" />
        <el-option label="已归档" value="archived" />
      </el-select>
      <span class="count">{{ total }} 条</span>
    </div>
    <el-result v-if="error" icon="error" title="记录加载失败"
      ><template #extra
        ><el-button :disabled="disabled" @click="$emit('retry')">重试</el-button></template
      ></el-result
    >
    <el-empty v-else-if="!loading && !entries.length" description="暂无记录" :image-size="64" />
    <ul v-else>
      <li v-for="entry in entries" :key="entry.id">
        <button
          class="entry-row"
          type="button"
          :disabled="disabled"
          @click="$emit('select', entry)"
        >
          <span class="entry-title">{{ entry.title || labels[entry.entry_type] }}</span>
          <span class="entry-meta"
            >{{ statusLabels[entry.status] }} · {{ formatDateTime(entry.updated_at) }}</span
          >
          <span class="entry-summary">{{ entry.body.slice(0, 180) }}</span>
          <el-icon class="entry-arrow"><ArrowRight /></el-icon>
        </button>
      </li>
    </ul>
    <el-pagination
      v-if="total > pageSize"
      :current-page="page"
      :page-size="pageSize"
      :total="total"
      :disabled="disabled"
      layout="prev, pager, next"
      @current-change="$emit('page', $event)"
    />
  </div>
</template>

<script setup lang="ts">
import { ArrowRight } from '@element-plus/icons-vue'
import type { ResearchEntry, ResearchEntryStatus } from '@/api/stockResearch'
import { formatDateTime } from '@/utils/datetime'

defineProps<{
  entries: ResearchEntry[]
  total: number
  page: number
  pageSize: number
  status: ResearchEntryStatus | ''
  loading: boolean
  error: boolean
  disabled: boolean
}>()
defineEmits<{
  retry: []
  select: [entry: ResearchEntry]
  status: [status: ResearchEntryStatus | '']
  page: [page: number]
}>()
const labels = { note: '笔记', research: '调研', decision: '决策', review: '复盘' }
const statusLabels = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
</script>

<style scoped>
.entry-list {
  min-width: 0;
}
.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-light);
}
.list-toolbar .el-select {
  width: 150px;
}
.count,
.entry-meta,
.entry-summary {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
ul {
  margin: 0 0 16px;
  padding: 0;
  list-style: none;
}
.entry-row {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 7px;
  width: 100%;
  padding: 18px 30px 18px 0;
  border: 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: transparent;
  text-align: left;
  cursor: pointer;
  color: var(--el-text-color-primary);
  overflow-wrap: anywhere;
}
.entry-row:hover {
  background: var(--el-fill-color-light);
}
.entry-row:focus-visible {
  outline: 2px solid var(--el-color-primary);
}
.entry-title {
  font-weight: 600;
  font-size: 15px;
}
.entry-summary {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.entry-arrow {
  position: absolute;
  right: 8px;
  top: 22px;
}
</style>
