"""Manual smoke test: end-to-end Codex device-code flow against the real service.

Prerequisites:
  1. MongoDB and Redis running (e.g. via docker-compose).
  2. .venv installed and `OAUTH_ENCRYPTION_KEY` set in the environment.
  3. A test user account exists. Set TEST_USER_ID env var to that user's _id.
  4. A working ChatGPT Plus/Pro subscription.

Run:
  PYTHONPATH=. .venv/bin/python scripts/smoke_test_oauth_codex.py

The script:
  1. Calls oauth_service.start_device_code_flow.
  2. Prints the user_code and verification_uri. You open the URI in a browser,
     enter the code, and approve the request on ChatGPT.
  3. Script polls every `interval` seconds until status becomes "bound".
  4. Once bound, calls oauth_service.resolve to decrypt + return the token.
  5. Constructs ChatCodexOAuth and calls .invoke("Reply with: SMOKE-TEST-OK").
  6. Prints the response. If it contains "SMOKE-TEST-OK" or any reasonable
     output, the Responses API adapter (PR-2.5) is correctly wired.

Exit codes:
  0 = PASS (full round-trip including a real Codex API call)
  2 = FAIL: missing prerequisites
  3 = FAIL: device-code flow error (network / OAuth)
  4 = FAIL: Codex API call failed (adapter or API issue)
"""
from __future__ import annotations

import asyncio
import os
import sys
import time


POLL_TIMEOUT_SECONDS = 600  # 10 minutes for the user to complete authorization


async def main() -> int:
    user_id = os.environ.get("TEST_USER_ID")
    if not user_id:
        print("FAIL: set TEST_USER_ID env var", file=sys.stderr)
        return 2
    if not os.environ.get("OAUTH_ENCRYPTION_KEY"):
        print("FAIL: set OAUTH_ENCRYPTION_KEY env var", file=sys.stderr)
        return 2

    # Lazy imports so prerequisites checks fail fast
    from app.core.database import init_db, get_database
    from app.core.redis_client import init_redis, get_redis
    from app.services import oauth_service
    import httpx

    print("Initializing MongoDB + Redis...")
    await init_db()
    await init_redis()
    db = get_database()
    collection = db["user_oauth_credentials"]
    redis_client = get_redis()
    http_client = httpx.AsyncClient(timeout=10.0)

    # --- Phase 1: start device-code flow ---
    print(f"\nStarting Codex device-code flow for user {user_id}")
    try:
        start_result = await oauth_service.start_device_code_flow(
            redis_client=redis_client,
            user_id=user_id,
            http_client=http_client,
        )
    except Exception as exc:
        print(f"FAIL: start_device_code_flow raised: {exc}", file=sys.stderr)
        return 3

    user_code = start_result["user_code"]
    verification_uri = start_result["verification_uri"]
    interval = max(int(start_result.get("interval", 5)), 1)
    expires_in = int(start_result.get("expires_in", 600))

    print()
    print("=" * 70)
    print(f"  USER CODE:  {user_code}")
    print(f"  Open this URL in your browser:")
    print(f"  {verification_uri}")
    print(f"  (Code expires in ~{expires_in}s)")
    print("=" * 70)
    print()
    print(f"Polling every {interval}s; press Ctrl+C to abort...")

    # --- Phase 2: poll until bound ---
    deadline = time.time() + min(POLL_TIMEOUT_SECONDS, expires_in)
    current_interval = interval
    bound = False
    while time.time() < deadline:
        try:
            poll = await oauth_service.poll_device_code_flow(
                redis_client=redis_client,
                collection=collection,
                user_id=user_id,
                http_client=http_client,
            )
        except Exception as exc:
            print(f"FAIL: poll_device_code_flow raised: {exc}", file=sys.stderr)
            return 3

        status = poll.get("status")
        if status == "bound":
            bound = True
            print("\n✓ Authorization complete. Token encrypted and stored.")
            break
        if status in ("expired", "denied"):
            print(f"\nFAIL: device-code flow ended with status={status}", file=sys.stderr)
            return 3
        if poll.get("increment_interval"):
            current_interval += 5
            print(f"  (server requested slow_down; backing off to {current_interval}s)")

        # Print a heartbeat with seconds remaining
        remaining = int(deadline - time.time())
        print(f"  ...pending (status={status}, {remaining}s remaining)")
        await asyncio.sleep(current_interval)

    if not bound:
        print("\nFAIL: polling timed out before user completed authorization",
              file=sys.stderr)
        return 3

    # --- Phase 3: resolve token + make a real Codex API call ---
    print("\nResolving token from MongoDB...")
    try:
        access_token = await oauth_service.resolve(collection, user_id, "codex")
    except Exception as exc:
        print(f"FAIL: resolve raised: {exc}", file=sys.stderr)
        return 4

    print(f"✓ Token resolved (length={len(access_token)}, starts with {access_token[:20]}...)")

    print("\nConstructing ChatCodexOAuth and calling .invoke()...")
    from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth
    try:
        chat = ChatCodexOAuth(model="gpt-5", access_token=access_token)
    except Exception as exc:
        print(f"FAIL: ChatCodexOAuth construction raised: {exc}", file=sys.stderr)
        return 4

    print("Sending prompt: 'Reply with exactly the phrase: SMOKE-TEST-OK'\n")
    try:
        resp = chat.invoke("Reply with exactly the phrase: SMOKE-TEST-OK")
    except Exception as exc:
        print(f"FAIL: chat.invoke raised: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 4

    content = getattr(resp, "content", str(resp))
    print(f"RESPONSE: {content!r}")

    if "SMOKE-TEST-OK" in str(content):
        print("\n✓ PASS — Codex Responses API end-to-end works")
        return 0

    if content:
        print("\n⚠ Got a response but it didn't contain the expected phrase.")
        print("  Adapter likely works; the model just didn't follow the instruction.")
        return 0

    print("\nFAIL: empty response from Codex", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
