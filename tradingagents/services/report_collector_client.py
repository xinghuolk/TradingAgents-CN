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

from tradingagents.utils.logging_init import get_logger

logger = get_logger("report_collector_client")

_REPORT_TYPES_HK_DEFAULT: Tuple[str, ...] = ("quarterly", "semi_annual", "annual")


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

    当前 report-collector 的 pdf 记录里缺少 source_url/publish_time（常为 None），
    因此这里用“year + 文件命名”做最小可用的匹配，避免误选旧季报：
    - 优先匹配 original_title/file_name 以 year 开头（例如 2026_quarterly_en）
    - 否则回退到第一条（由服务端默认排序决定）
    """
    if not pdfs:
        return None
    year = latest_meta.get("year")
    if isinstance(year, int):
        year_str = str(year)
        for item in pdfs:
            if not isinstance(item, dict):
                continue
            ot = str(item.get("original_title") or "")
            fn = str(item.get("file_name") or "")
            if ot.startswith(year_str) or fn.startswith(year_str):
                return item
    return pdfs[0] if isinstance(pdfs[0], dict) else None


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

    def extract_content(self, pdf_id: Optional[int] = None, pdf_path: Optional[str] = None) -> Dict[str, Any]:
        """
        提取PDF中的结构化财务数据

        Args:
            pdf_id: PDF记录ID（优先使用）
            pdf_path: PDF文件路径

        Returns:
            提取的财务数据，包含 income_statement, balance_sheet, cash_flow_statement, financial_metrics
        """
        payload = {}
        if pdf_id is not None:
            payload["pdf_id"] = pdf_id
        elif pdf_path:
            payload["pdf_path"] = pdf_path
        else:
            return {}

        try:
            resp = self._session.post(
                f"{self.base_url}/api/v1/extract/content",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("success"):
                return result.get("data", {})
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

    def fetch_financial_data(
        self,
        stock_code: str,
        market: str = "CN",
        report_type: str = "annual",
        max_reports: int = 5,
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
            content = self.extract_content(pdf_id=pdf_id)
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

                    content = self.extract_content(pdf_id=pdf_id)
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
