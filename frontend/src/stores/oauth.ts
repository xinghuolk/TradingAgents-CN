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
      if (this.pkceDialogOpen) return
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

        const listener = (event: MessageEvent) => {
          this._handlePkceMessage(event).catch((err) => {
            console.error('❌ PKCE message handler 失败', err)
          })
        }
        this._pkceMessageListener = listener
        window.addEventListener('message', listener)
      } catch (err) {
        console.error('❌ Claude Code 授权启动失败', err)
        ElMessage.error('启动 Claude Code 授权失败')
      }
    },

    async _handlePkceMessage(event: MessageEvent) {
      const data = event.data
      if (!data || typeof data !== 'object') return
      if (data.type === 'oauth-success' && data.provider === 'claude_code') {
        this._closePkceDialog()
        await this.fetchStatus('claude_code')
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
      if (this.deviceCodeDialogOpen) return
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
        // Network error — keep retrying each interval until the device code expires.
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
