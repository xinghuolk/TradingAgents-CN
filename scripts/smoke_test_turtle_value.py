#!/usr/bin/env python3
"""Environment-driven Turtle value-analysis smoke test.

The script can use local financial-report-llm-extractor PDFs when
FINANCIAL_REPORT_PDF_ROOT points at a downloads directory. Unit tests do not
depend on any absolute PDF paths; this script is only a local smoke entrypoint.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic Turtle value-analysis smoke summary."
    )
    parser.add_argument("--ticker", default="600519")
    parser.add_argument("--market", default="A")
    parser.add_argument("--trade-date", default="2026-05-19")
    parser.add_argument("--company-name", default="贵州茅台")
    parser.add_argument(
        "--holding-channel",
        default=None,
        help="持仓渠道（如 long_term_domestic / stock_connect）。"
             "不传则触发 fail-fast，R/GG 输出 non_decisionable。",
    )
    return parser.parse_args()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _summary(payload: str) -> dict[str, Any]:
    data = json.loads(payload)
    facts = data.get("facts") if isinstance(data, dict) else {}
    signals = data.get("signals") if isinstance(data, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    signals = signals if isinstance(signals, dict) else {}

    results = signals.get("results")
    result_keys = results.keys() if isinstance(results, dict) else []
    caveats = _dedupe(
        [*_as_list(facts.get("caveats")), *_as_list(signals.get("caveats"))]
    )

    return {
        "facts_status": facts.get("status"),
        "signals_status": signals.get("status"),
        "available_results": sorted(result_keys),
        "caveats": caveats,
    }


def main() -> int:
    args = _parse_args()
    captured_stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_stdout):
            from tradingagents.tools.turtle_analysis_tool import (
                prepare_turtle_analysis_payload,
            )

            payload = prepare_turtle_analysis_payload(
                ticker=args.ticker,
                market=args.market,
                trade_date=args.trade_date,
                company_name=args.company_name,
                holding_channel=args.holding_channel,
            )
    finally:
        diagnostics = captured_stdout.getvalue()
        if diagnostics:
            sys.stderr.write(diagnostics)

    print(json.dumps(_summary(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
