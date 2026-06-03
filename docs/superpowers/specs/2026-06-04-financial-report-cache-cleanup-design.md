# 财报 Extractor 缓存 / PDF 清理按钮（PR 1）

- 日期：2026-06-04
- 范围：单个 PR
- 涉及边界：`app/`（专有）、`frontend/`（专有）、只读引用 `tradingagents/dataflows/financial_reports/config.py`（Apache）

## 背景

`financial-report-llm-extractor` 在两个 **文件型** 目录里产生数据：

- LLM 抽取缓存：`FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT`，默认解析为
  `{extractor_root}/tmp/.cache`（容器内 `/app/external/financial-report-llm-extractor/tmp/.cache`）。
- 下载的 PDF：`FINANCIAL_REPORT_PDF_ROOT`，默认 `/app/external/financial-report-pdfs`。

路径解析逻辑已存在于 `tradingagents/dataflows/financial_reports/config.py`（约 76-96 行），并由
`GET /config/financial-report/status`（`app/routers/config.py`）暴露 `pdf_root` / `extractor_cache_root`。

现有缓存管理页 `frontend/src/views/Settings/CacheManagement.vue` 通过 `app/routers/cache.py`
（`/api/cache/*`）管理股票/新闻/基本面缓存，但 **无法清理** 财报 extractor 缓存和已下载 PDF。

## 目标

在「系统配置 → 缓存管理」页新增两个 **独立按钮**：

1. 清空财报 LLM extractor 缓存目录内容。
2. 清空已下载 PDF 目录内容。

每个按钮带二次确认弹窗；操作完成后提示 **删除文件数** 与 **释放空间**，并刷新统计。

## 非目标

- 不改动现有股票/新闻/基本面缓存逻辑。
- 不增加按股票/按公司的细粒度删除（只做整目录清空）。
- 不触碰 extractor 的 `cache_only` / `force_refresh` 等运行配置。
- 不删除目录本身，只删除其内容。

## 设计

### 后端

**新增 service：`app/services/financial_report_cache_service.py`（专有）**

职责：解析目标目录 → 统计 → 删除内容 → 返回统计。

- 路径来源：复用 `tradingagents/dataflows/financial_reports/config.py` 中已有的解析（读取
  `extractor_cache_root` / `pdf_root`），不在 service 内重复硬编码默认值。
- 提供两个函数（或一个带参数的 purge 函数 + 两个薄封装）：
  - `purge_extractor_cache() -> {deleted_files, freed_bytes, root}`
  - `purge_downloaded_pdfs() -> {deleted_files, freed_bytes, root}`
- 行为：
  1. 解析 root；若为空字符串或 `None` → 返回 `{deleted_files: 0, freed_bytes: 0, root: ""}`，不报错。
  2. 若目录不存在 → 同上返回 0。
  3. 递归遍历，累加文件数和字节数，再删除目录 **内容**（保留 root 目录本身）。
  4. 单个文件删除失败时捕获异常、记录 warning 日志、计入未删除项，不中断整体。
- 安全护栏：解析出的路径若等于 `/`、空、或用户家目录根等危险路径，**拒绝执行** 并抛出明确错误，由路由转成 400/500。
- 日志：使用 `logging.getLogger("webapi")`（或 `app.<name>`）记录开始/结果。

**新增端点：`app/routers/cache.py`（专有）**

沿用现有 `/api/cache/*` 端点的鉴权与 `ApiResponse` 返回风格：

- `DELETE /api/cache/financial-report/extractor` → 调 `purge_extractor_cache()`
- `DELETE /api/cache/financial-report/pdfs` → 调 `purge_downloaded_pdfs()`

返回数据：`{ deleted_files, freed_bytes, root }`，成功消息中文。

### 前端

**`frontend/src/api/cache.ts`**：新增

- `clearFinancialReportExtractorCache()` → `DELETE /api/cache/financial-report/extractor`
- `clearFinancialReportPdfs()` → `DELETE /api/cache/financial-report/pdfs`

**`frontend/src/views/Settings/CacheManagement.vue`**：在右侧「🛠️ 缓存操作」卡片内、
现有「清空所有缓存」分组后追加一个 `el-divider` + 新分组「📄 财报数据缓存」，含两个 `operation-item`：

- 「🧹 清理财报抽取缓存」按钮（`type="warning"`，独立 loading 态）
- 「🗑️ 清理已下载 PDF」按钮（`type="warning"`，独立 loading 态）

每个按钮：`ElMessageBox.confirm` 二次确认 → 调 API → 成功后
`ElMessage.success('已清理 N 个文件，释放 X')`（X 复用现有 `formatSize`）→ `refreshStats()`。
失败走现有 `error.message` 提示模式。新增两个 `ref` loading 变量。

## 数据流

```
按钮点击 → confirm → cacheApi.clearXxx()
  → DELETE /api/cache/financial-report/{extractor|pdfs}
    → financial_report_cache_service.purge_*()
      → 解析 root (来自 financial_reports/config.py)
      → 统计 + 删除内容
    ← {deleted_files, freed_bytes, root}
  ← ApiResponse
← ElMessage.success(文件数 + formatSize(freed_bytes)) → refreshStats()
```

## 错误处理

- root 为空 / 不存在：返回 0，前端提示「无可清理内容」或正常成功（0 文件）。
- 危险路径：service 抛错 → 端点返回错误 → 前端 `ElMessage.error`。
- 部分文件删除失败：记 warning，统计只计已删除项；端点仍返回成功 + 已删除统计。

## 测试

- Service 单测（pytest，`tests/`）：用 tmp 目录构造文件，断言
  - 统计正确（文件数 + 字节）
  - 删除后目录清空但 root 仍在
  - 空目录 / 不存在目录返回 0
  - 危险路径（`/`、空）被拒绝
- 端点 happy-path 测试（mock service 或用 tmp root）。

## 风险

- root 解析依赖环境变量；测试需注入临时路径，避免误删真实目录。
- 容器/宿主路径映射差异：service 只操作解析出的 root，不做 Docker remap（清理在后端进程视角的真实路径上执行）。
