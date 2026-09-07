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

SNAPSHOT_ROW = (
    "",
    "1",
    "000001",
    "匿名证券",
    "12.34",
    "1.23",
    "100",
    "100",
    "0",
    "10.00",
    "10.12",
    "0.10",
    "0.99",
    "1012.00",
    "100.00",
    "0",
    "0",
    "A",
    "10.00",
    "",
)

DELIVERY_ROW = (
    "2026-09-01",
    "09:30:00",
    "000001",
    "匿名证券",
    "证券买入",
    "100",
    "成交编号",
    "10.00",
    "1000.00",
    "100",
    "100",
    "100",
    "-1001.00",
    "1.00",
    "0",
    "0",
    "9999.00",
    "-1001.00",
    "合同编号",
    "A",
    "0",
    "CNY",
    "CNY",
    "1",
    "0",
    "CNY",
    "A",
    "",
)


def portfolio_bytes(
    header: tuple[str, ...], rows: tuple[tuple[str, ...], ...], line_ending: str = "\n"
) -> bytes:
    lines = ("\t".join(header), *("\t".join(row) for row in rows))
    return (line_ending.join(lines) + line_ending).encode("gb18030")


def snapshot_bytes(*rows: tuple[str, ...]) -> bytes:
    selected = rows or (SNAPSHOT_ROW,)
    return portfolio_bytes(SNAPSHOT_HEADER, tuple(selected))


def delivery_bytes(*rows: tuple[str, ...]) -> bytes:
    selected = rows or (DELIVERY_ROW,)
    return portfolio_bytes(DELIVERY_HEADER, tuple(selected))


def replace_cells(
    header: tuple[str, ...], row: tuple[str, ...], changes: dict[str, str]
) -> tuple[str, ...]:
    cells = list(row)
    for name, value in changes.items():
        cells[header.index(name)] = value
    return tuple(cells)


def snapshot_row(**changes: str) -> tuple[str, ...]:
    return replace_cells(SNAPSHOT_HEADER, SNAPSHOT_ROW, changes)


def delivery_row(**changes: str) -> tuple[str, ...]:
    return replace_cells(DELIVERY_HEADER, DELIVERY_ROW, changes)


def hk_pair() -> tuple[tuple[str, ...], tuple[str, ...]]:
    execution = delivery_row(
        **{"证券代码": "00700", "市场名称": "沪HK", "交易币种": "HKD", "发生金额": "0"}
    )
    settlement = delivery_row(
        **{
            "成交日期": "2026-09-03",
            "证券代码": "00700",
            "市场名称": "沪HK",
            "交易币种": "HKD",
            "发生金额": "-901.23",
        }
    )
    return execution, settlement
