#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""价值投资取数诊断脚本。

封装「codex 订阅 token 穿透上游 + force_refresh 重提取 + 跑 FRC adapter / turtle facts +
打印关键字段的 value/normalized_value/canonical_unit/reliability」，避免每次手写长命令。

用法（容器内执行）：
  # 看缓存现状（CACHE_FIRST，快，不跑 LLM）
  docker exec -w /app tradingagents-backend python scripts/diagnose_value_extraction.py 603345 CN

  # force_refresh 重提取（穿透 codex token 跑 LLM，慢）
  docker exec -w /app tradingagents-backend python scripts/diagnose_value_extraction.py 603345 CN --force

  # 跑 turtle facts，看各字段 reliability + TurtleFacts.status
  docker exec -w /app tradingagents-backend python scripts/diagnose_value_extraction.py 603345 A --force --turtle

详见 docs/analysis/00003_value_normalized_value_cache_and_debug_20260618.md
"""
import argparse
import asyncio
import os
import sys
from datetime import date

# 脚本位于 scripts/ 子目录，将项目根加入 sys.path 以便 import tradingagents / app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 关注的关键 money 字段
_KEY_FIELDS = [
    "net_profit", "operating_cash_flow", "capital_expenditures",
    "dividends_paid", "repurchase_of_stock", "stock_based_compensation",
    "cash", "total_assets",
]


def _resolve_codex_token(user_id: str) -> str | None:
    """从 DB 取 codex 订阅 token，并注入跑 LLM 所需的 deep-provider env。"""
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.services import oauth_service

    async def _go():
        client = AsyncIOMotorClient(os.getenv("TRADINGAGENTS_MONGODB_URL"))
        coll = client.get_default_database()["user_oauth_credentials"]
        return await oauth_service.resolve(coll, user_id, "codex")

    token = asyncio.run(_go())
    try:
        from app.core.config_bridge import bridge_config_to_env
        bridge_config_to_env()
    except Exception as exc:  # noqa: BLE001 — 独立进程下 DB 定价同步可能失败，不影响 token 穿透
        print(f"[warn] bridge_config_to_env 部分失败（不影响 codex 穿透）: {exc}")
    os.environ["TRADINGAGENTS_DEEP_PROVIDER"] = "codex"
    os.environ["TRADINGAGENTS_DEEP_MODEL"] = "gpt-5.5"
    os.environ["FINANCIAL_REPORT_FORCE_REFRESH"] = "true"
    print(f"codex token resolved: {bool(token)}")
    return token


def _show_money(label, fv):
    if fv is None:
        print(f"  {label}: 【不在 fields】")
        return
    v = getattr(fv, "value", None)
    if hasattr(v, "unit"):  # MoneyAmount
        vshow = f"MoneyAmount(value={v.value}, unit={v.unit}, cur={v.currency})"
    else:
        vshow = v
    print(f"  {label}: reliability={getattr(fv, 'reliability', None)} value={vshow}")


def main():
    ap = argparse.ArgumentParser(description="价值投资取数诊断")
    ap.add_argument("ticker", help="股票代码，如 603345 / 00001")
    ap.add_argument("market", nargs="?", default="CN", help="市场 CN/A/HK（默认 CN）")
    ap.add_argument("--period-end", default=None, help="报告期 YYYY-MM-DD（默认按交易日推断）")
    ap.add_argument("--force", action="store_true", help="force_refresh 重提取（穿透 codex token 跑 LLM）")
    ap.add_argument("--user", default="694c1bcc8590c91f8011a4eb", help="codex OAuth user_id")
    ap.add_argument("--turtle", action="store_true", help="跑 turtle facts（默认跑 FRC adapter）")
    args = ap.parse_args()

    token = _resolve_codex_token(args.user) if args.force else None

    from tradingagents.dataflows.financial_reports.adapter import create_financial_report_adapter
    from tradingagents.dataflows.financial_reports.config import get_financial_report_client_config

    cfg = get_financial_report_client_config()
    adapter = create_financial_report_adapter(cfg, subscription_token=token)

    if args.turtle:
        from tradingagents.dataflows.value_investment.turtle.report_adapter import get_turtle_report_facts
        trade_date = date.today().strftime("%Y-%m-%d")
        facts = get_turtle_report_facts(
            ticker=args.ticker, market=args.market, trade_date=trade_date,
            adapter=adapter, allow_llm_models=cfg.allow_llm_models,
        )
        print(f"\n=== TurtleFacts.status: {facts.status} (trade_date={trade_date}) ===")
        for name in ["dividends_paid", "buyback_amount", "dividend_payout_ratio_current_year",
                     "net_profit", "operating_cash_flow", "capex"]:
            fv = facts.fields.get(name)
            if fv is None:
                print(f"  {name}: 【不在 fields】")
            else:
                _show_money(name, fv)
    else:
        market = "CN" if args.market in ("A", "CN") else args.market
        r = adapter.get_annual_report_data(ticker=args.ticker, market=market, period_end=args.period_end)
        f = getattr(r.extraction, "fields", {}) or {}
        print(f"\n=== FRC extraction: {args.ticker} {market} period_end={r.period_end} "
              f"available={r.available} ===")
        if r.warnings:
            print(f"  warnings: {r.warnings[:3]}")
        if r.errors:
            print(f"  errors: {r.errors[:3]}")
        for fid in _KEY_FIELDS:
            d = f.get(fid)
            if d is None:
                print(f"  {fid}: 【字段不存在】")
            else:
                print(f"  {fid}: value={getattr(d, 'value', None)} "
                      f"normalized_value={getattr(d, 'normalized_value', None)} "
                      f"canonical_unit={getattr(d, 'canonical_unit', None)} "
                      f"unit={getattr(d, 'unit', None)}")


if __name__ == "__main__":
    main()
