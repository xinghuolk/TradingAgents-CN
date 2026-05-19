"""Map FinancialReportClient fields into TradingAgents financial_data."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .policy import FinancialReportPolicy


@dataclass(frozen=True)
class FinancialReportMergeResult:
    financial_data: dict[str, Any]
    details: dict[str, dict[str, Any]]
    caveats: list[str]


FIELD_TO_KEY = {
    "net_profit": "net_profits",
    "operating_cash_flow": "operating_cash_flow",
    "capital_expenditures": "capex",
    "money_cap": "cash_and_equivalents",
    "cash": "cash_and_equivalents",
    "total_cur_assets": "current_assets",
    "total_cur_liab": "current_liabilities",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "equity_attributable_to_owners": "equity_attributable_to_owners",
    "minority_int": "minority_int",
    "st_borr": "st_borr",
    "lt_borr": "lt_borr",
    "bond_payable": "bond_payable",
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fields(extraction: Any) -> dict[str, Any]:
    fields = getattr(extraction, "fields", None)
    return fields if isinstance(fields, dict) else {}


def _set_net_profit(data: dict[str, Any], value: float) -> None:
    existing = data.get("net_profits")
    net_profits = list(existing) if isinstance(existing, list) else []
    if net_profits:
        net_profits[0] = value
    else:
        net_profits = [value]
    data["net_profits"] = net_profits

    financials_list = data.get("financials_list")
    if isinstance(financials_list, list) and financials_list:
        copied_financials = list(financials_list)
        if isinstance(copied_financials[0], dict):
            first_financial = dict(copied_financials[0])
            first_financial["net_profit"] = value
            copied_financials[0] = first_financial
        data["financials_list"] = copied_financials
    else:
        data["financials_list"] = [{"net_profit": value}]


def _write_value(data: dict[str, Any], key: str, value: float) -> None:
    if key == "net_profits":
        _set_net_profit(data, value)
    else:
        data[key] = value


def _record_detail(
    *,
    details: dict[str, dict[str, Any]],
    field_id: str,
    key: str,
    status: str,
    value: float | None,
    source_label: str,
    caveat: str | None,
) -> None:
    details[field_id] = {
        "target_key": key,
        "status": status,
        "value": value,
        "source": source_label,
        "caveat": caveat,
    }


def _derive_aggregate_metrics(
    data: dict[str, Any], details: dict[str, dict[str, Any]], used_keys: set[str]
) -> None:
    """Compose aggregate keys (total_equity, interest_bearing_debt) from primitive
    catalog fields. Both require ALL primitive components to be present and numeric
    — partial composition would silently mislead downstream consumers."""
    data_source = data.setdefault("_data_source", {})

    equity_inputs = {"equity_attributable_to_owners", "minority_int"}
    if equity_inputs.issubset(used_keys):
        parent = _to_float(data.get("equity_attributable_to_owners"))
        minority = _to_float(data.get("minority_int"))
        if parent is not None and minority is not None:
            data["total_equity"] = parent + minority
            data_source["total_equity"] = "financial-report-client:derived"
            details["total_equity"] = {
                "target_key": "total_equity",
                "status": "derived",
                "value": data["total_equity"],
                "source": "financial-report-client:derived",
                "caveat": None,
            }

    ibd_inputs = {"st_borr", "lt_borr", "bond_payable"}
    if ibd_inputs.issubset(used_keys):
        st = _to_float(data.get("st_borr"))
        lt = _to_float(data.get("lt_borr"))
        bond = _to_float(data.get("bond_payable"))
        if st is not None and lt is not None and bond is not None:
            data["interest_bearing_debt"] = st + lt + bond
            data_source["interest_bearing_debt"] = "financial-report-client:derived"
            details["interest_bearing_debt"] = {
                "target_key": "interest_bearing_debt",
                "status": "derived",
                "value": data["interest_bearing_debt"],
                "source": "financial-report-client:derived",
                "caveat": None,
            }


def _derive_metrics(data: dict[str, Any], details: dict[str, dict[str, Any]], used_keys: set[str]) -> None:
    data_source = data.setdefault("_data_source", {})

    operating_cash_flow = _to_float(data.get("operating_cash_flow"))
    capex = _to_float(data.get("capex"))
    fcf_inputs = {"operating_cash_flow", "capex"}
    if used_keys.intersection(fcf_inputs) and operating_cash_flow is not None and capex is not None:
        data["free_cash_flow"] = operating_cash_flow - abs(capex)
        data_source["free_cash_flow"] = "financial-report-client:derived"
        details["free_cash_flow"] = {
            "target_key": "free_cash_flow",
            "status": "derived",
            "value": data["free_cash_flow"],
            "source": "financial-report-client:derived",
            "caveat": None,
        }

    current_assets = _to_float(data.get("current_assets"))
    current_liabilities = _to_float(data.get("current_liabilities"))
    current_ratio_inputs = {"current_assets", "current_liabilities"}
    if (
        used_keys.intersection(current_ratio_inputs)
        and current_assets is not None
        and current_liabilities is not None
        and current_liabilities > 0
    ):
        data["current_ratio"] = current_assets / current_liabilities
        data_source["current_ratio"] = "financial-report-client:derived"
        details["current_ratio"] = {
            "target_key": "current_ratio",
            "status": "derived",
            "value": data["current_ratio"],
            "source": "financial-report-client:derived",
            "caveat": None,
        }

    total_assets = _to_float(data.get("total_assets"))
    total_liabilities = _to_float(data.get("total_liabilities"))
    debt_ratio_inputs = {"total_assets", "total_liabilities"}
    if (
        used_keys.intersection(debt_ratio_inputs)
        and total_assets is not None
        and total_assets > 0
        and total_liabilities is not None
    ):
        data["debt_ratio"] = total_liabilities / total_assets
        data_source["debt_ratio"] = "financial-report-client:derived"
        details["debt_ratio"] = {
            "target_key": "debt_ratio",
            "status": "derived",
            "value": data["debt_ratio"],
            "source": "financial-report-client:derived",
            "caveat": None,
        }


def merge_financial_report_data(
    *,
    financial_data: dict[str, Any],
    extraction: Any,
    policy: FinancialReportPolicy,
) -> FinancialReportMergeResult:
    merged = dict(financial_data)
    merged["_data_source"] = dict(financial_data.get("_data_source") or {})
    merged["_supplemented_details"] = dict(financial_data.get("_supplemented_details") or {})
    details: dict[str, dict[str, Any]] = {}
    caveats: list[str] = []
    used_keys: set[str] = set()

    for field_id, key in FIELD_TO_KEY.items():
        field = _fields(extraction).get(field_id)
        if field is None:
            continue

        decision = policy.decide(field=field, result=extraction)
        value = _to_float(getattr(field, "value", None))

        if decision.caveat:
            caveats.append(decision.caveat)

        if decision.can_compute and value is not None:
            _write_value(merged, key, value)
            merged["_data_source"][key] = decision.source_label
            used_keys.add(key)
            status = "used"
        elif decision.can_display and value is not None:
            status = "display_only"
        else:
            status = "not_used"

        _record_detail(
            details=details,
            field_id=field_id,
            key=key,
            status=status,
            value=value,
            source_label=decision.source_label,
            caveat=decision.caveat,
        )

    _derive_aggregate_metrics(merged, details, used_keys)
    _derive_metrics(merged, details, used_keys)
    merged["_supplemented_details"].update(details)
    merged["_financial_report_client"] = {
        "company": getattr(extraction, "company", None),
        "market": getattr(extraction, "market", None),
        "period_end": getattr(extraction, "period_end", None),
        "catalog_version": getattr(extraction, "catalog_version", None),
        "caveats": caveats,
    }
    return FinancialReportMergeResult(financial_data=merged, details=details, caveats=caveats)
