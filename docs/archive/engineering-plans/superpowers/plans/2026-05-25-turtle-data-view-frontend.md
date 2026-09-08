# Turtle Data View Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing "价值投资分析" report entry in both `ReportDetail.vue` and `SingleAnalysis.vue` to show four sub-tabs (报告 / 数据 / 计算 / 状态) powered by a shared `TurtlePayloadPanel.vue` component, backed by a canonical `value_turtle_payload` field surfaced through both backend API endpoints.

**Architecture:** The backend adds a small extraction helper and two save-time writes so `value_turtle_payload` is a durable top-level field in `analysis_reports` and `analysis_tasks.result`; both API endpoints read it (with cross-source and disk fallbacks) and filter it out of the normal `reports` dict. The frontend parses the raw JSON string entirely client-side via pure-function helpers in `frontend/src/utils/turtlePayload.ts`, then renders the four sub-tabs inside `TurtlePayloadPanel.vue` which is imported by both Vue views. Graceful degradation: no/blank/invalid payload → markdown-only display, no sub-tabs shown.

**Tech Stack:** FastAPI (Python 3.10+), Motor/PyMongo (MongoDB), pytest (`.venv/bin/python -m pytest`), Vue 3 + TypeScript + Element Plus, Vite (`yarn build`), `vue-tsc` (`yarn type-check`).

---

## 关键约定

1. **许可**: `app/` 与 `frontend/` 属于 Proprietary 许可范围。新建文件须遵循同目录已有文件的头部风格（`app/` 使用简洁 docstring，`frontend/` 无头部注释 — 因相邻文件无 proprietary header）。
2. **测试执行**: `app/` 和 `tests/` 使用 `.venv/bin/python -m pytest <path> -v` 运行。不使用系统 pytest。
3. **前端测试**: `frontend/` 不存在测试运行器（无 Vitest/Jest）。前端任务的验证仅通过 `cd frontend && yarn type-check` 和 `cd frontend && yarn build` 进行。不引入新的测试运行器。
4. **提交权限**: 每个任务末尾包含提交步骤。**使用 subagent-driven-development 技能执行时，请事先获取批量提交授权，或逐次确认提交**。遵照 CLAUDE.md 的指示，未经明确要求不得提交。不执行推送。
5. **Canonical contract (spec §4)**: `value_turtle_payload` 始终为 top-level 字段。前端两个入口均需从 `reports` dict 中过滤 `value_turtle_payload` 键。
6. **M3 除外**: `f5a4761` 的 docker/env/extraction config 变更不纳入本计划的任务范围。

---

## Task 1: Backend — `extract_turtle_payload` shared helper (TDD)

### 概要

在 `app/services/turtle_payload_helper.py` 中创建 `extract_turtle_payload` 函数。该函数按 spec §4.2 的 cross-source priority 顺序从 `result_data` dict 中提取非空的 `value_turtle_payload` 字符串，并处理磁盘 fallback。

**Files:**
- Create: `app/services/turtle_payload_helper.py`
- Create: `tests/unit/test_turtle_payload_helper.py`

- [ ] **Step 1.1: Write failing tests**

  Create `tests/unit/test_turtle_payload_helper.py`:

  ```python
  """Tests for extract_turtle_payload helper (Spec 4 §4.2)."""
  import json
  from pathlib import Path

  import pytest


  VALID_PAYLOAD = json.dumps({"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}})


  class TestExtractTurtlePayload:
      """Priority order: result.value_turtle_payload > state.value_turtle_payload
      > reports.value_turtle_payload > disk reports/value_turtle_payload.json."""

      def _call(self, result_data, reports_dir=None):
          from app.services.turtle_payload_helper import extract_turtle_payload
          return extract_turtle_payload(result_data, reports_dir=reports_dir)

      # --- Priority 1: top-level value_turtle_payload ---

      def test_returns_top_level_payload(self):
          """result_data.value_turtle_payload → returned directly."""
          result_data = {"value_turtle_payload": VALID_PAYLOAD, "state": {}, "reports": {}}
          assert self._call(result_data) == VALID_PAYLOAD

      def test_ignores_empty_top_level(self):
          """Empty top-level → falls through to next source."""
          result_data = {
              "value_turtle_payload": "",
              "state": {"value_turtle_payload": VALID_PAYLOAD},
              "reports": {},
          }
          assert self._call(result_data) == VALID_PAYLOAD

      def test_ignores_whitespace_top_level(self):
          """Whitespace-only top-level → falls through."""
          result_data = {
              "value_turtle_payload": "   \n",
              "state": {"value_turtle_payload": VALID_PAYLOAD},
              "reports": {},
          }
          assert self._call(result_data) == VALID_PAYLOAD

      # --- Priority 2: state.value_turtle_payload ---

      def test_returns_state_payload(self):
          """result_data has no top-level payload → uses state."""
          result_data = {"state": {"value_turtle_payload": VALID_PAYLOAD}, "reports": {}}
          assert self._call(result_data) == VALID_PAYLOAD

      # --- Priority 3: reports.value_turtle_payload ---

      def test_returns_reports_payload(self):
          """No top-level or state → uses reports dict."""
          result_data = {"state": {}, "reports": {"value_turtle_payload": VALID_PAYLOAD}}
          assert self._call(result_data) == VALID_PAYLOAD

      # --- Priority 4: disk fallback ---

      def test_reads_disk_fallback(self, tmp_path):
          """No MongoDB payload → reads value_turtle_payload.json from disk."""
          (tmp_path / "value_turtle_payload.json").write_text(VALID_PAYLOAD, encoding="utf-8")
          result_data = {"state": {}, "reports": {}}
          assert self._call(result_data, reports_dir=tmp_path) == VALID_PAYLOAD

      def test_disk_fallback_missing_file_returns_empty(self, tmp_path):
          """Disk dir exists but file absent → returns ''."""
          result_data = {"state": {}, "reports": {}}
          assert self._call(result_data, reports_dir=tmp_path) == ""

      def test_no_sources_returns_empty(self):
          """No payload anywhere → returns ''."""
          assert self._call({}) == ""

      def test_none_result_data_returns_empty(self):
          """None result_data → returns ''."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          assert extract_turtle_payload(None) == ""

      def test_whitespace_in_reports_is_ignored(self):
          """Whitespace-only in reports → falls through to disk (none here) → ''."""
          result_data = {"state": {}, "reports": {"value_turtle_payload": "  "}}
          assert self._call(result_data) == ""
  ```

- [ ] **Step 1.2: Run tests — verify FAIL**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_turtle_payload_helper.py -v
  ```

  Expected output: all tests fail with `ModuleNotFoundError` or `ImportError` for `app.services.turtle_payload_helper`.

- [ ] **Step 1.3: Implement the helper**

  Create `app/services/turtle_payload_helper.py`:

  ```python
  """
  Shared helper for extracting canonical value_turtle_payload from analysis result data.
  Priority order (spec §4.2):
    1. result_data["value_turtle_payload"]
    2. result_data["state"]["value_turtle_payload"]
    3. result_data["reports"]["value_turtle_payload"]
    4. reports_dir / "value_turtle_payload.json"  (disk fallback)
  Returns "" when no valid (non-blank) payload found; never raises.
  """

  from __future__ import annotations

  import logging
  from pathlib import Path
  from typing import Any, Optional

  logger = logging.getLogger("app.turtle_payload_helper")


  def extract_turtle_payload(
      result_data: Optional[dict[str, Any]],
      reports_dir: Optional[Path] = None,
  ) -> str:
      """Return the canonical value_turtle_payload string, or '' if absent."""
      if not result_data:
          return ""

      # Priority 1: top-level field
      candidate = result_data.get("value_turtle_payload", "")
      if isinstance(candidate, str) and candidate.strip():
          return candidate

      # Priority 2: state sub-dict
      state = result_data.get("state") or {}
      if isinstance(state, dict):
          candidate = state.get("value_turtle_payload", "")
          if isinstance(candidate, str) and candidate.strip():
              return candidate

      # Priority 3: reports sub-dict
      reports = result_data.get("reports") or {}
      if isinstance(reports, dict):
          candidate = reports.get("value_turtle_payload", "")
          if isinstance(candidate, str) and candidate.strip():
              return candidate

      # Priority 4: disk fallback
      if reports_dir is not None:
          disk_path = Path(reports_dir) / "value_turtle_payload.json"
          try:
              if disk_path.exists():
                  content = disk_path.read_text(encoding="utf-8").strip()
                  if content:
                      logger.info(f"📂 Loaded value_turtle_payload from disk: {disk_path}")
                      return content
          except Exception as exc:
              logger.warning(f"⚠️ Failed to read disk payload at {disk_path}: {exc}")

      return ""
  ```

- [ ] **Step 1.4: Run tests — verify PASS**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_turtle_payload_helper.py -v
  ```

  Expected: all 10 tests PASSED.

- [ ] **Step 1.5: Commit**

  ```bash
  git add app/services/turtle_payload_helper.py tests/unit/test_turtle_payload_helper.py
  git commit -m "feat(turtle): add extract_turtle_payload shared helper with cross-source priority + disk fallback (Spec 4 §4.2)"
  ```

---

## Task 2: Backend — Save-time canonical persistence (TDD)

### 概要

修改 `app/services/simple_analysis_service.py`，在保存新分析结果时，将 `value_turtle_payload` 同时写入 `analysis_reports` 文档的顶层字段和 `analysis_tasks.result` 中。

**Files:**
- Modify: `app/services/simple_analysis_service.py` (~lines 2587-2651)
- Create: `tests/unit/test_turtle_save_canonical_payload.py`

- [ ] **Step 2.1: Write failing tests**

  Create `tests/unit/test_turtle_save_canonical_payload.py`:

  ```python
  """Tests for save-time canonical value_turtle_payload persistence (Spec 4 §4.1)."""
  import json
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest


  VALID_PAYLOAD = json.dumps({
      "facts": {"status": "complete", "report": {"fields": {}}, "market": {"fields": {}}},
      "signals": {"status": "complete", "results": {}},
  })


  def _make_state(payload: str = VALID_PAYLOAD):
      return {
          "value_report": "# 价值分析\n\n内容。",
          "value_turtle_payload": payload,
          "market_report": "# 市场分析\n\n内容。",
      }


  class TestSaveTimeCanonicalPersistence:
      """
      The 'document' inserted into analysis_reports must have a top-level
      'value_turtle_payload' key, and the $set dict for analysis_tasks must also
      include it.
      """

      def _extract_insert_call_doc(self, mock_db):
          """Return the document dict passed to insert_one."""
          return mock_db.analysis_reports.insert_one.call_args[0][0]

      def _extract_task_set_dict(self, mock_db):
          """Return the dict passed as the $set value to update_one."""
          return mock_db.analysis_tasks.update_one.call_args[0][1]["$set"]["result"]

      @pytest.mark.asyncio
      async def test_analysis_reports_doc_has_top_level_payload(self):
          """analysis_reports insert_one doc must have value_turtle_payload at top level."""
          from app.services.simple_analysis_service import SimpleAnalysisService

          state = _make_state(VALID_PAYLOAD)
          result = {
              "state": state,
              "analysis_date": "2026-05-25",
              "stock_symbol": "600519",
              "summary": "摘要",
              "recommendation": "买入",
              "confidence_score": 0.9,
              "risk_level": "低",
              "key_points": [],
              "execution_time": 10,
              "tokens_used": 1000,
              "analysts": ["value"],
              "research_depth": 1,
              "model_info": "test-model",
              "decision": {},
              "performance_metrics": {},
          }

          mock_db = MagicMock()
          insert_result = MagicMock()
          insert_result.inserted_id = "fake_id"
          mock_db.analysis_reports.insert_one = AsyncMock(return_value=insert_result)
          mock_db.analysis_tasks.update_one = AsyncMock()

          svc = SimpleAnalysisService.__new__(SimpleAnalysisService)

          with patch("app.services.simple_analysis_service.get_mongo_db", return_value=mock_db):
              # Actual method signature: _save_analysis_result_web_style(self, task_id, result)
              await svc._save_analysis_result_web_style(
                  task_id="task-001",
                  result=result,
              )

          doc = self._extract_insert_call_doc(mock_db)
          assert "value_turtle_payload" in doc, "top-level value_turtle_payload must be in analysis_reports doc"
          assert doc["value_turtle_payload"] == VALID_PAYLOAD

      @pytest.mark.asyncio
      async def test_analysis_tasks_result_has_payload(self):
          """analysis_tasks $set result must include value_turtle_payload."""
          from app.services.simple_analysis_service import SimpleAnalysisService

          state = _make_state(VALID_PAYLOAD)
          result = {
              "state": state,
              "analysis_date": "2026-05-25",
              "stock_symbol": "600519",
              "summary": "摘要",
              "recommendation": "买入",
              "confidence_score": 0.9,
              "risk_level": "低",
              "key_points": [],
              "execution_time": 10,
              "tokens_used": 1000,
              "analysts": ["value"],
              "research_depth": 1,
              "model_info": "test-model",
              "decision": {},
              "performance_metrics": {},
          }

          mock_db = MagicMock()
          insert_result = MagicMock()
          insert_result.inserted_id = "fake_id"
          mock_db.analysis_reports.insert_one = AsyncMock(return_value=insert_result)
          mock_db.analysis_tasks.update_one = AsyncMock()

          svc = SimpleAnalysisService.__new__(SimpleAnalysisService)

          with patch("app.services.simple_analysis_service.get_mongo_db", return_value=mock_db):
              await svc._save_analysis_result_web_style(
                  task_id="task-001",
                  result=result,
              )

          task_set = self._extract_task_set_dict(mock_db)
          assert "value_turtle_payload" in task_set, "value_turtle_payload must be in analysis_tasks.result $set"
          assert task_set["value_turtle_payload"] == VALID_PAYLOAD

      @pytest.mark.asyncio
      async def test_empty_payload_writes_empty_string(self):
          """Empty payload state → value_turtle_payload='' in both doc and task_set."""
          from app.services.simple_analysis_service import SimpleAnalysisService

          state = _make_state("")
          result = {
              "state": state,
              "analysis_date": "2026-05-25",
              "stock_symbol": "600519",
              "summary": "摘要",
              "recommendation": "买入",
              "confidence_score": 0.9,
              "risk_level": "低",
              "key_points": [],
              "execution_time": 10,
              "tokens_used": 1000,
              "analysts": ["value"],
              "research_depth": 1,
              "model_info": "test-model",
              "decision": {},
              "performance_metrics": {},
          }

          mock_db = MagicMock()
          insert_result = MagicMock()
          insert_result.inserted_id = "fake_id"
          mock_db.analysis_reports.insert_one = AsyncMock(return_value=insert_result)
          mock_db.analysis_tasks.update_one = AsyncMock()

          svc = SimpleAnalysisService.__new__(SimpleAnalysisService)

          with patch("app.services.simple_analysis_service.get_mongo_db", return_value=mock_db):
              await svc._save_analysis_result_web_style(
                  task_id="task-002",
                  result=result,
              )

          doc = self._extract_insert_call_doc(mock_db)
          assert doc.get("value_turtle_payload", "NOT_PRESENT") == ""

          task_set = self._extract_task_set_dict(mock_db)
          assert task_set.get("value_turtle_payload", "NOT_PRESENT") == ""
  ```

  > Note: The actual method is `_save_analysis_result_web_style(self, task_id, result)`. The test above uses that exact signature.

- [ ] **Step 2.2: Run tests — verify FAIL**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_turtle_save_canonical_payload.py -v
  ```

  Expected: tests fail (key missing in doc or method not callable).

- [ ] **Step 2.3: Implement — add payload extraction to the save method**

  In `app/services/simple_analysis_service.py`, the method is `_save_analysis_result_web_style` (line 2386). Find the `document = { ... }` dict near line 2587. Add the import at the top of the file and add the field to both the document and the tasks `$set`:

  **At the top of the file (after existing imports, around line 10):**
  ```python
  from app.services.turtle_payload_helper import extract_turtle_payload
  ```

  **Inside the method, just before `document = { ... }` near line 2586, add:**
  ```python
  # Extract canonical turtle payload (Spec 4 §4.1)
  _state_for_payload = result.get('state', {})
  _canonical_turtle_payload = extract_turtle_payload({
      "value_turtle_payload": _state_for_payload.get("value_turtle_payload", "") if isinstance(_state_for_payload, dict) else "",
      "state": {},
      "reports": {},
  })
  ```

  **In `document = { ... }`, add a new key after `"performance_metrics"` line (~2623):**
  ```python
                  # Spec 4: canonical turtle payload (raw JSON string, never parsed server-side)
                  "value_turtle_payload": _canonical_turtle_payload,
  ```

  **In the `analysis_tasks.update_one` `{"$set": {"result": { ... }}}` dict near line 2635, add inside the result dict (after `"decision"` line ~2650):**
  ```python
                      # Spec 4: canonical turtle payload
                      "value_turtle_payload": _canonical_turtle_payload,
  ```

- [ ] **Step 2.4: Run tests — verify PASS**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_turtle_save_canonical_payload.py -v
  ```

  Expected: all 3 tests PASSED.

- [ ] **Step 2.5: Run existing turtle tests to confirm no regressions**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_simple_analysis_service_turtle_payload.py -v
  ```

  Expected: all existing tests still PASSED.

- [ ] **Step 2.6: Commit**

  ```bash
  git add app/services/simple_analysis_service.py tests/unit/test_turtle_save_canonical_payload.py
  git commit -m "feat(turtle): write canonical value_turtle_payload to analysis_reports + analysis_tasks at save time (Spec 4 §4.1)"
  ```

---

## Task 3: Backend — `/api/analysis/tasks/{id}/result` returns canonical field (TDD)

### 概要

修改 `app/routers/analysis.py` 中的 `get_task_result` 端点：
1. 向 `final_result_data` 中添加 `value_turtle_payload`（使用 `extract_turtle_payload` helper）。
2. 从 `validated_reports` 中移除 `value_turtle_payload` 键。

**Files:**
- Modify: `app/routers/analysis.py` (~lines 646-684)
- Create: `tests/unit/test_analysis_router_turtle_payload.py`

- [ ] **Step 3.1: Write failing tests**

  Create `tests/unit/test_analysis_router_turtle_payload.py`:

  ```python
  """Tests for /api/analysis/tasks/{id}/result canonical value_turtle_payload (Spec 4 §4.2)."""
  import json
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest
  from fastapi.testclient import TestClient


  VALID_PAYLOAD = json.dumps({"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}})


  def _make_result_data(payload_loc: str = "top"):
      """Build a result_data dict with payload in the specified location."""
      base = {
          "analysis_id": "ana-001",
          "stock_symbol": "600519",
          "stock_code": "600519",
          "analysis_date": "2026-05-25",
          "summary": "摘要",
          "recommendation": "买入",
          "confidence_score": 0.9,
          "risk_level": "低",
          "key_points": [],
          "execution_time": 10,
          "tokens_used": 1000,
          "analysts": ["value"],
          "research_depth": "快速",
          "detailed_analysis": {},
          "state": {},
          "decision": {},
          "reports": {"value_report": "# 价值分析\n\n内容。"},
      }
      if payload_loc == "top":
          base["value_turtle_payload"] = VALID_PAYLOAD
      elif payload_loc == "state":
          base["state"] = {"value_turtle_payload": VALID_PAYLOAD}
      elif payload_loc == "reports":
          base["reports"]["value_turtle_payload"] = VALID_PAYLOAD
      # "none" → no payload
      return base


  class TestGetTaskResultTurtlePayload:
      """
      The endpoint builds final_result_data.
      We test the router logic by calling the helper functions it uses,
      since the endpoint requires DB and auth mocks.
      We test the extraction logic in isolation.
      """

      def test_extract_from_top_level(self):
          """extract_turtle_payload returns top-level payload."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          rd = _make_result_data("top")
          assert extract_turtle_payload(rd) == VALID_PAYLOAD

      def test_extract_from_state(self):
          """extract_turtle_payload returns state payload when top-level absent."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          rd = _make_result_data("state")
          assert extract_turtle_payload(rd) == VALID_PAYLOAD

      def test_extract_from_reports(self):
          """extract_turtle_payload returns reports payload when top and state absent."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          rd = _make_result_data("reports")
          assert extract_turtle_payload(rd) == VALID_PAYLOAD

      def test_no_payload_returns_empty(self):
          """extract_turtle_payload returns '' when no payload present."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          rd = _make_result_data("none")
          assert extract_turtle_payload(rd) == ""

      def test_validated_reports_excludes_value_turtle_payload(self):
          """After building validated_reports, value_turtle_payload must be excluded."""
          # Simulate the router's reports-validation loop
          reports_data = {
              "value_report": "# 价值分析\n\n内容。",
              "market_report": "# 市场\n\n内容。",
              "value_turtle_payload": VALID_PAYLOAD,  # must be filtered out
          }
          validated_reports = {
              k: v
              for k, v in reports_data.items()
              if k != "value_turtle_payload"
          }
          assert "value_turtle_payload" not in validated_reports
          assert "value_report" in validated_reports
          assert "market_report" in validated_reports
  ```

- [ ] **Step 3.2: Run tests — verify FAIL (last test passes, others check helper)**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_analysis_router_turtle_payload.py -v
  ```

  Expected: The 4 extract tests pass (helper already implemented in Task 1). The filter test may pass trivially. These tests verify the contract; the integration test below validates the actual endpoint code change.

- [ ] **Step 3.3: Implement — add `value_turtle_payload` to `final_result_data` and filter from `validated_reports`**

  In `app/routers/analysis.py`:

  **Near the top of the file, add import (after other imports):**
  ```python
  from app.services.turtle_payload_helper import extract_turtle_payload
  ```

  **In `get_task_result`, find the `final_result_data = { ... }` dict near line 646. Add after the `"decision"` line (~663):**
  ```python
              # Spec 4: canonical turtle payload (cross-source extraction)
              "value_turtle_payload": extract_turtle_payload(result_data),
  ```

  **In the validated_reports loop near line 670, change the loop body to skip `value_turtle_payload`:**

  Find this block:
  ```python
          for report_key, report_content in reports_data.items():
              # 确保报告键是字符串
              safe_key = safe_string(report_key, "unknown_report")

              # 确保报告内容是非空字符串
              if report_content is None:
                  validated_content = "报告内容暂无"
              elif isinstance(report_content, str):
                  validated_content = report_content.strip() if report_content.strip() else "报告内容为空"
              else:
                  validated_content = str(report_content).strip() if str(report_content).strip() else "报告内容格式错误"

              validated_reports[safe_key] = validated_content
  ```

  Replace with:
  ```python
          for report_key, report_content in reports_data.items():
              # 确保报告键是字符串
              safe_key = safe_string(report_key, "unknown_report")

              # Spec 4: filter value_turtle_payload from normal reports tab list
              if safe_key == "value_turtle_payload":
                  continue

              # 确保报告内容是非空字符串
              if report_content is None:
                  validated_content = "报告内容暂无"
              elif isinstance(report_content, str):
                  validated_content = report_content.strip() if report_content.strip() else "报告内容为空"
              else:
                  validated_content = str(report_content).strip() if str(report_content).strip() else "报告内容格式错误"

              validated_reports[safe_key] = validated_content
  ```

- [ ] **Step 3.4: Run tests — verify PASS**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_analysis_router_turtle_payload.py -v
  ```

  Expected: all 5 tests PASSED.

- [ ] **Step 3.5: Run broader test suite to confirm no regressions**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/ -v --tb=short -q 2>&1 | tail -20
  ```

  Expected: no new failures.

- [ ] **Step 3.6: Commit**

  ```bash
  git add app/routers/analysis.py tests/unit/test_analysis_router_turtle_payload.py
  git commit -m "feat(turtle): /api/analysis/tasks/{id}/result returns canonical value_turtle_payload + filters from reports (Spec 4 §4.2)"
  ```

---

## Task 4: Backend — `/api/reports/{id}/detail` returns canonical field (TDD)

### 概要

修改 `app/routers/reports.py` 中的 `get_report_detail` 端点：
1. 在 `analysis_reports` 命中路径和 `analysis_tasks` fallback 路径中，均将 `value_turtle_payload` 作为 canonical 顶层字段添加到 `report` dict 中。
2. 在两条路径中均从 `report.reports` 移除 `value_turtle_payload` 键。
3. 当 MongoDB 中不存在该字段且已知 `stock_symbol` + `analysis_date` 时，从磁盘 fallback 补充。

**Files:**
- Modify: `app/routers/reports.py` (~lines 238-347)
- Create: `tests/unit/test_reports_router_turtle_payload.py`

- [ ] **Step 4.1: Write failing tests**

  Create `tests/unit/test_reports_router_turtle_payload.py`:

  ```python
  """Tests for /api/reports/{id}/detail canonical value_turtle_payload (Spec 4 §4.2)."""
  import json
  from pathlib import Path

  import pytest


  VALID_PAYLOAD = json.dumps({"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}})


  class TestReportsDetailTurtlePayload:
      """
      These tests verify the extraction and filtering logic as pure functions,
      matching the patterns used in get_report_detail.
      """

      def test_extract_from_analysis_reports_top_level(self):
          """analysis_reports doc has value_turtle_payload at top level → returned."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          doc_as_result = {
              "value_turtle_payload": VALID_PAYLOAD,
              "reports": {"value_report": "# 价值分析"},
              "state": {},
          }
          result = extract_turtle_payload(doc_as_result)
          assert result == VALID_PAYLOAD

      def test_extract_from_analysis_tasks_fallback_state(self):
          """analysis_tasks.result has payload in state → returned."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          task_result = {
              "state": {"value_turtle_payload": VALID_PAYLOAD},
              "reports": {},
          }
          result = extract_turtle_payload(task_result)
          assert result == VALID_PAYLOAD

      def test_extract_from_analysis_tasks_fallback_reports(self):
          """analysis_tasks.result has payload in reports → returned."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          task_result = {
              "state": {},
              "reports": {"value_turtle_payload": VALID_PAYLOAD},
          }
          result = extract_turtle_payload(task_result)
          assert result == VALID_PAYLOAD

      def test_disk_fallback_for_historical_record(self, tmp_path):
          """No MongoDB payload → disk file → returned."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          (tmp_path / "value_turtle_payload.json").write_text(VALID_PAYLOAD, encoding="utf-8")
          result = extract_turtle_payload({"state": {}, "reports": {}}, reports_dir=tmp_path)
          assert result == VALID_PAYLOAD

      def test_reports_dict_filters_value_turtle_payload(self):
          """reports dict must not expose value_turtle_payload as a report tab."""
          reports = {
              "value_report": "# 价值分析\n\n内容。",
              "market_report": "# 市场",
              "value_turtle_payload": VALID_PAYLOAD,
          }
          filtered = {k: v for k, v in reports.items() if k != "value_turtle_payload"}
          assert "value_turtle_payload" not in filtered
          assert "value_report" in filtered

      def test_empty_payload_returns_empty_string(self):
          """No payload in any source → empty string."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          result = extract_turtle_payload({"state": {}, "reports": {}})
          assert result == ""

      def test_whitespace_payload_treated_as_empty(self):
          """Whitespace-only payload → falls through all sources → ''."""
          from app.services.turtle_payload_helper import extract_turtle_payload
          result = extract_turtle_payload({
              "value_turtle_payload": "   ",
              "state": {},
              "reports": {},
          })
          assert result == ""
  ```

- [ ] **Step 4.2: Run tests — verify behavior of helper (all should PASS already)**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_reports_router_turtle_payload.py -v
  ```

  Expected: all 7 tests PASSED (they test the helper which already exists; the implementation below wires it into the endpoint).

- [ ] **Step 4.3: Implement — wire `extract_turtle_payload` into `get_report_detail`**

  In `app/routers/reports.py`:

  **At the top of the file, add import (after existing imports):**
  ```python
  from app.services.turtle_payload_helper import extract_turtle_payload
  ```

  **In `get_report_detail`, find the `analysis_tasks` fallback branch that builds `report = { ... }` near line 281. The fallback `report` dict ends around line 302. After building `report`, add (before the `return`):**

  In the `analysis_tasks` fallback branch (after `report = { ... }` near line 303):
  ```python
              # Spec 4: extract canonical turtle payload (cross-source + disk fallback)
              _task_result = tasks_doc.get("result") or {}
              _reports_dir = _resolve_reports_dir(
                  _task_result.get("stock_symbol") or tasks_doc.get("stock_code"),
                  _task_result.get("analysis_date"),
              )
              report["value_turtle_payload"] = extract_turtle_payload(_task_result, reports_dir=_reports_dir)
              # Filter value_turtle_payload from the normal reports tab list
              if isinstance(report.get("reports"), dict):
                  report["reports"] = {k: v for k, v in report["reports"].items() if k != "value_turtle_payload"}
  ```

  In the `analysis_reports` branch (after `report = { ... }` near line 341, before `return`):
  ```python
              # Spec 4: extract canonical turtle payload (cross-source + disk fallback)
              _reports_dir = _resolve_reports_dir(
                  doc.get("stock_symbol"),
                  doc.get("analysis_date"),
              )
              report["value_turtle_payload"] = extract_turtle_payload(doc, reports_dir=_reports_dir)
              # Filter value_turtle_payload from the normal reports tab list
              if isinstance(report.get("reports"), dict):
                  report["reports"] = {k: v for k, v in report["reports"].items() if k != "value_turtle_payload"}
  ```

  **Also add the helper function `_resolve_reports_dir` near the top of the file (before the router endpoint definitions, after imports):**
  ```python
  import os
  from pathlib import Path as _Path


  def _resolve_reports_dir(stock_symbol: str | None, analysis_date: str | None) -> _Path | None:
      """Resolve the disk reports directory for a given stock/date (spec §4.2 disk fallback)."""
      if not stock_symbol or not analysis_date:
          return None
      date_str = str(analysis_date)[:10]
      base_env = os.getenv("TRADINGAGENTS_RESULTS_DIR")
      project_root = _Path.cwd()
      base_path = _Path(base_env) if base_env and _Path(base_env).is_absolute() else (project_root / (base_env or "results"))
      candidates = [
          base_path / stock_symbol / date_str / "reports",
          project_root / "data" / "analysis_results" / stock_symbol / date_str / "reports",
          project_root / "data" / "analysis_results" / "detailed" / stock_symbol / date_str / "reports",
      ]
      for d in candidates:
          if d.exists() and d.is_dir():
              return d
      return None
  ```

  > Note: If `os` and `Path` are already imported at the top of `reports.py`, do not duplicate those imports; just add `_resolve_reports_dir` as a module-level function.

- [ ] **Step 4.4: Run tests — verify PASS**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/test_reports_router_turtle_payload.py -v
  ```

  Expected: all 7 tests PASSED.

- [ ] **Step 4.5: Run full unit test suite**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/ -q --tb=short 2>&1 | tail -20
  ```

  Expected: no new failures.

- [ ] **Step 4.6: Commit**

  ```bash
  git add app/routers/reports.py tests/unit/test_reports_router_turtle_payload.py
  git commit -m "feat(turtle): /api/reports/{id}/detail returns canonical value_turtle_payload + disk fallback + filters from reports (Spec 4 §4.2)"
  ```

---

## Task 5: Frontend — Pure helpers `turtlePayload.ts` (implement + type-check)

### 概要

在 `frontend/src/utils/turtlePayload.ts` 中创建纯函数 helper 模块，定义 `ParsedTurtlePayload` TypeScript 接口以及所有解析函数和格式化函数。

**Files:**
- Create: `frontend/src/utils/turtlePayload.ts`

- [ ] **Step 5.1: Create the helpers file**

  Create `frontend/src/utils/turtlePayload.ts`:

  ```typescript
  /**
   * Pure helper functions for TurtleFacts/TurtleSignals payload parsing and formatting.
   * All functions are side-effect-free and suitable for unit testing.
   * (Spec 4 §5.1, M1 provenance, M2 source_reference parsing)
   */

  // ---------------------------------------------------------------------------
  // Types / Interfaces
  // ---------------------------------------------------------------------------

  export interface MoneyAmount {
    value: number
    currency?: string
    unit?: string
  }

  export interface FactField {
    name?: string
    value: unknown
    reliability?: string
    source_label?: string
    source_reference?: string
    caveat?: string
    unit?: string
  }

  export interface ReportFacts {
    fields?: Record<string, FactField>
    metadata?: {
      fx_rates?: Record<string, number>
      fx_rates_meta?: Record<string, FxRateMeta>
      period_end?: string
      [key: string]: unknown
    }
    status?: string
    caveats?: string[]
    historical?: Record<string, ReportFacts>
  }

  export interface MarketFacts {
    fields?: Record<string, FactField>
    metadata?: {
      market_as_of?: string
      provider?: string
      [key: string]: unknown
    }
    status?: string
    caveats?: string[]
  }

  export interface Facts {
    report?: ReportFacts
    market?: MarketFacts
    status?: string
    caveats?: string[]
  }

  export interface SignalResult {
    name?: string
    status?: string
    formula?: string
    substitution?: string
    value?: unknown
    unit?: string
    sources?: string[]
    missing_inputs?: string[]
  }

  export interface Signals {
    status?: string
    results?: Record<string, SignalResult>
    veto_reasons?: string[]
    caveats?: string[]
  }

  export interface ParsedTurtlePayload {
    facts: Facts
    signals: Signals
  }

  export interface FxRateMeta {
    provider?: string
    as_of?: string
    fetched_at?: string
    rate?: number
    derived_from?: string[]
  }

  export interface FxRateRow {
    pair: string
    rate: number
    provider: string
    asOf?: string
    fetchedAt?: string
    derivedFrom?: string[]
    isDerived: boolean
  }

  export interface MarketProvenance {
    marketAsOf?: string
    provider?: string
  }

  export interface ParsedSourceReference {
    pages: number[]
    provider?: string
    fetchedAt?: string
    fx?: string
    rest: string
  }

  // ---------------------------------------------------------------------------
  // parseTurtlePayload
  // ---------------------------------------------------------------------------

  /**
   * Parse the raw value_turtle_payload JSON string.
   * Returns null if the string is blank, missing, or not valid JSON.
   * Logs a console.warn on JSON parse failure (spec §7).
   */
  export function parseTurtlePayload(raw: string | null | undefined): ParsedTurtlePayload | null {
    if (!raw || !raw.trim()) {
      return null
    }
    try {
      const parsed = JSON.parse(raw)
      if (typeof parsed !== 'object' || parsed === null) {
        console.warn('[TurtlePayload] Parsed payload is not an object:', typeof parsed)
        return null
      }
      return parsed as ParsedTurtlePayload
    } catch (e) {
      console.warn('[TurtlePayload] Failed to parse value_turtle_payload JSON:', e)
      return null
    }
  }

  // ---------------------------------------------------------------------------
  // formatFactValue
  // ---------------------------------------------------------------------------

  /**
   * Format a fact field value for display.
   * - MoneyAmount objects: "{value} {currency} {unit}"
   * - null/undefined: "—"
   * - numbers: toLocaleString with up to 4 decimal places
   * - everything else: String()
   */
  export function formatFactValue(value: unknown): string {
    if (value === null || value === undefined) {
      return '—'
    }
    if (typeof value === 'object' && !Array.isArray(value)) {
      const obj = value as Record<string, unknown>
      if ('value' in obj) {
        const num = obj.value
        const numStr = typeof num === 'number'
          ? num.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
          : String(num)
        const currency = obj.currency ? ` ${obj.currency}` : ''
        const unit = obj.unit ? ` ${obj.unit}` : ''
        return `${numStr}${currency}${unit}`.trim()
      }
      return JSON.stringify(obj)
    }
    if (typeof value === 'number') {
      return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
    }
    if (typeof value === 'boolean') {
      return value ? '是' : '否'
    }
    return String(value)
  }

  // ---------------------------------------------------------------------------
  // extractPageRefs
  // ---------------------------------------------------------------------------

  /**
   * Extract page numbers from a source_reference string.
   * Matches "p.<number>" patterns (e.g. "net_profit p.7" → [7]).
   * Multiple matches: "p.7 p.12" → [7, 12].
   */
  export function extractPageRefs(sourceReference: string | null | undefined): number[] {
    if (!sourceReference) return []
    const matches = sourceReference.match(/p\.(\d+)/g)
    if (!matches) return []
    return matches.map(m => parseInt(m.replace('p.', ''), 10)).filter(n => !isNaN(n))
  }

  // ---------------------------------------------------------------------------
  // parseSourceReference (M2)
  // ---------------------------------------------------------------------------

  /**
   * Parse a composite source_reference string (Spec 3 / M2).
   *
   * Examples:
   *   "market_data.market_cap; provider=yfinance_hk; fetched_at=2026-05-23T10:00:00; FX HKD:CNY=0.92"
   *     → { pages: [], provider: "yfinance_hk", fetchedAt: "2026-05-23T10:00:00", fx: "HKD:CNY=0.92", rest: "market_data.market_cap" }
   *   "net_profit p.7"
   *     → { pages: [7], provider: undefined, fetchedAt: undefined, fx: undefined, rest: "net_profit p.7" }
   *
   * Parsing is lenient — missing segments leave the field undefined.
   */
  export function parseSourceReference(raw: string | null | undefined): ParsedSourceReference {
    if (!raw) {
      return { pages: [], rest: '' }
    }

    const pages = extractPageRefs(raw)

    // Split on semicolon to get segments
    const segments = raw.split(';').map(s => s.trim()).filter(Boolean)

    let provider: string | undefined
    let fetchedAt: string | undefined
    let fx: string | undefined
    const restParts: string[] = []

    for (const seg of segments) {
      const providerMatch = seg.match(/^provider=(.+)$/)
      if (providerMatch) {
        provider = providerMatch[1].trim()
        continue
      }
      const fetchedAtMatch = seg.match(/^fetched_at=(.+)$/)
      if (fetchedAtMatch) {
        fetchedAt = fetchedAtMatch[1].trim()
        continue
      }
      const fxMatch = seg.match(/^FX\s+(.+)$/)
      if (fxMatch) {
        fx = fxMatch[1].trim()
        continue
      }
      restParts.push(seg)
    }

    return {
      pages,
      provider,
      fetchedAt,
      fx,
      rest: restParts.join('; '),
    }
  }

  // ---------------------------------------------------------------------------
  // extractFxRates (M1)
  // ---------------------------------------------------------------------------

  /**
   * Merge fx_rates + fx_rates_meta from report metadata into a display row list.
   * - Direct pairs (provider=yfinance): isDerived=false.
   * - Derived pairs (provider=derived(...) or derived_from present): isDerived=true.
   * - Returns [] when fx_rates is absent or empty (single-currency / no FX triggered).
   * Lenient parsing — missing fields yield undefined, never throws.
   */
  export function extractFxRates(reportMetadata: unknown): FxRateRow[] {
    if (!reportMetadata || typeof reportMetadata !== 'object') return []
    const meta = reportMetadata as Record<string, unknown>

    const fxRates = meta.fx_rates
    if (!fxRates || typeof fxRates !== 'object') return []
    const ratesObj = fxRates as Record<string, number>

    const fxRatesMeta = (meta.fx_rates_meta ?? {}) as Record<string, FxRateMeta>

    const rows: FxRateRow[] = []
    for (const [pair, rate] of Object.entries(ratesObj)) {
      if (typeof rate !== 'number') continue
      const pairMeta = (fxRatesMeta[pair] ?? {}) as FxRateMeta
      const provider = pairMeta.provider ?? 'unknown'
      const isDerived = provider.startsWith('derived') || (Array.isArray(pairMeta.derived_from) && pairMeta.derived_from.length > 0)
      rows.push({
        pair,
        rate,
        provider,
        asOf: pairMeta.as_of,
        fetchedAt: pairMeta.fetched_at,
        derivedFrom: pairMeta.derived_from,
        isDerived,
      })
    }
    return rows
  }

  // ---------------------------------------------------------------------------
  // extractMarketProvenance (M1)
  // ---------------------------------------------------------------------------

  /**
   * Extract market provenance from market.metadata.
   * Returns { marketAsOf, provider } with undefined for missing keys.
   * Never throws.
   */
  export function extractMarketProvenance(marketMetadata: unknown): MarketProvenance {
    if (!marketMetadata || typeof marketMetadata !== 'object') {
      return {}
    }
    const meta = marketMetadata as Record<string, unknown>
    return {
      marketAsOf: typeof meta.market_as_of === 'string' ? meta.market_as_of : undefined,
      provider: typeof meta.provider === 'string' ? meta.provider : undefined,
    }
  }

  // ---------------------------------------------------------------------------
  // statusTagType
  // ---------------------------------------------------------------------------

  /**
   * Map a facts/signals status string to an Element Plus tag type.
   * complete → success, degraded → warning, non_decisionable → danger, unsupported → info.
   */
  export function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
    switch (status) {
      case 'complete': return 'success'
      case 'degraded': return 'warning'
      case 'non_decisionable': return 'danger'
      case 'unsupported': return 'info'
      default: return 'info'
    }
  }

  // ---------------------------------------------------------------------------
  // reliabilityTagType
  // ---------------------------------------------------------------------------

  /**
   * Map a reliability string to an Element Plus tag type.
   * high → success, medium → warning, low/estimated → info.
   */
  export function reliabilityTagType(reliability: string): 'success' | 'warning' | 'info' {
    switch (reliability) {
      case 'high': return 'success'
      case 'medium': return 'warning'
      default: return 'info'
    }
  }
  ```

- [ ] **Step 5.2: Run type-check — verify PASS**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn type-check
  ```

  Expected: exits with code 0, no type errors.

- [ ] **Step 5.3: Commit**

  ```bash
  git add frontend/src/utils/turtlePayload.ts
  git commit -m "feat(turtle): add pure turtlePayload helper functions + ParsedTurtlePayload interface (Spec 4 §5.1, M1, M2)"
  ```

---

## Task 6: Frontend — `TurtlePayloadPanel.vue` component (implement + type-check + build)

### 概要

创建 `frontend/src/components/Analysis/TurtlePayloadPanel.vue`。接收 `valueReport?` 和 `valueTurtlePayload?` 作为 props：当 payload 可解析时显示 Element Plus 子 tabs（报告/数据/计算/状态），否则仅显示 markdown。包含 M1 provenance block 和 M2 source_reference 解析。

**Files:**
- Create: `frontend/src/components/Analysis/TurtlePayloadPanel.vue`

Note: The directory `frontend/src/components/Analysis/` does not exist yet — the component file creation will create it implicitly via the editor. If the Bash `mkdir` is needed first, run `mkdir -p frontend/src/components/Analysis/`.

- [ ] **Step 6.1: Create the component directory**

  ```bash
  mkdir -p /Users/like/source/TradingAgents-CN/frontend/src/components/Analysis
  ```

- [ ] **Step 6.2: Create `TurtlePayloadPanel.vue`**

  Create `frontend/src/components/Analysis/TurtlePayloadPanel.vue`:

  ```vue
  <template>
    <div class="turtle-payload-panel">
      <!-- No payload: markdown-only fallback -->
      <div v-if="!parsedPayload" class="markdown-only">
        <div
          v-if="valueReport"
          class="markdown-content"
          v-html="renderMarkdown(valueReport)"
        />
        <el-empty v-else description="暂无报告内容" />
      </div>

      <!-- Payload available: sub-tabs -->
      <el-tabs v-else v-model="activeTab" type="card" class="turtle-sub-tabs">
        <!-- 报告 tab -->
        <el-tab-pane label="报告" name="report">
          <div class="tab-content">
            <div
              v-if="valueReport"
              class="markdown-content"
              v-html="renderMarkdown(valueReport)"
            />
            <el-empty v-else description="暂无报告正文" />
          </div>
        </el-tab-pane>

        <!-- 数据 tab -->
        <el-tab-pane label="数据" name="data">
          <div class="tab-content">
            <!-- 汇率与来源 (M1 provenance block) -->
            <template v-if="fxRateRows.length > 0 || marketProv.marketAsOf || marketProv.provider">
              <div class="provenance-section">
                <div class="section-title">汇率与来源</div>

                <!-- Market metadata -->
                <div v-if="marketProv.marketAsOf || marketProv.provider" class="market-prov">
                  <el-descriptions :column="2" size="small" border>
                    <el-descriptions-item v-if="marketProv.provider" label="市场数据来源">
                      {{ marketProv.provider }}
                    </el-descriptions-item>
                    <el-descriptions-item v-if="marketProv.marketAsOf" label="市值快照日">
                      {{ marketProv.marketAsOf }}
                    </el-descriptions-item>
                  </el-descriptions>
                </div>

                <!-- FX rates table -->
                <el-table
                  v-if="fxRateRows.length > 0"
                  :data="fxRateRows"
                  size="small"
                  class="fx-table"
                  border
                >
                  <el-table-column prop="pair" label="货币对" width="120" />
                  <el-table-column label="汇率" width="100">
                    <template #default="{ row }">
                      {{ row.rate.toFixed(6) }}
                    </template>
                  </el-table-column>
                  <el-table-column label="来源" width="180">
                    <template #default="{ row }">
                      <el-tag v-if="row.isDerived" type="info" size="small">派生 (via CNY)</el-tag>
                      <el-tag v-else type="success" size="small">直连</el-tag>
                      <span class="provider-text">{{ row.provider }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="as_of" width="130">
                    <template #default="{ row }">{{ row.asOf ?? '—' }}</template>
                  </el-table-column>
                  <el-table-column label="derived_from">
                    <template #default="{ row }">
                      <span v-if="row.derivedFrom && row.derivedFrom.length">
                        {{ row.derivedFrom.join(' + ') }}
                      </span>
                      <span v-else>—</span>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </template>

            <!-- Report facts -->
            <template v-if="reportFields.length > 0">
              <div class="section-title">财务报告数据</div>
              <el-table :data="reportFields" size="small" border class="facts-table">
                <el-table-column prop="name" label="字段" width="180" />
                <el-table-column label="值" width="180">
                  <template #default="{ row }">{{ row.formattedValue }}</template>
                </el-table-column>
                <el-table-column label="可靠性" width="100">
                  <template #default="{ row }">
                    <el-tag v-if="row.reliability" :type="reliabilityTagType(row.reliability)" size="small">
                      {{ row.reliability }}
                    </el-tag>
                    <span v-else>—</span>
                  </template>
                </el-table-column>
                <el-table-column label="来源标签" width="160">
                  <template #default="{ row }">{{ row.source_label ?? '—' }}</template>
                </el-table-column>
                <el-table-column label="来源引用">
                  <template #default="{ row }">
                    <div class="source-ref-cell">
                      <!-- Page chips (M2) -->
                      <el-tag
                        v-for="page in row.parsedRef.pages"
                        :key="page"
                        size="small"
                        type="warning"
                        class="page-chip"
                        style="cursor: pointer; margin-right: 4px;"
                        @click="handlePageChipClick()"
                      >
                        p.{{ page }}
                      </el-tag>
                      <!-- Provider -->
                      <span v-if="row.parsedRef.provider" class="ref-segment">{{ row.parsedRef.provider }}</span>
                      <!-- FX -->
                      <span v-if="row.parsedRef.fx" class="ref-segment ref-fx">FX {{ row.parsedRef.fx }}</span>
                      <!-- fetched_at -->
                      <span v-if="row.parsedRef.fetchedAt" class="ref-segment ref-date">{{ row.parsedRef.fetchedAt }}</span>
                      <!-- Raw fallback if nothing parsed -->
                      <span
                        v-if="!row.parsedRef.pages.length && !row.parsedRef.provider && !row.parsedRef.fx && row.parsedRef.rest"
                        :title="row.source_reference"
                      >{{ row.parsedRef.rest }}</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="备注" min-width="120">
                  <template #default="{ row }">{{ row.caveat ?? '—' }}</template>
                </el-table-column>
              </el-table>
            </template>

            <!-- Market facts -->
            <template v-if="marketFields.length > 0">
              <div class="section-title" style="margin-top: 16px;">市场数据</div>
              <el-table :data="marketFields" size="small" border class="facts-table">
                <el-table-column prop="name" label="字段" width="180" />
                <el-table-column label="值" width="180">
                  <template #default="{ row }">{{ row.formattedValue }}</template>
                </el-table-column>
                <el-table-column label="可靠性" width="100">
                  <template #default="{ row }">
                    <el-tag v-if="row.reliability" :type="reliabilityTagType(row.reliability)" size="small">
                      {{ row.reliability }}
                    </el-tag>
                    <span v-else>—</span>
                  </template>
                </el-table-column>
                <el-table-column label="来源标签" width="160">
                  <template #default="{ row }">{{ row.source_label ?? '—' }}</template>
                </el-table-column>
                <el-table-column label="来源引用">
                  <template #default="{ row }">
                    <div class="source-ref-cell">
                      <el-tag
                        v-for="page in row.parsedRef.pages"
                        :key="page"
                        size="small"
                        type="warning"
                        class="page-chip"
                        style="cursor: pointer; margin-right: 4px;"
                        @click="handlePageChipClick()"
                      >
                        p.{{ page }}
                      </el-tag>
                      <span v-if="row.parsedRef.provider" class="ref-segment">{{ row.parsedRef.provider }}</span>
                      <span v-if="row.parsedRef.fx" class="ref-segment ref-fx">FX {{ row.parsedRef.fx }}</span>
                      <span v-if="row.parsedRef.fetchedAt" class="ref-segment ref-date">{{ row.parsedRef.fetchedAt }}</span>
                      <span
                        v-if="!row.parsedRef.pages.length && !row.parsedRef.provider && !row.parsedRef.fx && row.parsedRef.rest"
                        :title="row.source_reference"
                      >{{ row.parsedRef.rest }}</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="备注" min-width="120">
                  <template #default="{ row }">{{ row.caveat ?? '—' }}</template>
                </el-table-column>
              </el-table>
            </template>

            <!-- Historical facts (collapsible) -->
            <template v-if="historicalPeriods.length > 0">
              <div class="section-title" style="margin-top: 16px;">历史期间</div>
              <el-collapse>
                <el-collapse-item
                  v-for="period in historicalPeriods"
                  :key="period.periodKey"
                  :title="period.periodKey"
                >
                  <el-table :data="period.fields" size="small" border class="facts-table">
                    <el-table-column prop="name" label="字段" width="180" />
                    <el-table-column label="值" width="180">
                      <template #default="{ row }">{{ row.formattedValue }}</template>
                    </el-table-column>
                    <el-table-column label="可靠性" width="100">
                      <template #default="{ row }">
                        <el-tag v-if="row.reliability" :type="reliabilityTagType(row.reliability)" size="small">
                          {{ row.reliability }}
                        </el-tag>
                        <span v-else>—</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="来源引用" min-width="160">
                      <template #default="{ row }">{{ row.source_reference ?? '—' }}</template>
                    </el-table-column>
                  </el-table>
                </el-collapse-item>
              </el-collapse>
            </template>

            <el-empty
              v-if="reportFields.length === 0 && marketFields.length === 0 && historicalPeriods.length === 0 && fxRateRows.length === 0"
              description="暂无数据字段"
            />
          </div>
        </el-tab-pane>

        <!-- 计算 tab -->
        <el-tab-pane label="计算" name="signals">
          <div class="tab-content">
            <template v-if="signalRows.length > 0">
              <el-table :data="signalRows" size="small" border class="signals-table">
                <el-table-column prop="name" label="指标" width="160" />
                <el-table-column label="状态" width="130">
                  <template #default="{ row }">
                    <el-tag :type="statusTagType(row.status ?? '')" size="small">
                      {{ row.status ?? '—' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="formula" label="公式" min-width="200" />
                <el-table-column prop="substitution" label="代入" min-width="200" />
                <el-table-column label="结果" width="120">
                  <template #default="{ row }">
                    {{ formatFactValue(row.value) }}
                    <span v-if="row.unit"> {{ row.unit }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="缺失输入" min-width="160">
                  <template #default="{ row }">
                    <template v-if="row.missing_inputs && row.missing_inputs.length > 0">
                      <el-tag
                        v-for="mi in row.missing_inputs"
                        :key="mi"
                        type="danger"
                        size="small"
                        style="margin-right: 4px; margin-bottom: 2px;"
                      >{{ mi }}</el-tag>
                    </template>
                    <span v-else>—</span>
                  </template>
                </el-table-column>
              </el-table>
            </template>
            <el-empty v-else description="暂无计算结果" />

            <!-- Veto reasons -->
            <template v-if="vetoReasons.length > 0">
              <div class="section-title" style="margin-top: 16px;">否决原因</div>
              <el-alert
                v-for="(reason, i) in vetoReasons"
                :key="i"
                :title="reason"
                type="warning"
                show-icon
                :closable="false"
                style="margin-bottom: 8px;"
              />
            </template>
          </div>
        </el-tab-pane>

        <!-- 状态 tab -->
        <el-tab-pane label="状态" name="status">
          <div class="tab-content">
            <!-- Status summary -->
            <el-descriptions title="状态概览" :column="2" border size="small" class="status-descriptions">
              <el-descriptions-item label="facts 状态">
                <el-tag :type="statusTagType(parsedPayload.facts?.status ?? '')" size="small">
                  {{ parsedPayload.facts?.status ?? '—' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="signals 状态">
                <el-tag :type="statusTagType(parsedPayload.signals?.status ?? '')" size="small">
                  {{ parsedPayload.signals?.status ?? '—' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="report facts 状态">
                <el-tag :type="statusTagType(parsedPayload.facts?.report?.status ?? '')" size="small">
                  {{ parsedPayload.facts?.report?.status ?? '—' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="market facts 状态">
                <el-tag :type="statusTagType(parsedPayload.facts?.market?.status ?? '')" size="small">
                  {{ parsedPayload.facts?.market?.status ?? '—' }}
                </el-tag>
              </el-descriptions-item>
            </el-descriptions>

            <!-- Caveats -->
            <template v-if="allCaveats.length > 0">
              <div class="section-title" style="margin-top: 16px;">注意事项</div>
              <el-alert
                v-for="(cav, i) in allCaveats"
                :key="i"
                :title="cav"
                type="warning"
                show-icon
                :closable="false"
                style="margin-bottom: 6px;"
              />
            </template>

            <!-- Missing inputs aggregated from signals -->
            <template v-if="allMissingInputs.length > 0">
              <div class="section-title" style="margin-top: 16px;">缺失输入汇总</div>
              <el-tag
                v-for="mi in allMissingInputs"
                :key="mi"
                type="danger"
                size="small"
                style="margin-right: 6px; margin-bottom: 6px;"
              >{{ mi }}</el-tag>
            </template>

            <el-empty
              v-if="allCaveats.length === 0 && allMissingInputs.length === 0"
              description="无注意事项"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </template>

  <script setup lang="ts">
  import { computed, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { marked } from 'marked'
  import {
    parseTurtlePayload,
    formatFactValue,
    parseSourceReference,
    extractFxRates,
    extractMarketProvenance,
    statusTagType,
    reliabilityTagType,
    type ParsedTurtlePayload,
    type FactField,
    type FxRateRow,
    type MarketProvenance,
  } from '@/utils/turtlePayload'

  // ---------------------------------------------------------------------------
  // Props
  // ---------------------------------------------------------------------------

  interface Props {
    valueReport?: string
    valueTurtlePayload?: string
  }

  const props = withDefaults(defineProps<Props>(), {
    valueReport: '',
    valueTurtlePayload: '',
  })

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  const activeTab = ref<string>('report')

  // ---------------------------------------------------------------------------
  // Parsed payload (reactive)
  // ---------------------------------------------------------------------------

  const parsedPayload = computed<ParsedTurtlePayload | null>(() =>
    parseTurtlePayload(props.valueTurtlePayload)
  )

  // ---------------------------------------------------------------------------
  // Markdown renderer
  // ---------------------------------------------------------------------------

  marked.setOptions({ breaks: true, gfm: true })

  function renderMarkdown(content: string): string {
    if (!content) return ''
    try {
      return marked.parse(content) as string
    } catch {
      return `<pre style="white-space: pre-wrap;">${content}</pre>`
    }
  }

  // ---------------------------------------------------------------------------
  // M1 Provenance computed
  // ---------------------------------------------------------------------------

  const fxRateRows = computed<FxRateRow[]>(() => {
    const reportMeta = parsedPayload.value?.facts?.report?.metadata
    return extractFxRates(reportMeta)
  })

  const marketProv = computed<MarketProvenance>(() => {
    const marketMeta = parsedPayload.value?.facts?.market?.metadata
    return extractMarketProvenance(marketMeta)
  })

  // ---------------------------------------------------------------------------
  // Facts field rows (数据 tab)
  // ---------------------------------------------------------------------------

  interface FactRow {
    name: string
    formattedValue: string
    reliability?: string
    source_label?: string
    source_reference?: string
    parsedRef: ReturnType<typeof parseSourceReference>
    caveat?: string
  }

  function fieldsToRows(fields: Record<string, FactField> | undefined): FactRow[] {
    if (!fields) return []
    return Object.entries(fields).map(([key, field]) => ({
      name: field.name ?? key,
      formattedValue: formatFactValue(field.value),
      reliability: field.reliability,
      source_label: field.source_label,
      source_reference: field.source_reference,
      parsedRef: parseSourceReference(field.source_reference),
      caveat: field.caveat,
    }))
  }

  const reportFields = computed<FactRow[]>(() =>
    fieldsToRows(parsedPayload.value?.facts?.report?.fields)
  )

  const marketFields = computed<FactRow[]>(() =>
    fieldsToRows(parsedPayload.value?.facts?.market?.fields)
  )

  // ---------------------------------------------------------------------------
  // Historical periods
  // ---------------------------------------------------------------------------

  interface HistoricalPeriod {
    periodKey: string
    fields: FactRow[]
  }

  const historicalPeriods = computed<HistoricalPeriod[]>(() => {
    const historical = parsedPayload.value?.facts?.report?.historical
    if (!historical || typeof historical !== 'object') return []
    return Object.entries(historical).map(([periodKey, periodData]) => ({
      periodKey,
      fields: fieldsToRows(periodData?.fields),
    }))
  })

  // ---------------------------------------------------------------------------
  // Signals rows (计算 tab)
  // ---------------------------------------------------------------------------

  interface SignalRow {
    name: string
    status?: string
    formula?: string
    substitution?: string
    value: unknown
    unit?: string
    sources?: string[]
    missing_inputs?: string[]
  }

  const signalRows = computed<SignalRow[]>(() => {
    const results = parsedPayload.value?.signals?.results
    if (!results) return []
    return Object.entries(results).map(([key, sr]) => ({
      name: sr.name ?? key,
      status: sr.status,
      formula: sr.formula,
      substitution: sr.substitution,
      value: sr.value,
      unit: sr.unit,
      sources: sr.sources,
      missing_inputs: sr.missing_inputs,
    }))
  })

  const vetoReasons = computed<string[]>(() =>
    parsedPayload.value?.signals?.veto_reasons ?? []
  )

  // ---------------------------------------------------------------------------
  // Status tab computed
  // ---------------------------------------------------------------------------

  const allCaveats = computed<string[]>(() => {
    const pl = parsedPayload.value
    if (!pl) return []
    return [
      ...(pl.facts?.caveats ?? []),
      ...(pl.facts?.report?.caveats ?? []),
      ...(pl.facts?.market?.caveats ?? []),
      ...(pl.signals?.caveats ?? []),
    ]
  })

  const allMissingInputs = computed<string[]>(() => {
    const results = parsedPayload.value?.signals?.results
    if (!results) return []
    const seen = new Set<string>()
    const out: string[] = []
    for (const sr of Object.values(results)) {
      for (const mi of (sr.missing_inputs ?? [])) {
        if (!seen.has(mi)) {
          seen.add(mi)
          out.push(mi)
        }
      }
    }
    return out
  })

  // ---------------------------------------------------------------------------
  // Page chip click handler (spec §7)
  // ---------------------------------------------------------------------------

  function handlePageChipClick(): void {
    ElMessage.info('当前报告暂未提供原文定位链接')
  }
  </script>

  <style scoped>
  .turtle-payload-panel {
    width: 100%;
  }

  /* Isolated sub-tab styles — must NOT inherit outer .analysis-tabs :deep rules */
  .turtle-sub-tabs {
    margin-top: 8px;
  }

  /* Override to keep sub-tabs compact (counteract outer :deep(.el-tabs__item) 55px height) */
  .turtle-sub-tabs :deep(.el-tabs__item) {
    height: 36px !important;
    line-height: 36px !important;
    padding: 0 14px !important;
    margin-right: 4px !important;
    background: var(--el-bg-color) !important;
    border: 1px solid var(--el-border-color) !important;
    border-radius: 6px !important;
    color: var(--el-text-color-regular) !important;
    font-weight: 500 !important;
    transform: none !important;
    box-shadow: none !important;
    font-size: 13px !important;
  }

  .turtle-sub-tabs :deep(.el-tabs__item.is-active) {
    background: var(--el-color-primary-light-9) !important;
    color: var(--el-color-primary) !important;
    border-color: var(--el-color-primary-light-5) !important;
    transform: none !important;
    box-shadow: none !important;
  }

  .turtle-sub-tabs :deep(.el-tabs__item:hover) {
    background: var(--el-fill-color-light) !important;
    transform: none !important;
    box-shadow: none !important;
  }

  .turtle-sub-tabs :deep(.el-tabs__header) {
    margin: 0 0 12px 0;
    background: transparent;
    padding: 0;
    border-radius: 0;
    box-shadow: none;
    border: none;
  }

  .tab-content {
    padding: 8px 0;
  }

  .section-title {
    font-weight: 600;
    font-size: 14px;
    color: var(--el-text-color-primary);
    margin-bottom: 8px;
    padding-left: 4px;
    border-left: 3px solid var(--el-color-primary);
  }

  .provenance-section {
    margin-bottom: 16px;
    padding: 12px;
    background: var(--el-fill-color-extra-light);
    border-radius: 6px;
    border: 1px solid var(--el-border-color-light);
  }

  .market-prov {
    margin-bottom: 10px;
  }

  .fx-table {
    margin-top: 8px;
  }

  .provider-text {
    font-size: 11px;
    color: var(--el-text-color-secondary);
    margin-left: 4px;
  }

  .facts-table,
  .signals-table {
    width: 100%;
  }

  .source-ref-cell {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
  }

  .page-chip {
    cursor: pointer;
  }

  .ref-segment {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .ref-fx {
    color: var(--el-color-warning);
  }

  .ref-date {
    color: var(--el-text-color-placeholder);
  }

  .status-descriptions {
    margin-bottom: 8px;
  }

  .markdown-content {
    line-height: 1.7;
  }

  .markdown-only {
    padding: 4px 0;
  }
  </style>
  ```

- [ ] **Step 6.3: Run type-check**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn type-check
  ```

  Expected: exits with code 0.

- [ ] **Step 6.4: Run build**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn build
  ```

  Expected: build succeeds with no errors (warnings are acceptable).

- [ ] **Step 6.5: Commit**

  ```bash
  git add frontend/src/components/Analysis/TurtlePayloadPanel.vue
  git commit -m "feat(turtle): add TurtlePayloadPanel.vue with 4 sub-tabs, M1 FX provenance, M2 source_reference parsing (Spec 4 §5)"
  ```

---

## Task 7: Frontend — `ReportDetail.vue` integration

### 概要

修改 `frontend/src/views/Reports/ReportDetail.vue`：
1. 在 `displayReports` computed 中从 `reports` dict 过滤掉 `value_turtle_payload`。
2. `value_report` 模块改用 `TurtlePayloadPanel` 渲染。
3. 其他模块保持现有的 markdown/json 渲染方式不变。

**Files:**
- Modify: `frontend/src/views/Reports/ReportDetail.vue`

- [ ] **Step 7.1: Add `TurtlePayloadPanel` import and `displayReports` computed**

  In `ReportDetail.vue`, in the `<script setup lang="ts">` block:

  **After the existing imports (around line 262–289), add:**
  ```typescript
  import TurtlePayloadPanel from '@/components/Analysis/TurtlePayloadPanel.vue'
  ```

  **After `const activeModule = ref('')` (around line 304), add:**
  ```typescript
  // Spec 4 §6.1: filter value_turtle_payload from normal report tab list
  const displayReports = computed(() => {
    const reports = (report.value as any)?.reports || {}
    return Object.fromEntries(
      Object.entries(reports).filter(([key]) => key !== 'value_turtle_payload')
    )
  })
  ```

- [ ] **Step 7.2: Update the template to use `displayReports` and render `TurtlePayloadPanel` for `value_report`**

  In the `<template>` section, find the `<el-tabs>` block near line 226:

  ```html
  <el-tabs v-model="activeModule" type="border-card">
    <el-tab-pane
      v-for="(content, moduleName) in report.reports"
      :key="moduleName"
      :label="getModuleDisplayName(moduleName)"
      :name="moduleName"
    >
      <div class="module-content">
        <div v-if="typeof content === 'string'" class="markdown-content">
          <div v-html="renderMarkdown(content)"></div>
        </div>
        <div v-else class="json-content">
          <pre>{{ JSON.stringify(content, null, 2) }}</pre>
        </div>
      </div>
    </el-tab-pane>
  </el-tabs>
  ```

  Replace with:
  ```html
  <el-tabs v-model="activeModule" type="border-card">
    <el-tab-pane
      v-for="(content, moduleName) in displayReports"
      :key="moduleName"
      :label="getModuleDisplayName(String(moduleName))"
      :name="String(moduleName)"
    >
      <div class="module-content">
        <!-- value_report: use TurtlePayloadPanel (Spec 4 §6.1) -->
        <template v-if="String(moduleName) === 'value_report'">
          <TurtlePayloadPanel
            :value-report="typeof content === 'string' ? content : ''"
            :value-turtle-payload="(report as any)?.value_turtle_payload ?? (report as any)?.reports?.value_turtle_payload ?? ''"
          />
        </template>
        <!-- All other modules: existing rendering -->
        <template v-else>
          <div v-if="typeof content === 'string'" class="markdown-content">
            <div v-html="renderMarkdown(String(content))"></div>
          </div>
          <div v-else class="json-content">
            <pre>{{ JSON.stringify(content, null, 2) }}</pre>
          </div>
        </template>
      </div>
    </el-tab-pane>
  </el-tabs>
  ```

  > Note: `(report as any)` casts are intentional — the `report` ref is typed as `null` initially. If the codebase has a proper type for the report object, use it.

- [ ] **Step 7.3: Update `activeModule` initialization to exclude `value_turtle_payload`**

  In `fetchReportDetail`, find this block (around line 342):
  ```typescript
      const reports = result.data.reports || {}
      const moduleNames = Object.keys(reports)
      if (moduleNames.length > 0) {
        activeModule.value = moduleNames[0]
      }
  ```

  Replace with:
  ```typescript
      const reports = result.data.reports || {}
      const moduleNames = Object.keys(reports).filter(k => k !== 'value_turtle_payload')
      if (moduleNames.length > 0) {
        activeModule.value = moduleNames[0]
      }
  ```

- [ ] **Step 7.4: Run type-check**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn type-check
  ```

  Expected: exits with code 0.

- [ ] **Step 7.5: Run build**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn build
  ```

  Expected: build succeeds.

- [ ] **Step 7.6: Commit**

  ```bash
  git add frontend/src/views/Reports/ReportDetail.vue
  git commit -m "feat(turtle): ReportDetail.vue uses TurtlePayloadPanel for value_report, filters value_turtle_payload from tabs (Spec 4 §6.1)"
  ```

---

## Task 8: Frontend — `SingleAnalysis.vue` integration + stable key + style isolation

### 概要

修改 `frontend/src/views/Analysis/SingleAnalysis.vue`：
1. 更新 `getAnalysisReports()` 的返回类型为 `Array<{ key: string; title: string; content: any }>`，并过滤 `value_turtle_payload`。
2. 在模板中使用 `report.key` 进行循环，当 `key === 'value_report'` 时改用 `TurtlePayloadPanel`。
3. 收窄外层 `.analysis-tabs :deep(.el-tabs__item)` 选择器，避免影响 `TurtlePayloadPanel` 内部的嵌套 tabs。

**Files:**
- Modify: `frontend/src/views/Analysis/SingleAnalysis.vue`

- [ ] **Step 8.1: Add `TurtlePayloadPanel` import**

  In `SingleAnalysis.vue`, in the `<script setup lang="ts">` block, after the existing imports, add:
  ```typescript
  import TurtlePayloadPanel from '@/components/Analysis/TurtlePayloadPanel.vue'
  ```

- [ ] **Step 8.2: Update `getAnalysisReports` to return stable `key` and filter `value_turtle_payload`**

  Find `getAnalysisReports` near line 1246. Replace the entire function:

  ```typescript
  // 获取分析报告（返回带稳定 key 的列表，过滤 value_turtle_payload）
  const getAnalysisReports = (data: any): Array<{ key: string; title: string; content: any }> => {
    console.log('📊 getAnalysisReports 输入数据:', data)
    const reports: Array<{ key: string; title: string; content: any }> = []

    // 优先从 reports 字段获取数据（新的API格式）
    let reportsData = data
    if (data && data.reports && typeof data.reports === 'object') {
      reportsData = data.reports
      console.log('📊 使用 data.reports:', reportsData)
    } else if (data && data.state && typeof data.state === 'object') {
      reportsData = data.state
      console.log('📊 使用 data.state:', reportsData)
    } else {
      console.log('📊 没有找到有效的报告数据')
      return reports
    }

    // 定义报告映射（按照完整的分析流程顺序）
    const reportMappings = [
      // 分析师团队 (5个)
      { key: 'market_report', title: '📈 市场技术分析', category: '分析师团队' },
      { key: 'sentiment_report', title: '💭 市场情绪分析', category: '分析师团队' },
      { key: 'news_report', title: '📰 新闻事件分析', category: '分析师团队' },
      { key: 'fundamentals_report', title: '💰 基本面分析', category: '分析师团队' },
      { key: 'value_report', title: '💎 价值投资分析', category: '分析师团队' },

      // 研究团队 (3个)
      { key: 'bull_researcher', title: '🐂 多头研究员', category: '研究团队' },
      { key: 'bear_researcher', title: '🐻 空头研究员', category: '研究团队' },
      { key: 'research_team_decision', title: '🔬 研究经理决策', category: '研究团队' },

      // 交易团队 (1个)
      { key: 'trader_investment_plan', title: '💼 交易员计划', category: '交易团队' },

      // 风险管理团队 (4个)
      { key: 'risky_analyst', title: '⚡ 激进分析师', category: '风险管理团队' },
      { key: 'safe_analyst', title: '🛡️ 保守分析师', category: '风险管理团队' },
      { key: 'neutral_analyst', title: '⚖️ 中性分析师', category: '风险管理团队' },
      { key: 'risk_management_decision', title: '👔 投资组合经理', category: '风险管理团队' },

      // 最终决策 (1个)
      { key: 'final_trade_decision', title: '🎯 最终交易决策', category: '最终决策' },

      // 兼容旧格式
      { key: 'investment_plan', title: '📋 投资建议', category: '其他' },
      { key: 'investment_debate_state', title: '🔬 研究团队决策（旧）', category: '其他' },
      { key: 'risk_debate_state', title: '⚖️ 风险管理团队（旧）', category: '其他' },
    ]

    // Spec 4: value_turtle_payload must never appear as a report tab
    reportMappings.forEach(mapping => {
      if (mapping.key === 'value_turtle_payload') return
      const content = reportsData[mapping.key]
      if (content) {
        console.log(`📊 找到报告: ${mapping.key} -> ${mapping.title}`)
        reports.push({
          key: mapping.key,
          title: mapping.title,
          content: content,
        })
      }
    })

    console.log(`📊 总共找到 ${reports.length} 个报告`)

    // 设置第一个报告为默认激活标签页
    if (reports.length > 0 && !activeReportTab.value) {
      activeReportTab.value = reports[0].key
    }

    return reports
  }
  ```

- [ ] **Step 8.3: Update the template tab loop and content rendering**

  Find the `<el-tabs>` block near line 600:

  ```html
  <el-tabs
    v-model="activeReportTab"
    type="card"
    class="analysis-tabs"
    tab-position="top"
    :key="analysisResults?.id || 'default'"
  >
    <el-tab-pane
      v-for="(report, key) in getAnalysisReports(analysisResults)"
      :key="key"
      :name="key.toString()"
      :label="report.title"
      class="report-tab-pane"
    >
      <!-- 标签页内容头部 -->
      <div class="report-header">
        <div class="report-title">
          <span class="report-icon">{{ getReportIcon(report.title) }}</span>
          <span class="report-name">{{ getReportName(report.title) }}</span>
        </div>
        <div class="report-description">{{ getReportDescription(report.title) }}</div>
      </div>

      <!-- 报告内容 -->
      <div class="report-content-wrapper">
        <div
          class="report-content"
          v-html="formatReportContent(report.content)"
          v-if="report.content"
        ></div>
        <div v-else class="no-content">
          <el-empty description="暂无内容" />
        </div>
      </div>
    </el-tab-pane>
  </el-tabs>
  ```

  Replace with:
  ```html
  <el-tabs
    v-model="activeReportTab"
    type="card"
    class="analysis-tabs"
    tab-position="top"
    :key="analysisResults?.id || 'default'"
  >
    <el-tab-pane
      v-for="report in getAnalysisReports(analysisResults)"
      :key="report.key"
      :name="report.key"
      :label="report.title"
      class="report-tab-pane"
    >
      <!-- 标签页内容头部 -->
      <div class="report-header">
        <div class="report-title">
          <span class="report-icon">{{ getReportIcon(report.title) }}</span>
          <span class="report-name">{{ getReportName(report.title) }}</span>
        </div>
        <div class="report-description">{{ getReportDescription(report.title) }}</div>
      </div>

      <!-- 报告内容 -->
      <div class="report-content-wrapper">
        <!-- value_report: use TurtlePayloadPanel (Spec 4 §6.2) -->
        <template v-if="report.key === 'value_report'">
          <TurtlePayloadPanel
            :value-report="typeof report.content === 'string' ? report.content : ''"
            :value-turtle-payload="analysisResults?.value_turtle_payload ?? analysisResults?.reports?.value_turtle_payload ?? ''"
          />
        </template>
        <!-- All other reports: existing rendering -->
        <template v-else>
          <div
            class="report-content"
            v-html="formatReportContent(report.content)"
            v-if="report.content"
          ></div>
          <div v-else class="no-content">
            <el-empty description="暂无内容" />
          </div>
        </template>
      </div>
    </el-tab-pane>
  </el-tabs>
  ```

- [ ] **Step 8.4: Fix `activeReportTab` initialization reference**

  In `getAnalysisReports`, the previous code set `activeReportTab.value = '0'` (array index). The new code sets it to `reports[0].key` (e.g. `'market_report'`). Confirm the existing `activeReportTab` ref is typed as `string` (not `number`). Find its declaration:

  ```typescript
  const activeReportTab = ref('')
  ```

  If it is already `ref('')` or `ref<string>('')`, no change needed. If it is `ref('0')` or another string, change the default to `ref('')` — the new `getAnalysisReports` sets it to the first key.

- [ ] **Step 8.5: Narrow the outer tab :deep selector for style isolation**

  In the `<style scoped>` block, find `.analysis-tabs { ... :deep(.el-tabs__item) { ... } }` near line 3197. Add a child combinator `> .el-tabs__header > .el-tabs__nav-scroll > .el-tabs__nav > ` to the selector, OR use the class-wrapping approach:

  Change this selector:
  ```css
  .analysis-tabs {
    /* ... */
    :deep(.el-tabs__item) {
      height: 55px !important;
      /* ... rest of styles ... */
    }
  ```

  To (add `.analysis-tabs >` child scoping via wrapping the `el-tabs__item` rule):
  ```css
  .analysis-tabs {
    /* 标签页头部样式 */
    /* existing :deep(.el-tabs__header) and :deep(.el-tabs__nav-wrap) rules stay unchanged */

    /* Spec 4 §6.3: scope to ONLY first-level tabs, not nested turtle sub-tabs */
    > :deep(.el-tabs__header .el-tabs__item) {
      height: 55px !important;
      line-height: 55px !important;
      padding: 0 20px !important;
      margin-right: 8px !important;
      background: var(--el-bg-color) !important;
      border: 2px solid var(--el-border-color) !important;
      border-radius: 12px !important;
      color: var(--el-text-color-regular) !important;
      font-weight: 600 !important;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1) !important;
      position: relative !important;
      overflow: hidden !important;
      border-bottom: 2px solid var(--el-border-color) !important;

      &:hover {
        background: var(--el-fill-color-light) !important;
        border-color: #2196f3 !important;
        transform: translateY(-2px) scale(1.02) !important;
        box-shadow: 0 4px 15px rgba(33,150,243,0.3) !important;
        color: #1976d2 !important;
      }

      &.is-active {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
        color: white !important;
        border-color: #667eea !important;
        box-shadow: 0 6px 20px rgba(102,126,234,0.4) !important;
        transform: translateY(-3px) scale(1.05) !important;

        &::before {
          content: '';
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: linear-gradient(135deg, rgba(255,255,255,0.2) 0%, rgba(255,255,255,0.1) 100%);
          border-radius: 10px;
          pointer-events: none;
        }
      }
    }
  ```

  > Note: The `> :deep(...)` syntax (direct child combinator before `:deep`) restricts the rule to the direct content of `.analysis-tabs` in Vue's scoped CSS. The `TurtlePayloadPanel` component has its own `<style scoped>` block with `.turtle-sub-tabs :deep(.el-tabs__item)` overrides, so its internal tabs are doubly protected: they match `> :deep(...)` only at the `.analysis-tabs` level, not within the component boundary.

  > If `> :deep(...)` does not work in the project's Vue version, use the alternative approach: add a wrapper div with class `.outer-tabs-only` around the `<el-tabs>` in the template, and scope the style to `.outer-tabs-only :deep(.el-tabs__item)` instead. Verify with `yarn type-check` — this is a CSS issue, not a TS issue, so `type-check` will pass either way; validate visually per spec §8.3.

- [ ] **Step 8.6: Run type-check**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn type-check
  ```

  Expected: exits with code 0. If there are type errors from the `getAnalysisReports` return type change (e.g. `report.key` not found in `v-for`), ensure the function is properly typed as `Array<{ key: string; title: string; content: any }>`.

- [ ] **Step 8.7: Run build**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn build
  ```

  Expected: build succeeds.

- [ ] **Step 8.8: Commit**

  ```bash
  git add frontend/src/views/Analysis/SingleAnalysis.vue
  git commit -m "feat(turtle): SingleAnalysis.vue integrates TurtlePayloadPanel, stable key, value_turtle_payload filter, style isolation (Spec 4 §6.2, §6.3)"
  ```

---

## Task 9: Final verification — full test suite + frontend build + manual smoke

### 概要

运行全部后端测试，确认前端 type-check 和 build 通过后，执行 spec §8.3 的手动验收检查清单。

**Files:**
- No new files.

- [ ] **Step 9.1: Run full backend unit test suite**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -40
  ```

  Expected: all tests PASSED, no regressions. The new test files from Tasks 1–4 all appear and pass.

- [ ] **Step 9.2: Run the specific new test files together**

  ```bash
  cd /Users/like/source/TradingAgents-CN && .venv/bin/python -m pytest \
    tests/unit/test_turtle_payload_helper.py \
    tests/unit/test_turtle_save_canonical_payload.py \
    tests/unit/test_analysis_router_turtle_payload.py \
    tests/unit/test_reports_router_turtle_payload.py \
    -v
  ```

  Expected: all tests PASSED.

- [ ] **Step 9.3: Run frontend type-check**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn type-check
  ```

  Expected: exits with code 0.

- [ ] **Step 9.4: Run frontend build**

  ```bash
  cd /Users/like/source/TradingAgents-CN/frontend && yarn build
  ```

  Expected: build succeeds with no errors.

- [ ] **Step 9.5: Manual smoke checklist (spec §8.3)**

  Start the full stack (MongoDB + Redis + FastAPI + Vue dev server) and perform these checks. Check each box when confirmed:

  - [ ] 已完成 Turtle 分析在 `ReportDetail` 的"💎 价值投资分析" tab 中显示 `报告 / 数据 / 计算 / 状态` 四个子 Tab。
  - [ ] 同一分析在 `SingleAnalysis` 完成页的"💎 价值投资分析" tab 显示一致的四个子 Tab。
  - [ ] 旧报告或无 payload 报告在 `ReportDetail` 中"价值投资分析" Tab 仍只显示原 markdown，无子 Tab。
  - [ ] 旧报告或无 payload 报告在 `SingleAnalysis` 中同上。
  - [ ] `reports.value_turtle_payload` 不作为普通报告 tab 出现在 `ReportDetail`（Tab 列表中无"value_turtle_payload" tab）。
  - [ ] `reports.value_turtle_payload` 不作为普通报告 tab 出现在 `SingleAnalysis`。
  - [ ] `SingleAnalysis` 外层报告 tabs（"📈 市场技术分析"等）高度为 55px，带渐变 active 样式。
  - [ ] `TurtlePayloadPanel` 内嵌 `报告 / 数据 / 计算 / 状态` tabs 高度约 36px，active 样式为浅蓝背景，无渐变/transform。
  - [ ] 数据 Tab 当前期 `facts.report.fields` 字段可读，表格有"字段名/值/可靠性/来源标签/来源引用/备注"列。
  - [ ] 数据 Tab 有「汇率与来源」区块（如分析有 FX 数据：显示 FX 表格，直连/派生区分；如无 FX 数据：区块隐藏）。
  - [ ] 数据 Tab 历史期间 collapse 区域可折叠查看。
  - [ ] 计算 Tab 能看到公式（formula）、代入（substitution）、结果（value）、缺失项（missing_inputs 红色 tag）。
  - [ ] 状态 Tab 能看到 facts.status / signals.status 带颜色 tag，caveats 列表。
  - [ ] 点击页码 chip（如"p.7"）弹出 `ElMessage.info('当前报告暂未提供原文定位链接')`。
  - [ ] API 响应 `/api/reports/{id}/detail` 和 `/api/analysis/tasks/{id}/result` 在 DevTools Network 中有 `value_turtle_payload` 顶层字段，且 `reports` dict 中不含该 key。

---

## Spec 覆盖自查

| Spec 章节 | 内容摘要 | 任务 |
|---|---|---|
| §2.1（范围内） | API 返回 canonical payload、保存时标准化、历史记录 fallback、TurtlePayloadPanel、两个 View 集成 | Task 1–8 |
| §2.2（范围外） | 不重构其他报告模块、不引入新前端测试工具链 | 未引入，已遵守 |
| §2.3 M3（infra commit） | f5a4761 docker/env/extraction config 不属于本计划 | 明确排除，无任务 |
| §3（原则） | 共享组件 / 透传 / 语义绑定 / 无 payload 退化 / 历史期轻量 / PDF 定位预留 / 过滤 reports.value_turtle_payload | Task 5–8 |
| §4（API 边界） | 保存时写入两个 canonical 位置，extraction priority，API 透传 | Task 1–4 |
| §4.1（保存时标准化） | analysis_reports + analysis_tasks.result | Task 1, 2 |
| §4.2（API 提取优先级） | priority 1-4 cross-source + disk fallback，两个 endpoint | Task 1, 3, 4 |
| §5.1（TurtlePayloadPanel props + helpers） | parseTurtlePayload / formatFactValue / extractPageRefs / parseSourceReference / extractFxRates / extractMarketProvenance / statusTagType / reliabilityTagType | Task 5, 6 |
| §5.2（数据 Tab） | report/market fields 表，M2 source_reference 解析，M1 FX provenance 区块，历史期 collapse | Task 5, 6 |
| §5.3（计算 Tab） | signals.results 公式表，missing_inputs 突出 | Task 6 |
| §5.4（状态 Tab） | facts/signals status tag，caveats，missing_inputs 汇总 | Task 6 |
| §6.1（ReportDetail 集成） | displayReports filter + value_report → TurtlePayloadPanel | Task 7 |
| §6.2（SingleAnalysis 集成） | stable key + filter + TurtlePayloadPanel for value_report | Task 8 |
| §6.3（样式隔离） | 外层 :deep selector 收窄，内层 .turtle-sub-tabs 独立 override | Task 6, 8 |
| §7（兼容与错误处理） | 旧/无 payload 降级，JSON 解析失败 console.warn，缺局部字段显示空状态，页码 chip ElMessage | Task 5, 6 |
| §8（测试策略） | 后端 TDD pytest，前端 type-check + build，手动验收清单 | Task 1–4 (TDD), Task 5–8 (type-check/build), Task 9 (smoke) |
| M1（FX provenance 展示） | extractFxRates + extractMarketProvenance + 数据 Tab 「汇率与来源」区块 | Task 5, 6 |
| M2（source_reference 解析） | parseSourceReference + 数据 Tab 列 | Task 5, 6 |
| M3（infra commit） | 明确 out of scope | (no task) |
