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

**两级回退匹配语义**,在所有解析点保持一致。provider 是**优先约束、不是硬约束**:

> 给定 `(model_name, provider)`:
> 1. `provider` 非空 → 先按 `(provider, model_name)` **精确匹配**(`provider` 与 `llm_configs[].provider` 做大小写不敏感比较),命中即用。
> 2. 第 1 步未命中(provider 为空,或精确对在 `llm_configs` 中不存在 —— 如模型被禁用、厂家被改名、存量 provider 字符串漂移)→ **回退到“首个 `model_name` 匹配”**(等价旧逻辑)。
> 3. 仍未命中 → 落到现有默认映射(`_get_default_provider_by_model`,最终硬编码 `dashscope`)。

**关键**:绝不能"provider 非空且精确未命中就直接跳到第 3 步默认映射"。那会让本来按 model_name 能匹配到可用配置的存量场景,反而 fall through 到 dashscope —— **比旧逻辑更差**。两级回退确保:这次改动对存量配置**永不劣化**,只在"精确对存在"时才更精确。

匹配命中后,用 `llm_configs` 里**存储的原始 provider 值**(而非用户传入的 provider 字符串)去查 `llm_providers` 取 backend_url/key,规避大小写漂移导致厂家层 `find_one({"name": provider})` miss。

这一两级回退保证:存量只存 `model_name`(无 provider)的配置和请求,在新代码下行为与旧代码**完全一致**,无需迁移。

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
  - 串入解析调用(约 1281–1282 的 `get_provider_and_url_by_model_sync`)与 `create_analysis_config`。
  - **自动切换/推荐模型时用推荐附带的 provider**(见下条),而非简单置 `None`。
- `config["quick_provider"]` / `config["deep_provider"]`(已存在,约 1315–1316)继续是混合模式的权威 provider —— 本设计让它们取值**更准确**,不改键名/结构(并行分支依赖它们)。

### `app/services/model_capability_service.py`(推荐产出端,补全 provider)

根因:`recommend_models_for_depth` 当前返回 `Tuple[str, str]`(纯 model_name),而它选中的候选对象 `quick_candidates[0]` / `deep_candidates[0]` **本身带 `.provider`,却被丢弃**。下游因此只能"按名字猜厂家",推荐出的同名模型会再次踩坑。

- `recommend_models_for_depth` 改为返回 `(quick_model, quick_provider, deep_model, deep_provider)`(候选对象的 `.provider` 直接带出);`_get_default_models` 同步返回 provider(默认映射可为 None,走两级回退)。
- 调用方(live 路径自动切换处、worker、以及 `ModelRecommendationResponse` 推荐接口)接收并透传 provider。
- `_get_default_provider_by_model` 之类的 capability 内部按 model_name 读能力等级/features 的匹配**不改**(跨厂家同名时能力属性一致,选错同名项对能力判断无影响)。
- 后端 `ModelRecommendationResponse`(`app/routers/model_capabilities.py`)新增 `quick_provider` / `deep_provider` 字段,供前端推荐回填携带 provider。

### `app/services/analysis_service.py`(worker 异步路径,被 `app/worker/analysis_worker.py` 调用)

该文件有**三个**独立的分析执行方法,各自带一套 model-config 内联读取块 + provider 反查,**三处都要改**:

- `_execute_analysis_sync_with_progress`(约 163;内联块 190–261,反查 240)
- `_execute_analysis_sync`(约 317;内联块 335–398,反查 380)
- `execute_analysis_task`(异步,约 708;`model_name ==` 在 734/744,反查 754,用 `await get_provider_by_model_name(quick_model)`)

每处:取 `task.parameters.quick_analysis_provider` / `deep_analysis_provider`;内联 `llm_config.get("model_name") == quick_model` 的循环与 `get_provider_by_model_name[_sync]` 调用改用统一两级回退判据(`provider` 给了精确配对、未命中回退首个匹配)。

### `app/core/config_bridge.py`

- 实际函数名是 `bridge_deep_llm_role_to_env`(约第 27 行)。其 `deep_model` 来自**全局** `unified_config.get_deep_analysis_model()`(供 financial-report-extractor 复用),不是某次请求的 task 参数 —— 此处没有 per-request 的 deep_provider。
- 方案:provider 取自 system_settings 里新存的全局 `deep_analysis_provider`(若有),传给两级回退解析器;否则维持现状(按 model_name 反查)。因解析器已是两级回退,即便不传 provider 也**不会比现状更差**,故此处可作为低优先项。

## 前端设计(三页)

**约束**:Element Plus `el-select` 的 `v-model` 绑定单一字符串,跨厂家同名时仅凭 `model_name` 无法区分选了哪家。

**方案**:option 的 value 用复合键 `${provider}::${model_name}`(仅作为下拉选中态的唯一键);提交时拆回 `{model, provider}` 两个独立字段发给后端(后端契约不变)。拆分时只按**第一个** `::` 分割(provider 名不含 `::`),规避 `model_name` 含特殊字符的边角风险。

同名模型**全部列出、按厂家区分**:label 显示模型名,副标题显示 `provider / model_name`。

**子组件必须改内部 `:value`(不止父组件 split)**:`DeepModelSelector.vue`、`ModelConfig.vue` 的 `el-option` 当前 `:value="model.model_name"`(裸名),且 `DeepModelSelector` 的 `:key` 也只用 `model_name`(已是潜在重复 key)。要让复合 value 的选中态对得上,**子组件 option 的 `:value` 与 `:key` 都要改成复合键**,否则 v-model 选中复合值却找不到对应 option。可沿用现有单字符串 v-model 透传复合值、由父组件拆分(无需新增 emit)。

**回填的数据来源(三页都缺 provider,首次回填必然按名字猜)**:后端目前对"已存模型选择"只返回裸 `model_name` —— 系统设置走 `getSystemSettings`(无 provider),单股/批量走 `getDefaultModels`/默认模型接口(同样无 provider)。因此**首次回填只能按 `model_name` 在 `enabledModels` 里猜厂家**;一旦用户在带厂家区分的下拉里确认并保存,provider 即被显式持久化,后续回填不再靠猜。UI 上多命中时必须有明确的默认选中项(优先 `default_provider`、再第一条),避免"猜测"被静默固化成错误的显式 provider。

### 系统设置 `frontend/src/views/Settings/ConfigManagement.vue`

- 两个下拉(587/609)的 option value 改为复合键;新增一个本地复合值绑定 v-model。
- 保存时拆成 `quick_analysis_model` + `quick_analysis_provider`(deep 同理)提交。
- **回填兼容**:加载存量设置时可能只有 `model_name`(无 provider)。在 `enabledModels` 中按 `model_name` 查找:唯一命中 → 用其 provider 组装复合键;多命中(同名)→ 优先 `default_provider`、再退第一条;组装出复合键回填以正确选中。
- `default_provider` 的 watch 维持 PR #24 的语义(仅当所选模型已不在已启用列表时才清空)。

### 单股 `frontend/src/views/Analysis/SingleAnalysis.vue` 与批量 `frontend/src/views/Analysis/BatchAnalysis.vue`

- `modelSettings` 新增 `quickAnalysisProvider` / `deepAnalysisProvider`。
- 下拉(含 `DeepModelSelector` / `ModelConfig.vue` 透传)用复合 value。
- 提交 payload(SingleAnalysis 约 962–963 处)加 `quick_analysis_provider` / `deep_analysis_provider`。
- 自动推荐回填(`applyRecommendedModels`,SingleAnalysis 约 2175 / `ModelConfig.vue` 约 294):从扩展后的 `ModelRecommendationResponse` 取 `quick_provider` / `deep_provider` 一并写入 `modelSettings`,**不再靠名字猜**。
- 回填兼容逻辑同系统设置页(两页"已存值"来源 `getDefaultModels`/默认模型接口同样无 provider,首次回填按名字猜,规则一致)。

## 测试

### 后端单测(新增 `tests/unit/`)

- 匹配语义:`provider` 精确匹配命中正确厂家;两家同名(如 openai 与 openrouter 都有 `gpt-4o`)各选一家时分别选对。
- **两级回退(防劣化,最关键用例)**:`provider` 非空但 `(provider, model_name)` 精确对**不存在**于 `llm_configs`(模型禁用/厂家改名/provider 漂移)时,必须回退到"首个 `model_name` 匹配"而**不是**直接落 dashscope 默认 —— 断言此场景与旧逻辑(`provider=None`)结果一致,证明永不劣化。
- `provider=None`(存量)回退首个匹配,与旧代码逐字段一致。
- 大小写漂移:用户传入 provider 与 `llm_configs[].provider` 大小写不同时仍精确命中,且命中后用**存储的原始 provider 值**查 `llm_providers`。
- 推荐路径:`recommend_models_for_depth` 返回的 provider 等于所选候选的 `.provider`(不再丢弃)。
- worker 三个方法块:内联模型配置读取在带/不带 provider 时分别取对配置。
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
