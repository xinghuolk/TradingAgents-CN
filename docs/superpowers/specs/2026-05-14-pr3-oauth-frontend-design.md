# PR-3：订阅鉴权前端 UI 设计

> 状态：设计已批准，等待 spec 评审 → 实施计划
> 日期：2026-05-14
> 关联文档：
> - 整体路线图：[`docs/design/llm-subscription-auth-design.md`](../../design/llm-subscription-auth-design.md)
> - PR-2 后端设计：[`docs/superpowers/specs/2026-05-14-pr2-web-oauth-design.md`](2026-05-14-pr2-web-oauth-design.md)
> - PR-2 实施计划：[`docs/superpowers/plans/2026-05-14-pr2-web-oauth.md`](../plans/2026-05-14-pr2-web-oauth.md)
> - PR-1 实施计划：[`docs/superpowers/plans/2026-05-14-subscription-auth-pr1.md`](../plans/2026-05-14-subscription-auth-pr1.md)
> 实施分支：从 `main`（PR-1 + PR-2 + PR-2.5 已合并）开新分支 `feat/pr3-oauth-frontend`

## 1. 目标与范围

为 PR-2 后端的 7 个 REST 端点提供完整的 Vue 3 Web UI：让用户在浏览器内完成 Claude Code / Codex 订阅授权，查看绑定状态，手动刷新或解绑。

### 1.1 范围 in scope

- 「订阅授权」主面板（新增 `ConfigManagement` 左菜单项）
- Anthropic PKCE flow 弹窗与回调消息处理
- Codex device-code flow modal（含轮询）
- LLMConfigDialog 集成：选订阅 provider 时显示绑定状态卡 / 警告 + 「立即授权」按钮
- Pinia store 统一管理 OAuth 状态与流程触发

### 1.2 范围 out of scope

- **合规免责声明 modal**（本地使用场景暂不需要；未来上线生产前再加，届时补 `consent_acknowledged_at` 字段 + 端点 + 前端 modal）
- LLMConfig 改为 per-user（架构层面，明确不动；保持系统级 + per-user OAuth 组合）
- 多语言 i18n（项目中文优先，UI 文案硬编码中文）
- 浏览器扩展 / desktop app
- Codex CLI 路径 `~/.codex/auth.json` 自动写回（属 PR-1 deferred）
- macOS Keychain writer（PR-1 final review C1 deferred）

## 2. 架构

**LLMConfig 维持系统级（admin 配置一份，所有用户共享）+ OAuth 凭据 per-user**。两者通过 `analysis_service.py` 在 request 时按当前 user_id 拉取 OAuth token 注入 config。

```
┌────────────────────────────────────────────────────────────────┐
│ Frontend (Vue 3 + Element Plus + Pinia)                        │
├────────────────────────────────────────────────────────────────┤
│  src/views/Settings/ConfigManagement.vue                       │
│   └── 左菜单新增「订阅授权」                                    │
│       └── components/SubscriptionAuthManagement.vue (新)       │
│           └── 横向并排两张卡片：Claude Code / Codex            │
│                                                                │
│  src/components/oauth/ (新目录)                                │
│   ├── ClaudeCodePkceDialog.vue       PKCE 弹窗触发 + 监听      │
│   └── CodexDeviceCodeDialog.vue      device-code 大码 + 轮询    │
│                                                                │
│  src/stores/oauth.ts (新)            状态 + actions             │
│  src/api/oauth.ts (新)               REST 包装                  │
│                                                                │
│  src/App.vue                         挂载 2 个全局 dialog       │
│  src/views/Settings/components/                                │
│    LLMConfigDialog.vue (改)          选订阅 provider 时显示状态 │
└────────────────────────────────────────────────────────────────┘
                          │
                          │  REST + postMessage
                          ▼
┌────────────────────────────────────────────────────────────────┐
│ Backend (PR-2 已实现 + PR-3 微调)                              │
├────────────────────────────────────────────────────────────────┤
│  GET    /api/oauth/status/{provider}     → bound? + expiry     │
│  GET    /api/oauth/authorize/claude_code → authorize_url       │
│  POST   /api/oauth/authorize/codex       → user_code + uri     │
│  GET    /api/oauth/callback/claude_code  → HTML + postMessage  │
│  POST   /api/oauth/poll/codex            → status 状态机       │
│  POST   /api/oauth/refresh/{provider}    → 强制刷新             │
│  DELETE /api/oauth/unbind/{provider}     → 删 doc              │
└────────────────────────────────────────────────────────────────┘

PR-3 不新增后端端点（合规 modal 已从 scope 移除）。
```

## 3. 前端模块详设

### 3.1 `src/api/oauth.ts` (新)

封装 7 + 1 个端点。返回 axios response 的 `.data` 部分。

```typescript
import { request } from './request'

export type OAuthProvider = 'claude_code' | 'codex'

export interface OAuthStatus {
  bound: boolean
  provider: OAuthProvider
  expires_at: string | null      // ISO datetime
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

export interface PollCodexResponse {
  status: 'pending' | 'bound' | 'expired' | 'denied'
  increment_interval: boolean
}

export const oauthApi = {
  status: (provider: OAuthProvider) =>
    request.get<OAuthStatus>(`/api/oauth/status/${provider}`),

  authorizeClaudeCode: () =>
    request.get<AuthorizeClaudeCodeResponse>('/api/oauth/authorize/claude_code'),

  authorizeCodex: () =>
    request.post<AuthorizeCodexResponse>('/api/oauth/authorize/codex'),

  pollCodex: () =>
    request.post<PollCodexResponse>('/api/oauth/poll/codex'),

  refresh: (provider: OAuthProvider) =>
    request.post<{ status: 'refreshed' }>(`/api/oauth/refresh/${provider}`),

  unbind: (provider: OAuthProvider) =>
    request.delete(`/api/oauth/unbind/${provider}`),
}
```

### 3.2 `src/stores/oauth.ts` (新)

```typescript
import { defineStore } from 'pinia'
import { oauthApi, OAuthProvider, OAuthStatus } from '@/api/oauth'

interface DeviceCodeState {
  user_code: string
  verification_uri: string
  expires_at: number   // epoch ms
  interval: number     // seconds, may grow on slow_down
  poll_timer: number | null
}

export const useOAuthStore = defineStore('oauth', {
  state: () => ({
    claudeCodeStatus: null as OAuthStatus | null,
    codexStatus: null as OAuthStatus | null,

    // Dialog open/close flags (single source of truth for App.vue mounted dialogs)
    pkceDialogOpen: false,
    pkceDialogPopup: null as Window | null,
    pkceDialogProvider: null as OAuthProvider | null,    // claude_code

    deviceCodeDialogOpen: false,
    deviceCodeState: null as DeviceCodeState | null,
  }),

  actions: {
    async fetchStatus(provider: OAuthProvider): Promise<OAuthStatus> { … },
    async fetchAllStatus(): Promise<void> { … },

    /** Entry point — called from "登录 Claude" button anywhere */
    async startClaudeCodeFlow(): Promise<void> {
      // 1. Get authorize_url
      const resp = await oauthApi.authorizeClaudeCode()
      // 2. Open popup
      const popup = window.open(resp.authorize_url, '_blank', 'width=600,height=800')
      if (!popup) { /* popup blocked */ throw new Error('弹窗被浏览器拦截') }
      this.pkceDialogPopup = popup
      this.pkceDialogOpen = true
      // 3. Listen for message from popup
      window.addEventListener('message', this._handlePkceMessage)
    },

    _handlePkceMessage(event: MessageEvent) {
      // Verify origin? backend callback HTML uses '*' target; on production
      // verify event.source === this.pkceDialogPopup or check origin
      if (event.data?.type === 'oauth-success') {
        this.pkceDialogOpen = false
        this.pkceDialogPopup?.close()
        this.fetchStatus('claude_code')
      } else if (event.data?.type === 'oauth-error') {
        this.pkceDialogOpen = false
        this.pkceDialogPopup?.close()
        // Show error toast
      }
      window.removeEventListener('message', this._handlePkceMessage)
    },

    async startCodexFlow(): Promise<void> {
      const resp = await oauthApi.authorizeCodex()
      this.deviceCodeState = {
        user_code: resp.user_code,
        verification_uri: resp.verification_uri,
        expires_at: Date.now() + resp.expires_in * 1000,
        interval: resp.interval,
        poll_timer: null,
      }
      this.deviceCodeDialogOpen = true
      this._startPolling()
    },

    _startPolling() {
      if (!this.deviceCodeState) return
      this.deviceCodeState.poll_timer = window.setTimeout(async () => {
        const result = await oauthApi.pollCodex()
        if (result.status === 'bound') {
          this.deviceCodeDialogOpen = false
          this.deviceCodeState = null
          await this.fetchStatus('codex')
          return
        }
        if (result.status === 'expired' || result.status === 'denied') {
          // Show toast, close dialog
          this.deviceCodeDialogOpen = false
          this.deviceCodeState = null
          return
        }
        // pending — maybe slow down
        if (result.increment_interval && this.deviceCodeState) {
          this.deviceCodeState.interval += 5
        }
        // Check expiry
        if (this.deviceCodeState && Date.now() >= this.deviceCodeState.expires_at) {
          this.deviceCodeDialogOpen = false
          this.deviceCodeState = null
          // Show "expired" toast
          return
        }
        this._startPolling()
      }, (this.deviceCodeState.interval ?? 5) * 1000)
    },

    cancelCodexFlow() {
      if (this.deviceCodeState?.poll_timer) {
        clearTimeout(this.deviceCodeState.poll_timer)
      }
      this.deviceCodeDialogOpen = false
      this.deviceCodeState = null
    },

    async refresh(provider: OAuthProvider) {
      await oauthApi.refresh(provider)
      await this.fetchStatus(provider)
    },

    async unbind(provider: OAuthProvider) {
      await oauthApi.unbind(provider)
      await this.fetchStatus(provider)
    },
  },
})
```

### 3.3 `views/Settings/components/SubscriptionAuthManagement.vue` (新)

主面板组件。挂在 `ConfigManagement.vue` 左菜单的新「订阅授权」项下。

**布局**：横向并排两张 Element Plus `el-card`，等宽。

**每张卡片**显示：
- 头部：provider 名（Claude Code / Codex）+ 副标题（Anthropic Pro/Max 订阅 / OpenAI Plus/Pro 订阅）
- 状态徽章：`el-tag`，type=`success` 已绑定 / `info` 未绑定 / `warning` 即将过期
- 已绑定时：
  - 「有效期：还剩 47 分」（动态计算 `expires_at - now()`）
  - 「上次刷新：2 分钟前」（dayjs.fromNow）
  - 「手动刷新」按钮（调 `store.refresh(provider)`，loading 状态）
  - 「解绑」按钮（弹 `el-message-box` 二次确认，调 `store.unbind`）
- 未绑定时：
  - 「使用您的 X 订阅运行多智能体分析，无需 API Key」
  - 「登录 X」按钮（调 `store.startClaudeCodeFlow()` / `store.startCodexFlow()`）

**生命周期**：
- `onMounted`：调 `store.fetchAllStatus()`
- 定时刷新：`setInterval(fetchAllStatus, 30000)`，组件卸载时清
- 监听 `store.$subscribe` 实时反映状态变化

### 3.4 `components/oauth/ClaudeCodePkceDialog.vue` (新)

挂载在 `App.vue`，由 `store.pkceDialogOpen` 控制显示。

**内容**：
- Element Plus `el-dialog` (modal)
- 显示 loading spinner + 文字「正在等待您在弹出窗口完成授权...」
- 「取消」按钮 → `store.pkceDialogOpen = false` + 关闭 popup（如果还开着）
- 弹窗被浏览器拦截时显示「弹窗被拦截，请允许后[重试]」

**注意**：实际授权动作发生在 store 打开的 popup window 里。这个 Dialog 仅是父窗口的「等待中」UI。

### 3.5 `components/oauth/CodexDeviceCodeDialog.vue` (新)

挂载在 `App.vue`，由 `store.deviceCodeDialogOpen` 控制。

**内容**（B 方案 — 大码居中）：
- 标题：「使用 ChatGPT 订阅登录」
- 大码区：`el-card` shadow="never"，背景 light blue，内容 36px monospace 居中 + 复制按钮
- 跳转链接：「→ 打开授权页 (auth.openai.com/codex/device)」（点击 `window.open` 新 tab）
- 等待提示：黄色 alert「⏱ 等待您完成授权... code 还剩 X 分 Y 秒」
- 「取消」按钮 → `store.cancelCodexFlow()`

**注意**：轮询逻辑在 store 里，Dialog 只显示状态。Dialog 关闭时 store 自动清 timer。

### 3.6 `App.vue` 改动

在 root template 加：

```vue
<template>
  <!-- existing app structure -->
  <ClaudeCodePkceDialog />
  <CodexDeviceCodeDialog />
</template>
```

挂载位置：放在 `el-config-provider` 内部、router-view 同级。

### 3.7 `views/Settings/components/LLMConfigDialog.vue` 改动

定位现有 `LLMConfigDialog.vue` 中 provider 选择 + API Key 输入区域。改动：

1. **provider 下拉**加两个选项：「Claude Code（订阅）」、「Codex（订阅）」
2. **选中订阅 provider 时**：
   - 隐藏 API Key 输入框
   - 显示状态卡：
     - 当前 admin 已绑定 → 绿色提示「✓ 您的订阅已绑定（有效期还剩 X 分）」+ 「管理订阅」链接（跳到「订阅授权」tab）
     - 当前 admin 未绑定 → 黄色 alert「⚠️ 您（admin）当前未绑定此订阅，配置仍可保存，但您本人将无法用此配置跑分析」+「立即授权」按钮（调 `store.startClaudeCodeFlow()`）
   - Base URL 输入框：禁用 + 提示「订阅模式由 OAuth 路由，无需自定义」
3. **保存逻辑不变** — admin 可保存系统级配置，即使自己未绑定（其他用户绑了能用）

## 4. 后端微调

**无后端改动**。PR-2 的 7 个端点已完全覆盖 PR-3 所需。合规 modal 推迟到未来生产化时再加（届时补 `consent_acknowledged_at` 字段、`POST /api/oauth/consent/{provider}` 端点、以及状态端点的 `bound = has ciphertext` 判定）。

## 5. 关键交互流程

### 5.1 Claude Code PKCE flow

```
[用户在订阅授权 tab 或 LLMConfigDialog 点击「登录 Claude」]
  → store.startClaudeCodeFlow()
  → GET /api/oauth/authorize/claude_code → authorize_url + state
  → window.open(authorize_url, 'oauth_popup', '600x800')
  → ClaudeCodePkceDialog 显示「等待中...」
  → addEventListener('message', _handlePkceMessage)

[用户在 popup 中授权]
  → claude.ai 重定向到 /api/oauth/callback/claude_code?code=...&state=...
  → 后端验证 state → 交换 token → 加密入库
  → 返回 HTML，含 <script>window.opener.postMessage(...)</script>
  → popup 关闭自己

[父窗口收到 postMessage]
  → store._handlePkceMessage(event)
  → store.pkceDialogOpen = false
  → store.fetchStatus('claude_code') → 卡片刷新
```

### 5.2 Codex device-code flow

```
[点击「登录 ChatGPT」]
  → store.startCodexFlow()
  → POST /api/oauth/authorize/codex → user_code + verification_uri + interval
  → store.deviceCodeState = {...}, deviceCodeDialogOpen = true
  → CodexDeviceCodeDialog 显示大码 + 跳转链接 + 等待提示
  → store._startPolling() 启动 setTimeout 链

[用户去浏览器]
  → 打开 verification_uri (https://auth.openai.com/codex/device)
  → 在页面输 user_code
  → 在 ChatGPT 确认授权

[轮询]
  → POST /api/oauth/poll/codex (每 interval 秒)
  → status='pending' → 继续轮询，可能 increment_interval=true 则 +5s
  → status='bound' → 关闭 dialog → 刷新 status
  → status='expired'/'denied' → 关闭 dialog → toast 错误
  → expires_at 超时 → 关闭 dialog → toast「code 已过期」

[用户取消]
  → store.cancelCodexFlow() → 清 timer → 关闭 dialog
```

### 5.3 手动刷新

```
[点击「手动刷新」按钮]
  → store.refresh(provider)
  → POST /api/oauth/refresh/{provider} → force_refresh=true
  → 后端调 refresh_codex / refresh_claude_code → 写回 MongoDB
  → store.fetchStatus(provider) → 卡片更新「有效期」「上次刷新」
```

### 5.4 解绑

```
[点击「解绑」按钮]
  → el-message-box.confirm「确定解绑 X？此后该用户分析需重新授权」
  → 用户确认
  → store.unbind(provider)
  → DELETE /api/oauth/unbind/{provider}
  → store.fetchStatus(provider) → 卡片变「未绑定」
```

## 6. 错误处理

| 场景 | 处理 |
|------|------|
| 网络挂 (API 返回 5xx / timeout) | Element Plus `ElMessage.error('网络异常，请稍后重试')` |
| `OAuthCredentialError`（refresh/resolve 失败 → 400） | toast 错误 + 提示「请重新授权」+「现在去授权」按钮 |
| PKCE popup 被拦截 | Dialog 显示「弹窗被浏览器拦截，请在地址栏允许后[重试]」 |
| Codex device-code 过期 | 「code 已过期，请重新开始」+ 关闭 dialog |
| Codex 用户拒绝 | 「您拒绝了授权，已取消」+ 关闭 dialog |
| Codex polling 网络中断 | 自动重试一次；连续失败 → 关闭 dialog + 提示 |
| Cloudflare 403（理论可能） | toast「服务端临时不可用，请稍后重试」+ 记录到 console |

## 7. 测试策略

### 7.1 单元测试

后端（pytest）：
- PR-3 不动后端，PR-2 既有测试维持不变；无新增后端测试。

前端（Vitest，如果项目使用）：
- 项目未发现 vitest/jest 配置；前端测试由人工 + smoke 完成。

### 7.2 手动端到端

新增 `scripts/smoke_test_pr3_ui.md`（不是脚本，是 checklist）：
- [ ] 全新用户首次进入 `订阅授权` tab → 两张卡都「未绑定」
- [ ] 点「登录 Claude」→ popup 打开 → claude.ai 授权 → popup 关闭 → 卡片变「已绑定」
- [ ] 点「登录 ChatGPT」→ 大码 modal → 浏览器输 code → 卡片变「已绑定」
- [ ] 手动刷新按钮：`expires_at` 与 `last_refresh_at` 都更新
- [ ] 解绑：二次确认后卡片变「未绑定」，再刷新页面状态正确
- [ ] LLMConfigDialog 选 codex：admin 未绑时显示警告 + 「立即授权」按钮，点击触发 OAuth flow；admin 已绑时显示绿色状态
- [ ] 各种错误状态友好显示（断网、popup 拦截、过期、拒绝）

## 8. 工作量与拆分

**预估 ~2.5 人日**：

| 部分 | 工作量 |
|------|--------|
| API + Pinia store | ~0.5 人日 |
| 两个 dialog 组件（PKCE + device-code） | ~0.75 人日 |
| SubscriptionAuthManagement.vue 主面板 | ~0.5 人日 |
| LLMConfigDialog 集成 | ~0.25 人日 |
| ConfigManagement 菜单 + App.vue 挂载 | ~0.1 人日 |
| 联调 + 手动测试 + 微调 | ~0.5 人日 |

**不拆分**，作为单个 PR-3 提交。理由：组件耦合紧密（store + dialog + 状态卡 + 集成必须一起测），拆分会引入半成品中间状态。

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 弹窗被浏览器拦截 | UI 显著提示「请允许弹窗」，提供「重试」按钮 |
| `postMessage` 跨域 | 后端 callback HTML 已用 `'*'` target；前端验证 `event.origin === window.location.origin` 或 `event.source === this.popup` |
| Codex 轮询期间用户关闭对话框 | `cancelCodexFlow` 显式清 timer；组件 `unmounted` 钩子兜底 |
| Token 自然过期 UI 不更新 | 主面板 30s 间隔拉 status；卡片显示「即将过期」徽章 |
| 弹窗被关闭但 store 状态没清 | 在 `unmounted` 时调 `store.pkceDialogOpen = false`；30s 自检 popup 存活 |
| 多 tab 同步 | 不解决（首版 YAGNI）；用户在 tab1 绑定后 tab2 刷新页面可见 |

## 10. 待定问题

1. **取消 PKCE flow 时关闭 popup**：popup 是用户浏览器的窗口，能否被父窗口主动 close()？取决于浏览器是否允许（同源开的 popup 一般可以）。**默认决定**：尝试 `popup.close()`，失败也不影响主流程，仅记 console。

2. **状态徽章「即将过期」阈值**：默认 10 分钟内显示。**默认决定**：硬编码 10min，留 TODO 注释。

## 11. 参考

- 后端 API：`app/routers/oauth.py`（PR-2 已实现的 7 个端点，PR-3 不新增）
- 设计文档：`docs/design/llm-subscription-auth-design.md` § 6 PR-3 节
- UI 组件库：Element Plus 2.4+
- 状态管理：Pinia 2.1+
- 项目 i18n 现状：Element Plus 整体设了 zh-cn locale，业务字串硬编码中文（无 vue-i18n）
