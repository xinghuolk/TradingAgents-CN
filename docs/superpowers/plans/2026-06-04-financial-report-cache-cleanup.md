# 财报 Extractor 缓存 / PDF 清理按钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「系统配置 → 缓存管理」页新增两个按钮，分别清空财报 LLM extractor 抽取缓存目录、和已下载 PDF 目录，并回显删除文件数与释放空间。

**Architecture:** 新增一个后端 service（解析路径 → 统计 → 删除目录内容 → 返回统计），在现有 `app/routers/cache.py` 暴露两个 DELETE 端点，前端在 `cache.ts` 加两个 API 函数并在 `CacheManagement.vue` 加两个按钮。路径解析复用 `tradingagents/dataflows/financial_reports/config.py` 的 `get_financial_report_client_config()`，不重复硬编码。

**Tech Stack:** FastAPI（`app/`，专有）、pytest、Vue 3 + Element Plus（`frontend/`，专有）。

参考 spec：`docs/superpowers/specs/2026-06-04-financial-report-cache-cleanup-design.md`

---

## File Structure

- Create: `app/services/financial_report_cache_service.py` — 路径解析 + 目录清理 + 统计（唯一新增业务逻辑文件）
- Create: `tests/test_financial_report_cache_service.py` — service 单测
- Modify: `app/routers/cache.py` — 新增两个 DELETE 端点
- Modify: `frontend/src/api/cache.ts` — 新增两个清理函数 + 一个读取财报路径状态的函数
- Modify: `frontend/src/views/Settings/CacheManagement.vue` — 新增「财报数据缓存」按钮分组

---

### Task 1: 后端清理 service

**Files:**
- Create: `app/services/financial_report_cache_service.py`
- Test: `tests/test_financial_report_cache_service.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_financial_report_cache_service.py`：

```python
"""财报缓存清理 service 单测。"""
import os
from pathlib import Path

import pytest

from app.services.financial_report_cache_service import (
    _purge_directory_contents,
    purge_extractor_cache,
    purge_downloaded_pdfs,
)


def _make_tree(root: Path):
    """在 root 下造 2 个顶层文件 + 1 个含 2 个文件的子目录。"""
    (root / "a.json").write_text("aaaa", encoding="utf-8")        # 4 bytes
    (root / "b.pdf").write_bytes(b"123456")                       # 6 bytes
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("cc", encoding="utf-8")            # 2 bytes
    (sub / "d.txt").write_text("ddd", encoding="utf-8")           # 3 bytes
    # 总计 4 个文件，15 bytes


def test_purge_counts_and_deletes(tmp_path):
    _make_tree(tmp_path)
    result = _purge_directory_contents(str(tmp_path))
    assert result["deleted_files"] == 4
    assert result["freed_bytes"] == 15
    assert result["root"] == str(tmp_path)
    # 目录本身仍在，但内容被清空
    assert tmp_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_purge_empty_dir(tmp_path):
    result = _purge_directory_contents(str(tmp_path))
    assert result == {"deleted_files": 0, "freed_bytes": 0, "root": str(tmp_path)}


def test_purge_missing_dir(tmp_path):
    missing = tmp_path / "nope"
    result = _purge_directory_contents(str(missing))
    assert result == {"deleted_files": 0, "freed_bytes": 0, "root": str(missing)}


def test_purge_empty_root_string():
    result = _purge_directory_contents("")
    assert result == {"deleted_files": 0, "freed_bytes": 0, "root": ""}


def test_purge_rejects_filesystem_root():
    with pytest.raises(ValueError):
        _purge_directory_contents("/")


def test_purge_rejects_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    with pytest.raises(ValueError):
        _purge_directory_contents(str(fake_home))


def test_purge_extractor_cache_reads_config(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "x.json").write_text("x", encoding="utf-8")
    monkeypatch.delenv("DOCKER_CONTAINER", raising=False)
    monkeypatch.setenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", str(cache_dir))
    result = purge_extractor_cache()
    assert result["deleted_files"] == 1
    assert result["root"] == str(cache_dir)
    assert list(cache_dir.iterdir()) == []


def test_purge_downloaded_pdfs_reads_config(monkeypatch, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "y.pdf").write_bytes(b"abc")
    monkeypatch.delenv("DOCKER_CONTAINER", raising=False)
    monkeypatch.setenv("FINANCIAL_REPORT_PDF_ROOT", str(pdf_dir))
    result = purge_downloaded_pdfs()
    assert result["deleted_files"] == 1
    assert result["root"] == str(pdf_dir)
    assert list(pdf_dir.iterdir()) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_financial_report_cache_service.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.services.financial_report_cache_service'`

- [ ] **Step 3: 写最小实现**

创建 `app/services/financial_report_cache_service.py`：

```python
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
        # 先统计该顶层条目占用
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
        # 再删除
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_financial_report_cache_service.py -v`
Expected: PASS（8 个用例全过）

- [ ] **Step 5: 提交**

```bash
git add app/services/financial_report_cache_service.py tests/test_financial_report_cache_service.py
git commit -m "feat(cache): add financial-report extractor cache & PDF purge service"
```

---

### Task 2: 后端两个清理端点

**Files:**
- Modify: `app/routers/cache.py`（在 `/clear` 端点后、`/details` 端点前插入）

- [ ] **Step 1: 写实现**

在 `app/routers/cache.py` 的 `clear_all_cache`（约 122 行结束）之后、`get_cache_details` 之前插入：

```python
@router.delete("/financial-report/extractor")
async def clear_financial_report_extractor_cache(
    current_user: dict = Depends(get_current_user)
):
    """
    清空财报 LLM extractor 抽取缓存目录内容

    Returns:
        dict: {deleted_files, freed_bytes, root}
    """
    try:
        from app.services.financial_report_cache_service import purge_extractor_cache

        result = purge_extractor_cache()
        logger.warning(
            f"用户 {current_user['username']} 清理了财报抽取缓存: {result}"
        )
        return ok(
            data=result,
            message=f"已清理 {result['deleted_files']} 个文件"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"清理财报抽取缓存失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"清理财报抽取缓存失败: {str(e)}"
        )


@router.delete("/financial-report/pdfs")
async def clear_financial_report_pdfs(
    current_user: dict = Depends(get_current_user)
):
    """
    清空已下载的财报 PDF 目录内容

    Returns:
        dict: {deleted_files, freed_bytes, root}
    """
    try:
        from app.services.financial_report_cache_service import purge_downloaded_pdfs

        result = purge_downloaded_pdfs()
        logger.warning(
            f"用户 {current_user['username']} 清理了已下载 PDF: {result}"
        )
        return ok(
            data=result,
            message=f"已清理 {result['deleted_files']} 个文件"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"清理已下载 PDF 失败: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"清理已下载 PDF 失败: {str(e)}"
        )
```

- [ ] **Step 2: 导入冒烟检查**

Run: `python -c "import app.routers.cache"`
Expected: 无报错（确认语法、import 正常）。

- [ ] **Step 3: 路由注册检查**

Run: `python -c "import app.routers.cache as c; print([r.path for r in c.router.routes if 'financial-report' in r.path])"`
Expected: 打印 `['/api/cache/financial-report/extractor', '/api/cache/financial-report/pdfs']`

- [ ] **Step 4: 提交**

```bash
git add app/routers/cache.py
git commit -m "feat(cache): expose financial-report extractor/PDF purge endpoints"
```

---

### Task 3: 前端 API 函数

**Files:**
- Modify: `frontend/src/api/cache.ts`（文件末尾，`getCacheBackendInfo` 之后追加）

- [ ] **Step 1: 写实现**

在 `frontend/src/api/cache.ts` 末尾（约 104 行 `getCacheBackendInfo` 之后）追加：

```typescript
/**
 * 财报缓存清理结果
 */
export interface FinancialReportPurgeResult {
  deleted_files: number
  freed_bytes: number
  root: string
}

/**
 * 财报提取器路径状态（仅取展示用的两个目录）
 */
export interface FinancialReportPaths {
  pdf_root: string
  extractor_cache_root: string
}

/**
 * 读取财报提取器配置状态（用于在确认弹窗中展示真实目录路径）
 */
export function getFinancialReportPaths() {
  return request<FinancialReportPaths>({
    url: '/api/config/financial-report/status',
    method: 'get'
  })
}

/**
 * 清理财报 LLM extractor 抽取缓存
 */
export function clearFinancialReportExtractorCache() {
  return request<FinancialReportPurgeResult>({
    url: '/api/cache/financial-report/extractor',
    method: 'delete'
  })
}

/**
 * 清理已下载的财报 PDF
 */
export function clearFinancialReportPdfs() {
  return request<FinancialReportPurgeResult>({
    url: '/api/cache/financial-report/pdfs',
    method: 'delete'
  })
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && yarn type-check`
Expected: 无新增类型错误（`vue-tsc --noEmit` 通过）。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/api/cache.ts
git commit -m "feat(cache): add frontend API for financial-report cache purge"
```

---

### Task 4: 前端按钮分组

**Files:**
- Modify: `frontend/src/views/Settings/CacheManagement.vue`

- [ ] **Step 1: 模板 — 新增按钮分组**

在 `frontend/src/views/Settings/CacheManagement.vue` 中，「🗑️ 清空所有缓存」分组（结尾 `</div>` 在第 132 行）之后、`operations-content` 的闭合 `</div>`（第 133 行）之前，插入：

```html
            <el-divider />

            <!-- 财报数据缓存 -->
            <div class="operation-item">
              <h4>📄 财报数据缓存</h4>
              <p class="warning-text">⚠️ 清理操作不可恢复，请确认服务端配置的目录无误</p>

              <el-button
                type="warning"
                @click="clearExtractorCache"
                :loading="extractorLoading"
                style="margin-bottom: 12px"
              >
                <el-icon><Delete /></el-icon>
                清理财报抽取缓存
              </el-button>
              <br />
              <el-button
                type="warning"
                @click="clearPdfs"
                :loading="pdfsLoading"
              >
                <el-icon><Delete /></el-icon>
                清理已下载 PDF
              </el-button>
            </div>
```

- [ ] **Step 2: 脚本 — 引入新 API、loading 状态、路径**

在 `<script setup>` 中，把现有 import（约 212 行）

```typescript
import * as cacheApi from '@/api/cache'
```

保持不变（`cacheApi` 已聚合所有导出，可直接用 `cacheApi.clearFinancialReportExtractorCache` 等）。

在现有 loading 变量（约 215-218 行 `clearAllLoading` 附近）后新增：

```typescript
const extractorLoading = ref(false)
const pdfsLoading = ref(false)
const financialReportPaths = ref({ pdf_root: '', extractor_cache_root: '' })
```

- [ ] **Step 3: 脚本 — 加载路径 + 两个清理方法**

在 `clearAllCache` 方法（约 349 行结束）之后新增：

```typescript
const loadFinancialReportPaths = async () => {
  try {
    const response = await cacheApi.getFinancialReportPaths()
    const data: any = response.data || response
    financialReportPaths.value = {
      pdf_root: data.pdf_root || '',
      extractor_cache_root: data.extractor_cache_root || ''
    }
  } catch (error) {
    // 路径读取失败不阻塞页面，确认弹窗回退为通用文案
    console.warn('读取财报缓存路径失败:', error)
  }
}

const clearExtractorCache = async () => {
  try {
    const dir = financialReportPaths.value.extractor_cache_root || '（服务端配置的抽取缓存目录）'
    await ElMessageBox.confirm(
      `确定要清空财报抽取缓存目录吗？\n目录：${dir}\n此操作无法恢复！`,
      '确认清理',
      { type: 'warning', confirmButtonText: '确定清理', cancelButtonText: '取消' }
    )

    extractorLoading.value = true
    const response = await cacheApi.clearFinancialReportExtractorCache()
    const data: any = response.data || response
    ElMessage.success(`已清理 ${data.deleted_files} 个文件，释放 ${formatSize(data.freed_bytes)}`)
    await refreshStats()
  } catch (error: any) {
    if (error !== 'cancel') {
      console.error('清理财报抽取缓存失败:', error)
      ElMessage.error(error.message || '清理财报抽取缓存失败')
    }
  } finally {
    extractorLoading.value = false
  }
}

const clearPdfs = async () => {
  try {
    const dir = financialReportPaths.value.pdf_root || '（服务端配置的 PDF 目录）'
    await ElMessageBox.confirm(
      `确定要清空已下载 PDF 目录吗？\n目录：${dir}\n注意：若该目录与 report-collector 共享，将删除其源文件。此操作无法恢复！`,
      '确认清理',
      { type: 'warning', confirmButtonText: '确定清理', cancelButtonText: '取消' }
    )

    pdfsLoading.value = true
    const response = await cacheApi.clearFinancialReportPdfs()
    const data: any = response.data || response
    ElMessage.success(`已清理 ${data.deleted_files} 个文件，释放 ${formatSize(data.freed_bytes)}`)
    await refreshStats()
  } catch (error: any) {
    if (error !== 'cancel') {
      console.error('清理已下载 PDF 失败:', error)
      ElMessage.error(error.message || '清理已下载 PDF 失败')
    }
  } finally {
    pdfsLoading.value = false
  }
}
```

- [ ] **Step 4: 脚本 — onMounted 加载路径**

把现有 `onMounted`（约 390 行）：

```typescript
onMounted(() => {
  refreshStats()
  loadCacheDetails()
})
```

改为：

```typescript
onMounted(() => {
  refreshStats()
  loadCacheDetails()
  loadFinancialReportPaths()
})
```

- [ ] **Step 5: 类型检查 + 构建**

Run: `cd frontend && yarn type-check`
Expected: 通过，无新增错误。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/views/Settings/CacheManagement.vue
git commit -m "feat(cache): add financial-report cache cleanup buttons to settings page"
```

---

## 收尾验证（人工 / 集成）

- [ ] 启动后端 + 前端（或 Docker），进入「系统配置 → 缓存管理」。
- [ ] 确认出现「📄 财报数据缓存」分组与两个按钮。
- [ ] 点「清理财报抽取缓存」→ 确认弹窗显示真实目录路径 → 确认后提示「已清理 N 个文件，释放 X」。
- [ ] 点「清理已下载 PDF」→ 确认弹窗含共享目录警告 + 真实路径 → 确认后提示删除统计。
- [ ] 后端日志（`webapi`）记录了 root 与统计。
