# -*- coding: utf-8 -*-
"""
Report-Collector REST 客户端

同步客户端，使用 requests 库。
LangChain tool 上下文是同步的，使用 async 会导致事件循环冲突。

Report-Collector API:
  GET  /health                        - 健康检查
  GET  /api/v1/pdfs                   - 列出已下载PDF
  POST /api/v1/reports/cn/batch-download - 批量下载A股年报
  POST /api/v1/reports/hk/batch-download - 批量下载港股年报
  POST /api/v1/extract/content        - 提取PDF结构化财务数据
"""

import re
import requests
from datetime import date, datetime
from typing import Dict, List, Optional, Any, Iterable, Tuple
from urllib.parse import urlparse

from tradingagents.utils.logging_init import get_logger

logger = get_logger("report_collector_client")

_REPORT_TYPES_HK_DEFAULT: Tuple[str, ...] = ("quarterly", "semi_annual", "annual")
_V2_SCHEMA_VERSION = "v2"
_SUPPORTED_SCHEMA_VERSIONS = {"v1", "v2"}
_V2_STATEMENTS: Tuple[str, ...] = (
    "income_statement",
    "balance_sheet",
    "cash_flow_statement",
    "financial_metrics",
)


def _parse_period_end_date(report_period: str) -> Optional[date]:
    """
    从 report_period 文本中提取“期间截止日”（用于跨类型选最新）。

    例:
    - "year ended December 31, 2025" -> 2025-12-31
    - "six months ended June 30, 2025" -> 2025-06-30
    - "quarter ended September 30, 2025" -> 2025-09-30
    """
    if not report_period or not isinstance(report_period, str):
        return None
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", report_period)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%B %d, %Y").date()
    except Exception:
        return None


def _pick_pdf_for_latest_meta(
    pdfs: List[Dict[str, Any]],
    latest_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    在 list_pdfs 的结果中，尽量挑出与 search-latest 返回最匹配的那份 PDF。

    优先匹配顺序：
    1) source_url 与 search-latest 的 pdf_url/web_path 路径对齐
    2) 标题相似度（title vs original_title/file_name）
    3) 发布日期 / 报告年份 / 季度标记（q1/q2/q3/q4/h1/fy）
    4) 无法判定时回退第一条（沿用服务端排序）
    """
    if not pdfs:
        return None

    def _normalize_text(value: Any) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"\s+", " ", text)

    def _normalize_path(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        parsed = urlparse(raw)
        path = parsed.path if (parsed.scheme or parsed.netloc) else raw
        path = path.split("?", 1)[0].split("#", 1)[0].strip().lower()
        return re.sub(r"/+", "/", path)

    def _extract_filename(value: Any) -> str:
        path = _normalize_path(value)
        if not path:
            return ""
        return path.rsplit("/", 1)[-1]

    def _extract_period_token(value: Any) -> str:
        text = _normalize_text(value)
        if not text:
            return ""
        if ("q4" in text or "fourth quarter" in text) and (
            "full year" in text or "annual results" in text or "year ended" in text
        ):
            return "q4_fy"
        for token, patterns in (
            ("q1", [r"\bq1\b", r"first quarter", r"\b1q\b"]),
            ("q2", [r"\bq2\b", r"second quarter", r"\b2q\b", r"six months", r"interim"]),
            ("q3", [r"\bq3\b", r"third quarter", r"\b3q\b", r"nine months"]),
            ("q4", [r"\bq4\b", r"fourth quarter", r"\b4q\b"]),
            ("h1", [r"\bh1\b", r"six months", r"interim"]),
            ("fy", [r"full year", r"annual", r"year ended", r"\bfy\b"]),
        ):
            if any(re.search(p, text, re.IGNORECASE) for p in patterns):
                return token
        return ""

    def _parse_iso_datetime(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None

    def _parse_hk_release_time(value: Any) -> Optional[datetime]:
        text = str(value or "").replace("Release Time:", "").strip()
        if not text:
            return None
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    latest_pdf_url = _normalize_path(latest_meta.get("pdf_url"))
    latest_web_path = _normalize_path(latest_meta.get("web_path"))
    latest_title = _normalize_text(latest_meta.get("title"))
    latest_file_name = _extract_filename(latest_meta.get("pdf_url")) or _extract_filename(latest_meta.get("web_path"))
    latest_period_token = _extract_period_token(
        f"{latest_meta.get('title') or ''} {latest_meta.get('pdf_url') or ''} {latest_meta.get('web_path') or ''}"
    )
    latest_year = latest_meta.get("year")
    latest_publish_dt = _parse_iso_datetime(latest_meta.get("publish_time")) or _parse_hk_release_time(
        latest_meta.get("release_time")
    )

    best_item: Optional[Dict[str, Any]] = None
    best_score = -1
    best_download_dt: Optional[datetime] = None

    for item in pdfs:
        if not isinstance(item, dict):
            continue

        score = 0
        source_url = _normalize_path(item.get("source_url"))
        item_file = _extract_filename(item.get("file_name"))
        item_title_text = _normalize_text(item.get("original_title") or item.get("file_name"))
        item_period_token = _extract_period_token(
            f"{item.get('original_title') or ''} {item.get('file_name') or ''}"
        )

        if latest_pdf_url and source_url:
            if source_url == latest_pdf_url:
                score += 200
            elif source_url.endswith(latest_pdf_url) or latest_pdf_url.endswith(source_url):
                score += 150

        if latest_web_path and source_url:
            if source_url == latest_web_path:
                score += 180
            elif source_url.endswith(latest_web_path) or latest_web_path.endswith(source_url):
                score += 130

        if latest_file_name and item_file and latest_file_name == item_file:
            score += 90

        if latest_title and item_title_text:
            if latest_title == item_title_text:
                score += 70
            elif latest_title in item_title_text or item_title_text in latest_title:
                score += 45

        if isinstance(latest_year, int) and latest_year == item.get("report_year"):
            score += 30

        if latest_period_token and item_period_token and latest_period_token == item_period_token:
            score += 25

        announcement_dt = _parse_iso_datetime(item.get("announcement_date"))
        if latest_publish_dt and announcement_dt and latest_publish_dt.date() == announcement_dt.date():
            score += 20

        download_dt = _parse_iso_datetime(item.get("download_time"))
        if score > best_score:
            best_score = score
            best_item = item
            best_download_dt = download_dt
        elif score == best_score and best_item is not None:
            # 分数相同则倾向下载时间更晚的记录
            if download_dt and (best_download_dt is None or download_dt > best_download_dt):
                best_item = item
                best_download_dt = download_dt

    if best_item is not None and best_score > 0:
        return best_item

    # 兜底：按年份前缀匹配
    if isinstance(latest_year, int):
        year_str = str(latest_year)
        for item in pdfs:
            if not isinstance(item, dict):
                continue
            ot = str(item.get("original_title") or "")
            fn = str(item.get("file_name") or "")
            if ot.startswith(year_str) or fn.startswith(year_str):
                return item
    return pdfs[0] if isinstance(pdfs[0], dict) else None


def _select_statement_period_id(
    statement: str,
    facts: List[Dict[str, Any]],
    primary_period_id: Optional[str],
    period_scope_map: Dict[str, str],
) -> Optional[str]:
    """为指定 statement 选择最合适的 period_id。"""
    period_counts: Dict[str, int] = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("statement") != statement:
            continue
        period_id = fact.get("period_id")
        if isinstance(period_id, str) and period_id:
            period_counts[period_id] = period_counts.get(period_id, 0) + 1

    if not period_counts:
        return None

    if isinstance(primary_period_id, str) and primary_period_id in period_counts:
        return primary_period_id

    if statement == "balance_sheet":
        # 资产负债表优先点时点口径
        point_in_time_candidates = [
            pid for pid in period_counts if period_scope_map.get(pid) == "point_in_time"
        ]
        if point_in_time_candidates:
            if isinstance(primary_period_id, str) and re.match(r"^\d{4}", primary_period_id):
                target_year = primary_period_id[:4]
                same_year = [pid for pid in point_in_time_candidates if pid.startswith(target_year)]
                if same_year:
                    return sorted(
                        same_year,
                        key=lambda pid: (period_counts.get(pid, 0), pid),
                        reverse=True,
                    )[0]
            return sorted(
                point_in_time_candidates,
                key=lambda pid: (period_counts.get(pid, 0), pid),
                reverse=True,
            )[0]

    return sorted(
        period_counts,
        key=lambda pid: (period_counts.get(pid, 0), pid),
        reverse=True,
    )[0]


def _build_statement_from_facts(
    statement: str,
    period_id: str,
    facts: List[Dict[str, Any]],
    base: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从 V2 facts 中提取指定 statement+period_id 的指标，回填旧字段结构。"""
    merged = dict(base or {})
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if fact.get("statement") != statement or fact.get("period_id") != period_id:
            continue
        metric = fact.get("metric")
        if not isinstance(metric, str) or not metric:
            continue
        value = fact.get("value")
        if value is None:
            continue
        merged[metric] = value
    return merged


def _hydrate_v2_compat_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 V2 的 facts/document 口径回填到兼容字段：
    - income_statement / balance_sheet / cash_flow_statement / financial_metrics
    - _v2_selection / _v2_statement_data
    """
    if not isinstance(payload, dict):
        return payload
    if payload.get("schema_version") != _V2_SCHEMA_VERSION:
        return payload

    facts = payload.get("facts")
    if not isinstance(facts, list) or not facts:
        return payload

    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    periods = payload.get("periods") if isinstance(payload.get("periods"), list) else []
    period_scope_map: Dict[str, str] = {}
    for period in periods:
        if not isinstance(period, dict):
            continue
        period_id = period.get("period_id")
        scope = period.get("scope")
        if isinstance(period_id, str) and isinstance(scope, str):
            period_scope_map[period_id] = scope

    primary_period_id = document.get("primary_period_id") if isinstance(document, dict) else None
    if not isinstance(primary_period_id, str) or not primary_period_id:
        for period in periods:
            if isinstance(period, dict) and period.get("is_primary") and isinstance(period.get("period_id"), str):
                primary_period_id = period.get("period_id")
                break

    selected_periods: Dict[str, Optional[str]] = {}
    statement_data: Dict[str, Dict[str, Any]] = {}
    for statement in _V2_STATEMENTS:
        selected_period = _select_statement_period_id(
            statement=statement,
            facts=facts,
            primary_period_id=primary_period_id,
            period_scope_map=period_scope_map,
        )
        selected_periods[statement] = selected_period
        if not selected_period:
            continue

        existing_statement = payload.get(statement) if isinstance(payload.get(statement), dict) else {}
        rebuilt = _build_statement_from_facts(
            statement=statement,
            period_id=selected_period,
            facts=facts,
            base=existing_statement,
        )
        if rebuilt:
            statement_data[statement] = rebuilt
            payload[statement] = rebuilt

    if statement_data:
        payload["_v2_statement_data"] = statement_data

    payload["_v2_selection"] = {
        "schema_version": _V2_SCHEMA_VERSION,
        "primary_period_id": primary_period_id,
        "income_statement_period_id": selected_periods.get("income_statement"),
        "balance_sheet_period_id": selected_periods.get("balance_sheet"),
        "cash_flow_statement_period_id": selected_periods.get("cash_flow_statement"),
        "financial_metrics_period_id": selected_periods.get("financial_metrics"),
    }

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    for key in ("stock_code", "stock_name", "report_type", "report_period", "fiscal_year", "period_type"):
        if key not in metadata and document.get(key) is not None:
            metadata[key] = document.get(key)
    if metadata:
        payload["metadata"] = metadata

    payload.setdefault("_selection", {})
    if isinstance(payload.get("_selection"), dict):
        payload["_selection"].setdefault("primary_period_id", primary_period_id)
        payload["_selection"].setdefault(
            "balance_period_id",
            selected_periods.get("balance_sheet"),
        )

    return payload


class ReportCollectorClient:
    """Report-Collector 服务的同步 REST 客户端"""

    def __init__(self, base_url: str = "http://localhost", port: int = 8001, timeout: int = 60):
        self.base_url = f"{base_url.rstrip('/')}:{port}"
        self.timeout = timeout
        self._session = requests.Session()

    def is_available(self) -> bool:
        """检查 report-collector 服务是否可用"""
        try:
            resp = self._session.get(f"{self.base_url}/health", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def list_pdfs(
        self,
        stock_code: str,
        market: str = "CN",
        report_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        列出已下载的PDF文件

        Args:
            stock_code: 股票代码
            market: 市场 (CN/HK)
            report_type: 报告类型 (annual/semi_annual/quarterly)
            limit: 返回数量

        Returns:
            PDF信息列表
        """
        params = {
            "stock_code": stock_code,
            "market": market,
            "limit": limit,
        }
        if report_type:
            params["report_type"] = report_type

        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/pdfs",
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                return result.get("data", {}).get("pdfs", [])
            return []
        except Exception as e:
            logger.warning(f"列出PDF失败: {e}")
            return []

    def batch_download(
        self,
        stock_code: str,
        market: str = "CN",
        report_type: str = "annual",
        max_count: int = 5,
    ) -> Dict[str, Any]:
        """
        批量下载财报PDF

        Args:
            stock_code: 股票代码
            market: 市场 (CN/HK)
            report_type: 报告类型
            max_count: 最大下载数量

        Returns:
            下载结果
        """
        market_path = market.lower()  # cn 或 hk
        payload = {
            "stock_code": stock_code,
            "report_type": report_type,
            "max_count": max_count,
        }

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/reports/{market_path}/batch-download",
                json=payload,
                timeout=self.timeout * 2,  # 下载需要更长超时
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                return result.get("data", {})
            logger.warning(f"批量下载失败: {result.get('error')}")
            return {}
        except Exception as e:
            logger.warning(f"批量下载请求失败: {e}")
            return {}

    def extract_content(
        self,
        pdf_id: Optional[int] = None,
        pdf_path: Optional[str] = None,
        schema_version: str = _V2_SCHEMA_VERSION,
        min_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        提取PDF中的结构化财务数据

        Args:
            pdf_id: PDF记录ID（优先使用）
            pdf_path: PDF文件路径
            schema_version: 返回结构版本（v1/v2，默认 v2）
            min_confidence: 仅 v2 生效，过滤低于阈值的 facts

        Returns:
            提取的财务数据（v2 下会自动回填兼容字段）
        """
        payload = {}
        if pdf_id is not None:
            payload["pdf_id"] = pdf_id
        elif pdf_path:
            payload["pdf_path"] = pdf_path
        else:
            return {}

        schema_version = schema_version if schema_version in _SUPPORTED_SCHEMA_VERSIONS else _V2_SCHEMA_VERSION
        if schema_version == _V2_SCHEMA_VERSION and min_confidence is not None:
            try:
                mc = float(min_confidence)
                if 0 <= mc <= 1:
                    payload["min_confidence"] = mc
            except Exception:
                pass

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/extract/content",
                params={"schema": schema_version},
                headers={"X-Schema-Version": schema_version},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                data = result.get("data", {}) or {}
                if not isinstance(data, dict):
                    return {}
                if schema_version == _V2_SCHEMA_VERSION:
                    data = _hydrate_v2_compat_payload(data)
                return data
            logger.warning(f"提取内容失败: {result.get('error')}")
            return {}
        except Exception as e:
            logger.warning(f"提取内容请求失败: {e}")
            return {}

    def search_latest_reports(
        self,
        market: str,
        stock_code: str,
        report_types: Optional[List[str]] = None,
        max_count: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        跨类型检索并按“披露发布时间”倒序返回报告列表。

        对应服务端接口：GET /api/v1/reports/search-latest
        返回字段通常包含：report_type、pdf_url、publish_time、publish_timestamp、title 等。
        """
        params: Dict[str, Any] = {
            "market": market,
            "stock_code": stock_code,
            "max_count": max_count,
        }
        if report_types:
            # 服务端同时兼容重复参数和逗号分隔，这里用逗号分隔更简洁
            params["report_types"] = ",".join([t for t in report_types if t])

        try:
            logger.info(
                f"[report-collector] search-latest 请求: market={market} stock_code={stock_code} "
                f"types={params.get('report_types') or 'ALL'} max_count={max_count}"
            )
            resp = self._session.get(
                f"{self.base_url}/api/v1/reports/search-latest",
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                data = result.get("data", {}) or {}
                reports = data.get("reports", []) or []
                if reports:
                    top = reports[0] if isinstance(reports[0], dict) else {}
                    logger.info(
                        f"[report-collector] search-latest 返回 {len(reports)} 条，最新: "
                        f"type={top.get('report_type')} publish_time={top.get('publish_time')} title={top.get('title')}"
                    )
                else:
                    logger.info("[report-collector] search-latest 返回 0 条")
                return reports
            return []
        except Exception as e:
            # 兼容旧版 report-collector（没有该接口）或网络问题
            logger.debug(f"search_latest_reports 失败（忽略，回退旧策略）: {e}")
            return []

    def fetch_latest_pdf_info(
        self,
        stock_code: str,
        market: str = "HK",
        report_types: Optional[Iterable[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """下载并返回最新披露财报 PDF 信息，不做财务内容抽取。"""
        types = tuple(report_types) if report_types is not None else _REPORT_TYPES_HK_DEFAULT

        latest_reports = self.search_latest_reports(
            market=market,
            stock_code=stock_code,
            report_types=list(types),
            max_count=1,
        )
        if latest_reports:
            latest_meta = latest_reports[0] if isinstance(latest_reports[0], dict) else {}
            latest_rt = latest_meta.get("report_type")
            if isinstance(latest_rt, str) and latest_rt:
                self.batch_download(
                    stock_code=stock_code,
                    market=market,
                    report_type=latest_rt,
                    max_count=2,
                )
                pdfs = self.list_pdfs(
                    stock_code=stock_code,
                    market=market,
                    report_type=latest_rt,
                    limit=20,
                )
                picked = _pick_pdf_for_latest_meta(pdfs, latest_meta)
                if picked:
                    result = dict(picked)
                    result["_selection"] = {
                        "selected_report_type": latest_rt,
                        "publish_time": latest_meta.get("publish_time"),
                        "publish_timestamp": latest_meta.get("publish_timestamp"),
                        "pdf_url": latest_meta.get("pdf_url"),
                        "web_path": latest_meta.get("web_path"),
                        "release_time": latest_meta.get("release_time"),
                        "title": latest_meta.get("title"),
                        "language": latest_meta.get("language"),
                    }
                    return result

        for rt in types:
            pdfs = self.list_pdfs(
                stock_code=stock_code,
                market=market,
                report_type=rt,
                limit=1,
            )
            if not pdfs:
                self.batch_download(
                    stock_code=stock_code,
                    market=market,
                    report_type=rt,
                    max_count=1,
                )
                pdfs = self.list_pdfs(
                    stock_code=stock_code,
                    market=market,
                    report_type=rt,
                    limit=1,
                )
            if pdfs and isinstance(pdfs[0], dict):
                result = dict(pdfs[0])
                result["_selection"] = {"selected_report_type": rt}
                return result
        return None

    def fetch_financial_data(
        self,
        stock_code: str,
        market: str = "CN",
        report_type: str = "annual",
        max_reports: int = 5,
        schema_version: str = _V2_SCHEMA_VERSION,
    ) -> List[Dict[str, Any]]:
        """
        高级方法：获取股票的结构化财务数据

        流程:
        1. 检查已有PDF
        2. 不足则补充下载
        3. 逐份提取结构化数据
        4. 按年份降序返回

        Args:
            stock_code: 股票代码
            market: 市场 (CN/HK)
            report_type: 报告类型 (annual/semi_annual/quarterly)
            max_reports: 最大年报数量

        Returns:
            提取的财务数据列表（按年份降序）
        """
        # 1. 检查已有PDF
        existing_pdfs = self.list_pdfs(stock_code, market, report_type=report_type, limit=max_reports)
        logger.info(f"[report-collector] {stock_code} 已有 {len(existing_pdfs)} 份{report_type} PDF")

        # 2. 不足则补充下载
        if len(existing_pdfs) < max_reports:
            logger.info(f"[report-collector] 为 {stock_code} 补充下载 {report_type}...")
            download_result = self.batch_download(
                stock_code=stock_code,
                market=market,
                report_type=report_type,
                max_count=max_reports,
            )
            if download_result:
                downloaded_count = download_result.get("downloaded_count", 0)
                logger.info(f"[report-collector] 新下载 {downloaded_count} 份{report_type}")
                # 重新获取列表
                existing_pdfs = self.list_pdfs(stock_code, market, report_type=report_type, limit=max_reports)

        if not existing_pdfs:
            logger.warning(f"[report-collector] {stock_code} 没有可用的{report_type} PDF")
            return []

        # 3. 逐份提取
        extracted_reports = []
        for pdf_info in existing_pdfs[:max_reports]:
            pdf_id = pdf_info.get("id")
            if pdf_id is None:
                continue

            logger.info(f"[report-collector] 提取 PDF#{pdf_id}: {pdf_info.get('file_name', 'unknown')}")
            content = self.extract_content(pdf_id=pdf_id, schema_version=schema_version)
            if content:
                # 附加元数据
                content["_pdf_info"] = {
                    "id": pdf_id,
                    "file_name": pdf_info.get("file_name"),
                    "report_year": pdf_info.get("report_year"),
                    "stock_code": pdf_info.get("stock_code"),
                }
                extracted_reports.append(content)

        # 4. 按年份降序排序
        extracted_reports.sort(
            key=lambda x: x.get("_pdf_info", {}).get("report_year") or 0,
            reverse=True,
        )

        logger.info(f"[report-collector] {stock_code} 共提取 {len(extracted_reports)} 份{report_type}数据")
        return extracted_reports

    def fetch_latest_financial_data(
        self,
        stock_code: str,
        market: str = "HK",
        report_types: Optional[Iterable[str]] = None,
        schema_version: str = _V2_SCHEMA_VERSION,
    ) -> Optional[Dict[str, Any]]:
        """
        跨 report_type 获取“最新一份”财务数据（用于季报/半年报/年报口径混用时的统一选择）。

        优先策略（更准确）：
        - 若 report-collector 提供 /api/v1/reports/search-latest：
          先跨类型按“披露发布时间”选最新的那条记录 → 取其 report_type → 再下载并提取该类型最新PDF

        回退策略（旧版兼容、可解释）：
        - 各类型各取 1 份 → 解析 metadata.report_period → 按“报告期截止日”选最新
        """
        types = tuple(report_types) if report_types is not None else _REPORT_TYPES_HK_DEFAULT

        # 1) 优先走“按披露发布时间”搜索（跨类型）
        latest_reports = self.search_latest_reports(
            market=market,
            stock_code=stock_code,
            report_types=list(types),
            max_count=1,
        )
        if latest_reports:
            latest_meta = latest_reports[0] if isinstance(latest_reports[0], dict) else {}
            latest_rt = latest_meta.get("report_type")
            if isinstance(latest_rt, str) and latest_rt:
                logger.info(
                    f"[report-collector] 按发布时间选中最新报告: stock_code={stock_code} "
                    f"report_type={latest_rt} publish_time={latest_meta.get('publish_time')}"
                )
                # 关键修复：不能仅“有一份 quarterly 就用哪份”。
                # 必须确保提取的是与 search-latest 对应的“最新披露”那一份。
                try:
                    # 触发下载（服务端会做去重；这里宁愿多请求一次，也避免命中旧缓存）
                    self.batch_download(
                        stock_code=stock_code,
                        market=market,
                        report_type=latest_rt,
                        max_count=2,
                    )

                    pdfs = self.list_pdfs(
                        stock_code=stock_code,
                        market=market,
                        report_type=latest_rt,
                        limit=20,
                    )
                    picked = _pick_pdf_for_latest_meta(pdfs, latest_meta)
                    if not picked:
                        raise RuntimeError("无法从PDF列表中选择匹配项")
                    pdf_id = picked.get("id")
                    if pdf_id is None:
                        raise RuntimeError("匹配到的PDF缺少id")

                    content = self.extract_content(pdf_id=pdf_id, schema_version=schema_version)
                    if not content:
                        raise RuntimeError("extract_content 返回空")

                    # 附加元数据，便于上游展示与调试
                    content["_pdf_info"] = {
                        "id": pdf_id,
                        "file_name": picked.get("file_name"),
                        "report_year": picked.get("report_year"),
                        "stock_code": picked.get("stock_code"),
                    }

                    # 解析报告期截止日（如果 extractor 能给出 report_period）
                    data = content.get("data") if isinstance(content.get("data"), dict) else content
                    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
                    report_period = meta.get("report_period") if isinstance(meta, dict) else None
                    end_dt = _parse_period_end_date(report_period or "") or date.min

                    if isinstance(data, dict):
                        data.setdefault("_selection", {})
                        if isinstance(data.get("_selection"), dict):
                            data["_selection"].update(
                                {
                                    "selected_report_type": latest_rt,
                                    "report_period_end": end_dt.isoformat() if end_dt != date.min else None,
                                    "publish_time": latest_meta.get("publish_time"),
                                    "publish_timestamp": latest_meta.get("publish_timestamp"),
                                    "pdf_url": latest_meta.get("pdf_url"),
                                    "web_path": latest_meta.get("web_path"),
                                    "release_time": latest_meta.get("release_time"),
                                    "title": latest_meta.get("title"),
                                    "language": latest_meta.get("language"),
                                    "picked_pdf_id": pdf_id,
                                    "picked_file_name": picked.get("file_name"),
                                    "picked_original_title": picked.get("original_title"),
                                    "picked_download_time": picked.get("download_time"),
                                }
                            )

                    logger.info(
                        f"[report-collector] 已匹配并提取最新披露PDF: stock_code={stock_code} "
                        f"report_type={latest_rt} pdf_id={pdf_id} file={picked.get('file_name')}"
                    )
                    return content
                except Exception as e:
                    logger.warning(f"[report-collector] 使用 search-latest 精确匹配失败，回退旧策略: {e}")

        # 2) 回退到旧策略：按报告期截止日选最新
        candidates: List[Tuple[date, str, Dict[str, Any]]] = []

        for rt in types:
            try:
                extracted_list = self.fetch_financial_data(
                    stock_code=stock_code,
                    market=market,
                    report_type=rt,
                    max_reports=1,
                    schema_version=schema_version,
                )
            except Exception as e:
                logger.debug(f"[report-collector] {stock_code} {rt} 获取失败（忽略）: {e}")
                continue

            if not extracted_list:
                continue

            extracted = extracted_list[0]
            data = extracted.get("data") if isinstance(extracted, dict) and isinstance(extracted.get("data"), dict) else extracted
            meta = data.get("metadata", {}) if isinstance(data, dict) else {}
            report_period = meta.get("report_period") if isinstance(meta, dict) else None
            end_dt = _parse_period_end_date(report_period or "") or date.min

            # 附加选择元信息，便于上游展示与调试
            if isinstance(data, dict):
                data.setdefault("_selection", {})
                if isinstance(data.get("_selection"), dict):
                    data["_selection"].update(
                        {
                            "selected_report_type": rt,
                            "report_period_end": end_dt.isoformat() if end_dt != date.min else None,
                        }
                    )

            candidates.append((end_dt, rt, extracted))

        if not candidates:
            return None

        # 按报告期截止日选最新；若并列，按 rt 字符串做稳定排序
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]
