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
  - [ ] No spurious "暂无可用模型" toast.
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
