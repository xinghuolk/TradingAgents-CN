"""Spec 1 §6.5: turtle payload 持久化行为 + 空内容短路。"""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_report_modules(stock_symbol: str = "600519"):
    """构造 report_modules 字典（与 simple_analysis_service 中定义一致）。"""
    return {
        'market_report': {
            'filename': 'market_report.md',
            'title': f'{stock_symbol} 股票技术分析报告',
            'state_key': 'market_report',
        },
        'value_report': {
            'filename': 'value_report.md',
            'title': '价值投资分析',
            'state_key': 'value_report',
        },
        'value_turtle_payload': {
            'filename': 'value_turtle_payload.json',
            'title': '价值投资 Turtle payload',
            'state_key': 'value_turtle_payload',
        },
        'investment_plan': {
            'filename': 'investment_plan.md',
            'title': f'{stock_symbol} 投资决策报告',
            'state_key': 'investment_plan',
        },
    }


# ---------------------------------------------------------------------------
# Tests for _save_report_modules_to_disk
# ---------------------------------------------------------------------------

class TestSaveReportModulesToDisk:
    """测试提取出的 helper _save_report_modules_to_disk。"""

    def _call_helper(self, state, reports_dir, stock_symbol="600519"):
        from app.services.simple_analysis_service import SimpleAnalysisService
        report_modules = _build_report_modules(stock_symbol)
        return SimpleAnalysisService._save_report_modules_to_disk(
            state, reports_dir, report_modules
        )

    def test_persist_writes_turtle_payload_when_present(self, tmp_path):
        """非空 payload → 写到 reports_dir/value_turtle_payload.json，内容 JSON 解析成功。"""
        payload = json.dumps(
            {"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}},
            ensure_ascii=False,
        )
        state = {
            "value_report": "# 模拟报告\n\n内容",
            "value_turtle_payload": payload,
            "market_report": "# 市场报告",
            "investment_plan": "",
        }

        saved = self._call_helper(state, tmp_path)

        out_file = tmp_path / "value_turtle_payload.json"
        assert out_file.exists(), "value_turtle_payload.json 应当被写出"
        parsed = json.loads(out_file.read_text(encoding="utf-8"))
        assert parsed["facts"]["status"] == "complete"
        assert "value_turtle_payload" in saved

    def test_persist_skips_empty_payload(self, tmp_path):
        """空字符串 payload → 不写文件。"""
        state = {
            "value_report": "# 模拟报告\n\n内容",
            "value_turtle_payload": "",
        }

        saved = self._call_helper(state, tmp_path)

        out_file = tmp_path / "value_turtle_payload.json"
        assert not out_file.exists(), "空 payload 不应写文件"
        assert "value_turtle_payload" not in saved

    def test_persist_skips_whitespace_only_payload(self, tmp_path):
        """纯空白字符 payload → 跳过。"""
        state = {
            "value_report": "# 模拟报告\n\n内容",
            "value_turtle_payload": "   \n\t  ",
        }

        saved = self._call_helper(state, tmp_path)

        out_file = tmp_path / "value_turtle_payload.json"
        assert not out_file.exists(), "纯空白 payload 不应写文件"
        assert "value_turtle_payload" not in saved

    def test_persist_skips_empty_value_report(self, tmp_path):
        """空 value_report 也应被短路（§6.5 普遍性改进）。"""
        state = {
            "value_report": "",
            "value_turtle_payload": "",
        }

        saved = self._call_helper(state, tmp_path)

        assert not (tmp_path / "value_report.md").exists(), "空 value_report 不应写文件"
        assert "value_report" not in saved

    def test_persist_writes_nonempty_value_report(self, tmp_path):
        """非空 value_report 正常写出。"""
        state = {
            "value_report": "# 价值分析\n\n## 摘要\n\n详细内容。",
            "value_turtle_payload": "",
        }

        saved = self._call_helper(state, tmp_path)

        out_file = tmp_path / "value_report.md"
        assert out_file.exists(), "非空 value_report 应被写出"
        assert "value_report" in saved

    def test_persist_absent_key_skipped(self, tmp_path):
        """state 中完全没有的 key 应被跳过，不报错。"""
        state = {
            "value_report": "# 报告",
            # value_turtle_payload 不在 state 中
        }

        saved = self._call_helper(state, tmp_path)

        assert not (tmp_path / "value_turtle_payload.json").exists()
        assert "value_turtle_payload" not in saved

    def test_persist_returns_dict_with_file_paths(self, tmp_path):
        """返回的 saved_files dict 值应是绝对路径字符串。"""
        payload = '{"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}}'
        state = {
            "market_report": "# 市场",
            "value_turtle_payload": payload,
        }

        saved = self._call_helper(state, tmp_path)

        for key, path_str in saved.items():
            assert Path(path_str).is_absolute(), f"{key} 的路径应是绝对路径"
            assert Path(path_str).exists(), f"{key} 文件应已存在"
