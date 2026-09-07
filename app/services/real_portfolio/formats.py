import csv
import io
from datetime import date
from hashlib import sha256

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import DecodedPortfolioFile, ParsedPortfolioFile

PARSER_VERSION = "portfolio-v1"

SNAPSHOT_HEADER = (
    "操作",
    "序号",
    "证券代码",
    "证券名称",
    "总盈亏",
    "盈亏比例(%)",
    "股票余额",
    "可用余额",
    "冻结数量",
    "参考成本",
    "市价",
    "当日盈亏",
    "当日盈亏比(%)",
    "市值",
    "仓位占比(%)",
    "当日买入",
    "当日卖出",
    "交易市场",
    "盈亏价格",
    "",
)

DELIVERY_HEADER = (
    "成交日期",
    "成交时间",
    "证券代码",
    "证券名称",
    "操作",
    "成交数量",
    "成交编号",
    "成交均价",
    "成交金额",
    "本次余额",
    "股票余额",
    "可用余额",
    "发生金额",
    "手续费",
    "印花税",
    "其他杂费",
    "资金余额",
    "本次金额",
    "合同编号",
    "市场名称",
    "过户费",
    "交易币种",
    "结算币种",
    "结算汇率",
    "港股交易费",
    "币种",
    "市场代码",
    "",
)

FORMAT_BY_HEADER = {
    SNAPSHOT_HEADER: "guotai-snapshot-v1",
    DELIVERY_HEADER: "guotai-delivery-v1",
}


def decode_portfolio_file(content: bytes) -> DecodedPortfolioFile:
    try:
        text = content.decode("gb18030", errors="strict")
    except UnicodeDecodeError:
        raise PortfolioError(
            "INVALID_ENCODING", "file must be valid GB18030 text"
        ) from None

    rows = tuple(
        tuple(row)
        for row in csv.reader(io.StringIO(text, newline=""), delimiter="\t")
    )
    if not rows or rows[0] not in FORMAT_BY_HEADER:
        raise PortfolioError(
            "UNSUPPORTED_FORMAT", "unsupported portfolio file header"
        )

    header, *body = rows
    return DecodedPortfolioFile(
        format_id=FORMAT_BY_HEADER[header],
        parser_version=PARSER_VERSION,
        file_sha256=sha256(content).hexdigest(),
        header=header,
        rows=tuple(body),
    )


def parse_portfolio_file(
    content: bytes, as_of: date | None = None
) -> ParsedPortfolioFile:
    decoded = decode_portfolio_file(content)
    if decoded.format_id == "guotai-snapshot-v1":
        if as_of is None:
            raise PortfolioError(
                "SNAPSHOT_DATE_REQUIRED",
                "snapshot imports require as_of",
                {"source_type": "snapshot"},
            )
        from app.services.real_portfolio.snapshot import parse_snapshot_v1

        parsed = parse_snapshot_v1(decoded, as_of)
    else:
        if as_of is not None:
            raise PortfolioError(
                "DELIVERY_AS_OF_NOT_ALLOWED",
                "delivery statements take dates from source rows",
            )
        from app.services.real_portfolio.delivery import parse_delivery_v1

        parsed = parse_delivery_v1(decoded)

    if not parsed.rows or not any(row.usable for row in parsed.rows):
        raise PortfolioError(
            "NO_USABLE_ROWS", "portfolio file contains no usable data rows"
        )
    return parsed


def fingerprint_identifier(domain: str, value: str) -> str | None:
    if value.strip() in {"", "NULL", "0000000000"}:
        return None
    # This identity namespace is permanent, independent of parser software versions.
    fingerprint_input = f"portfolio-v1\0{domain}\0{value}".encode()
    return sha256(fingerprint_input).hexdigest()
