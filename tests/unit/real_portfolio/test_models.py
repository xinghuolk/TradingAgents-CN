from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from typing import get_type_hints

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    DecodedPortfolioFile,
    EvidenceRef,
    ParseWarning,
    SecurityId,
    canonical_decimal_string,
)


def test_security_id_accepts_supported_markets_only():
    assert str(SecurityId("A", "600519")) == "A:600519"
    assert str(SecurityId("HK", "00700")) == "HK:00700"
    with pytest.raises(ValueError, match="invalid security id"):
        SecurityId("US", "AAPL")


def test_security_id_is_immutable():
    security = SecurityId("A", "600519")
    with pytest.raises(FrozenInstanceError):
        security.code = "000001"


def test_decoded_file_rejects_mutable_row_collection():
    with pytest.raises(TypeError, match="rows must be a tuple"):
        DecodedPortfolioFile(
            format_id="guotai-snapshot-v1",
            parser_version="v1",
            file_sha256="abc123",
            header=("证券代码",),
            rows=[],
        )


def test_parse_warning_is_immutable():
    warning = ParseWarning(
        warning_type="source_fact_replaced",
        import_id=None,
        line_number=2,
        impact_from=None,
        impact_through=None,
        message="newer fact selected",
        affects_quantity=True,
    )

    with pytest.raises(FrozenInstanceError):
        warning.message = "changed"


def test_provenance_records_reconstruct_with_opaque_string_import_id():
    warning = ParseWarning(
        warning_type="source_fact_replaced",
        import_id=None,
        line_number=2,
        impact_from=None,
        impact_through=None,
        message="newer fact selected",
        affects_quantity=True,
    )
    evidence = EvidenceRef(
        import_id=None,
        line_number=2,
        fact_key="fact-1",
        role="execution",
    )

    reconstructed_warning = replace(warning, import_id="delivery-1")
    reconstructed_evidence = replace(evidence, import_id="delivery-1")

    assert reconstructed_warning.import_id == "delivery-1"
    assert reconstructed_evidence.import_id == "delivery-1"
    assert get_type_hints(ParseWarning)["import_id"] == str | None
    assert get_type_hints(EvidenceRef)["import_id"] == str | None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.000"), "0"),
        (Decimal("12.3400"), "12.34"),
        (Decimal("1E+3"), "1000"),
        (Decimal("1.20E-3"), "0.0012"),
    ],
)
def test_decimal_serializer_returns_canonical_fixed_point_string(value, expected):
    assert canonical_decimal_string(value) == expected


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_decimal_serializer_rejects_non_finite_values(value):
    with pytest.raises(ValueError, match="finite"):
        canonical_decimal_string(value)


def test_portfolio_error_has_stable_safe_code():
    error = PortfolioError("SNAPSHOT_DATE_REQUIRED", "snapshot imports require as_of")
    assert error.code == "SNAPSHOT_DATE_REQUIRED"
    assert error.message == "snapshot imports require as_of"
    assert error.context == {}
