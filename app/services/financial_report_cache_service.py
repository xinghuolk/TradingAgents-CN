"""财报 extractor 缓存与已下载 PDF 的清理服务。

注意：FINANCIAL_REPORT_PDF_ROOT 可能被配置为指向 report-collector 的 downloads 源目录
（见 .env.example）。清理 PDF 会删除该目录下全部内容，调用方/前端需向用户展示真实路径。
路径解析与 FINANCIAL_REPORT_CLIENT_ENABLED 无关，功能关闭时同样可清理。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from tradingagents.dataflows.financial_reports.config import (
    get_financial_report_client_config,
)

logger = logging.getLogger("webapi")


def _is_dangerous_path(path: Path) -> bool:
    """拒绝清理文件系统根、盘符根或用户家目录。"""
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):  # 根 "/" 或盘符根 "C:\\"
        return True
    if resolved == Path.home().resolve():
        return True
    return False


def _purge_directory_contents(root: str) -> dict:
    """删除 root 目录下的全部内容（保留 root 本身），返回统计。

    root 为空 / 不存在 / 非目录 → 返回 0，不报错。
    危险路径 → 抛 ValueError。
    单文件删除失败 → 记 warning，不计入已删除数，不中断。
    返回的 deleted_files 仅统计正常文件，空子目录不计入。
    """
    result = {"deleted_files": 0, "freed_bytes": 0, "root": root or ""}
    if not root:
        return result

    path = Path(root)
    if not path.exists() or not path.is_dir():
        return result
    if _is_dangerous_path(path):
        raise ValueError(f"拒绝清理危险路径: {path}")

    deleted_files = 0
    freed_bytes = 0
    for entry in list(path.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            files_here = 0
            bytes_here = 0
            for sub in entry.rglob("*"):
                if sub.is_file() and not sub.is_symlink():
                    try:
                        bytes_here += sub.stat().st_size
                    except OSError:
                        pass
                    files_here += 1
        else:
            files_here = 1
            try:
                bytes_here = entry.stat().st_size
            except OSError:
                bytes_here = 0
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            deleted_files += files_here
            freed_bytes += bytes_here
        except OSError as exc:
            logger.warning("删除 %s 失败: %s", entry, exc)

    result["deleted_files"] = deleted_files
    result["freed_bytes"] = freed_bytes
    return result


def purge_extractor_cache() -> dict:
    """清空财报 LLM extractor 抽取缓存目录内容。"""
    cfg = get_financial_report_client_config()
    logger.info("开始清理财报 extractor 缓存: %s", cfg.extractor_cache_root)
    result = _purge_directory_contents(cfg.extractor_cache_root)
    logger.info("财报 extractor 缓存清理完成: %s", result)
    return result


def purge_downloaded_pdfs() -> dict:
    """清空已下载的财报 PDF 目录内容。"""
    cfg = get_financial_report_client_config()
    logger.info("开始清理已下载 PDF: %s", cfg.pdf_root)
    result = _purge_directory_contents(cfg.pdf_root)
    logger.info("已下载 PDF 清理完成: %s", result)
    return result
