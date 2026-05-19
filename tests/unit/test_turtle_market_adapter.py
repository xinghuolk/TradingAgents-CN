from tradingagents.dataflows.value_investment.turtle.market_adapter import (
    build_market_facts,
    default_tax_rate,
)


def test_default_tax_rate_by_holding_channel():
    assert default_tax_rate("A", "long_term_domestic") == 0.0
    assert default_tax_rate("HK", "stock_connect") == 0.20
    assert default_tax_rate("HK", "direct_h_share") == 0.28
    assert default_tax_rate("US", "w8ben") == 0.10


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
    assert facts.fields["tax_rate"].reliability == "reliable"
    assert "tax_rate unknown for HK:None" not in caveats


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
