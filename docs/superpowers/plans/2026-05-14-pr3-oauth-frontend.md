# PR-3: 订阅鉴权前端 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Vue 3 frontend for the OAuth subscription auth backend (PR-2): a "订阅授权" management panel, two flow dialogs (Anthropic PKCE / Codex device-code), and LLMConfigDialog integration so admins can pick `claude_code` / `codex` as a provider without an API key.

**Architecture:** Pinia store (`useOAuthStore`) owns all OAuth state + flow orchestration. Two globally-mounted dialogs in `App.vue` are driven by the store. A new menu item in `ConfigManagement.vue` shows a `SubscriptionAuthManagement` panel with two cards (one per provider). `LLMConfigDialog` is patched to inject two synthetic provider options that swap the API-key input for a binding status card.

**Tech Stack:** Vue 3 + TypeScript + Element Plus 2.4 + Pinia 2.1 + axios (existing `request.ts` wrapper) + dayjs (for relative time). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-14-pr3-oauth-frontend-design.md`

**Branch:** `feat/pr3-oauth-frontend` (already checked out)

---

## File Structure

**New files (5):**
- `frontend/src/api/oauth.ts` — REST wrapper for `/api/oauth/*`.
- `frontend/src/stores/oauth.ts` — Pinia store: status cache + flow actions.
- `frontend/src/components/oauth/ClaudeCodePkceDialog.vue` — PKCE "等待中" dialog (global).
- `frontend/src/components/oauth/CodexDeviceCodeDialog.vue` — Device-code dialog with big `user_code` + polling timer (global).
- `frontend/src/views/Settings/components/SubscriptionAuthManagement.vue` — Main panel with two `el-card`s.

**Modified files (3):**
- `frontend/src/App.vue` — Mount the two dialogs as root-level globals.
- `frontend/src/views/Settings/ConfigManagement.vue` — Add `subscription-auth` menu item + content section + lazy-load.
- `frontend/src/views/Settings/components/LLMConfigDialog.vue` — Inject `claude_code` / `codex` synthetic options; conditional UI (status card vs. API-key fields).

**Docs / smoke (1):**
- `scripts/smoke_test_pr3_ui.md` — Human checklist (not a runnable script).

Each file has one responsibility:
- `api/oauth.ts`: pure REST, no UI deps.
- `stores/oauth.ts`: state machine, no DOM (except `window.open` / `addEventListener('message')`).
- Each dialog component: one flow, dumb-ish, reads/writes store.
- `SubscriptionAuthManagement.vue`: read-only summary + button wiring.
- `LLMConfigDialog.vue`: untouched for non-subscription providers; new conditional branch only when `provider in ('claude_code', 'codex')`.

---

## Conventions to follow (codebase-specific)

- **API style:** the existing `frontend/src/api/request.ts` exports a raw axios `request` whose response interceptor returns `response.data` directly. For OAuth, the backend returns raw Pydantic JSON (NOT wrapped in `ApiResponse<T>`), so `await request.get<OAuthStatus>('/api/oauth/status/claude_code')` returns the `OAuthStatusResponse` object. **Use the raw `request` import**, not `ApiClient.get` (which assumes the `{success, data, message}` envelope and would mis-handle the OAuth payloads).
- **Pinia style:** Options API style (`defineStore('name', { state: () => ({}), actions: {} })`) — matches `stores/app.ts`, `stores/auth.ts`. Do NOT use the Composition API setup-store style.
- **Logging:** `console.log/.warn/.error` with emoji prefixes (`🔐`, `✅`, `❌`) matches existing style in `request.ts` / `App.vue`. No external logger.
- **Element Plus icons:** import from `@element-plus/icons-vue` (already a dep).
- **No new test framework:** project has no Vitest config. Verification = `yarn type-check`, `yarn build`, plus manual checklist.
- **Language:** UI strings hardcoded Chinese (no i18n in project).

---

## Task 1: OAuth API wrapper

**Files:**
- Create: `frontend/src/api/oauth.ts`

- [ ] **Step 1: Create the API module**

```typescript
// frontend/src/api/oauth.ts
import { request } from './request'

export type OAuthProvider = 'claude_code' | 'codex'

export interface OAuthStatus {
  bound: boolean
  provider: OAuthProvider
  expires_at: string | null
  last_refresh_at: string | null
}

export interface AuthorizeClaudeCodeResponse {
  authorize_url: string
  state: string
  expires_in: number
}

export interface AuthorizeCodexResponse {
  user_code: string
  verification_uri: string
  expires_in: number
  interval: number
}

export type CodexPollStatus = 'pending' | 'bound' | 'expired' | 'denied'

export interface PollCodexResponse {
  status: CodexPollStatus
  increment_interval: boolean
}

// All OAuth endpoints return raw Pydantic JSON (NOT the {success,data,message} envelope),
// so we use the raw `request` whose response interceptor still strips to response.data.
export const oauthApi = {
  status(provider: OAuthProvider): Promise<OAuthStatus> {
    return request.get(`/api/oauth/status/${provider}`)
  },

  authorizeClaudeCode(): Promise<AuthorizeClaudeCodeResponse> {
    return request.get('/api/oauth/authorize/claude_code')
  },

  authorizeCodex(): Promise<AuthorizeCodexResponse> {
    return request.post('/api/oauth/authorize/codex')
  },

  pollCodex(): Promise<PollCodexResponse> {
    return request.post('/api/oauth/poll/codex')
  },

  refresh(provider: OAuthProvider): Promise<{ status: string }> {
    return request.post(`/api/oauth/refresh/${provider}`)
  },

  unbind(provider: OAuthProvider): Promise<void> {
    return request.delete(`/api/oauth/unbind/${provider}`)
  },
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS (file compiles; new types don't break anything because no consumers yet).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/oauth.ts
git commit -m "feat(pr3): add OAuth REST API wrapper"
```

---

## Task 2: Pinia OAuth store

**Files:**
- Create: `frontend/src/stores/oauth.ts`

- [ ] **Step 1: Write the store**

```typescript
// frontend/src/stores/oauth.ts
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import {
  oauthApi,
  type OAuthProvider,
  type OAuthStatus,
} from '@/api/oauth'

interface DeviceCodeState {
  user_code: string
  verification_uri: string
  expires_at: number   // epoch ms
  interval: number     // seconds, may grow on slow_down
  poll_timer: number | null
}

interface OAuthStoreState {
  claudeCodeStatus: OAuthStatus | null
  codexStatus: OAuthStatus | null

  pkceDialogOpen: boolean
  pkceDialogPopup: Window | null

  deviceCodeDialogOpen: boolean
  deviceCodeState: DeviceCodeState | null

  // Bound listener — kept as a field so we can `removeEventListener` the same fn.
  _pkceMessageListener: ((event: MessageEvent) => void) | null
}

export const useOAuthStore = defineStore('oauth', {
  state: (): OAuthStoreState => ({
    claudeCodeStatus: null,
    codexStatus: null,

    pkceDialogOpen: false,
    pkceDialogPopup: null,

    deviceCodeDialogOpen: false,
    deviceCodeState: null,

    _pkceMessageListener: null,
  }),

  getters: {
    statusFor: (state) => (provider: OAuthProvider): OAuthStatus | null =>
      provider === 'claude_code' ? state.claudeCodeStatus : state.codexStatus,
  },

  actions: {
    async fetchStatus(provider: OAuthProvider): Promise<OAuthStatus> {
      const status = await oauthApi.status(provider)
      if (provider === 'claude_code') {
        this.claudeCodeStatus = status
      } else {
        this.codexStatus = status
      }
      return status
    },

    async fetchAllStatus(): Promise<void> {
      await Promise.all([
        this.fetchStatus('claude_code').catch((e) => console.error('❌ status claude_code 失败', e)),
        this.fetchStatus('codex').catch((e) => console.error('❌ status codex 失败', e)),
      ])
    },

    async startClaudeCodeFlow(): Promise<void> {
      try {
        const resp = await oauthApi.authorizeClaudeCode()
        const popup = window.open(
          resp.authorize_url,
          'oauth_popup',
          'width=600,height=800',
        )
        if (!popup) {
          ElMessage.error('弹窗被浏览器拦截，请在地址栏允许弹窗后重试')
          return
        }
        this.pkceDialogPopup = popup
        this.pkceDialogOpen = true

        const listener = (event: MessageEvent) => this._handlePkceMessage(event)
        this._pkceMessageListener = listener
        window.addEventListener('message', listener)
      } catch (err) {
        console.error('❌ Claude Code 授权启动失败', err)
        ElMessage.error('启动 Claude Code 授权失败')
      }
    },

    _handlePkceMessage(event: MessageEvent) {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'oauth-success' && data.provider === 'claude_code') {
        this._closePkceDialog()
        this.fetchStatus('claude_code')
        ElMessage.success('Claude Code 授权成功')
      } else if (data.type === 'oauth-error' && data.provider === 'claude_code') {
        this._closePkceDialog()
        ElMessage.error(`Claude Code 授权失败：${data.error || '未知错误'}`)
      }
    },

    _closePkceDialog() {
      this.pkceDialogOpen = false
      try {
        this.pkceDialogPopup?.close()
      } catch (e) {
        console.warn('⚠️ 关闭 popup 失败（浏览器可能拦截）', e)
      }
      this.pkceDialogPopup = null
      if (this._pkceMessageListener) {
        window.removeEventListener('message', this._pkceMessageListener)
        this._pkceMessageListener = null
      }
    },

    cancelPkceFlow() {
      this._closePkceDialog()
    },

    async startCodexFlow(): Promise<void> {
      try {
        const resp = await oauthApi.authorizeCodex()
        this.deviceCodeState = {
          user_code: resp.user_code,
          verification_uri: resp.verification_uri,
          expires_at: Date.now() + resp.expires_in * 1000,
          interval: resp.interval,
          poll_timer: null,
        }
        this.deviceCodeDialogOpen = true
        this._scheduleNextPoll()
      } catch (err) {
        console.error('❌ Codex 授权启动失败', err)
        ElMessage.error('启动 Codex 授权失败')
      }
    },

    _scheduleNextPoll() {
      if (!this.deviceCodeState) return
      const intervalMs = (this.deviceCodeState.interval || 5) * 1000
      this.deviceCodeState.poll_timer = window.setTimeout(
        () => this._pollOnce(),
        intervalMs,
      )
    },

    async _pollOnce() {
      if (!this.deviceCodeState) return
      try {
        const result = await oauthApi.pollCodex()
        if (!this.deviceCodeState) return // cancelled mid-flight
        if (result.status === 'bound') {
          this._closeDeviceCodeDialog()
          await this.fetchStatus('codex')
          ElMessage.success('Codex 授权成功')
          return
        }
        if (result.status === 'expired') {
          this._closeDeviceCodeDialog()
          ElMessage.error('授权码已过期，请重新开始')
          return
        }
        if (result.status === 'denied') {
          this._closeDeviceCodeDialog()
          ElMessage.warning('您拒绝了授权')
          return
        }
        // pending
        if (result.increment_interval) {
          this.deviceCodeState.interval += 5
        }
        if (Date.now() >= this.deviceCodeState.expires_at) {
          this._closeDeviceCodeDialog()
          ElMessage.error('授权码已过期，请重新开始')
          return
        }
        this._scheduleNextPoll()
      } catch (err) {
        console.error('❌ Codex 轮询失败', err)
        // Network blip — retry one more interval; if still failing, bail.
        if (this.deviceCodeState) {
          if (Date.now() >= this.deviceCodeState.expires_at) {
            this._closeDeviceCodeDialog()
            ElMessage.error('授权过程网络中断，请重新开始')
            return
          }
          this._scheduleNextPoll()
        }
      }
    },

    _closeDeviceCodeDialog() {
      if (this.deviceCodeState?.poll_timer) {
        clearTimeout(this.deviceCodeState.poll_timer)
      }
      this.deviceCodeDialogOpen = false
      this.deviceCodeState = null
    },

    cancelCodexFlow() {
      this._closeDeviceCodeDialog()
    },

    async refresh(provider: OAuthProvider): Promise<void> {
      try {
        await oauthApi.refresh(provider)
        await this.fetchStatus(provider)
        ElMessage.success('刷新成功')
      } catch (err) {
        console.error('❌ 刷新 token 失败', err)
        ElMessage.error('刷新失败，请重新授权')
      }
    },

    async unbind(provider: OAuthProvider): Promise<void> {
      try {
        await oauthApi.unbind(provider)
        await this.fetchStatus(provider)
        ElMessage.success('已解绑')
      } catch (err) {
        console.error('❌ 解绑失败', err)
        ElMessage.error('解绑失败')
      }
    },
  },
})
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/oauth.ts
git commit -m "feat(pr3): add Pinia OAuth store with PKCE + device-code flows"
```

---

## Task 3: ClaudeCodePkceDialog

**Files:**
- Create: `frontend/src/components/oauth/ClaudeCodePkceDialog.vue`

- [ ] **Step 1: Create the dialog component**

```vue
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
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/oauth/ClaudeCodePkceDialog.vue
git commit -m "feat(pr3): add ClaudeCodePkceDialog waiting modal"
```

---

## Task 4: CodexDeviceCodeDialog

**Files:**
- Create: `frontend/src/components/oauth/CodexDeviceCodeDialog.vue`

- [ ] **Step 1: Create the dialog component**

```vue
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
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/oauth/CodexDeviceCodeDialog.vue
git commit -m "feat(pr3): add CodexDeviceCodeDialog with big code + polling timer"
```

---

## Task 5: SubscriptionAuthManagement main panel

**Files:**
- Create: `frontend/src/views/Settings/components/SubscriptionAuthManagement.vue`

- [ ] **Step 1: Create the panel component**

```vue
<!-- frontend/src/views/Settings/components/SubscriptionAuthManagement.vue -->
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
```

- [ ] **Step 2: Create the per-provider card subcomponent**

Create: `frontend/src/views/Settings/components/SubscriptionProviderCard.vue`

```vue
<!-- frontend/src/views/Settings/components/SubscriptionProviderCard.vue -->
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
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/Settings/components/SubscriptionAuthManagement.vue frontend/src/views/Settings/components/SubscriptionProviderCard.vue
git commit -m "feat(pr3): add SubscriptionAuthManagement panel + ProviderCard"
```

---

## Task 6: Wire ConfigManagement menu + content section

**Files:**
- Modify: `frontend/src/views/Settings/ConfigManagement.vue`

- [ ] **Step 1: Add menu item between 大模型配置 and 数据源配置**

Open `frontend/src/views/Settings/ConfigManagement.vue`. Find lines 43-50 (the `<el-menu-item index="llm">` and `<el-menu-item index="datasource">`). Insert a new menu item between them:

Replace:

```vue
            <el-menu-item index="llm">
              <el-icon><Cpu /></el-icon>
              <span>大模型配置</span>
            </el-menu-item>
            <el-menu-item index="datasource">
              <el-icon><DataBoard /></el-icon>
              <span>数据源配置</span>
            </el-menu-item>
```

With:

```vue
            <el-menu-item index="llm">
              <el-icon><Cpu /></el-icon>
              <span>大模型配置</span>
            </el-menu-item>
            <el-menu-item index="subscription-auth">
              <el-icon><Lock /></el-icon>
              <span>订阅授权</span>
            </el-menu-item>
            <el-menu-item index="datasource">
              <el-icon><DataBoard /></el-icon>
              <span>数据源配置</span>
            </el-menu-item>
```

- [ ] **Step 2: Add the icon import**

Find the icon import block (around line 1078-1094). Add `Lock` to the imported icons:

Replace:

```ts
import {
  Setting,
  Cpu,
  DataBoard,
  Coin,
  Tools,
  Download,
  Upload,
  Plus,
  Refresh,
  Key,
  OfficeBuilding,
  CircleCheck,
  Collection,
  Star,
  Money
} from '@element-plus/icons-vue'
```

With:

```ts
import {
  Setting,
  Cpu,
  DataBoard,
  Coin,
  Tools,
  Download,
  Upload,
  Plus,
  Refresh,
  Key,
  Lock,
  OfficeBuilding,
  CircleCheck,
  Collection,
  Star,
  Money
} from '@element-plus/icons-vue'
```

- [ ] **Step 3: Add the content section**

Find the LLM section's end (around line 367 — `</el-card>` followed by the datasource section starting around line 368). Insert a new content section between them.

Find this anchor (around line 366-368):

```vue
        </el-card>

        <!-- 数据源配置 -->
        <el-card v-show="activeTab === 'datasource'" class="config-content" shadow="never">
```

Replace with:

```vue
        </el-card>

        <!-- 订阅授权 -->
        <el-card v-show="activeTab === 'subscription-auth'" class="config-content" shadow="never">
          <template #header>
            <div class="card-header">
              <h3>订阅授权</h3>
              <span class="header-hint">
                使用您的 Anthropic / ChatGPT 订阅授权 TradingAgents-CN 调用大模型。
              </span>
            </div>
          </template>
          <SubscriptionAuthManagement />
        </el-card>

        <!-- 数据源配置 -->
        <el-card v-show="activeTab === 'datasource'" class="config-content" shadow="never">
```

- [ ] **Step 4: Import the new panel component**

Find the import block (around line 1106-1113). Add the SubscriptionAuthManagement import:

Replace:

```ts
import ConfigValidator from '@/components/ConfigValidator.vue'
import LLMConfigDialog from './components/LLMConfigDialog.vue'
import ProviderDialog from './components/ProviderDialog.vue'
import ModelCatalogManagement from './components/ModelCatalogManagement.vue'
import DataSourceConfigDialog from './components/DataSourceConfigDialog.vue'
import MarketCategoryManagement from './components/MarketCategoryManagement.vue'
import DataSourceGroupingDialog from './components/DataSourceGroupingDialog.vue'
import SortableDataSourceList from './components/SortableDataSourceList.vue'
```

With:

```ts
import ConfigValidator from '@/components/ConfigValidator.vue'
import LLMConfigDialog from './components/LLMConfigDialog.vue'
import ProviderDialog from './components/ProviderDialog.vue'
import ModelCatalogManagement from './components/ModelCatalogManagement.vue'
import DataSourceConfigDialog from './components/DataSourceConfigDialog.vue'
import MarketCategoryManagement from './components/MarketCategoryManagement.vue'
import DataSourceGroupingDialog from './components/DataSourceGroupingDialog.vue'
import SortableDataSourceList from './components/SortableDataSourceList.vue'
import SubscriptionAuthManagement from './components/SubscriptionAuthManagement.vue'
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Settings/ConfigManagement.vue
git commit -m "feat(pr3): add 订阅授权 menu item + content section to ConfigManagement"
```

---

## Task 7: Mount global dialogs in App.vue

**Files:**
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Add the two dialogs to the template**

Open `frontend/src/App.vue`. Find the existing template block (lines 1-25):

```vue
<template>
  <div id="app" class="app-container">
    <!-- 网络状态指示器 -->
    <NetworkStatus />

    <!-- 主要内容区域 -->
    <router-view v-slot="{ Component, route }">
      <transition
        :name="(route?.meta?.transition as string) || 'fade'"
        mode="out-in"
        appear
      >
        <keep-alive :include="keepAliveComponents">
          <component :is="Component" :key="route?.fullPath || 'default'" />
        </keep-alive>
      </transition>
    </router-view>

    <!-- 配置向导 -->
    <ConfigWizard
      v-model="showConfigWizard"
      @complete="handleWizardComplete"
    />
  </div>
</template>
```

Replace with:

```vue
<template>
  <div id="app" class="app-container">
    <!-- 网络状态指示器 -->
    <NetworkStatus />

    <!-- 主要内容区域 -->
    <router-view v-slot="{ Component, route }">
      <transition
        :name="(route?.meta?.transition as string) || 'fade'"
        mode="out-in"
        appear
      >
        <keep-alive :include="keepAliveComponents">
          <component :is="Component" :key="route?.fullPath || 'default'" />
        </keep-alive>
      </transition>
    </router-view>

    <!-- 配置向导 -->
    <ConfigWizard
      v-model="showConfigWizard"
      @complete="handleWizardComplete"
    />

    <!-- OAuth 全局弹窗 -->
    <ClaudeCodePkceDialog />
    <CodexDeviceCodeDialog />
  </div>
</template>
```

- [ ] **Step 2: Add imports for the dialogs**

In the same file, find the script-setup imports (line 27-32). Add the two dialog imports:

Replace:

```ts
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import NetworkStatus from '@/components/NetworkStatus.vue'
import axios from 'axios'
import { configApi } from '@/api/config'
```

With:

```ts
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import NetworkStatus from '@/components/NetworkStatus.vue'
import axios from 'axios'
import { configApi } from '@/api/config'
import ClaudeCodePkceDialog from '@/components/oauth/ClaudeCodePkceDialog.vue'
import CodexDeviceCodeDialog from '@/components/oauth/CodexDeviceCodeDialog.vue'
```

Note: `ConfigWizard` is auto-resolved (already working in the existing template), so the two new dialogs are imported explicitly to match.

- [ ] **Step 3: Type-check**

Run: `cd frontend && yarn type-check`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat(pr3): mount OAuth PKCE + device-code dialogs as App.vue globals"
```

---

## Task 8: LLMConfigDialog subscription provider integration

**Files:**
- Modify: `frontend/src/views/Settings/components/LLMConfigDialog.vue`

The strategy: inject `claude_code` and `codex` as synthetic options in the provider dropdown. When the user picks one, swap the API-key / Base-URL inputs for a status card with «管理订阅» / «立即授权» buttons. Saving still calls `configApi.updateLLMConfig` unchanged.

- [ ] **Step 1: Add the synthetic provider injection**

Find `loadProviders` (around lines 702-725). Replace the function body so it injects the two subscription providers at the top of the dropdown:

Replace:

```ts
// 加载可用的厂家列表
const loadProviders = async (showSuccessMessage = false) => {
  providersLoading.value = true
  try {
    const providers = await configApi.getLLMProviders()
    // 只显示启用的厂家
    availableProviders.value = providers.filter(p => p.is_active)
    console.log('✅ 加载厂家列表成功:', availableProviders.value.length)

    if (showSuccessMessage) {
      ElMessage.success(`已刷新供应商列表，共 ${availableProviders.value.length} 个启用的供应商`)
    }

    // 如果是新增模式且没有选择供应商，默认选择第一个
    if (!isEdit.value && !formData.value.provider && availableProviders.value.length > 0) {
      formData.value.provider = availableProviders.value[0].name
      await handleProviderChange(formData.value.provider)
    }
  } catch (error) {
    console.error('❌ 加载厂家列表失败:', error)
    ElMessage.error('加载厂家列表失败')
  } finally {
    providersLoading.value = false
  }
}
```

With:

```ts
// 订阅类（OAuth）供应商不存在于 providers 表中——以合成项注入下拉
const SUBSCRIPTION_PROVIDERS: LLMProvider[] = [
  {
    id: 'claude_code',
    name: 'claude_code',
    display_name: 'Claude Code (订阅)',
    is_active: true,
    supported_features: ['chat'],
  } as LLMProvider,
  {
    id: 'codex',
    name: 'codex',
    display_name: 'Codex / ChatGPT (订阅)',
    is_active: true,
    supported_features: ['chat'],
  } as LLMProvider,
]

const SUBSCRIPTION_PROVIDER_NAMES = new Set(SUBSCRIPTION_PROVIDERS.map(p => p.name))

// 加载可用的厂家列表
const loadProviders = async (showSuccessMessage = false) => {
  providersLoading.value = true
  try {
    const providers = await configApi.getLLMProviders()
    // 只显示启用的厂家，前置两个订阅类合成项
    availableProviders.value = [
      ...SUBSCRIPTION_PROVIDERS,
      ...providers.filter(p => p.is_active),
    ]
    console.log('✅ 加载厂家列表成功:', availableProviders.value.length)

    if (showSuccessMessage) {
      ElMessage.success(`已刷新供应商列表，共 ${availableProviders.value.length} 个供应商`)
    }

    // 如果是新增模式且没有选择供应商，默认选择第一个真实厂家（跳过订阅项）
    if (!isEdit.value && !formData.value.provider) {
      const firstReal = availableProviders.value.find(p => !SUBSCRIPTION_PROVIDER_NAMES.has(p.name))
      if (firstReal) {
        formData.value.provider = firstReal.name
        await handleProviderChange(formData.value.provider)
      }
    }
  } catch (error) {
    console.error('❌ 加载厂家列表失败:', error)
    ElMessage.error('加载厂家列表失败')
  } finally {
    providersLoading.value = false
  }
}
```

- [ ] **Step 2: Add a `isSubscriptionProvider` computed flag**

Find the `Computed` block (around line 379-380). Right after `const isEdit = computed(() => !!props.config)`, add:

Replace:

```ts
// Computed
const isEdit = computed(() => !!props.config)
```

With:

```ts
// Computed
const isEdit = computed(() => !!props.config)

const isSubscriptionProvider = computed(() =>
  SUBSCRIPTION_PROVIDER_NAMES.has(formData.value.provider),
)
</script>
```

Note: that `</script>` close is just a marker — do NOT add a duplicate `</script>` to the file. Remove the trailing `</script>` from the snippet when applying; this is shown to help identify the location.

Actually — let me give a cleaner replacement that doesn't risk a stray tag. Replace the line:

```ts
const isEdit = computed(() => !!props.config)
```

With:

```ts
const isEdit = computed(() => !!props.config)

const isSubscriptionProvider = computed(() =>
  SUBSCRIPTION_PROVIDER_NAMES.has(formData.value.provider),
)
```

- [ ] **Step 3: Import the OAuth store and types at the top of `<script setup>`**

Find the existing imports (around line 350-355):

Replace:

```ts
<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { configApi, type LLMProvider, type LLMConfig, validateLLMConfig } from '@/api/config'
```

With:

```ts
<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { configApi, type LLMProvider, type LLMConfig, validateLLMConfig } from '@/api/config'
import { useOAuthStore } from '@/stores/oauth'

const oauthStore = useOAuthStore()
```

- [ ] **Step 4: On dialog open, refresh OAuth status for both providers**

Find the `watch(() => props.visible, async (visible) => { … })` block (around line 600-649). At the end of the `if (visible) { … }` body (right before the closing `}`), trigger an OAuth status fetch:

Find this anchor near the end of the visible block (around line 645-648):

```ts
        selectedModelKey.value = ''
      }
    }
  }
)
```

Replace with:

```ts
        selectedModelKey.value = ''
      }
      // 拉一次订阅状态，让状态卡反映最新绑定
      oauthStore.fetchAllStatus()
    }
  }
)
```

- [ ] **Step 5: Replace the Base URL form-item with a conditional block**

In the template, find the existing API Base URL block (around lines 89-97):

```vue
      <el-form-item label="API基础URL" prop="api_base">
        <el-input
          v-model="formData.api_base"
          placeholder="可选，自定义API端点（留空使用厂家默认地址）"
        />
        <div class="form-tip">
          💡 API密钥已在厂家配置中设置，此处只需配置模型参数
        </div>
      </el-form-item>
```

Replace with:

```vue
      <el-form-item label="API基础URL" prop="api_base" v-if="!isSubscriptionProvider">
        <el-input
          v-model="formData.api_base"
          placeholder="可选，自定义API端点（留空使用厂家默认地址）"
        />
        <div class="form-tip">
          💡 API密钥已在厂家配置中设置，此处只需配置模型参数
        </div>
      </el-form-item>

      <!-- 订阅类 provider 的状态卡 -->
      <el-form-item label="订阅状态" v-if="isSubscriptionProvider">
        <div v-if="subscriptionStatus?.bound" class="subscription-status bound">
          <el-icon><CircleCheck /></el-icon>
          <span>您的订阅已绑定，无需 API Key</span>
          <el-link type="primary" @click="goManageSubscription">管理订阅 →</el-link>
        </div>
        <div v-else class="subscription-status unbound">
          <el-icon><Warning /></el-icon>
          <span>您当前未绑定此订阅。配置仍可保存（系统级），但您本人将无法用此配置跑分析。</span>
          <el-button size="small" type="warning" @click="bindSubscription">立即授权</el-button>
        </div>
        <div class="form-tip">
          💡 订阅模式由 OAuth 路由，无需自定义 API Key 或 Base URL
        </div>
      </el-form-item>
```

- [ ] **Step 6: Add the `CircleCheck` / `Warning` icon imports and helper logic**

In the script imports, replace:

```ts
import { Refresh } from '@element-plus/icons-vue'
```

With:

```ts
import { Refresh, CircleCheck, Warning } from '@element-plus/icons-vue'
```

Then, right after the `isSubscriptionProvider` computed you added in Step 2, add:

```ts
const subscriptionStatus = computed(() => {
  if (!isSubscriptionProvider.value) return null
  return oauthStore.statusFor(formData.value.provider as 'claude_code' | 'codex')
})

const bindSubscription = () => {
  if (formData.value.provider === 'claude_code') {
    oauthStore.startClaudeCodeFlow()
  } else if (formData.value.provider === 'codex') {
    oauthStore.startCodexFlow()
  }
}

const goManageSubscription = () => {
  emit('navigate-subscription')
}
```

- [ ] **Step 7: Update the emits to expose `navigate-subscription`**

Find the existing `defineEmits` (around line 368-371):

Replace:

```ts
// Emits
const emit = defineEmits<{
  'update:visible': [value: boolean]
  'success': []
}>()
```

With:

```ts
// Emits
const emit = defineEmits<{
  'update:visible': [value: boolean]
  'success': []
  'navigate-subscription': []
}>()
```

- [ ] **Step 8: Wire `@navigate-subscription` on the LLMConfigDialog usage in ConfigManagement**

Open `frontend/src/views/Settings/ConfigManagement.vue` and `grep -n 'LLMConfigDialog' ConfigManagement.vue` to find the existing `<LLMConfigDialog>` tag. Look for a line like:

```vue
<LLMConfigDialog v-model:visible="..." :config="..." @success="..." />
```

Add `@navigate-subscription="activeTab = 'subscription-auth'"` to the existing usage. If multiple usages exist, add it to each. For example:

If you find:

```vue
<LLMConfigDialog
  v-model:visible="llmDialogVisible"
  :config="currentLLMConfig"
  @success="loadLLMConfigs"
/>
```

Replace with:

```vue
<LLMConfigDialog
  v-model:visible="llmDialogVisible"
  :config="currentLLMConfig"
  @success="loadLLMConfigs"
  @navigate-subscription="activeTab = 'subscription-auth'"
/>
```

Verify the variable name (`llmDialogVisible`, `currentLLMConfig`) by reading the file — they may differ slightly. The required addition is just `@navigate-subscription="activeTab = 'subscription-auth'"` on the existing tag(s).

- [ ] **Step 9: Add scoped styles for the status card**

At the end of the `<style lang="scss" scoped>` block in `LLMConfigDialog.vue` (after the existing rules, around line 770), add:

```scss
.subscription-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  flex-wrap: wrap;

  &.bound {
    background: var(--el-color-success-light-9);
    color: var(--el-color-success);
  }

  &.unbound {
    background: var(--el-color-warning-light-9);
    color: var(--el-color-warning);
  }
}
```

- [ ] **Step 10: Type-check + build**

Run: `cd frontend && yarn type-check`
Expected: PASS.

Run: `cd frontend && yarn build`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/views/Settings/components/LLMConfigDialog.vue frontend/src/views/Settings/ConfigManagement.vue
git commit -m "feat(pr3): integrate subscription providers into LLMConfigDialog"
```

---

## Task 9: Smoke test checklist

**Files:**
- Create: `scripts/smoke_test_pr3_ui.md`

- [ ] **Step 1: Write the checklist**

```markdown
# PR-3 UI Smoke Test Checklist

> Manual end-to-end checklist for the OAuth subscription auth frontend.
> Prereqs: backend running (`uvicorn app.main:app --reload`), MongoDB + Redis up,
> a real Anthropic Pro/Max account and ChatGPT Plus/Pro account for binding,
> `OAUTH_ENCRYPTION_KEY` set in `.env`.

## Setup

- [ ] Start backend: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- [ ] Start frontend: `cd frontend && yarn dev`
- [ ] Log in as an admin user in the web UI.

## 1. 订阅授权 panel — first impression

- [ ] Navigate to: 设置 → 配置管理 → 订阅授权
- [ ] Both cards render with: title, subtitle, 「未绑定」 badge, hint text, action button.
- [ ] No console errors.

## 2. Claude Code PKCE flow

- [ ] Click 「登录 Claude」.
- [ ] PKCE waiting dialog appears with spinner + "请在弹出窗口中完成授权" hint.
- [ ] A popup window opens to `claude.ai/oauth/authorize?...`.
- [ ] Complete authorization in the popup.
- [ ] Popup auto-closes; PKCE dialog auto-closes; success toast shows.
- [ ] Card flips to «✓ 已绑定» with «有效期：还剩 X 分» and «上次刷新：刚刚».
- [ ] Refresh the page; binding persists.

## 3. Codex device-code flow

- [ ] Click 「登录 ChatGPT」.
- [ ] Dialog shows big `user_code` (e.g. `ABCD-EFGH`) + verification link + countdown.
- [ ] Click 「复制 code」; confirm clipboard contains the code.
- [ ] Click the verification link → opens `auth.openai.com/codex/device` in new tab.
- [ ] Enter the code there and authorize via your ChatGPT account.
- [ ] Within ~5s the polling picks up; dialog closes; success toast shows.
- [ ] Card flips to «✓ 已绑定» with valid expiry.

## 4. Manual refresh

- [ ] Click 「手动刷新」 on each bound card.
- [ ] «上次刷新» updates to «刚刚»; «有效期» bumps forward.

## 5. Unbind

- [ ] Click 「解绑」 on a bound card → confirm dialog appears.
- [ ] Confirm → card flips to «未绑定». Refresh page; still «未绑定».

## 6. LLMConfigDialog integration

- [ ] In 大模型配置 tab, click 「添加配置」.
- [ ] Provider dropdown now lists «Claude Code (订阅)» and «Codex / ChatGPT (订阅)» at the top.
- [ ] Select «Claude Code (订阅)»:
  - [ ] API Base URL field disappears.
  - [ ] If currently bound (per the admin user): green status box shows «您的订阅已绑定», + 「管理订阅 →」 link.
  - [ ] Click 「管理订阅 →」 → dialog closes, tab switches to 订阅授权.
  - [ ] If currently unbound: yellow status box shows warning + 「立即授权」 button.
  - [ ] Click 「立即授权」 → OAuth flow starts from inside the LLMConfigDialog.
- [ ] Select a model name + save → the LLM config is persisted system-wide.

## 7. Error / edge cases

- [ ] **Popup blocked**: temporarily block popups for `localhost:5173` in browser settings; click 「登录 Claude」; expect Element Plus error toast «弹窗被浏览器拦截...».
- [ ] **Codex code expired**: open Codex flow but don't authorize; wait 10+ minutes; expect dialog to auto-close with «授权码已过期» toast.
- [ ] **Codex cancel mid-flow**: click 「取消」 in the Codex dialog while polling; dialog closes immediately; no further polls (verify in Network tab).
- [ ] **Background status refresh**: stay on 订阅授权 tab; wait 30s; verify another `/api/oauth/status/*` request fires (Network tab).

## 8. UI polish spot-checks

- [ ] Cards stack vertically on small viewports (resize browser narrower than ~600px).
- [ ] Countdown ticks every second in Codex dialog.
- [ ] «即将过期» orange tag appears when bound and `<10 min` from expiry (force via backend refresh + clock).
```

- [ ] **Step 2: Commit**

```bash
git add scripts/smoke_test_pr3_ui.md
git commit -m "docs(pr3): add manual UI smoke test checklist"
```

---

## Task 10: Final build + lint + manual smoke

- [ ] **Step 1: Full build & type-check**

Run: `cd frontend && yarn type-check && yarn build`
Expected: both PASS without errors.

- [ ] **Step 2: Lint**

Run: `cd frontend && yarn lint`
Expected: PASS. Fix any errors before continuing (do NOT --no-verify or skip).

- [ ] **Step 3: Start dev servers**

Open two terminals:

```bash
# Terminal 1
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2
cd frontend && yarn dev
```

- [ ] **Step 4: Walk the smoke checklist**

Open `scripts/smoke_test_pr3_ui.md` and tick through every item. Report failures inline rather than continuing past them.

- [ ] **Step 5: If all green, do a final commit (or skip if nothing changed)**

If you found any minor tweaks during smoke testing (UI copy, spacing, color), include them in a final commit:

```bash
git add -A
git commit -m "fix(pr3): polish from smoke testing"
```

- [ ] **Step 6: Push the branch**

```bash
git push -u origin feat/pr3-oauth-frontend
```

- [ ] **Step 7: Open PR**

```bash
gh pr create --title "PR-3: 订阅鉴权前端 UI" --body "$(cat <<'EOF'
## Summary
- 新增 `订阅授权` 配置子菜单，可视化管理 Claude Code (Anthropic) / Codex (ChatGPT) 订阅绑定
- 实现 PKCE flow 弹窗（Claude）与 device-code flow 弹窗（Codex），含轮询、倒计时、复制 code
- LLMConfigDialog 集成：选订阅 provider 时隐藏 API Key 输入，显示绑定状态卡 + 「立即授权」/「管理订阅」入口
- 新增 Pinia store (`useOAuthStore`) 统一管理 OAuth 状态与流程
- 不动后端（合规 modal 已在 PR-3 spec 中明确 out-of-scope）

## Test plan
- [ ] `yarn type-check && yarn build && yarn lint` 全部通过
- [ ] 走完 `scripts/smoke_test_pr3_ui.md` 全部 8 个 section
- [ ] Claude Code 与 Codex 都至少完整跑过一次「绑定 → 刷新 → 解绑 → 重绑」循环

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Notes

**Spec coverage:**
- § 1.1 scope — ✓ 全覆盖（panel + 两个 dialog + LLMConfigDialog 集成 + store）
- § 2 architecture — ✓ 文件结构匹配
- § 3.1 API wrapper — ✓ Task 1
- § 3.2 Pinia store — ✓ Task 2（去 consent 后的版本）
- § 3.3 SubscriptionAuthManagement — ✓ Task 5
- § 3.4 ClaudeCodePkceDialog — ✓ Task 3
- § 3.5 CodexDeviceCodeDialog — ✓ Task 4
- § 3.6 App.vue mount — ✓ Task 7
- § 3.7 LLMConfigDialog 改动 — ✓ Task 8
- § 4 后端微调（无） — ✓ skipped intentionally
- § 5 交互流程 — ✓ 已在 store 中实现
- § 6 错误处理 — ✓ Task 2 / dialogs 中分散覆盖；smoke § 7 验证
- § 7 测试策略 — ✓ Task 9 (smoke checklist)
- § 8 工作量 — 当前 plan ~10 任务 × ~15 分钟/任务 ≈ 2.5 人日，对齐
- § 9 风险 — 弹窗拦截、postMessage、轮询取消、自然过期 — 都在 store + dialogs 里处理
- § 10 待定问题 — popup.close 兜底（try/catch），10min 「即将过期」阈值（硬编码 + 注释）

**Plan tweaks vs spec:**
- spec § 3.3 描述的卡片直接画在 SubscriptionAuthManagement.vue 中；plan 拆出 SubscriptionProviderCard.vue 子组件，避免 DRY 违例（两张卡 95% 一致）。架构上更清晰，工作量不变。
- spec § 3.7 «3 个全局 dialog» 历史遗留措辞已在 spec 修订中改为 2 个，plan 配套只挂 2 个。
- spec 让 store action 直接调 `window.open` 与 `addEventListener`；plan 把 listener 引用存到 state 字段 `_pkceMessageListener` 以便 `removeEventListener`，这是必要的实现细节（不存引用就会泄漏 listener）。

**Placeholder scan:** No TBD / TODO / "similar to". All code blocks complete.

**Type consistency:** `OAuthStatus.bound` is `boolean`; consumers check `status?.bound` everywhere. `statusFor` getter returns `OAuthStatus | null` matching component prop. `OAuthProvider` literal type used consistently.
