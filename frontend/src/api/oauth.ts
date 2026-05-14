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
