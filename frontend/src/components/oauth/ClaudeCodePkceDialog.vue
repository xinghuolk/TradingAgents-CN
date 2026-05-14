<!-- frontend/src/components/oauth/ClaudeCodePkceDialog.vue -->
<template>
  <el-dialog
    :model-value="store.pkceDialogOpen"
    title="正在使用 Anthropic 订阅登录"
    width="480px"
    :close-on-click-modal="false"
    :show-close="false"
    @close="handleCancel"
  >
    <div class="pkce-body">
      <el-icon class="is-loading spinner" :size="32"><Loading /></el-icon>
      <p class="pkce-hint">
        请在弹出窗口中完成 Anthropic 账号授权。<br />
        完成后窗口会自动关闭。
      </p>
      <p class="pkce-sub">
        如果弹窗被浏览器拦截，请允许该站点的弹窗后点击「取消」并重试。
      </p>
    </div>

    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'
import { useOAuthStore } from '@/stores/oauth'

const store = useOAuthStore()

const handleCancel = () => {
  store.cancelPkceFlow()
}
</script>

<style lang="scss" scoped>
.pkce-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 12px 0;
  gap: 16px;
}

.spinner {
  color: var(--el-color-primary);
}

.pkce-hint {
  font-size: 14px;
  color: var(--el-text-color-primary);
  margin: 0;
  line-height: 1.6;
}

.pkce-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 0;
  line-height: 1.5;
}
</style>
