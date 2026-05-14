"""Manual smoke test: round-trip a single prompt through Claude Code OAuth.

Run locally on a machine where `claude login` has been run at least once:

    .venv/bin/python scripts/smoke_test_claude_code_oauth.py

Prints either the model's response or a diagnostic error. Does NOT run in CI.
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    from tradingagents.llm_adapters import subscription_credentials as sc
    from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth

    try:
        cred = sc.resolve("claude_code")
    except sc.SubscriptionCredentialError as exc:
        print(f"FAIL: no Claude Code credentials → {exc}", file=sys.stderr)
        return 2

    print(f"OK: loaded credentials from {cred.source}, "
          f"expires_at_ms={cred.expires_at_ms}")

    # Note: claude-opus-4-7 does not accept temperature= (deprecated for this model).
    chat = ChatClaudeCodeOAuth(model="claude-opus-4-7", max_tokens=100)
    try:
        resp = chat.invoke("Reply with exactly the word: SMOKE-TEST-OK")
    except Exception as exc:
        print(f"FAIL: invoke raised → {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    text = getattr(resp, "content", str(resp))
    print(f"RESPONSE: {text!r}")
    if "SMOKE-TEST-OK" in str(text):
        print("PASS")
        return 0
    print("WARN: response did not contain the expected string, but the call succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
