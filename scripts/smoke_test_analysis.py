"""Manual smoke test: run a minimal TradingAgents analysis end-to-end.

Defaults to Codex (PR-2 + PR-2.5 OAuth path). Override the provider via env:

  SMOKE_PROVIDER=codex          # default — ChatGPT subscription
  SMOKE_PROVIDER=claude_code    # Claude Pro/Max subscription
  SMOKE_PROVIDER=openai         # api key (OPENAI_API_KEY)
  SMOKE_PROVIDER=anthropic      # api key (ANTHROPIC_API_KEY)
  SMOKE_PROVIDER=google         # api key (GOOGLE_API_KEY)
  SMOKE_PROVIDER=deepseek       # api key (DEEPSEEK_API_KEY)
  SMOKE_PROVIDER=openrouter     # api key (OPENROUTER_API_KEY)
  SMOKE_PROVIDER=ollama         # local, no key needed
  SMOKE_PROVIDER=dashscope      # api key (DASHSCOPE_API_KEY) — generic fallback after PR-2
  SMOKE_PROVIDER=qianfan        # api key (QIANFAN_API_KEY)        — generic fallback after PR-2
  SMOKE_PROVIDER=zhipu          # api key (ZHIPU_API_KEY)          — generic fallback after PR-2

Also configurable:
  SMOKE_QUICK_MODEL    (default: provider-dependent)
  SMOKE_DEEP_MODEL     (default: same as quick)
  SMOKE_SYMBOL         (default: AAPL)
  SMOKE_DATE           (default: today)
  SMOKE_ANALYSTS       (default: "market" — fastest path)
  SMOKE_BACKEND_URL    (required for dashscope/qianfan/zhipu — they have no
                        env-detected default after PR-2's refactor)

Prerequisites:
  - For codex: ChatGPT subscription + OAuth already bound (or local
    ~/.codex/auth.json from `codex login`)
  - For claude_code: Claude Pro/Max + OAuth bound (or ~/.claude/.credentials.json)
  - For api-key providers: appropriate API key + backend_url in env

This is a SHORT analysis (1 debate round, 1 analyst, no memory).
Real production analysis with full team takes 3-5 minutes; this is ~1-2 min.

Run:
  PYTHONPATH=. .venv/bin/python scripts/smoke_test_analysis.py

Exit codes:
  0 = PASS (final_trade_decision returned)
  2 = FAIL: missing prerequisites / unknown provider
  3 = FAIL: graph construction error
  4 = FAIL: analysis run failed mid-flow
"""
from __future__ import annotations

import os
import sys
from datetime import date

from dotenv import load_dotenv


load_dotenv()


_PROVIDER_DEFAULTS = {
    "codex":       {"quick": "gpt-5.5",                "deep": "gpt-5.5",                "url": ""},
    "claude_code": {"quick": "claude-opus-4-7",        "deep": "claude-opus-4-7",        "url": ""},
    "openai":      {"quick": "gpt-4o-mini",            "deep": "gpt-4o",                  "url": ""},
    "anthropic":   {"quick": "claude-3-5-haiku-latest", "deep": "claude-3-5-sonnet-latest", "url": ""},
    "google":      {"quick": "gemini-2.0-flash",       "deep": "gemini-2.0-flash",       "url": ""},
    "deepseek":    {"quick": "deepseek-chat",          "deep": "deepseek-chat",          "url": "https://api.deepseek.com"},
    "openrouter":  {"quick": "openai/gpt-4o-mini",     "deep": "openai/gpt-4o",          "url": "https://openrouter.ai/api/v1"},
    "ollama":      {"quick": "qwen2.5:7b",             "deep": "qwen2.5:14b",            "url": "http://localhost:11434/v1"},
    # Below are post-PR-2 generic-fallback providers. backend_url is REQUIRED
    # via env or via SMOKE_BACKEND_URL — there's no auto-detect.
    "dashscope":   {"quick": "qwen-turbo",             "deep": "qwen-plus",              "url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "qianfan":     {"quick": "ernie-3.5-8k",           "deep": "ernie-4.0-8k-latest",    "url": "https://qianfan.baidubce.com/v2"},
    "zhipu":       {"quick": "glm-4-flash",            "deep": "glm-4-plus",             "url": "https://open.bigmodel.cn/api/paas/v4"},
}


def main() -> int:
    provider = os.environ.get("SMOKE_PROVIDER", "codex").lower()
    if provider not in _PROVIDER_DEFAULTS:
        print(f"FAIL: unknown SMOKE_PROVIDER: {provider}", file=sys.stderr)
        print(f"Supported: {sorted(_PROVIDER_DEFAULTS)}", file=sys.stderr)
        return 2

    defaults = _PROVIDER_DEFAULTS[provider]
    quick_model = os.environ.get("SMOKE_QUICK_MODEL", defaults["quick"])
    deep_model = os.environ.get("SMOKE_DEEP_MODEL", defaults["deep"])
    symbol = os.environ.get("SMOKE_SYMBOL", "AAPL")
    test_date = os.environ.get("SMOKE_DATE", date.today().isoformat())
    analysts_csv = os.environ.get("SMOKE_ANALYSTS", "market")
    analysts = [a.strip() for a in analysts_csv.split(",") if a.strip()]
    backend_url = os.environ.get("SMOKE_BACKEND_URL", defaults["url"])

    print(f"Smoke test:")
    print(f"  provider:    {provider}")
    print(f"  quick model: {quick_model}")
    print(f"  deep model:  {deep_model}")
    print(f"  backend_url: {backend_url or '<default>'}")
    print(f"  stock:       {symbol}")
    print(f"  date:        {test_date}")
    print(f"  analysts:    {analysts}")
    print()

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config.update({
        "llm_provider": provider,
        "quick_think_llm": quick_model,
        "deep_think_llm": deep_model,
        "max_debate_rounds": 1,            # minimal — keep cost / latency low
        "max_risk_discuss_rounds": 1,
        "memory_enabled": False,            # avoid embedding setup
        "online_tools": True,
    })
    if backend_url:
        config["backend_url"] = backend_url

    print("Constructing TradingAgentsGraph...")
    try:
        ta = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=config,
        )
    except Exception as exc:
        print(f"FAIL: graph construction raised: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 3

    print("\nRunning analysis (~1-3 min)...\n")
    try:
        final_state, decision = ta.propagate(symbol, test_date)
    except Exception as exc:
        print(f"\nFAIL: propagate raised: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 4

    print("\n" + "=" * 70)
    print("FINAL DECISION:")
    print("=" * 70)
    if isinstance(decision, dict):
        for k, v in decision.items():
            preview = str(v)[:200]
            print(f"  {k}: {preview}")
    else:
        print(decision)
    print("=" * 70)
    print(f"\n✓ PASS — analysis completed for {symbol} via provider={provider}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
