"""Task 4/11: mapper.py 币种守卫 + repurchase_of_stock 导出 TDD 测试。"""
import pytest
from types import SimpleNamespace
from tradingagents.dataflows.financial_reports.mapper import merge_financial_report_data
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy


def _field(value, currency='HKD'):
    """构造 field mock。
    policy.decide 需要：
      - field.value            — 字段数值
      - field.is_present       — 值存在标记（默认 True）
      - field.is_reliable      — True → can_compute=True（非 LLM 路径）
      - field.source           — 不为 'llm'，走非 LLM 分支
      - field.confidence       — 不为 'llm_supplement'，走非 LLM 分支
      - field.field_id         — caveat 里用到（可选，有默认值 'unknown'）
      - field.raw_bucket       — is_reliable=False 时用到（此处 is_reliable=True，不会触发）
    currency 是我们自己加的属性，policy.decide 不会读它，仅供测试验证。
    """
    return SimpleNamespace(
        value=value,
        currency=currency,
        is_present=True,
        is_reliable=True,
        source='akshare',
        confidence=None,
        field_id='test_field',
        raw_bucket='',
    )


def _extraction(fields, currency='HKD'):
    """构造 extraction mock。
    policy.decide(result=extraction) 会读：
      - extraction.llm_model   — 用于 model_name（不存在 → ''）
      - extraction.llm_provider — 用于 model_name（不存在 → ''）
    mapper 会读：
      - extraction.fields      — dict[str, field]
      - extraction.company/market/period_end/catalog_version — 元信息
      - extraction.currency    — 我们新加的币种属性
    """
    return SimpleNamespace(
        fields=fields,
        company='00001',
        market='HK',
        period_end='2025-12-31',
        catalog_version='v1',
        currency=currency,
    )


def _policy():
    return FinancialReportPolicy(allow_llm_models=('gpt-5.5',))


# ── 测试 1：_currency 从 extraction 写入 merged ──────────────────────────────
def test_tags_currency_from_extraction():
    ext = _extraction({'net_profit': _field(11841000000.0)})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data['_currency'] == 'HKD'


# ── 测试 2：repurchase_of_stock 正确导出 ─────────────────────────────────────
def test_exports_repurchase_of_stock():
    ext = _extraction({'repurchase_of_stock': _field(0.0)})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data.get('repurchase_of_stock') == 0.0


# ── 测试 3：existing _currency 与 extraction.currency 不一致 → ValueError ────
def test_rejects_cross_currency_existing():
    ext = _extraction({'net_profit': _field(1.0)}, currency='HKD')
    with pytest.raises(ValueError):
        merge_financial_report_data(
            financial_data={'_currency': 'CNY'},
            extraction=ext,
            policy=_policy(),
        )
