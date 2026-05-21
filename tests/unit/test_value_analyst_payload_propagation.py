# -*- coding: utf-8 -*-
"""Spec 1 §6.3: value_analyst_node 所有写 value_report 的 return 路径都带 value_turtle_payload。"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage


class TestPayloadPropagation:
    """所有写 value_report 的 return 路径都必须带 value_turtle_payload key（即使值为空串）。"""

    @pytest.fixture
    def base_state(self):
        return {
            "company_of_interest": "600519",
            "trade_date": "2026-05-19",
            "messages": [],
            "value_tool_call_count": 0,
        }

    @pytest.fixture
    def us_state(self, base_state):
        state = dict(base_state)
        state["company_of_interest"] = "AAPL"
        return state

    def _make_market_info(self, is_china=False, is_hk=False, market_name="A股"):
        return {
            "is_china": is_china,
            "is_hk": is_hk,
            "market_name": market_name,
        }

    # ------------------------------------------------------------------
    # Path 1: US market unsupported
    # ------------------------------------------------------------------

    def test_us_market_unsupported_returns_empty_payload(self, us_state):
        """美股 unsupported 路径：value_report 是 unsupported 说明，value_turtle_payload 为空串。"""
        from tradingagents.agents.analysts.value_analyst import create_value_analyst

        llm = MagicMock()
        toolkit = MagicMock()
        node = create_value_analyst(llm, toolkit)

        us_market_info = self._make_market_info(is_china=False, is_hk=False, market_name="美股")

        with patch("tradingagents.utils.stock_utils.StockUtils.get_market_info", return_value=us_market_info):
            result = node(us_state)

        assert "value_report" in result
        assert "美股" in result["value_report"] or "AAPL" in result["value_report"]
        assert "value_turtle_payload" in result
        assert result["value_turtle_payload"] == ""

    # ------------------------------------------------------------------
    # Path 2: Turtle success
    # ------------------------------------------------------------------

    def test_turtle_success_returns_original_payload(self, base_state):
        """成功路径：从 ToolMessage 拿 payload，作为 value_turtle_payload 返回。"""
        payload_json = (
            '{"facts": {"context": {"ticker": "600519", "market": "A", "trade_date": "2026-05-19",'
            ' "period_end": "2025-12-31", "holding_channel": "A", "company_name": "贵州茅台"},'
            ' "report": {"fields": {}, "metadata": {}, "caveats": []}, "market": {"fields": {}, "caveats": []},'
            ' "status": "complete", "caveats": []},'
            ' "signals": {"status": "complete", "results": {}, "veto_reasons": [], "caveats": []}}'
        )
        base_state["messages"] = [
            ToolMessage(
                content=payload_json,
                name="prepare_turtle_analysis",
                tool_call_id="x",
            ),
        ]

        from tradingagents.agents.analysts.value_analyst import create_value_analyst

        llm = MagicMock()
        llm.invoke.return_value = AIMessage(content="# Turtle 报告\n\n分析内容")
        toolkit = MagicMock()
        node = create_value_analyst(llm, toolkit)

        cn_market_info = self._make_market_info(is_china=True, market_name="A股")
        with patch("tradingagents.utils.stock_utils.StockUtils.get_market_info", return_value=cn_market_info), \
             patch("tradingagents.agents.analysts.value_analyst._get_company_name", return_value="贵州茅台"):
            result = node(base_state)

        assert "value_report" in result
        assert "value_turtle_payload" in result
        assert result["value_turtle_payload"] == payload_json

    # ------------------------------------------------------------------
    # Path 3: Turtle exception
    # ------------------------------------------------------------------

    def test_turtle_exception_still_returns_payload(self, base_state):
        """异常路径：报告生成失败但 payload 已拿到，仍透传给状态。"""
        payload_json = (
            '{"facts": {"context": {"ticker": "600519", "market": "A", "trade_date": "2026-05-19",'
            ' "period_end": "2025-12-31", "holding_channel": "A", "company_name": "贵州茅台"},'
            ' "report": {"fields": {}, "metadata": {}, "caveats": []}, "market": {"fields": {}, "caveats": []},'
            ' "status": "complete", "caveats": []},'
            ' "signals": {"status": "complete", "results": {}, "veto_reasons": [], "caveats": []}}'
        )
        base_state["messages"] = [
            ToolMessage(
                content=payload_json,
                name="prepare_turtle_analysis",
                tool_call_id="x",
            ),
        ]

        from tradingagents.agents.analysts.value_analyst import create_value_analyst

        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("LLM 调用失败")
        toolkit = MagicMock()
        node = create_value_analyst(llm, toolkit)

        cn_market_info = self._make_market_info(is_china=True, market_name="A股")
        with patch("tradingagents.utils.stock_utils.StockUtils.get_market_info", return_value=cn_market_info), \
             patch("tradingagents.agents.analysts.value_analyst._get_company_name", return_value="贵州茅台"):
            result = node(base_state)

        assert "value_report" in result
        assert "失败" in result["value_report"]
        assert "value_turtle_payload" in result
        assert result["value_turtle_payload"] == payload_json

    # ------------------------------------------------------------------
    # Path 4: ToolNode intermediate state (no value_report written — should NOT have payload key forced)
    # ------------------------------------------------------------------

    def test_toolnode_intermediate_does_not_write_value_report(self, base_state):
        """ToolNode 中间态不写 value_report，state merge 保留旧值。"""
        from tradingagents.agents.analysts.value_analyst import create_value_analyst
        from langchain_core.runnables import RunnableLambda

        tool_call_result = AIMessage(
            content="",
            tool_calls=[{"name": "prepare_turtle_analysis", "args": {}, "id": "tc1", "type": "tool_call"}],
        )

        # Build a proper LangChain-compatible bound LLM mock that returns tool_call_result
        bound_llm = RunnableLambda(lambda _input: tool_call_result)

        llm = MagicMock()
        llm.bind_tools.return_value = bound_llm

        toolkit = MagicMock()
        # Give the tool a string .name attribute so the node can join it
        mock_tool = MagicMock()
        mock_tool.name = "prepare_turtle_analysis"
        toolkit.prepare_turtle_analysis = mock_tool
        node = create_value_analyst(llm, toolkit)

        cn_market_info = self._make_market_info(is_china=True, market_name="A股")
        with patch("tradingagents.utils.stock_utils.StockUtils.get_market_info", return_value=cn_market_info), \
             patch("tradingagents.agents.analysts.value_analyst._get_company_name", return_value="贵州茅台"):
            result = node(base_state)

        # ToolNode path returns messages only — no value_report, no value_turtle_payload
        assert "value_report" not in result
        assert "messages" in result
