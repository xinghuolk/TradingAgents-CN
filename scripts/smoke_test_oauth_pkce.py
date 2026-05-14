"""Manual smoke test: end-to-end Anthropic PKCE flow against the real service.

Prerequisites:
  1. MongoDB and Redis running (e.g. via docker-compose).
  2. .venv installed and `OAUTH_ENCRYPTION_KEY` set in the environment.
  3. A test user account exists. Set TEST_USER_ID env var to that user's _id.

Run:
  PYTHONPATH=. .venv/bin/python scripts/smoke_test_oauth_pkce.py

The script:
  1. Calls oauth_service.start_pkce_flow with a localhost redirect_uri.
  2. Prints the authorize_url; user opens it in a browser, authorizes, and
     is redirected to localhost (which will 404 — that's fine, copy the
     ?code=... and ?state=... from the URL bar).
  3. User pastes code and state into the script's stdin prompts.
  4. Script calls complete_pkce_flow, which stores the encrypted token in
     MongoDB.
  5. Script then calls resolve() and prints the decrypted access_token's
     first 20 chars to confirm round-trip works.

Exit codes:
  0 = PASS (token round-trip successful)
  2 = FAIL: missing prerequisites
  3 = FAIL: PKCE flow error
"""
from __future__ import annotations

import asyncio
import os
import sys


async def main() -> int:
    user_id = os.environ.get("TEST_USER_ID")
    if not user_id:
        print("FAIL: set TEST_USER_ID env var", file=sys.stderr)
        return 2
    if not os.environ.get("OAUTH_ENCRYPTION_KEY"):
        print("FAIL: set OAUTH_ENCRYPTION_KEY env var", file=sys.stderr)
        return 2

    from app.core.database import init_db, get_mongo_client
    from app.core.redis_client import get_redis
    from app.services import oauth_service
    import httpx

    await init_db()
    db = get_mongo_client()[os.environ.get("MONGODB_DATABASE", "tradingagents")]
    collection = db["user_oauth_credentials"]
    redis_client = get_redis()
    http_client = httpx.AsyncClient(timeout=10.0)

    redirect_uri = "http://localhost:8000/api/oauth/callback/claude_code"
    print(f"Starting PKCE flow for user {user_id} with redirect_uri={redirect_uri}")
    start_result = await oauth_service.start_pkce_flow(
        redis_client=redis_client,
        user_id=user_id,
        redirect_uri=redirect_uri,
    )
    print()
    print("Open this URL in your browser:")
    print(start_result["authorize_url"])
    print()
    print("After authorizing, the page will try to redirect to localhost and fail.")
    print("Copy the `code` and `state` query parameters from the URL bar.")
    print()
    code = input("Paste code: ").strip()
    state = input("Paste state: ").strip()

    try:
        bound_user_id = await oauth_service.complete_pkce_flow(
            redis_client=redis_client,
            collection=collection,
            state=state,
            code=code,
            redirect_uri=redirect_uri,
            http_client=http_client,
        )
        print(f"PASS: bound user_id={bound_user_id}")
    except Exception as exc:
        print(f"FAIL: complete_pkce_flow raised: {exc}", file=sys.stderr)
        return 3

    try:
        token = await oauth_service.resolve(collection, user_id, "claude_code")
        print(f"PASS: resolve returned token starting with {token[:20]}...")
    except Exception as exc:
        print(f"FAIL: resolve raised: {exc}", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
