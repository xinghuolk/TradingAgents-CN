from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import SecurityId, canonical_decimal_string


def test_security_id_accepts_supported_markets_only():
    assert str(SecurityId("A", "600519")) == "A:600519"
    assert str(SecurityId("HK", "00700")) == "HK:00700"
    with pytest.raises(ValueError, match="invalid security id"):
        SecurityId("US", "AAPL")


def test_security_id_is_immutable():
    security = SecurityId("A", "600519")
    with pytest.raises(FrozenInstanceError):
        security.code = "000001"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_decimal_serializer_rejects_non_finite_values(value):
    with pytest.raises(ValueError, match="finite"):
        canonical_decimal_string(value)


def test_portfolio_error_has_stable_safe_code():
    error = PortfolioError("SNAPSHOT_DATE_REQUIRED", "snapshot imports require as_of")
    assert error.code == "SNAPSHOT_DATE_REQUIRED"
    assert error.message == "snapshot imports require as_of"
    assert error.context == {}
