# PR #7 评审记录：Turtle v0.15 价值分析师 flow

- **PR**：[#7 feat: add Turtle v0.15 value analyst flow](https://github.com/xinghuolk/TradingAgents-CN/pull/7)
- **合并提交**：`ca6fa00`
- **作者**：xinghuolk
- **评审日期**：2026-05-21
- **规模**：+7,594 / -25，13 个生产文件 + 7 个测试模块 + 4 份文档

> **状态说明（2026-05-21 后置补注）**：本文所有 `file:line` 引用与"76 用例通过"等数字均基于合并提交 `ca6fa00` 的状态。修复决策已通过 Spec 1 落地（`docs/superpowers/specs/2026-05-21-turtle-correctness-fixes-design.md`）。本文保留为历史评审记录，**不再是当前执行指令**。

## 1. 变更概览

将旧版「穿透回报率」管线替换为 Turtle v0.15 价值投资 flow，核心改动：

- 新增 `tradingagents/dataflows/value_investment/turtle/` 包：`facts`、`report_adapter`、`market_adapter`、`calculations`、`decision`、`formatting` 六个模块。
- 新增 LangChain 工具 `prepare_turtle_analysis`（`tradingagents/tools/turtle_analysis_tool.py`），返回 `{facts, signals}` 的 JSON payload。
- `tradingagents/agents/analysts/value_analyst.py` 切换到 `prepare_turtle_analysis`；下一轮看到 ToolMessage 后用**无工具绑定**的 LLM 调用直接生成最终报告。
- `tradingagents/graph/trading_graph.py` 同步替换 `value` ToolNode；Toolkit 中保留 `get_value_investment_analysis` 用于向后兼容。
- `tradingagents/dataflows/providers/hk/hk_stock.py` 在 `get_stock_info` 中补充 `price` 与 `shares_outstanding`。
- 新增 7 个 unit-test 模块（76 用例通过）。

整体架构干净地分离了 **facts → 确定性 signals → LLM 决策** 三层，对 `complete / degraded / non_decisionable / unsupported` 状态有显式传播。frozen dataclass + `__post_init__` 的深拷贝**降低了外部 aliasing 风险**（虽然 `fields: dict` / `caveats: list` 仍是可变容器，并非深度不可变——构造与序列化时做防御性拷贝即可满足实际需要）；每个事实都带 `source_label`、`source_reference`、`reliability`、`caveat`，相比旧版是实质性升级。

## 2. 问题清单

### 2.1 🔴 `decision.py` 脱敏过度匹配，会破坏字段名

**位置**：`tradingagents/dataflows/value_investment/turtle/decision.py:11-37`

`_SOURCE_TEXT_REDACTIONS` 通过 `re.sub(re.escape(word), "[已省略]", text, flags=IGNORECASE)` 应用，**没有单词边界**。实测复现：

```
'buyback_amount'      → '[已省略]back_amount'    # "buy" 子串匹配
'shareholder_return'  → 'share[已省略]er_return' # "hold" 子串匹配
```

**影响**：`build_non_decisionable_report` 渲染 `missing_inputs` 时会出现乱码。当前 **仅在测试中触发**，且已经导出在 `__all__` 中。

> **澄清（2026-05-21）**：原版表述"一旦分析师命中 non_decisionable 就是默认 fallback"略过强——实际 `value_analyst.py` 的不可决策路径仍走 `build_turtle_decision_prompt` + LLM 调用（同一 prompt 链路），并不调用 `build_non_decisionable_report`。后者是导出的辅助函数 / 测试覆盖入口，bug 真实但运行时影响有限。Spec 1 §7.1 选择**完全删除** redaction（而非选择性保留），从根本消除 bug。

**修复思路**：identifier 与 enum 都是内部定义、不会泄露投资动作词汇，建议只对自由文本 caveat / source label 脱敏；或以白名单方式仅处理特定字段。中文词不能依赖 `\b`，需要按字段类别区分。

### 2.2 🟡 `facts.status` 在工具 payload 中被硬编码为 `"complete"`

**位置**：`tradingagents/tools/turtle_analysis_tool.py:62-69`

```python
facts = TurtleFacts(
    ...,
    status="complete",
    caveats=[*report.caveats, *market_facts.caveats],
)
```

而 `value_analyst.py` 的系统提示要求：*"若 facts.status 或 signals.status 为 non_decisionable，只能输出不可决策报告"*。实际只有 `signals.status` 会进入 `non_decisionable`（来自 `compute_turtle_signals`），facts 层的状态永远不反映 adapter 的降级，违反文档化契约。

**修复思路**：让 report/market adapter 自己上报 status，或基于 caveat 启发式推导 facts.status（"stale extraction" / "rf_rate invalid" → `degraded`，缺失关键 money 字段 → `non_decisionable`）。

> **2026-05-21 补注**：Spec 1 §2 / §4 已选定 **adapter-emitted status + `merge_status` 聚合** 路径——不维护"关键字段白名单"，per-formula 不可决策性由 `compute_turtle_signals` 单独判定，facts 层只表达"数据采集结果是否有阴影"。原版括号里的"缺失关键 money 字段 → `non_decisionable`"启发式被 Spec 1 拒绝（容易与 calculation 层的字段需求漂移）。详见 Spec 1 §3 决策 2 与 §4.3。

### 2.3 🟡 工具签名在 `agent_utils.py` 与 `turtle_analysis_tool.py` 之间漂移

- `Toolkit.prepare_turtle_analysis`：`company_name: Annotated[str, ...] = ""`
- `tools/turtle_analysis_tool.prepare_turtle_analysis`：`company_name: Annotated[str | None, ...] = None`

行为上有 `company_name or ticker` 兜底，但暴露给 LLM 的 schema 不一致。两边对齐为同一签名即可。

### 2.4 🟢 次要

- **HKD/USD 金额都用 `MoneyUnit = "yuan"`**（`report_adapter._field_unit`）：含义是"基础货币单位"，但字面量带 RMB 色彩。`to_hundred_million` 数学上没问题，纯粹是可读性问题，建议将字面量改为 `"base"`。
- **Caveat 重复风险**：`market_adapter.py` 总会追加 `DEFAULT_CHANNEL_CAVEAT`，同时可能追加 `tax_rate unknown for …`。需检查渲染出的 prompt 是否过噪。
- **`compute_turtle_signals` 复用 facts caveat 并就地修改**：返回值通过 `__post_init__` 深拷贝，对外安全；但内部把新 caveat 写进了来自 `_combined_caveats(facts)` 的本地 list，维护时注意。
- **`payout_anchor` 公式实质就是别名**：`payout_anchor = avg_payout_ratio_3y`，无额外语义，可考虑去掉。

## 3. 项目规范对齐

- ✅ 日志使用 `logging.getLogger(__name__)`（dataflows）与 `get_logger("value_investment")`（分析师），符合 CLAUDE.md。
- ✅ 中文用户面字符串保留（prompt、不可决策报告 caveat 文案）。
- ✅ 许可证边界正确——新文件全部在 `tradingagents/`（Apache 2.0）下，未触碰 `app/` / `frontend/`。
- ✅ 港股按需取数（符合 CLAUDE.md「HK and US data are on-demand + cached」），无新增定时同步。
- ✅ 测试位于 `tests/unit/`，pytest 可发现，未污染仓库根目录。
- ✅ smoke 脚本把捕获的 stdout 转写到 stderr，保持 stdout JSON 可解析。

## 4. 安全

- 无硬编码密钥 / API key。
- `_env_rf_rate` 校验数值，遇到非法输入抛 `ValueError`，不透传。
- 脱敏层（尽管有 2.1 的 bug）直觉是对的：避免投资动作词汇泄漏进确定性的不可决策报告。

## 5. 测试覆盖

calculations、decision、facts、report-adapter、market-adapter、value-analyst integration、entry 共 76 个用例通过。decision-builder 测试断言精确字符串输出，适合回归。

**缺口**：缺一个 **"不可决策报告不会把 `buyback_amount` 等含 `buy`/`hold` 子串的字段名弄乱"** 的回归测试 —— 这个用例本来就能抓到 2.1 的 bug。

## 6. 后续建议（follow-up）

| 优先级 | 事项 | 涉及文件 |
|--------|------|----------|
| P1 | 修复 `_safe_non_decision_text` identifier 子串误匹配 | `tradingagents/dataflows/value_investment/turtle/decision.py` |
| P1 | 真正派生 `facts.status`，不再硬编码 `"complete"` | `tradingagents/tools/turtle_analysis_tool.py`（可能需要扩展 report/market adapter 返回值） |
| P2 | 对齐 `company_name` 工具 schema | `tradingagents/agents/utils/agent_utils.py`、`tradingagents/tools/turtle_analysis_tool.py` |
| P2 | 补端到端的不可决策报告回归测试 | `tests/unit/test_turtle_decision.py` |
| P3 | 重命名 `MoneyUnit = "yuan"` 为 `"base"` | `tradingagents/dataflows/value_investment/turtle/facts.py`、`report_adapter.py` |
| P3 | 评估 `payout_anchor` 是否值得保留 | `tradingagents/dataflows/value_investment/turtle/calculations.py` |

## 7. 总评

架构层面的迁移做得扎实，事实/信号/决策三层分离清晰，测试覆盖到位。fallback 报告器中有一个真实 bug（2.1）、`facts.status` 存在契约漂移（2.2）。建议用小型 follow-up PR 处理，无需回滚。
