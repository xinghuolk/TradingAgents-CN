import re

from tradingagents.dataflows.value_investment.turtle.market_adapter import (
    build_market_facts,
    default_tax_rate,
    get_turtle_market_facts,
)


def test_default_tax_rate_by_holding_channel():
    assert default_tax_rate("A", "long_term_domestic") == 0.0
    assert default_tax_rate("HK", "stock_connect") == 0.20
    assert default_tax_rate("HK", "direct_h_share") == 0.20  # direct_h_share 分支已移除，回落 HK 默认 0.20
    assert default_tax_rate("US", "w8ben") == 0.10


def test_get_turtle_market_facts_routes_hk_to_hk_market_provider(monkeypatch):
    def reject_legacy_a_share_market_fetch(ticker, market):
        raise AssertionError("HK market facts must not use the A-share market fetcher")

    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_market_data_structured",
        reject_legacy_a_share_market_fetch,
    )
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_dividend_data_sync",
        lambda ticker, market: {"avg_payout_ratio_3y": 0.45, "records": [{"year": 2025}]},
    )
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_buyback_data_sync",
        lambda ticker, market: {"total_cancelled_amount": 1_000_000, "records": [{"year": 2025}]},
    )
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._get_industry_dynamic",
        lambda ticker, market: "legacy-industry",
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info",
        lambda ticker: {
            "market_cap": 2_000_000_000_000,
            "price": 400.0,
            "industry": "互联网",
            "source": "yfinance_hk",
        },
    )

    facts = get_turtle_market_facts("0700.HK", "HK", "stock_connect")

    assert facts.fields["market_cap"].value.value == 2_000_000_000_000
    assert facts.fields["market_cap"].value.currency == "HKD"
    assert facts.fields["close_price"].value == 400.0
    assert facts.fields["industry"].value == "互联网"
    assert "market_cap missing" not in " ".join(facts.caveats)


def test_get_turtle_market_facts_does_not_promote_hk_yfinance_actions(monkeypatch):
    def reject_legacy_hk_action_fetch(ticker, market):
        raise AssertionError("HK action facts must not use the A-share dividend/buyback fetchers")

    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_dividend_data_sync",
        reject_legacy_hk_action_fetch,
    )
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_buyback_data_sync",
        reject_legacy_hk_action_fetch,
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info",
        lambda ticker: {"market_cap": 2_000_000_000_000, "price": 400.0},
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_dividend_data",
        lambda ticker: {"avg_payout_ratio_3y": 0.35, "records": [{"year": 2025}]},
        raising=False,
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_buyback_data",
        lambda ticker: {"total_cancelled_amount": 4_000_000_000, "records": [{"year": 2025}]},
        raising=False,
    )

    facts = get_turtle_market_facts("0700.HK", "HK", "stock_connect")

    assert facts.fields["market_cap"].value.currency == "HKD"
    assert "dividend_avg_payout_ratio_3y" not in facts.fields
    assert "buyback_amount" not in facts.fields
    caveats = " ".join(facts.caveats)
    assert "dividend data missing" in caveats
    assert "buyback data missing" in caveats


def test_build_market_facts_marks_missing_market_cap_as_caveat():
    facts = build_market_facts(
        ticker="600519",
        market="A",
        holding_channel="long_term_domestic",
        market_data={"close_price": 1500.0},
        dividend_data={"avg_payout_ratio_3y": 0.5, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="白酒",
        rf_rate=0.025,
    )

    assert "market_cap missing" in " ".join(facts.caveats)
    assert facts.fields["tax_rate"].value == 0.0


def test_build_market_facts_preserves_buyback_missing_instead_of_zero_when_unverified():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="stock_connect",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data=None,
        industry="综合企业",
        rf_rate=0.025,
    )

    assert "buyback data missing" in " ".join(facts.caveats)
    assert "buyback_amount" not in facts.fields


def test_build_market_facts_treats_legacy_empty_dividend_zero_as_missing():
    facts = build_market_facts(
        ticker="600519",
        market="A",
        holding_channel="long_term_domestic",
        market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
        dividend_data={
            "avg_payout_ratio_3y": 0,
            "records": [],
            "consecutive_years": 0,
            "total_dividend_years": 0,
        },
        buyback_data={"total_cancelled_amount": 1_000_000, "records": [{"year": 2025}]},
        industry="白酒",
        rf_rate=0.025,
    )

    assert "avg_payout_ratio_3y missing" in " ".join(facts.caveats)
    assert "dividend_avg_payout_ratio_3y" not in facts.fields
    assert facts.fields["dividend_records"].value == []


def test_build_market_facts_treats_legacy_empty_buyback_zero_as_missing():
    facts = build_market_facts(
        ticker="600519",
        market="A",
        holding_channel="long_term_domestic",
        market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": [{"year": 2025}]},
        buyback_data={
            "total_cancelled_amount": 0,
            "latest_year_amount": 0,
            "has_active_buyback": False,
            "records": [],
        },
        industry="白酒",
        rf_rate=0.025,
    )

    assert "buyback_amount missing" in " ".join(facts.caveats)
    assert "buyback_amount" not in facts.fields
    assert facts.fields["buyback_records"].value == []


def test_build_market_facts_preserves_verified_zero_buyback_when_records_exist():
    facts = build_market_facts(
        ticker="600519",
        market="A",
        holding_channel="long_term_domestic",
        market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": [{"year": 2025}]},
        buyback_data={
            "total_cancelled_amount": 0,
            "records": [{"year": 2025, "amount": 0, "is_cancelled": True}],
        },
        industry="白酒",
        rf_rate=0.025,
    )

    assert "buyback_amount missing" not in " ".join(facts.caveats)
    assert facts.fields["buyback_amount"].value.value == 0


def test_build_market_facts_marks_partial_dividend_data_as_caveats():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="stock_connect",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"records": None},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="综合企业",
        rf_rate=0.025,
    )

    caveats = " ".join(facts.caveats)
    assert "avg_payout_ratio_3y missing" in caveats
    assert "dividend records missing" in caveats
    assert "dividend_avg_payout_ratio_3y" not in facts.fields
    assert "dividend_records" not in facts.fields


def test_build_market_facts_omits_negative_market_cap_with_invalid_caveat():
    facts = build_market_facts(
        ticker="600519",
        market="A",
        holding_channel="long_term_domestic",
        market_data={"market_cap": -1.0, "close_price": 1500.0},
        dividend_data={"avg_payout_ratio_3y": 0.5, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="白酒",
        rf_rate=0.025,
    )

    assert "market_cap invalid" in " ".join(facts.caveats)
    assert "market_cap" not in facts.fields


def test_build_market_facts_marks_non_finite_and_bool_market_cap_invalid():
    for market_cap in (True, float("nan"), float("inf")):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data={"market_cap": market_cap, "close_price": 1500.0},
            dividend_data={"avg_payout_ratio_3y": 0.5, "records": []},
            buyback_data={"total_cancelled_amount": 0, "records": []},
            industry="白酒",
            rf_rate=0.025,
        )

        assert "market_cap invalid" in " ".join(facts.caveats)
        assert "market_cap" not in facts.fields


def test_build_market_facts_marks_present_buyback_missing_amount_as_caveat():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="stock_connect",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data={"records": []},
        industry="综合企业",
        rf_rate=0.025,
    )

    assert "buyback_amount missing" in " ".join(facts.caveats)
    assert "buyback_amount" not in facts.fields


def test_build_market_facts_omits_negative_buyback_with_invalid_caveat():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="stock_connect",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data={"total_cancelled_amount": -1, "records": []},
        industry="综合企业",
        rf_rate=0.025,
    )

    assert "buyback_amount invalid" in " ".join(facts.caveats)
    assert "buyback_amount" not in facts.fields


def test_build_market_facts_marks_unsupported_tax_rate_as_display_default():
    facts = build_market_facts(
        ticker="XYZ",
        market="EU",
        holding_channel="unknown_channel",
        market_data={"market_cap": 1_000_000, "close_price": 10.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="unknown",
        rf_rate=0.025,
    )

    tax_rate = facts.fields["tax_rate"]
    assert tax_rate.value == 0.0
    assert tax_rate.reliability == "display_only"
    assert "tax_rate unknown for EU:unknown_channel" in " ".join(facts.caveats)


def test_build_market_facts_marks_unknown_hk_tax_channel_as_display_default():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="unknown_channel",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="综合企业",
        rf_rate=0.025,
    )

    tax_rate = facts.fields["tax_rate"]
    assert tax_rate.value == 0.20
    assert tax_rate.reliability == "display_only"
    assert "tax_rate unknown for HK:unknown_channel" in " ".join(facts.caveats)


def test_build_market_facts_defaults_missing_hk_holding_channel():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel=None,
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="综合企业",
        rf_rate=0.025,
    )

    caveats = " ".join(facts.caveats)
    assert facts.fields["holding_channel"].value == "stock_connect"
    assert facts.fields["tax_rate"].value == 0.20
    # 港股通默认即使非显式也按政策默认给 reliable（带渠道假设 caveat）
    assert facts.fields["tax_rate"].reliability == "reliable"
    assert "tax_rate unknown for HK:None" not in caveats
    assert "港股通" in (facts.fields["tax_rate"].caveat or "")


def test_build_market_facts_invalid_rf_env_value_adds_caveat(monkeypatch):
    monkeypatch.setenv("TURTLE_RF_RATE_HK", "not-a-number")

    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="stock_connect",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="综合企业",
    )

    assert "rf_rate invalid" in " ".join(facts.caveats)
    assert "rf_rate" not in facts.fields


def _valid_market_data():
    return {
        "market_cap": 1_500_000_000_000,
        "close_price": 1800.0,
        "total_shares": 1_000_000_000,
        "industry": "白酒",
    }


class TestFailFastDefaults:
    def test_a_share_default_channel_is_reliable_policy_default(self):
        # A股 long_term_domestic(0%) 是政策无条件可靠默认：非显式也 reliable、无 caveat
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel=None,
            market_data=_valid_market_data(),
            dividend_data=None,
            buyback_data=None,
            industry="白酒",
        )
        tax_rate = facts.fields["tax_rate"]
        assert tax_rate.reliability == "reliable"
        assert tax_rate.caveat is None

    def test_explicit_channel_keeps_tax_rate_reliable(self):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data=_valid_market_data(),
            dividend_data=None,
            buyback_data=None,
            industry="白酒",
        )
        tax_rate = facts.fields["tax_rate"]
        assert tax_rate.reliability == "reliable"
        assert tax_rate.caveat is None or "default" not in tax_rate.caveat

    def test_empty_holding_channel_string_counts_as_default(self):
        # 空字符串回落到 A股默认 long_term_domestic，命中政策可靠默认 → reliable
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="   ",
            market_data=_valid_market_data(),
            dividend_data=None,
            buyback_data=None,
            industry="白酒",
        )
        assert facts.fields["tax_rate"].reliability == "reliable"


class TestNoUnconditionalDefaultChannelCaveat:
    def test_explicit_channel_does_not_add_default_channel_caveat(self):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data=_valid_market_data(),
            dividend_data=None,
            buyback_data=None,
            industry="白酒",
        )
        joined = "; ".join(facts.caveats)
        assert "default holding_channel" not in joined
        assert "tax_rate uses default" not in joined


class TestMarketAdapterStatus:
    def test_all_reliable_explicit_channel_is_complete(self):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data=_valid_market_data(),
            dividend_data={"avg_payout_ratio_3y": 0.5, "records": [{"year": 2024}]},
            buyback_data={"total_cancelled_amount": 0, "records": [{"year": 2024}]},
            industry="白酒",
            rf_rate=0.025,
        )
        assert facts.status == "complete"

    def test_market_action_missing_caveats_do_not_degrade_market_status(self):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
            dividend_data=None,
            buyback_data=None,
            industry="白酒",
            rf_rate=0.025,
        )

        assert "dividend data missing" in facts.caveats
        assert "buyback data missing" in facts.caveats
        assert facts.status == "complete"

    def test_market_partial_action_caveats_do_not_degrade_market_status(self):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
            dividend_data={"records": None},
            buyback_data={"records": []},
            industry="白酒",
            rf_rate=0.025,
        )

        joined = " ".join(facts.caveats)
        assert "avg_payout_ratio_3y missing" in joined
        assert "dividend records missing" in joined
        assert "buyback_amount missing" in joined
        assert facts.status == "complete"

    def test_market_material_caveat_still_degrades_market_status(self):
        # tax_rate display_only 仍是 material caveat：用 HK unknown_channel 触发（A股/港股通默认现已 reliable）
        facts = build_market_facts(
            ticker="00001",
            market="HK",
            holding_channel="unknown_channel",
            market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
            dividend_data=None,
            buyback_data=None,
            industry="综合企业",
            rf_rate=0.025,
        )

        assert any("tax_rate unknown" in caveat for caveat in facts.caveats)
        assert facts.status == "degraded"

    def test_a_share_default_channel_yields_complete(self):
        # A股默认渠道(long_term_domestic)现按政策可靠默认 → reliable → complete
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel=None,
            market_data=_valid_market_data(),
            dividend_data={"avg_payout_ratio_3y": 0.5, "records": [{"year": 2024}]},
            buyback_data={"total_cancelled_amount": 0, "records": [{"year": 2024}]},
            industry="白酒",
            rf_rate=0.025,
        )
        assert facts.status == "complete"

    def test_no_market_data_at_all_is_non_decisionable(self):
        facts = build_market_facts(
            ticker="600519",
            market="A",
            holding_channel="long_term_domestic",
            market_data=None,
            dividend_data=None,
            buyback_data=None,
            industry=None,
            rf_rate=None,
        )
        assert facts.status == "non_decisionable"


def test_build_market_facts_carries_provider_and_market_as_of():
    mf = build_market_facts(
        ticker="00700", market="HK", holding_channel="stock_connect",
        market_data={"market_cap": 1e10, "close_price": 100.0, "source": "yfinance_hk"},
        dividend_data=None, buyback_data=None, industry=None,
    )
    cap_ref = mf.fields["market_cap"].value.source_reference
    assert "provider=yfinance_hk" in cap_ref
    assert "fetched_at=" in cap_ref
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", mf.metadata["market_as_of"])


def test_build_market_facts_provider_unknown_when_source_missing():
    mf = build_market_facts(
        ticker="600519", market="A", holding_channel="long_term_domestic",
        market_data={"market_cap": 1e10},
        dividend_data=None, buyback_data=None, industry=None,
    )
    assert "provider=unknown" in mf.fields["market_cap"].value.source_reference


from tradingagents.dataflows.value_investment.turtle import market_adapter


def test_fetch_turtle_market_data_stamps_ashare_source(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_market_data_structured",
        lambda ticker, market: {"market_cap": 1e10, "close_price": 100.0, "total_shares": 1e8},
    )
    data = market_adapter._fetch_turtle_market_data("600519", "A")
    assert data["source"] == "akshare.stock_individual_info_em"


def test_fetch_turtle_market_data_keeps_existing_source(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_market_data_structured",
        lambda ticker, market: {"market_cap": 1e10, "source": "custom"},
    )
    data = market_adapter._fetch_turtle_market_data("600519", "A")
    assert data["source"] == "custom"


def _mk(market, holding_channel):
    return build_market_facts(
        ticker="TEST",
        market=market,
        holding_channel=holding_channel,
        market_data={"market_cap": 1_000_000_000, "close_price": 10.0},
        dividend_data={"avg_payout_ratio_3y": 0.4, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="测试",
        rf_rate=0.025,
    )


def test_a_share_default_channel_tax_rate_reliable_no_caveat():
    facts = _mk("A", None)  # holding_channel 非显式 → 默认 long_term_domestic
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.0
    assert tr.reliability == "reliable"
    assert tr.caveat is None
    assert not any("tax_rate" in c for c in facts.caveats)


def test_hk_stock_connect_default_channel_reliable_with_caveat():
    facts = _mk("HK", None)  # 非显式 → 默认 stock_connect
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.20
    assert tr.reliability == "reliable"
    assert tr.caveat is not None and "港股通" in tr.caveat
    assert any("港股通" in c for c in facts.caveats)


def test_hk_stock_connect_explicit_reliable_with_caveat():
    facts = _mk("HK", "stock_connect")
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.20
    assert tr.reliability == "reliable"
    assert "港股通" in (tr.caveat or "")


def test_us_w8ben_default_channel_stays_display_only():
    facts = _mk("US", None)  # 非显式 → 默认 w8ben；US 不在可靠默认表，走旧逻辑
    tr = facts.fields["tax_rate"]
    assert tr.reliability == "display_only"


def test_us_w8ben_explicit_reliable():
    facts = _mk("US", "w8ben")
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.10
    assert tr.reliability == "reliable"


def test_hk_unknown_channel_display_only():
    facts = _mk("HK", "foobar")
    tr = facts.fields["tax_rate"]
    assert tr.reliability == "display_only"
    assert any("unknown" in c for c in facts.caveats)


def test_hk_direct_h_share_removed_now_display_only():
    # direct_h_share=28% 语义错误已移除：显式传它不再是 reliable 28%
    facts = _mk("HK", "direct_h_share")
    tr = facts.fields["tax_rate"]
    assert tr.reliability == "display_only"
