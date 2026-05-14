<template>
  <el-card class="provider-card" shadow="never">
    <template #header>
      <div class="card-header">
        <div class="header-text">
          <strong>{{ title }}</strong>
          <div class="subtitle">{{ subtitle }}</div>
        </div>
        <el-tag :type="badgeType" size="small">{{ badgeLabel }}</el-tag>
      </div>
    </template>

    <div v-if="status?.bound" class="bound-body">
      <div class="meta-row">
        <span class="label">有效期：</span>
        <span class="value">{{ expiryDisplay }}</span>
      </div>
      <div class="meta-row">
        <span class="label">上次刷新：</span>
        <span class="value">{{ lastRefreshDisplay }}</span>
      </div>
      <div class="actions">
        <el-button size="small" @click="$emit('refresh')">手动刷新</el-button>
        <el-button size="small" type="danger" plain @click="$emit('unbind')">解绑</el-button>
      </div>
    </div>

    <div v-else class="empty-body">
      <p class="empty-hint">{{ emptyHint }}</p>
      <el-button type="primary" @click="$emit('bind')">{{ emptyCta }}</el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { OAuthStatus } from '@/api/oauth'

interface Props {
  provider: 'claude_code' | 'codex'
  title: string
  subtitle: string
  emptyCta: string
  emptyHint: string
  status: OAuthStatus | null
  now: number  // epoch ms, ticking, passed from parent
}

const props = defineProps<Props>()
defineEmits<{
  bind: []
  refresh: []
  unbind: []
}>()

// Threshold for "即将过期" tag — 10 minutes (TODO: make configurable if needed)
const NEAR_EXPIRY_MS = 10 * 60 * 1000

const expiryEpoch = computed<number | null>(() => {
  if (!props.status?.expires_at) return null
  return new Date(props.status.expires_at).getTime()
})

const isNearExpiry = computed(() => {
  if (!expiryEpoch.value) return false
  const remaining = expiryEpoch.value - props.now
  return remaining > 0 && remaining < NEAR_EXPIRY_MS
})

const isExpired = computed(() => {
  if (!expiryEpoch.value) return false
  return expiryEpoch.value - props.now <= 0
})

const badgeType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  if (!props.status?.bound) return 'info'
  if (isExpired.value) return 'danger'
  if (isNearExpiry.value) return 'warning'
  return 'success'
})

const badgeLabel = computed(() => {
  if (!props.status?.bound) return '未绑定'
  if (isExpired.value) return '已过期'
  if (isNearExpiry.value) return '即将过期'
  return '✓ 已绑定'
})

const expiryDisplay = computed(() => {
  if (!expiryEpoch.value) return '—'
  const remainingMs = expiryEpoch.value - props.now
  if (remainingMs <= 0) return '已过期'
  const minutes = Math.floor(remainingMs / 60000)
  if (minutes < 60) return `还剩 ${minutes} 分`
  const hours = Math.floor(minutes / 60)
  return `还剩 ${hours} 小时 ${minutes % 60} 分`
})

const lastRefreshDisplay = computed(() => {
  if (!props.status?.last_refresh_at) return '—'
  const epoch = new Date(props.status.last_refresh_at).getTime()
  const diffMs = props.now - epoch
  if (diffMs < 60000) return '刚刚'
  const minutes = Math.floor(diffMs / 60000)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
})
</script>

<style lang="scss" scoped>
.provider-card {
  min-height: 220px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-text strong {
  font-size: 16px;
}

.subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.bound-body,
.empty-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 0;
}

.meta-row {
  font-size: 13px;
  display: flex;
  gap: 6px;
}

.meta-row .label {
  color: var(--el-text-color-secondary);
  min-width: 72px;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.empty-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
  line-height: 1.6;
}
</style>
