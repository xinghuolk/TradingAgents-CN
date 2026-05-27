"""Decision prompt builders for Turtle v0.15."""

from __future__ import annotations

from .facts import FormulaResult, TurtleComputedSignals, TurtleFacts
from .formatting import facts_to_markdown, signals_to_markdown


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _all_caveats(facts: TurtleFacts, signals: TurtleComputedSignals) -> list[str]:
    return _unique_strings(
        [
            *facts.caveats,
            *facts.report.caveats,
            *facts.market.caveats,
            *signals.veto_reasons,
            *signals.caveats,
        ]
    )


def _formula_status_lines(results: dict[str, FormulaResult]) -> list[str]:
    lines: list[str] = []
    for name in sorted(results):
        result = results[name]
        missing = ", ".join(result.missing_inputs) if result.missing_inputs else "无"
        lines.append(
            f"- {name}: status={result.status}, value={result.value}, missing_inputs={missing}"
        )
    return lines


def build_turtle_decision_prompt(
    facts: TurtleFacts,
    signals: TurtleComputedSignals,
) -> str:
    """Build a Chinese Turtle decision prompt from supplied facts and signals."""
    return "\n\n".join(
        [
            "# Turtle v0.15 决策分析任务",
            (
                "你是 Turtle v0.15 价值投资决策分析器。只能使用下方提供的 "
                "TurtleFacts 与 TurtleComputedSignals，不得读取、查询或假设其他信息。"
            ),
            "禁止调用任何外部工具，包括搜索、行情、数据库、代码执行或文件读取工具。",
            "不得编造缺失数据；缺失、降级、不支持或不可决策的数据必须原样披露。",
            (
                "若 facts.status 或 signals.status 为 non_decisionable，只能输出不可决策报告，"
                "不得推断任何交易结论。"
            ),
            (
                "否决/停止逻辑：任何关键公式或关键输入被标记为 unsupported 或 "
                "non_decisionable，都必须停止形成最终可投资结论；只能说明阻断原因、"
                "缺失项、公式状态与后续需要补齐的数据。"
            ),
            facts_to_markdown(facts),
            signals_to_markdown(signals),
            (
                "## Turtle v0.15 Spec 2 口径说明\n"
                "- 分红按 holding_channel 对应 tax_rate 扣税；注销型回购对继续持有股东无即时税务事件，"
                "R/GG 中 buyback_amount_3y_avg 不扣税。\n"
                "- repurchase_of_stock 被用作报告侧 buyback_amount 输入，但当前 payload 未验证股份注销进度；"
                "若回购未注销，O 可能高估股东回报。\n"
                "- commitment_ratio 本版本未抽取；payout_M 使用 max(payout_3y_avg, latest_signal)，"
                "未应用承诺上限。\n"
                "- latest_signal 使用回看的最新年 dividends_paid/net_profit 代理前瞻 DPS 调整值，"
                "且它同时是 payout_3y_avg 的成员；在支付率上行、亏损年被排除或承诺上限缺失时，"
                "payout_M 与 R/GG 可能偏高。"
            ),
            (
                "## 输出结构\n"
                "1. 数据状态：概述 facts.status、signals.status 与关键 caveats。\n"
                "2. 公式核对：逐项引用公式、代入式、数值、单位、来源和缺失输入。"
                "注意 capex 按绝对值参与 owner_earnings（`ocf - abs(capex)`），"
                "无论数据源以正号还是负号披露。\n"
                "3. 否决检查：说明是否触发 unsupported/non_decisionable 停止逻辑。\n"
                "4. 结论：仅在所有关键输入和公式可决策时给出投资判断；否则输出不可决策报告。"
            ),
        ]
    )


def build_non_decisionable_report(
    facts: TurtleFacts,
    signals: TurtleComputedSignals,
) -> str:
    """Build a deterministic non-decisionable report without LLM or tool calls."""
    context = facts.context
    missing_inputs = _unique_strings(
        [
            missing
            for result_name in sorted(signals.results)
            for missing in signals.results[result_name].missing_inputs
        ]
    )
    caveats = _all_caveats(facts, signals)
    formula_lines = _formula_status_lines(signals.results)

    lines = [
        "# Turtle 不可决策报告",
        "",
        "结论：不可决策；关键输入或公式状态不足。不输出任何交易动作建议。",
        "",
        "## 标的",
        f"- ticker: {context.ticker}",
        f"- market: {context.market}",
        f"- trade_date: {context.trade_date}",
        f"- company_name: {context.company_name}",
        f"- facts_status: {facts.status}",
        f"- signals_status: {signals.status}",
        "",
        "## 缺失输入",
    ]

    if missing_inputs:
        lines.extend(f"- {name}" for name in missing_inputs)
    else:
        lines.append("- 无")

    lines.extend(["", "## 限制与备注"])
    if caveats:
        lines.extend(f"- {caveat}" for caveat in caveats)
    else:
        lines.append("- 无")

    lines.extend(["", "## 公式状态"])
    if formula_lines:
        lines.extend(formula_lines)
    else:
        lines.append("- 无公式结果")

    return "\n".join(lines)
