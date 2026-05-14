<template>
  <div class="subscription-auth">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="12">
        <ProviderCard
          provider="claude_code"
          title="Claude Code"
          subtitle="Anthropic Pro/Max 订阅"
          empty-cta="登录 Claude"
          empty-hint="使用您的 Anthropic Pro / Max 订阅运行多智能体分析，无需 API Key。"
          :status="store.claudeCodeStatus"
          :now="now"
          @bind="store.startClaudeCodeFlow"
          @refresh="store.refresh('claude_code')"
          @unbind="confirmUnbind('claude_code', 'Claude Code')"
        />
      </el-col>
      <el-col :xs="24" :sm="12">
        <ProviderCard
          provider="codex"
          title="Codex (ChatGPT)"
          subtitle="OpenAI Plus/Pro 订阅"
          empty-cta="登录 ChatGPT"
          empty-hint="使用您的 ChatGPT 订阅运行多智能体分析，无需 OpenAI API Key。"
          :status="store.codexStatus"
          :now="now"
          @bind="store.startCodexFlow"
          @refresh="store.refresh('codex')"
          @unbind="confirmUnbind('codex', 'Codex')"
        />
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useOAuthStore } from '@/stores/oauth'
import type { OAuthProvider } from '@/api/oauth'
import ProviderCard from './SubscriptionProviderCard.vue'

const store = useOAuthStore()

// One ticking clock shared across both cards (used for countdown / relative time).
const now = ref(Date.now())
const tickId = window.setInterval(() => {
  now.value = Date.now()
}, 1000)

// Background refresh: pull status every 30s while the panel is mounted.
const refreshId = window.setInterval(() => {
  store.fetchAllStatus()
}, 30000)

onMounted(() => {
  store.fetchAllStatus()
})

onUnmounted(() => {
  clearInterval(tickId)
  clearInterval(refreshId)
})

const confirmUnbind = async (provider: OAuthProvider, displayName: string) => {
  try {
    await ElMessageBox.confirm(
      `确定解绑 ${displayName} 吗？此后您本人将无法使用该订阅运行分析，需重新授权。`,
      '解绑确认',
      {
        type: 'warning',
        confirmButtonText: '解绑',
        cancelButtonText: '取消',
      },
    )
    await store.unbind(provider)
  } catch {
    // Cancelled
  }
}
</script>

<style lang="scss" scoped>
.subscription-auth {
  padding: 8px 0;
}
</style>
