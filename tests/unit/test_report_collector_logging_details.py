import logging
import importlib.util
from pathlib import Path

from tradingagents.utils.logging_manager import StructuredFormatter


def _load_merge_financial_data():
    module_path = (
        Path(__file__).resolve().parents[2]
        / 'tradingagents'
        / 'dataflows'
        / 'value_investment'
        / 'report_data_mapper.py'
    )
    spec = importlib.util.spec_from_file_location('report_data_mapper_local', module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.merge_financial_data


merge_financial_data = _load_merge_financial_data()


def test_structured_formatter_keeps_extra_fields():
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name='report_data_mapper',
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg='merge done',
        args=(),
        exc_info=None,
    )
    record.event_type = 'report_collector_merge'
    record.supplemented_fields = ['operating_cash_flow']
    record.supplemented_details = {
        'operating_cash_flow': {
            'akshare_value': None,
            'collector_value': 1466.0,
        }
    }

    formatted = formatter.format(record)
    assert '"event_type": "report_collector_merge"' in formatted
    assert '"supplemented_fields": ["operating_cash_flow"]' in formatted
    assert '"collector_value": 1466.0' in formatted


def test_merge_financial_data_exposes_supplemented_details():
    akshare_data = {
        'operating_cash_flow': None,
        'financials_list': [],
    }
    rc_data = {
        'operating_cash_flow': 1466.0,
        'financials_list': [{'report_year': 2025, 'net_profit': 140.0}],
    }

    merged = merge_financial_data(akshare_data, rc_data)

    details = merged.get('_supplemented_details') or {}
    assert 'operating_cash_flow' in details
    assert details['operating_cash_flow']['collector_value'] == 1466.0
    assert 'financials_list' in details
