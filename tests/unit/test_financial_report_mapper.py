from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.mapper import (
    FinancialReportMergeResult,
    merge_financial_report_data,
)
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    currency: str | None = "CNY"
    unit: str | None = None
    confidence: object = "verified"
    source: str | None = "akshare"
    raw_bucket: str | None = "clean_present"
    evidence_page: int | None = None
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeResult:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2024-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    fields: dict[str, FakeField] | None = None


def test_reliable_fields_override_financial_data_and_compute_fcf():
    extraction = FakeResult(fields={
        "net_profit": FakeField("net_profit", Decimal("100")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("130")),
        "capital_expenditures": FakeField("capital_expenditures", Decimal("30")),
        "equity_attributable_to_owners": FakeField("equity_attributable_to_owners", Decimal("700")),
        "minority_int": FakeField("minority_int", Decimal("100")),
        "total_cur_assets": FakeField("total_cur_assets", Decimal("500")),
        "total_cur_liab": FakeField("total_cur_liab", Decimal("250")),
        "total_assets": FakeField("total_assets", Decimal("1000")),
        "total_liabilities": FakeField("total_liabilities", Decimal("400")),
    })
    base = {
        "net_profits": [1.0],
        "operating_cash_flow": 1.0,
        "free_cash_flow": None,
        "financials_list": [{"net_profit": 1.0}],
        "_data_source": {"net_profits": "akshare"},
    }

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert isinstance(merged, FinancialReportMergeResult)
    assert merged.financial_data["net_profits"][0] == 100.0
    assert merged.financial_data["operating_cash_flow"] == 130.0
    assert merged.financial_data["capex"] == 30.0
    assert merged.financial_data["free_cash_flow"] == 100.0
    assert merged.financial_data["current_ratio"] == 2.0
    assert merged.financial_data["debt_ratio"] == 0.4
    assert merged.financial_data["total_equity"] == 800.0
    assert merged.financial_data["_data_source"]["net_profits"] == "financial-report-client"
    assert merged.financial_data["_data_source"]["free_cash_flow"] == "financial-report-client:derived"
    assert merged.financial_data["_data_source"]["total_equity"] == "financial-report-client:derived"
    assert merged.details["current_ratio"]["status"] == "derived"
    assert merged.details["debt_ratio"]["status"] == "derived"
    assert merged.details["total_equity"]["status"] == "derived"
    assert merged.caveats == []
    assert base["net_profits"] == [1.0]
    assert base["financials_list"] == [{"net_profit": 1.0}]
    assert base["_data_source"] == {"net_profits": "akshare"}


def test_unavailable_field_does_not_override_existing_value():
    extraction = FakeResult(fields={
        "operating_cash_flow": FakeField(
            "operating_cash_flow",
            None,
            source=None,
            raw_bucket="source_unavailable",
            is_reliable=False,
            is_present=False,
        )
    })
    base = {"operating_cash_flow": 77.0, "_data_source": {"operating_cash_flow": "akshare"}}

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert merged.financial_data["operating_cash_flow"] == 77.0
    assert merged.financial_data["_data_source"]["operating_cash_flow"] == "akshare"
    assert merged.details["operating_cash_flow"]["status"] == "not_used"
    assert merged.details["operating_cash_flow"]["caveat"] == "operating_cash_flow unavailable: source_unavailable"


def test_no_used_financial_report_fields_do_not_rederive_existing_metrics():
    extraction = FakeResult(fields={})
    base = {
        "operating_cash_flow": 120.0,
        "capex": 40.0,
        "free_cash_flow": 90.0,
        "current_assets": 200.0,
        "current_liabilities": 100.0,
        "current_ratio": 3.0,
        "total_assets": 800.0,
        "total_liabilities": 200.0,
        "debt_ratio": 0.4,
        "_data_source": {
            "free_cash_flow": "akshare",
            "current_ratio": "akshare",
            "debt_ratio": "akshare",
        },
    }

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert merged.financial_data["free_cash_flow"] == 90.0
    assert merged.financial_data["current_ratio"] == 3.0
    assert merged.financial_data["debt_ratio"] == 0.4
    assert merged.financial_data["_data_source"]["free_cash_flow"] == "akshare"
    assert merged.financial_data["_data_source"]["current_ratio"] == "akshare"
    assert merged.financial_data["_data_source"]["debt_ratio"] == "akshare"
    assert "free_cash_flow" not in merged.details
    assert "current_ratio" not in merged.details
    assert "debt_ratio" not in merged.details


def test_partial_financial_report_update_rederives_existing_metric_inputs():
    extraction = FakeResult(fields={
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("150")),
    })
    base = {
        "operating_cash_flow": 100.0,
        "capex": 30.0,
        "free_cash_flow": 70.0,
        "_data_source": {
            "operating_cash_flow": "akshare",
            "capex": "akshare",
            "free_cash_flow": "akshare",
        },
    }

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert merged.financial_data["operating_cash_flow"] == 150.0
    assert merged.financial_data["capex"] == 30.0
    assert merged.financial_data["free_cash_flow"] == 120.0
    assert merged.financial_data["_data_source"]["operating_cash_flow"] == "financial-report-client"
    assert merged.financial_data["_data_source"]["capex"] == "akshare"
    assert merged.financial_data["_data_source"]["free_cash_flow"] == "financial-report-client:derived"
    assert merged.details["free_cash_flow"]["status"] == "derived"


def test_partial_equity_primitives_do_not_rederive_total_equity():
    extraction = FakeResult(fields={
        "equity_attributable_to_owners": FakeField(
            "equity_attributable_to_owners",
            Decimal("900"),
        ),
        "minority_int": FakeField(
            "minority_int",
            None,
            source=None,
            raw_bucket="source_unavailable",
            is_reliable=False,
            is_present=False,
        ),
    })
    base = {
        "equity_attributable_to_owners": 700.0,
        "minority_int": 100.0,
        "total_equity": 800.0,
        "_data_source": {
            "equity_attributable_to_owners": "akshare",
            "minority_int": "akshare",
            "total_equity": "akshare",
        },
    }

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert merged.financial_data["equity_attributable_to_owners"] == 900.0
    assert merged.financial_data["minority_int"] == 100.0
    assert merged.financial_data["total_equity"] == 800.0
    assert merged.financial_data["_data_source"]["total_equity"] == "akshare"
    assert "total_equity" not in merged.details
    assert merged.details["minority_int"]["status"] == "not_used"


def test_partial_debt_primitives_do_not_rederive_interest_bearing_debt():
    extraction = FakeResult(fields={
        "st_borr": FakeField("st_borr", Decimal("12")),
        "lt_borr": FakeField("lt_borr", Decimal("24")),
        "bond_payable": FakeField(
            "bond_payable",
            None,
            source=None,
            raw_bucket="source_unavailable",
            is_reliable=False,
            is_present=False,
        ),
    })
    base = {
        "st_borr": 10.0,
        "lt_borr": 20.0,
        "bond_payable": 30.0,
        "interest_bearing_debt": 60.0,
        "_data_source": {
            "st_borr": "akshare",
            "lt_borr": "akshare",
            "bond_payable": "akshare",
            "interest_bearing_debt": "akshare",
        },
    }

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert merged.financial_data["st_borr"] == 12.0
    assert merged.financial_data["lt_borr"] == 24.0
    assert merged.financial_data["bond_payable"] == 30.0
    assert merged.financial_data["interest_bearing_debt"] == 60.0
    assert merged.financial_data["_data_source"]["interest_bearing_debt"] == "akshare"
    assert "interest_bearing_debt" not in merged.details
    assert merged.details["bond_payable"]["status"] == "not_used"


def test_deepseek_llm_supplement_is_not_used_for_compute():
    extraction = FakeResult(
        llm_provider="deepseek",
        llm_model="deepseek-v3",
        fields={
            "capital_expenditures": FakeField(
                "capital_expenditures",
                Decimal("50"),
                source="llm",
                raw_bucket="llm_supplement_present",
                is_reliable=False,
            )
        },
    )
    base = {"capex": None}

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=("codex",)),
    )

    assert merged.financial_data["capex"] is None
    assert merged.details["capital_expenditures"]["status"] == "display_only"
    assert "display-only" in merged.details["capital_expenditures"]["caveat"]


def test_codex_llm_supplement_can_be_used_when_allowlisted():
    extraction = FakeResult(
        llm_provider="openai",
        llm_model="codex-subscription",
        fields={
            "st_borr": FakeField(
                "st_borr",
                Decimal("12"),
                source="llm",
                raw_bucket="llm_supplement_present",
                is_reliable=False,
            )
        },
    )
    base = {"st_borr": None}

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=("codex",)),
    )

    assert merged.financial_data["st_borr"] == 12.0
    assert merged.financial_data["_data_source"]["st_borr"] == (
        "financial-report-client:llm:codex-subscription"
    )
    assert "allowed by policy" in merged.caveats[0]
