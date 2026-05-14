<!-- frontend/src/components/oauth/CodexDeviceCodeDialog.vue -->
<template>
  <el-dialog
    :model-value="store.deviceCodeDialogOpen"
    title="使用 ChatGPT 订阅登录"
    width="520px"
    :close-on-click-modal="false"
    :show-close="false"
    @close="handleCancel"
  >
    <div v-if="state" class="codex-body">
      <p class="hint">在 ChatGPT 页面输入此 code：</p>

      <div class="code-box">{{ state.user_code }}</div>

      <el-button size="small" @click="copyCode">
        <el-icon><CopyDocument /></el-icon>
        复制 code
      </el-button>

      <div class="verification-link">
        <el-link type="primary" :href="state.verification_uri" target="_blank">
          → 打开授权页 ({{ verificationDisplayHost }})
        </el-link>
      </div>

      <el-alert type="warning" :closable="false" class="poll-alert">
        <template #default>
          <div>⏱ 等待您完成授权...</div>
          <div class="countdown">code 还剩 {{ countdown }}</div>
        </template>
      </el-alert>
    </div>

    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useOAuthStore } from '@/stores/oauth'

const store = useOAuthStore()
const state = computed(() => store.deviceCodeState)

const now = ref(Date.now())
const tickId = window.setInterval(() => {
  now.value = Date.now()
}, 1000)
onUnmounted(() => clearInterval(tickId))

const countdown = computed(() => {
  if (!state.value) return '--:--'
  const remainingMs = Math.max(0, state.value.expires_at - now.value)
  const minutes = Math.floor(remainingMs / 60000)
  const seconds = Math.floor((remainingMs % 60000) / 1000)
  return `${minutes} 分 ${seconds.toString().padStart(2, '0')} 秒`
})

const verificationDisplayHost = computed(() => {
  if (!state.value) return ''
  try {
    return new URL(state.value.verification_uri).host
  } catch {
    return state.value.verification_uri
  }
})

const copyCode = async () => {
  if (!state.value) return
  try {
    await navigator.clipboard.writeText(state.value.user_code)
    ElMessage.success('已复制 code')
  } catch (err) {
    console.error('❌ 复制失败', err)
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

const handleCancel = () => {
  store.cancelCodexFlow()
}
</script>

<style lang="scss" scoped>
.codex-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 16px;
  padding: 8px 0;
}

.hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0;
}

.code-box {
  background: var(--el-color-primary-light-9);
  border: 2px solid var(--el-color-primary);
  border-radius: 8px;
  padding: 20px 24px;
  font-size: 36px;
  font-weight: bold;
  letter-spacing: 8px;
  color: var(--el-color-primary);
  font-family: 'Monaco', 'Menlo', monospace;
  min-width: 280px;
}

.verification-link {
  border-top: 1px dashed var(--el-border-color);
  border-bottom: 1px dashed var(--el-border-color);
  padding: 12px 0;
  width: 100%;
}

.poll-alert {
  width: 100%;
}

.countdown {
  font-size: 12px;
  margin-top: 2px;
  color: var(--el-text-color-secondary);
}
</style>
