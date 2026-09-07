from datetime import date
from hashlib import sha256

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import (
    decode_portfolio_file,
    fingerprint_identifier,
    parse_portfolio_file,
)
from tests.unit.real_portfolio.fixtures import (
    DELIVERY_HEADER,
    DELIVERY_ROW,
    SNAPSHOT_HEADER,
    SNAPSHOT_ROW,
    delivery_bytes,
    portfolio_bytes,
    snapshot_bytes,
)


@pytest.mark.parametrize(
    ("content", "format_id", "expected_header", "expected_row"),
    (
        (snapshot_bytes(), "guotai-snapshot-v1", SNAPSHOT_HEADER, SNAPSHOT_ROW),
        (delivery_bytes(), "guotai-delivery-v1", DELIVERY_HEADER, DELIVERY_ROW),
    ),
)
def test_decode_requires_exact_header_and_preserves_source_hash(
    content, format_id, expected_header, expected_row
):
    decoded = decode_portfolio_file(content)

    assert decoded.format_id == format_id
    assert decoded.parser_version == "portfolio-v1"
    assert decoded.header == expected_header
    assert decoded.rows == (expected_row,)
    assert decoded.file_sha256 == sha256(content).hexdigest()


def test_decode_keeps_lf_and_crlf_source_hashes_distinct():
    lf_content = portfolio_bytes(SNAPSHOT_HEADER, (SNAPSHOT_ROW,), "\n")
    crlf_content = portfolio_bytes(SNAPSHOT_HEADER, (SNAPSHOT_ROW,), "\r\n")

    lf = decode_portfolio_file(lf_content)
    crlf = decode_portfolio_file(crlf_content)

    assert lf.header == SNAPSHOT_HEADER
    assert crlf.header == SNAPSHOT_HEADER
    assert lf.rows == (SNAPSHOT_ROW,)
    assert crlf.rows == (SNAPSHOT_ROW,)
    assert lf.file_sha256 == sha256(lf_content).hexdigest()
    assert crlf.file_sha256 == sha256(crlf_content).hexdigest()
    assert lf.file_sha256 != crlf.file_sha256


def test_decode_wraps_invalid_gb18030_bytes_in_a_safe_error():
    with pytest.raises(PortfolioError) as captured:
        decode_portfolio_file(b"\xff\xff")

    assert captured.value.code == "INVALID_ENCODING"
    assert captured.value.message == "file must be valid GB18030 text"


def test_decode_rejects_unrecognized_header():
    content = portfolio_bytes(("unknown", ""), (("value", ""),))

    with pytest.raises(PortfolioError) as captured:
        decode_portfolio_file(content)

    assert captured.value.code == "UNSUPPORTED_FORMAT"


def test_decode_requires_trailing_empty_header_cell():
    content = portfolio_bytes(SNAPSHOT_HEADER[:-1], (SNAPSHOT_ROW[:-1],))

    with pytest.raises(PortfolioError) as captured:
        decode_portfolio_file(content)

    assert captured.value.code == "UNSUPPORTED_FORMAT"


def test_header_only_snapshot_is_rejected_when_it_has_no_usable_rows():
    content = portfolio_bytes(SNAPSHOT_HEADER, ())

    with pytest.raises(PortfolioError) as captured:
        parse_portfolio_file(content, as_of=date(2026, 9, 6))

    assert captured.value.code == "NO_USABLE_ROWS"


def test_blank_snapshot_rows_are_rejected_when_none_are_usable():
    content = portfolio_bytes(SNAPSHOT_HEADER, ((), ("",) * len(SNAPSHOT_HEADER)))

    with pytest.raises(PortfolioError) as captured:
        parse_portfolio_file(content, as_of=date(2026, 9, 6))

    assert captured.value.code == "NO_USABLE_ROWS"


def test_delivery_rejects_as_of_before_delivery_parsing():
    with pytest.raises(PortfolioError) as captured:
        parse_portfolio_file(delivery_bytes(), as_of=date(2026, 9, 6))

    assert captured.value.code == "DELIVERY_AS_OF_NOT_ALLOWED"


@pytest.mark.parametrize("value", ("", "   ", "NULL", "0000000000"))
def test_identifier_fingerprint_ignores_blank_and_placeholder_values(value):
    assert fingerprint_identifier("contract", value) is None


def test_identifier_fingerprint_is_domain_scoped_and_deterministic():
    transaction = fingerprint_identifier("transaction", "ABC-1")

    assert transaction == sha256(b"portfolio-v1\0transaction\0ABC-1").hexdigest()
    assert transaction != fingerprint_identifier("contract", "ABC-1")
