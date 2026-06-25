#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""value_flow_probe.py — 系统化跑完整 value（Turtle 海龟）分析流程，分阶段 dump 中间值供调试。

与 diagnose_value_extraction.py 的区别：后者只看 FRC 取数层；本脚本贯穿到**决策信号层**
（compute_turtle_signals），并把每个阶段的中间值落盘，回答「哪些数据阻碍 value 决策」。

流程阶段（逐层落盘）：
  1) FRC extraction      —— 上游归一后的 raw 字段（value/normalized_value/canonical_unit/unit）
  2) TurtleFacts         —— report + market + historical 合并（已适配 MoneyAmount + reliability + caveats）
  3) TurtleComputedSignals —— 各价值公式 status/value/missing_inputs + veto_reasons
  4) summary             —— 阻断矩阵（公式 × 缺失输入）+ 各层 status + 关键指标 + 缺失输入聚合

输出目录：data/value_probe/<ticker>_<market>_<trade_date>/
  extraction.json · turtle_facts.json · signals.json · summary.json · summary.md

用法（容器内执行）：
  # CACHE_FIRST，快，不跑 LLM（验证日常 backend 路径）
  docker exec -w /app tradingagents-backend python scripts/value_flow_probe.py 600519 A

  # force_refresh 穿透 codex token 重提取（慢）
  docker exec -w /app tradingagents-backend python scripts/value_flow_probe.py 600519 A --force

  # 港股 + 指定持仓渠道（影响 tax_rate 计算）
  docker exec -w /app tradingagents-backend python scripts/value_flow_probe.py 00700 HK --holding-channel long_term_hk_via_connect

详见 docs/analysis/00004_600519_value_field_coverage_20260624.md
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import date
from decimal import Decimal


def _json_default(o):
    """上游 money 字段为 Decimal，不可直接 JSON 序列化 → Decimal 转 float，其余兜底转 str。"""
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def _dump_json(obj, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, default=_json_default)

# 脚本位于 scripts/ 子目录，将项目根加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# FRC 取数层关注的关键 money 字段（与 diagnose 脚本一致）
_KEY_FRC_FIELDS = [
    "net_profit", "operating_cash_flow", "capital_expenditures",
    "dividends_paid", "repurchase_of_stock", "stock_based_compensation",
    "cash", "total_assets",
]


def _resolve_codex_token(user_id: str) -> str | None:
    """从 DB 取 codex 订阅 token，并注入跑 LLM 所需的 deep-provider env（同 diagnose 脚本）。"""
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


def _frc_market(market: str) -> str:
    """turtle 工具用 A/HK；FRC adapter 用 CN/HK。"""
    return "CN" if market.upper() in ("A", "CN", "CHINA") else market.upper()


def _money_to_dict(v):
    """MoneyAmount → dict；标量原样。"""
    if hasattr(v, "unit"):  # MoneyAmount
        return {
            "value": getattr(v, "value", None),
            "unit": getattr(v, "unit", None),
            "currency": getattr(v, "currency", None),
            "reliability": getattr(v, "reliability", None),
        }
    return v


def _dump_extraction(adapter, ticker: str, frc_market: str, period_end: str | None) -> dict:
    """阶段 1：FRC raw extraction。"""
    r = adapter.get_annual_report_data(ticker=ticker, market=frc_market, period_end=period_end)
    fields = getattr(r.extraction, "fields", {}) or {}
    out_fields = {}
    for fid, d in fields.items():
        out_fields[fid] = {
            "value": getattr(d, "value", None),
            "normalized_value": getattr(d, "normalized_value", None),
            "canonical_unit": getattr(d, "canonical_unit", None),
            "currency": getattr(d, "currency", None),
            "unit": getattr(d, "unit", None),
            "is_reliable": getattr(d, "is_reliable", None),
        }
    return {
        "available": r.available,
        "period_end": r.period_end,
        "warnings": list(r.warnings or []),
        "errors": list(r.errors or []),
        "key_fields": {k: out_fields.get(k, "【字段不存在】") for k in _KEY_FRC_FIELDS},
        "all_fields": out_fields,
    }


def _build_summary(facts: dict, signals: dict) -> dict:
    """阶段 4：阻断矩阵 + 各层 status + 缺失输入聚合。"""
    results = signals.get("results", {})
    blocking = {}          # 公式 -> 缺失输入（仅非 complete）
    missing_freq = {}      # 缺失输入 -> 阻断的公式数
    for name, r in results.items():
        miss = r.get("missing_inputs") or []
        if r.get("status") != "complete" or miss:
            blocking[name] = {
                "status": r.get("status"),
                "value": r.get("value"),
                "missing_inputs": miss,
            }
        for m in miss:
            missing_freq[m] = missing_freq.get(m, 0) + 1

    computable = {
        name: r.get("value")
        for name, r in results.items()
        if r.get("status") == "complete"
    }
    report = facts.get("report", {})
    market = facts.get("market", {})
    return {
        "statuses": {
            "facts": facts.get("status"),
            "report": report.get("status"),
            "market": market.get("status"),
            "signals": signals.get("status"),
        },
        "veto_reasons": signals.get("veto_reasons") or [],
        "blocking_formulas": blocking,
        "computable_formulas": computable,
        "missing_input_frequency": dict(
            sorted(missing_freq.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "caveats": {
            "facts": facts.get("caveats") or [],
            "signals": signals.get("caveats") or [],
        },
    }


def _summary_markdown(ticker, market, trade_date, extraction, summary) -> str:
    st = summary["statuses"]
    lines = [
        f"# value_flow_probe — {ticker} ({market}) @ {trade_date}",
        "",
        "## 各层 status",
        f"- FRC extraction: available={extraction['available']}, period_end={extraction['period_end']}",
        f"- report facts: **{st['report']}**",
        f"- market facts: **{st['market']}**",
        f"- TurtleFacts(合并): **{st['facts']}**",
        f"- **signals(决策层): {st['signals']}**",
        "",
        "## 阻断的公式（status≠complete 或有 missing_inputs）",
    ]
    if summary["blocking_formulas"]:
        lines.append("| 公式 | status | value | 缺失输入 |")
        lines.append("|---|---|---|---|")
        for name, b in summary["blocking_formulas"].items():
            miss = ", ".join(b["missing_inputs"]) or "—"
            lines.append(f"| {name} | {b['status']} | {b['value']} | {miss} |")
    else:
        lines.append("（无，全部公式 complete）")
    lines += ["", "## 可计算的公式"]
    if summary["computable_formulas"]:
        for name, v in summary["computable_formulas"].items():
            lines.append(f"- {name} = {v}")
    else:
        lines.append("（无）")
    lines += ["", "## 缺失输入聚合（按阻断公式数排序）"]
    if summary["missing_input_frequency"]:
        for m, n in summary["missing_input_frequency"].items():
            lines.append(f"- `{m}` — 阻断 {n} 个公式")
    else:
        lines.append("（无）")
    if summary["veto_reasons"]:
        lines += ["", "## 否决原因（veto_reasons）"]
        lines += [f"- {v}" for v in summary["veto_reasons"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="系统化 value(Turtle) 流程探针")
    ap.add_argument("ticker", help="股票代码，如 600519 / 00700")
    ap.add_argument("market", nargs="?", default="A", help="市场 A/CN/HK（默认 A）")
    ap.add_argument("--trade-date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--company-name", default="", help="公司名（可选，仅用于 context）")
    ap.add_argument("--holding-channel", default=None, help="持仓渠道（影响 tax_rate 计算）")
    ap.add_argument("--force", action="store_true", help="force_refresh 穿透 codex token 重提取（慢）")
    ap.add_argument("--user", default="694c1bcc8590c91f8011a4eb", help="codex OAuth user_id")
    ap.add_argument("--out-root", default="data/value_probe", help="输出根目录")
    args = ap.parse_args()

    trade_date = args.trade_date or date.today().strftime("%Y-%m-%d")
    token = _resolve_codex_token(args.user) if args.force else None

    from tradingagents.dataflows.financial_reports.adapter import create_financial_report_adapter
    from tradingagents.dataflows.financial_reports.config import get_financial_report_client_config
    from tradingagents.tools.turtle_analysis_tool import prepare_turtle_analysis_payload

    out_dir = os.path.join(args.out_root, f"{args.ticker}_{args.market.upper()}_{trade_date}")
    os.makedirs(out_dir, exist_ok=True)

    # 阶段 1：FRC extraction（独立 adapter 调用，拿 raw 字段）
    cfg = get_financial_report_client_config()
    adapter = create_financial_report_adapter(cfg, subscription_token=token)
    extraction = _dump_extraction(adapter, args.ticker, _frc_market(args.market), None)
    _dump_json(extraction, os.path.join(out_dir, "extraction.json"))

    # 阶段 2+3：完整 TurtleFacts + signals（工具内部组装 report+market+FX）
    payload = prepare_turtle_analysis_payload(
        ticker=args.ticker, market=args.market.upper(), trade_date=trade_date,
        company_name=args.company_name, holding_channel=args.holding_channel,
    )
    data = json.loads(payload)
    facts, signals = data["facts"], data["signals"]
    _dump_json(facts, os.path.join(out_dir, "turtle_facts.json"))
    _dump_json(signals, os.path.join(out_dir, "signals.json"))

    # 阶段 4：summary
    summary = _build_summary(facts, signals)
    _dump_json(summary, os.path.join(out_dir, "summary.json"))
    md = _summary_markdown(args.ticker, args.market.upper(), trade_date, extraction, summary)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(md)

    # stdout 精简汇总
    st = summary["statuses"]
    print(f"\n===== value_flow_probe: {args.ticker} ({args.market.upper()}) @ {trade_date} =====")
    print(f"FRC available={extraction['available']} | report={st['report']} "
          f"market={st['market']} facts={st['facts']} | signals={st['signals']}")
    if summary["blocking_formulas"]:
        print("阻断公式：")
        for name, b in summary["blocking_formulas"].items():
            miss = ", ".join(b["missing_inputs"]) or "—"
            print(f"  {name}: {b['status']} value={b['value']} missing=[{miss}]")
    print("缺失输入聚合：")
    for m, n in summary["missing_input_frequency"].items():
        print(f"  {m} — 阻断 {n} 个公式")
    print(f"\n中间值已落盘：{out_dir}/")
    print("  extraction.json · turtle_facts.json · signals.json · summary.json · summary.md")


if __name__ == "__main__":
    main()
