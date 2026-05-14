import importlib.util
from pathlib import Path

from tradingagents.services.report_collector_client import (
    _hydrate_v2_compat_payload,
    _pick_pdf_for_latest_meta,
    ReportCollectorClient,
)


def _load_mapper():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "tradingagents"
        / "dataflows"
        / "value_investment"
        / "report_data_mapper.py"
    )
    spec = importlib.util.spec_from_file_location("report_data_mapper_local", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


map_extracted_reports_to_financial_data = _load_mapper().map_extracted_reports_to_financial_data


def test_pick_pdf_for_latest_meta_prefers_source_url_match():
    pdfs = [
        {
            "id": 1,
            "file_name": "2026_quarterly_q3_en.pdf",
            "original_title": "2026 Q3 Results",
            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0110/old.pdf",
            "report_year": 2026,
        },
        {
            "id": 2,
            "file_name": "2026_quarterly_q4_fy_en.pdf",
            "original_title": "2026 Q4 and Full Year Results",
            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0204/new.pdf",
            "report_year": 2026,
        },
    ]
    latest_meta = {
        "report_type": "quarterly",
        "title": "ANNOUNCEMENT OF THE 2026 Q4 AND FULL YEAR FINANCIAL RESULTS",
        "web_path": "/listedco/listconews/sehk/2026/0204/new.pdf",
        "pdf_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0204/new.pdf",
        "publish_time": "2026-02-04T18:00:00",
        "year": 2026,
    }

    picked = _pick_pdf_for_latest_meta(pdfs, latest_meta)
    assert picked is not None
    assert picked["id"] == 2


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.extract_called = False
        self.post_calls = []

    def get(self, url, params=None, timeout=None):
        if url.endswith("/api/v1/reports/search-latest"):
            return _FakeResponse({
                "success": True,
                "data": {
                    "reports": [{
                        "report_type": "annual",
                        "title": "2026 Annual Results",
                        "pdf_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0204/new.pdf",
                        "publish_time": "2026-02-04T18:00:00",
                        "year": 2026,
                    }]
                },
            })
        if url.endswith("/api/v1/pdfs"):
            return _FakeResponse({
                "success": True,
                "data": {
                    "pdfs": [
                        {
                            "id": 1,
                            "file_name": "old.pdf",
                            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0110/old.pdf",
                            "report_year": 2026,
                        },
                        {
                            "id": 2,
                            "file_name": "new.pdf",
                            "source_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0204/new.pdf",
                            "report_year": 2026,
                        },
                    ]
                },
            })
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, timeout=None, params=None, headers=None):
        self.post_calls.append(url)
        if url.endswith("/api/v1/extract/content"):
            self.extract_called = True
            raise AssertionError("PDF provider path must not call extraction")
        if url.endswith("/api/v1/reports/hk/batch-download"):
            return _FakeResponse({"success": True, "data": {"downloaded_count": 1}})
        raise AssertionError(f"unexpected POST {url}")


def test_fetch_latest_pdf_info_selects_pdf_without_extracting_content():
    client = ReportCollectorClient(base_url="http://collector", port=8001)
    fake_session = _FakeSession()
    client._session = fake_session

    pdf_info = client.fetch_latest_pdf_info(
        stock_code="09987",
        market="HK",
        report_types=("annual",),
    )

    assert pdf_info is not None
    assert pdf_info["id"] == 2
    assert pdf_info["_selection"]["selected_report_type"] == "annual"
    assert fake_session.extract_called is False


def test_hydrate_v2_compat_payload_uses_statement_periods():
    payload = {
        "schema_version": "v2",
        "document": {
            "stock_code": "09987",
            "report_type": "quarterly",
            "primary_period_id": "2025Q3_YTD",
        },
        "periods": [
            {"period_id": "2025Q3_YTD", "scope": "year_to_date", "is_primary": True},
            {"period_id": "2025Q3_BALANCE", "scope": "point_in_time"},
            {"period_id": "2024Q3_YTD", "scope": "year_to_date"},
        ],
        "facts": [
            {
                "statement": "income_statement",
                "metric": "revenue",
                "period_id": "2025Q3_YTD",
                "value": 123.0,
            },
            {
                "statement": "income_statement",
                "metric": "net_profit",
                "period_id": "2025Q3_YTD",
                "value": 10.0,
            },
            {
                "statement": "income_statement",
                "metric": "revenue",
                "period_id": "2024Q3_YTD",
                "value": 98.0,
            },
            {
                "statement": "balance_sheet",
                "metric": "total_assets",
                "period_id": "2025Q3_BALANCE",
                "value": 1000.0,
            },
            {
                "statement": "balance_sheet",
                "metric": "total_liabilities",
                "period_id": "2025Q3_BALANCE",
                "value": 400.0,
            },
            {
                "statement": "cash_flow_statement",
                "metric": "operating_cash_flow",
                "period_id": "2025Q3_YTD",
                "value": 50.0,
            },
            {
                "statement": "financial_metrics",
                "metric": "roe",
                "period_id": "2025Q3_YTD",
                "value": 9.2,
            },
        ],
    }

    hydrated = _hydrate_v2_compat_payload(payload)

    assert hydrated["income_statement"]["revenue"] == 123.0
    assert hydrated["income_statement"]["net_profit"] == 10.0
    assert hydrated["balance_sheet"]["total_assets"] == 1000.0
    assert hydrated["balance_sheet"]["total_liabilities"] == 400.0
    assert hydrated["cash_flow_statement"]["operating_cash_flow"] == 50.0
    assert hydrated["financial_metrics"]["roe"] == 9.2
    assert hydrated["_v2_selection"]["primary_period_id"] == "2025Q3_YTD"
    assert hydrated["_v2_selection"]["balance_sheet_period_id"] == "2025Q3_BALANCE"


def test_report_data_mapper_prefers_v2_statement_data():
    extracted_reports = [
        {
            "_v2_statement_data": {
                "income_statement": {"net_profit": 30.0, "revenue": 300.0},
                "balance_sheet": {
                    "total_assets": 1200.0,
                    "total_equity": 700.0,
                    "total_liabilities": 500.0,
                    "cash_and_equivalents": 150.0,
                    "current_assets": 400.0,
                    "current_liabilities": 200.0,
                },
                "cash_flow_statement": {"operating_cash_flow": 90.0, "free_cash_flow": 55.0},
                "financial_metrics": {"roe": 12.0},
            },
            "income_statement": {"net_profit": 1.0, "revenue": 1.0},
            "balance_sheet": {"total_assets": 1.0},
            "cash_flow_statement": {"operating_cash_flow": 1.0, "free_cash_flow": 1.0},
            "financial_metrics": {"roe": 1.0},
            "_pdf_info": {"report_year": 2025},
        }
    ]

    mapped = map_extracted_reports_to_financial_data(extracted_reports)

    assert mapped["net_profits"][0] == 30.0
    assert mapped["total_assets"] == 1200.0
    assert mapped["total_equity"] == 700.0
    assert mapped["operating_cash_flow"] == 90.0
    assert mapped["free_cash_flow"] == 55.0
    assert mapped["roe_avg_3y"] == 12.0
