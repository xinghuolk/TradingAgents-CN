# Provider-Aware 模型选择设计

## 背景

承接 PR #24(`fix/global-model-dropdown`)。该 PR 把系统设置页的"快速分析模型/深度决策模型"下拉从"按 `default_provider` 过滤"改为"显示全部已启用模型",使用户能跨厂家选择模型。

codex bot 随后提出 P2:快速/深度分析模型在系统里**只存 `model_name`(纯字符串)**,不带 provider。当多个已启用厂家配置了**同名模型**(例如 OpenAI 官方、OpenRouter、302AI、自建端点都提供 `gpt-4o`)时,后端按"首个匹配 `model_name`"解析 provider/endpoint/key —— 用户选了"第二家的 gpt-4o",却会**静默使用第一家的 endpoint/API Key**。跨厂家同名在本项目中**确实会发生**,因此这是一个正确性问题,必须修复,且必须区分到具体厂家。

## 目标

让快速/深度模型选择**端到端携带 provider 信息**,使跨厂家同名模型解析到用户实际选择的厂家;同时**向后兼容**存量纯 `model_name` 配置(无需数据迁移)。

## 非目标

- 不改 `tradingagents/graph/setup.py` / `trading_graph.py` 的节点-模型角色分配,也不引入 `model_usage` 字段 —— 那属于并行分支 `codex/node-model-usage` 的范围(两者正交,见“与并行工作的边界”)。
- 不重构 `token_usage` 统计、不改 Token 统计页。
- 不引入数据库迁移脚本。

## 核心契约(贯穿全设计)

**单一匹配语义**,在所有解析点保持一致:

> 给定 `(model_name, provider)`:`provider` 非空 → 按 `(provider, model_name)` **精确匹配**(`provider` 大小写不敏感比较),跳过不同厂家的同名项;`provider` 为 `None`/空 → **回退到现有的“首个 `model_name` 匹配”**。

这一回退保证:存量只存 `model_name`(无 provider)的配置和请求,在新代码下行为与旧代码**完全一致**,无需迁移。

## 数据契约(存储 + 传输)

新增**独立可选字段**,`model_name` 字段不变:

- `app/models/analysis.py` 的 `AnalysisParameters` 新增:
  - `quick_analysis_provider: Optional[str] = None`
  - `deep_analysis_provider: Optional[str] = None`
- system_settings 同样支持读写这两个键(若存储/校验层对字段有白名单,需放行)。
- 前端 `api/analysis.ts` 的分析参数类型新增这两个可选字段。

后端始终以"两个独立字段(model + provider)"接收;复合键只是前端下拉选中态的内部表示(见“前端设计”)。

## 后端解析层(统一闸门)

把分散的"按 `model_name` 找配置/provider"的逻辑统一支持可选 `provider`。

### `app/services/simple_analysis_service.py`(LIVE 同步路径,用户点“分析”实际走此路)

- `get_provider_and_url_by_model_sync(model_name, provider=None)`:遍历 `llm_configs` 时,`provider` 非空则要求 `(provider, model_name)` 同时匹配,否则跳过;`provider` 为空保持"首个 `model_name` 匹配"。
- `get_provider_by_model_name(model_name, provider=None)`(异步)、`get_provider_by_model_name_sync(model_name, provider=None)`:透传 `provider`;`provider` 已显式指定且有效时可直接返回,无需反查。
- `create_analysis_config(..., quick_provider=None, deep_provider=None)`:新增两参,传给上述解析函数(522-523 处的 `get_provider_and_url_by_model_sync`)。
- 活路径(`_run_analysis_sync` 内,约 1259–1305):
  - 取 `request.parameters.quick_analysis_provider` / `deep_analysis_provider`(默认 `None`,在 if/else 前初始化以避免 `NameError`)。
  - 串入 1301–1302 的解析调用与 `create_analysis_config`。
  - **自动切换推荐模型时清空 provider**:当 `validate_model_pair` 不通过、`recommend_models_for_depth` 改写了 quick/deep model 时,把对应 provider 置 `None`,因为推荐模型未必属于用户原先选择的厂家,应回退反查。
- `config["quick_provider"]` / `config["deep_provider"]`(已存在,1315–1316)继续是混合模式的权威 provider —— 本设计让它们取值**更准确**,不改键名/结构(并行分支依赖它们)。

### `app/services/analysis_service.py`(worker 异步路径,被 `app/worker/analysis_worker.py` 调用)

- 取 `task.parameters.quick_analysis_provider` / `deep_analysis_provider`。
- 多处内联 `llm_config.get("model_name") == quick_model` 的循环(读取 max_tokens/temperature 等模型完整配置)与 `get_provider_by_model_name_sync(quick_model)` 调用,改用统一的匹配判据(`provider` 给了精确配对、没给回退)。**注意该文件有两个几乎重复的分析方法块,都要改**。

### `app/core/config_bridge.py`

- `bridge_deep_model_to_env`(及相关投射):若能拿到 `deep_provider` 则优先用它确定 provider 后再解析 backend_url,否则维持现状(按 `model_name` 反查)。

## 前端设计(三页)

**约束**:Element Plus `el-select` 的 `v-model` 绑定单一字符串,跨厂家同名时仅凭 `model_name` 无法区分选了哪家。

**方案**:option 的 value 用复合键 `${provider}::${model_name}`(仅作为下拉选中态的唯一键);提交时拆回 `{model, provider}` 两个独立字段发给后端(后端契约不变)。拆分时只按**第一个** `::` 分割(provider 名不含 `::`),规避 `model_name` 含特殊字符的边角风险。

同名模型**全部列出、按厂家区分**:label 显示模型名,副标题显示 `provider / model_name`。

### 系统设置 `frontend/src/views/Settings/ConfigManagement.vue`

- 两个下拉(587/609)的 option value 改为复合键;新增一个本地复合值绑定 v-model。
- 保存时拆成 `quick_analysis_model` + `quick_analysis_provider`(deep 同理)提交。
- **回填兼容**:加载存量设置时可能只有 `model_name`(无 provider)。在 `enabledModels` 中按 `model_name` 查找:唯一命中 → 用其 provider 组装复合键;多命中(同名)→ 优先 `default_provider`、再退第一条;组装出复合键回填以正确选中。
- `default_provider` 的 watch 维持 PR #24 的语义(仅当所选模型已不在已启用列表时才清空)。

### 单股 `frontend/src/views/Analysis/SingleAnalysis.vue` 与批量 `frontend/src/views/Analysis/BatchAnalysis.vue`

- `modelSettings` 新增 `quickAnalysisProvider` / `deepAnalysisProvider`。
- 下拉(含 `DeepModelSelector` / `ModelConfig.vue` 透传)用复合 value。
- 提交 payload(SingleAnalysis 约 962–963 处)加 `quick_analysis_provider` / `deep_analysis_provider`。
- 自动推荐(`modelRecommendation` 回填,约 2177–2178)时一并带上 provider。
- 回填兼容逻辑同系统设置页。

## 测试

### 后端单测(新增 `tests/unit/`)

- 匹配语义:`provider` 精确匹配命中正确厂家;同名不同厂家选对;`provider=None` 回退首个匹配;找不到时返回合理默认。
- 向后兼容:纯 `model_name`(无 provider)的存量请求在新代码下行为与旧代码一致。
- worker 路径:内联模型配置读取在带/不带 provider 时分别取对配置。
- 全量 `pytest tests/unit/`,区分预存失败 vs 本次新引入。

### 前端

- `yarn type-check` 基线对比(改动前后各跑一次;该仓库有约 242 个预存 type 错误),确认改动文件不新增 type 错误。

### 诚实边界

单测 mock transport,**真实跨厂家同名调用仍需部署后手测**:配置两家都提供 `gpt-4o`,各选一家,验证请求打到正确的 endpoint/API Key。该限制写入 PR 说明。

## 与并行工作的边界(`codex/node-model-usage`)

并行分支 `codex/node-model-usage`(目前仅 spec、无代码)负责"记录每个节点实际使用的 provider/model/token/成本/耗时"并展示,主战场在 `tradingagents/graph/`(节点/LLM 包装)与 `ReportDetail.vue`。

两者正交:本设计在"分析前**选对** provider",对方在"分析中/后**记录**用量"。对方依赖 `config["quick_provider"]` / `deep_provider`,本设计只让其取值更准、不改键名结构。本设计**不触碰** graph 节点角色与 `model_usage` 字段。可独立并行、各自合并;两者均合入后做一次端到端验证(provider 选对 + 记账正确)。

## 提交与回滚

- 在 PR #24 分支 `fix/global-model-dropdown` 上叠加,分提交:后端一提交、前端一提交,便于审查。
- 向后兼容、无破坏性迁移;回滚即还原相关文件,存量配置不受影响。
