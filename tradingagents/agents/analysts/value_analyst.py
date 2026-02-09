# -*- coding: utf-8 -*-
"""
价值投资分析师

基于「龟龟投资」穿透回报率模型，提供：
- 穿透回报率分析（分红 + 回购）
- 现金健康度评估
- 5维健康评分
- 价值投资建议
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.utils.tool_logging import log_analyst_module
from tradingagents.utils.logging_init import get_logger

logger = get_logger("value_investment")


def create_value_analyst(llm, toolkit):
    """
    创建价值投资分析师节点

    Args:
        llm: 语言模型实例
        toolkit: 工具集（包含价值投资工具）

    Returns:
        价值投资分析师节点函数
    """

    @log_analyst_module("value")
    def value_analyst_node(state):
        logger.debug("📊 [DEBUG] ===== 价值投资分析师节点开始 =====")

        # 工具调用计数器 - 防止无限循环
        messages = state.get("messages", [])
        tool_message_count = sum(1 for msg in messages if isinstance(msg, ToolMessage))

        tool_call_count = state.get("value_tool_call_count", 0)
        max_tool_calls = 1  # 一次工具调用获取所有数据

        if tool_message_count > tool_call_count:
            tool_call_count = tool_message_count
            logger.info(f"🔧 [工具调用计数] 更新计数器: {tool_call_count}")

        logger.info(f"🔧 [工具调用计数] 当前: {tool_call_count}/{max_tool_calls}")

        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"📊 [价值投资分析师] 开始分析: {ticker}")

        # 获取市场信息
        from tradingagents.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        logger.info(f"📊 [价值投资分析师] 市场类型: {market_info['market_name']}")

        # 确定市场类型
        if market_info['is_china']:
            market_type = "A"
        elif market_info['is_hk']:
            market_type = "HK"
        else:
            market_type = "US"
            logger.warning(f"⚠️ [价值投资分析师] 美股暂不支持价值投资分析")
            return {
                "value_report": f"美股 {ticker} 暂不支持穿透回报率分析",
                "value_tool_call_count": tool_call_count
            }

        # 获取公司名称
        company_name = _get_company_name(ticker, market_info)

        # 使用价值投资工具
        tools = [toolkit.get_value_investment_analysis]

        # 系统提示
        system_message = f"""
你是一位专业的价值投资分析师，专注于「穿透回报率」分析模型。

⚠️ 强制要求：你必须调用 get_value_investment_analysis 工具获取真实分析数据！

任务：分析 {company_name}（股票代码：{ticker}，{market_info['market_name']}）的价值投资潜力

🔴 立即调用 get_value_investment_analysis 工具
参数：ticker='{ticker}', market='{market_type}'

📊 分析要点：
1. 穿透回报率 = 分红收益率 + 回购收益率
2. 现金健康度（稳健/健康/警示/不健康）
3. 5维健康评分（盈利、偿债、成长、运营、现金流）
4. 健康标签（high_roe, stable_dividend 等）
5. 基于以上数据的价值投资建议

🌍 语言要求：
- 所有分析内容使用中文
- 投资建议使用中文：适合长期持有、谨慎持有、不建议持有

🚫 严格禁止：
- 不允许假设或编造数据
- 不允许不调用工具直接回答
- 不允许使用英文术语

现在立即调用工具！
"""

        system_prompt = """
🔴 强制要求：必须调用工具获取真实数据！
🚫 绝对禁止：不允许假设、编造或直接回答！

工作流程：
1. 如果消息历史中没有 ToolMessage，立即调用 get_value_investment_analysis 工具
2. 如果已有 ToolMessage，🚨 绝对禁止再次调用工具！
3. 收到工具数据后，生成完整的价值投资分析报告

可用工具：{tool_names}
当前日期：{current_date}
分析目标：{company_name}（{ticker}）

{system_message}
"""

        # 创建提示模板
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])

        # 填充参数
        tool_names = []
        for tool in tools:
            if hasattr(tool, 'name'):
                tool_names.append(tool.name)
            elif hasattr(tool, '__name__'):
                tool_names.append(tool.__name__)
            else:
                tool_names.append(str(tool))

        prompt = prompt.partial(
            system_message=system_message,
            tool_names=", ".join(tool_names),
            current_date=current_date,
            ticker=ticker,
            company_name=company_name
        )

        # 创建 LLM 链
        try:
            chain = prompt | llm.bind_tools(tools)
            logger.info(f"📊 [价值投资分析师] ✅ 工具绑定成功")
        except Exception as e:
            logger.error(f"📊 [价值投资分析师] ❌ 工具绑定失败: {e}")
            raise e

        # 调用 LLM
        logger.info(f"📊 [价值投资分析师] 开始调用 LLM...")

        try:
            result = chain.invoke({"messages": state["messages"]})
            logger.info(f"📊 [价值投资分析师] LLM 返回类型: {type(result)}")

            # 处理结果
            if isinstance(result, AIMessage):
                # 检查是否有工具调用
                if hasattr(result, 'tool_calls') and result.tool_calls:
                    logger.info(f"📊 [价值投资分析师] 检测到工具调用: {len(result.tool_calls)} 个")
                    return {
                        "messages": [result],
                        "value_tool_call_count": tool_call_count + 1
                    }
                else:
                    # 直接返回报告
                    report_content = result.content if result.content else ""
                    logger.info(f"📊 [价值投资分析师] 生成报告，长度: {len(report_content)}")
                    return {
                        "value_report": report_content,
                        "value_tool_call_count": tool_call_count
                    }
            else:
                return {
                    "value_report": str(result),
                    "value_tool_call_count": tool_call_count
                }

        except Exception as e:
            logger.error(f"❌ [价值投资分析师] LLM 调用失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "value_report": f"价值投资分析失败: {str(e)}",
                "value_tool_call_count": tool_call_count
            }

    return value_analyst_node


def _get_company_name(ticker: str, market_info: dict) -> str:
    """获取公司名称"""
    try:
        if market_info['is_china']:
            from tradingagents.dataflows.interface import get_china_stock_info_unified
            stock_info = get_china_stock_info_unified(ticker)

            if stock_info and "股票名称:" in stock_info:
                return stock_info.split("股票名称:")[1].split("\n")[0].strip()

            return f"股票{ticker}"

        elif market_info['is_hk']:
            clean_ticker = ticker.replace('.HK', '').replace('.hk', '')
            return f"港股{clean_ticker}"

        else:
            return f"股票{ticker}"

    except Exception as e:
        logger.error(f"获取公司名称失败: {e}")
        return f"股票{ticker}"
