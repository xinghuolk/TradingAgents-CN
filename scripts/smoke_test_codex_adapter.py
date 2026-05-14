"""Manual smoke test for the Codex Responses API adapter — bypasses OAuth flow.

Reads ~/.codex/auth.json directly (via PR-1's subscription_credentials reader),
constructs ChatCodexOAuth, and makes a real Codex API call.

Use this for fast adapter iteration. Skips MongoDB, Redis, Web OAuth, and
device-code authorization — they're not needed if you've already run
`codex login` once via the official Codex CLI.

For the full Web OAuth path validation, use smoke_test_oauth_codex.py instead.

Prerequisites:
  1. Run `codex login` via the official Codex CLI at least once so that
     ~/.codex/auth.json exists.
  2. .venv installed.

Run:
  PYTHONPATH=. .venv/bin/python scripts/smoke_test_codex_adapter.py

Exit codes:
  0 = PASS (got a real response from Codex)
  2 = FAIL: missing local credentials (~/.codex/auth.json)
  4 = FAIL: Codex API call failed
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    from tradingagents.llm_adapters import subscription_credentials as sc

    # Step 1: verify local CLI credentials exist
    try:
        cred = sc.resolve("codex")
    except sc.SubscriptionCredentialError as exc:
        print(f"FAIL: no Codex credentials → {exc}", file=sys.stderr)
        print("Run `codex login` first to populate ~/.codex/auth.json.",
              file=sys.stderr)
        return 2

    print(f"OK: loaded credentials from {cred.source}, "
          f"expires_at_ms={cred.expires_at_ms}")

    # Step 2: construct adapter and invoke
    from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth
    model = os.environ.get("CODEX_SMOKE_MODEL", "gpt-5")
    print(f"\nConstructing ChatCodexOAuth (model={model})...")
    try:
        # access_token=None → adapter falls back to sc.resolve("codex") internally
        chat = ChatCodexOAuth(model=model)
    except Exception as exc:
        print(f"FAIL: ChatCodexOAuth construction raised: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    prompt = "Reply with exactly the phrase: SMOKE-TEST-OK"
    print(f"\nSending prompt: {prompt!r}\n")
    try:
        resp = chat.invoke(prompt)
    except Exception as exc:
        print(f"FAIL: chat.invoke raised: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 4

    content = getattr(resp, "content", str(resp))
    print(f"RESPONSE: {content!r}")

    if "SMOKE-TEST-OK" in str(content):
        print("\n✓ PASS — Codex Responses API adapter works end-to-end")
        return 0

    if content:
        print("\n⚠ Got a response, but it didn't contain the expected phrase.")
        print("  The adapter is wired correctly; the model just didn't follow")
        print("  the instruction verbatim.")
        return 0

    print("\nFAIL: empty response from Codex", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
