"""
Task 5: 测试 model_usage 持久化
验证 AnalysisResult 模型支持 model_usage 字段，以及序列化行为。
"""
from __future__ import annotations

import pytest
from typing import Any, Dict


MODEL_USAGE_SAMPLE: Dict[str, Any] = {
    "summary": {
        "total_calls": 1,
        "total_input_tokens": 10,
        "total_output_tokens": 4,
        "total_duration_seconds": 0.5,
        "costs_by_currency": {"CNY": 0.01},
    },
    "nodes": {
        "value_analyst": {
            "display_name": "价值投资分析",
            "provider": "codex",
            "model": "gpt-5.5",
            "providers": ["codex"],
            "models": ["gpt-5.5"],
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 4,
            "cost": 0.01,
            "currency": "CNY",
            "costs_by_currency": {"CNY": 0.01},
            "duration_seconds": 0.5,
            "partial": False,
            "partial_reason": None,
        }
    },
}


class TestAnalysisResultModelUsageField:
    """AnalysisResult.model_usage 字段的单元测试"""

    def test_analysis_result_has_model_usage_field(self):
        """AnalysisResult 应包含 model_usage 字段"""
        from app.models.analysis import AnalysisResult

        result = AnalysisResult(model_usage=MODEL_USAGE_SAMPLE)
        assert result.model_usage is not None
        assert result.model_usage == MODEL_USAGE_SAMPLE

    def test_analysis_result_model_usage_default_is_none(self):
        """AnalysisResult() 不传 model_usage 时默认为 None（向后兼容）"""
        from app.models.analysis import AnalysisResult

        result = AnalysisResult()
        assert result.model_usage is None

    def test_analysis_result_dict_contains_model_usage(self):
        """AnalysisResult.dict() 序列化后应包含 model_usage 键"""
        from app.models.analysis import AnalysisResult

        result = AnalysisResult(model_usage=MODEL_USAGE_SAMPLE)
        d = result.dict()
        assert "model_usage" in d
        assert d["model_usage"] == MODEL_USAGE_SAMPLE

    def test_analysis_result_dict_without_model_usage_is_backward_compat(self):
        """未传 model_usage 时，.dict() 仍应包含 model_usage 键（值为 None）"""
        from app.models.analysis import AnalysisResult

        result = AnalysisResult()
        d = result.dict()
        assert "model_usage" in d
        assert d["model_usage"] is None

    def test_analysis_result_model_usage_summary_accessible(self):
        """model_usage 内的 summary 子字段可访问"""
        from app.models.analysis import AnalysisResult

        result = AnalysisResult(model_usage=MODEL_USAGE_SAMPLE)
        assert result.model_usage["summary"]["total_calls"] == 1
        assert result.model_usage["summary"]["costs_by_currency"]["CNY"] == 0.01

    def test_analysis_result_model_usage_nodes_accessible(self):
        """model_usage 内的 nodes 子字段可访问"""
        from app.models.analysis import AnalysisResult

        result = AnalysisResult(model_usage=MODEL_USAGE_SAMPLE)
        node = result.model_usage["nodes"]["value_analyst"]
        assert node["provider"] == "codex"
        assert node["model"] == "gpt-5.5"
        assert node["partial"] is False

    def test_analysis_result_serializable_with_model_usage(self):
        """AnalysisResult 含 model_usage 时可被 JSON 序列化（无循环引用/不可序列化类型）"""
        import json
        from app.models.analysis import AnalysisResult

        result = AnalysisResult(model_usage=MODEL_USAGE_SAMPLE)
        serialized = result.json()  # Pydantic v1 风格；v2 用 model_dump_json()
        parsed = json.loads(serialized)
        assert parsed["model_usage"] is not None
        assert parsed["model_usage"]["summary"]["total_calls"] == 1

    def test_model_usage_extraction_from_state_dict(self):
        """模拟从 propagate 返回的 state dict 中提取 model_usage（纯函数逻辑测试）"""
        # 模拟 state dict（propagate 的第一个返回值）
        fake_state = {
            "final_trade_decision": "持有",
            "model_usage": MODEL_USAGE_SAMPLE,
            "performance_metrics": {},
        }

        # 与 analysis_service.py 实现一致的提取逻辑
        model_usage = (
            fake_state.get("model_usage", {}) if isinstance(fake_state, dict) else {}
        )

        assert model_usage == MODEL_USAGE_SAMPLE
        assert model_usage["summary"]["total_calls"] == 1

    def test_model_usage_extraction_from_non_dict_state(self):
        """state 非 dict 时，提取逻辑应安全返回空字典"""
        fake_state = None  # 异常情况

        model_usage = (
            fake_state.get("model_usage", {}) if isinstance(fake_state, dict) else {}
        )

        assert model_usage == {}

    def test_model_usage_missing_from_state_returns_empty(self):
        """state 中无 model_usage 键时，应返回空字典（向后兼容老报告）"""
        fake_state = {
            "final_trade_decision": "持有",
            # 无 model_usage 键
        }

        model_usage = (
            fake_state.get("model_usage", {}) if isinstance(fake_state, dict) else {}
        )

        assert model_usage == {}
