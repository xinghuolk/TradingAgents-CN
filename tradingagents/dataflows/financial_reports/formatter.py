"""Format FinancialReportClient data for agent-visible reports."""

from __future__ import annotations

from typing import Any


CORE_FIELDS = (
    "net_profit",
    "operating_cash_flow",
    "capital_expenditures",
    "total_equity",
    "cash_and_equivalents",
    "interest_bearing_debt",
    "current_assets",
    "current_liabilities",
    "total_assets",
    "total_liabilities",
)


def _one_line(value: Any) -> str:
    return " ".join(str("" if value is None else value).split())


def _staleness_text(extraction: Any) -> str:
    staleness = getattr(extraction, "staleness", None)
    return _one_line(getattr(staleness, "value", staleness or "unknown"))


def _format_value(field: Any) -> str:
    value = getattr(field, "value", None)
    currency = getattr(field, "currency", None)
    unit = getattr(field, "unit", None)
    suffix = " ".join(_one_line(part) for part in (currency, unit) if part)
    return f"{_one_line(value)} {suffix}".strip()


def _field_line(field_id: str, field: Any) -> str:
    page = getattr(field, "evidence_page", None)
    source = _one_line(getattr(field, "source", None) or "unknown")
    page_text = f", page {page}" if page is not None else ""
    reliability = "reliable" if bool(getattr(field, "is_reliable", False)) else "display-only"
    return f"- {_one_line(field_id)}: {_format_value(field)} ({reliability}, source={source}{page_text})"


def format_annual_report_section(extraction: Any, caveats: list[str], max_fields: int = 12) -> str:
    raw_fields = getattr(extraction, "fields", {})
    fields = raw_fields if isinstance(raw_fields, dict) else {}
    company = _one_line(getattr(extraction, "company", ""))
    market = _one_line(getattr(extraction, "market", ""))
    period_end = _one_line(getattr(extraction, "period_end", ""))
    catalog_version = _one_line(getattr(extraction, "catalog_version", ""))
    staleness = _staleness_text(extraction)

    lines = [
        "## 年报权威数据（FinancialReportClient）",
        f"- extraction: {company} {market} {period_end}",
        f"- catalog_version: {catalog_version}",
        f"- staleness: {staleness}",
    ]

    staleness_obj = getattr(extraction, "staleness", None)
    if bool(getattr(staleness_obj, "is_stale", False)):
        lines.append("- caveat: stale extraction; display-only unless explicitly allowed")
    if bool(getattr(staleness_obj, "is_missing", False)):
        lines.append("- caveat: missing annual-report extraction")

    shown = 0
    for field_id in CORE_FIELDS:
        if shown >= max_fields:
            break
        field = fields.get(field_id)
        if field is None or getattr(field, "value", None) is None:
            continue
        lines.append(_field_line(field_id, field))
        shown += 1

    for caveat in caveats:
        if caveat:
            lines.append(f"- caveat: {_one_line(caveat)}")

    if shown == 0 and not caveats:
        lines.append("- no usable annual-report fields")

    return "\n".join(lines)


def format_value_report_source_note(financial_data: dict[str, Any]) -> str:
    raw_source_map = financial_data.get("_data_source")
    source_map = raw_source_map if isinstance(raw_source_map, dict) else {}
    frc_fields = [
        key for key, source in source_map.items()
        if isinstance(source, str) and source.startswith("financial-report-client")
    ]
    if not frc_fields:
        return ""

    raw_meta = financial_data.get("_financial_report_client")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    raw_caveats = meta.get("caveats")
    caveats = raw_caveats if isinstance(raw_caveats, list) else []

    lines = [
        "",
        "▶ 年报数据来源说明（FinancialReportClient）",
        "───────────────────────────────────────────────────────────────",
        f"  FinancialReportClient 字段参与计算: {', '.join(sorted(_one_line(field) for field in frc_fields))}",
        f"  extraction: {_one_line(meta.get('company', ''))} {_one_line(meta.get('market', ''))} {_one_line(meta.get('period_end', ''))}",
        f"  catalog_version: {_one_line(meta.get('catalog_version', ''))}",
    ]
    for caveat in caveats[:5]:
        lines.append(f"  caveat: {_one_line(caveat)}")
    return "\n".join(lines) + "\n"
