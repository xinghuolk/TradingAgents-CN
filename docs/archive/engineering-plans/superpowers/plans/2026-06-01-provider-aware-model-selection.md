# Provider-Aware 模型选择(P2 修复)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让快速/深度分析模型选择端到端携带 provider,使跨厂家同名模型(如多家都配 `gpt-4o`)解析到用户实际选择的厂家,且向后兼容存量纯 `model_name` 配置(无迁移、永不劣化)。

**Architecture:** 后端核心解析器改为**两级回退**(precise `(provider, model_name)` → `model_name`-only → 默认映射);新增可选 `quick/deep_analysis_provider` 字段贯穿 schema、live 路径(`simple_analysis_service`)、worker 三方法(`analysis_service`)、推荐产出端(`model_capability_service` + 推荐接口);前端三页下拉用复合键 `provider::model_name` 携带 provider、提交时拆分、回填兼容存量裸名。

**Tech Stack:** FastAPI + Pydantic(后端)、pytest(后端测试)、Vue 3 + Element Plus + TypeScript(前端)。

**分支:** 在 `fix/global-model-dropdown`(PR #24)上叠加。所有后端提交一组、前端一组,便于审查。

**关键不变量(每个 task 都要守住):**
- provider 是**优先约束、非硬约束**:精确未命中必须回退 model_name 匹配,绝不直接跳默认映射(否则比旧逻辑更差)。
- 不传 provider 时,所有解析点行为与改动前**逐字段一致**。
- 不碰 `tradingagents/graph/` 节点角色与 `model_usage`(并行分支 `codex/node-model-usage` 的范围)。

---

## 文件结构

**后端(修改):**
- `app/services/simple_analysis_service.py` — 核心解析器两级回退 + live 路径透传 provider。**所有解析点的单一真相源。**
- `app/models/analysis.py` — `AnalysisParameters` 加两个可选 provider 字段。
- `app/services/model_capability_service.py` — 新增带 provider 的推荐方法(不破坏旧方法)。
- `app/routers/model_capabilities.py` — 推荐接口响应加 provider 字段。
- `app/services/analysis_service.py` — worker 三个方法块透传 provider。
- `app/core/config_bridge.py` — (低优先)deep provider 来自全局设置。

**后端(新增测试):**
- `tests/unit/test_provider_aware_resolution.py` — 两级回退、向后兼容、大小写漂移、推荐带 provider。

**前端(修改):**
- `frontend/src/api/analysis.ts` — 类型加可选 provider 字段。
- `frontend/src/views/Settings/ConfigManagement.vue` — 系统设置两下拉复合键 + 提交拆分 + 回填。
- `frontend/src/views/Analysis/SingleAnalysis.vue` — 单股下拉复合键 + payload + 推荐回填带 provider。
- `frontend/src/views/Analysis/BatchAnalysis.vue` — 批量下拉复合键 + payload。
- `frontend/src/views/Settings/components/ModelConfig.vue` — 子组件 option `:value`/`:key` 复合键。
- `frontend/src/views/Analysis/components/DeepModelSelector.vue`(若路径不同以实际为准) — 同上。

---

## 后端

### Task 1: 核心解析器两级回退(simple_analysis_service)

**Files:**
- Modify: `app/services/simple_analysis_service.py`(`get_provider_and_url_by_model_sync` 定义约 101,匹配循环 127-128;异步 `get_provider_by_model_name` 约 54;同步反查 `get_provider_by_model_name_sync` 约 87)
- Test: `tests/unit/test_provider_aware_resolution.py`

- [ ] **Step 1: 写失败测试 —— 两级回退语义**

新建 `tests/unit/test_provider_aware_resolution.py`:

```python
"""Provider-aware 模型解析:两级回退(precise → model_name → default)。"""
from app.services.simple_analysis_service import _match_llm_config


def _cfgs():
    return [
        {"model_name": "gpt-4o", "provider": "openai", "api_base": "https://api.openai.com/v1"},
        {"model_name": "gpt-4o", "provider": "openrouter", "api_base": "https://openrouter.ai/api/v1"},
        {"model_name": "qwen-turbo", "provider": "dashscope", "api_base": None},
    ]


def test_precise_match_picks_correct_provider():
    cfg = _match_llm_config(_cfgs(), "gpt-4o", "openrouter")
    assert cfg is not None and cfg["provider"] == "openrouter"


def test_precise_match_case_insensitive():
    cfg = _match_llm_config(_cfgs(), "gpt-4o", "OpenRouter")
    assert cfg is not None and cfg["provider"] == "openrouter"


def test_provider_none_falls_back_to_first_model_name_match():
    cfg = _match_llm_config(_cfgs(), "gpt-4o", None)
    assert cfg is not None and cfg["provider"] == "openai"  # 首个匹配


def test_precise_miss_falls_back_to_model_name_not_default():
    # provider 给了但该 (provider, model) 不存在 → 回退 model_name 匹配,而不是返回 None
    cfg = _match_llm_config(_cfgs(), "gpt-4o", "ghost-provider")
    assert cfg is not None and cfg["provider"] == "openai"  # 永不劣化


def test_no_match_returns_none():
    assert _match_llm_config(_cfgs(), "no-such-model", "openai") is None
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `.venv/bin/pytest tests/unit/test_provider_aware_resolution.py -q`
Expected: FAIL — `ImportError: cannot import name '_match_llm_config'`

- [ ] **Step 3: 实现 `_match_llm_config` 辅助函数**

在 `app/services/simple_analysis_service.py` 中,`get_provider_and_url_by_model_sync` 定义之前加入模块级函数:

```python
def _match_llm_config(llm_configs, model_name, provider=None):
    """从 llm_configs 中按两级回退匹配模型配置。

    1. provider 非空 → 先按 (provider, model_name) 精确匹配(provider 大小写不敏感)。
    2. 第 1 步未命中(provider 为空 / 精确对不存在)→ 回退首个 model_name 匹配(等价旧逻辑)。
    3. 仍未命中 → 返回 None(由调用方落默认映射)。

    provider 是优先约束、非硬约束:精确未命中绝不直接返回 None,
    必须回退 model_name 匹配,确保对存量配置永不劣化。
    """
    same_name = [c for c in llm_configs if c.get("model_name") == model_name]
    if not same_name:
        return None
    if provider:
        for c in same_name:
            if str(c.get("provider") or "").lower() == provider.lower():
                return c
    # 回退:首个 model_name 匹配
    return same_name[0]
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `.venv/bin/pytest tests/unit/test_provider_aware_resolution.py -q`
Expected: PASS(5 passed)

- [ ] **Step 5: 用 `_match_llm_config` 改写 `get_provider_and_url_by_model_sync` 的匹配循环**

把 `app/services/simple_analysis_service.py` 约 101 的函数签名改为 `def get_provider_and_url_by_model_sync(model_name: str, provider: str = None) -> dict:`,并把约 124-128 的:

```python
        if doc and "llm_configs" in doc:
            llm_configs = doc["llm_configs"]

            for config_dict in llm_configs:
                if config_dict.get("model_name") == model_name:
                    provider = config_dict.get("provider")
                    api_base = config_dict.get("api_base")
```

改为(注意:命中后用配置里**存储的原始 provider** 覆盖局部变量,后续 `find_one({"name": provider})` 用它):

```python
        if doc and "llm_configs" in doc:
            llm_configs = doc["llm_configs"]
            config_dict = _match_llm_config(llm_configs, model_name, provider)
            if config_dict is not None:
                    provider = config_dict.get("provider")  # 用存储的原始 provider 值,规避大小写漂移
                    api_base = config_dict.get("api_base")
```

> 注意:原代码此处是 `for ... if ...:` 双层缩进,函数体在 `if` 内。改为单个 `if config_dict is not None:` 后,**保持原函数体的缩进层级不变**(原 `provider = ...` 那段继续留在 `if` 块内,直到 `return {...}`)。只是把"循环+条件"换成"一次匹配+条件",函数体一字不改。实现时通读到该分支的 `return` 与 `client.close()`,确保缩进闭合。

- [ ] **Step 6: 给异步/同步反查版加 provider 透传**

`get_provider_by_model_name`(异步,约 54)签名加 `provider: str = None`,函数体开头加:

```python
        # provider 已显式指定且有效时直接返回,无需反查
        if provider:
            return provider
```

`get_provider_by_model_name_sync`(约 87)签名加 `provider: str = None`,函数体改为 `provider_info = get_provider_and_url_by_model_sync(model_name, provider)`。

- [ ] **Step 7: 运行解析测试 + import 冒烟**

Run: `.venv/bin/pytest tests/unit/test_provider_aware_resolution.py -q && .venv/bin/python -c "import app.services.simple_analysis_service; print('OK')"`
Expected: PASS + `OK`

- [ ] **Step 8: Commit**

```bash
git add app/services/simple_analysis_service.py tests/unit/test_provider_aware_resolution.py
git commit -m "feat(model-select): 两级回退解析器 _match_llm_config + provider 透传"
```

---

### Task 2: schema 新增可选 provider 字段

**Files:**
- Modify: `app/models/analysis.py:52-53`

- [ ] **Step 1: 加字段**

把 `app/models/analysis.py` 的:

```python
    # 模型配置
    quick_analysis_model: Optional[str] = "qwen-turbo"
    deep_analysis_model: Optional[str] = "qwen-max"
```

改为:

```python
    # 模型配置
    quick_analysis_model: Optional[str] = "qwen-turbo"
    deep_analysis_model: Optional[str] = "qwen-max"
    # 模型所属厂家(可选):区分跨厂家同名模型;None 时后端按 model_name 两级回退
    quick_analysis_provider: Optional[str] = None
    deep_analysis_provider: Optional[str] = None
```

- [ ] **Step 2: 验证旧文档反序列化不破坏**

Run:
```bash
.venv/bin/python -c "
from app.models.analysis import AnalysisParameters
# 旧文档(无 provider 字段)
p = AnalysisParameters(**{'quick_analysis_model': 'gpt-4o', 'deep_analysis_model': 'gpt-4o'})
assert p.quick_analysis_provider is None and p.deep_analysis_provider is None
# 新文档(带 provider)
p2 = AnalysisParameters(quick_analysis_model='gpt-4o', quick_analysis_provider='openrouter')
assert p2.quick_analysis_provider == 'openrouter'
print('schema OK')
"
```
Expected: `schema OK`

- [ ] **Step 3: Commit**

```bash
git add app/models/analysis.py
git commit -m "feat(model-select): AnalysisParameters 新增可选 quick/deep_analysis_provider"
```

---

### Task 3: live 路径(simple_analysis_service)透传 provider

**Files:**
- Modify: `app/services/simple_analysis_service.py`(`create_analysis_config` 约 374-398、解析调用约 505-506;live 路径 `_run_analysis_sync` 取参与解析约 1259-1305)

- [ ] **Step 1: `create_analysis_config` 加 provider 参数并透传**

签名(约 390-399)在 `deep_model_config` 后加两参:

```python
    quick_model_config: dict = None,  # 新增：快速模型的完整配置
    deep_model_config: dict = None,   # 新增：深度模型的完整配置
    quick_provider: str = None,       # 新增：快速模型所属厂家(区分跨厂家同名)
    deep_provider: str = None         # 新增：深度模型所属厂家(区分跨厂家同名)
) -> dict:
```

约 505-506 的解析调用改为传 provider:

```python
        quick_provider_info = get_provider_and_url_by_model_sync(quick_model, quick_provider)
        deep_provider_info = get_provider_and_url_by_model_sync(deep_model, deep_provider)
```

- [ ] **Step 2: live 路径取请求里的 provider 并初始化**

在 `_run_analysis_sync` 取模型的 if 块之前(约 `research_depth = ...` 之后,即 1256-1258 附近)加初始化:

```python
            # 前端指定的模型厂家(区分跨厂家同名);自动推荐分支保持 None → 两级回退
            req_quick_provider = None
            req_deep_provider = None
```

在"使用前端指定的模型"分支(约 1266-1267 `quick_model = request.parameters.quick_analysis_model` 之后)加:

```python
                req_quick_provider = getattr(request.parameters, 'quick_analysis_provider', None)
                req_deep_provider = getattr(request.parameters, 'deep_analysis_provider', None)
```

- [ ] **Step 3: 解析调用与 create_analysis_config 串入 provider**

约 1301-1302 的解析调用改为:

```python
            quick_provider_info = get_provider_and_url_by_model_sync(quick_model, req_quick_provider)
            deep_provider_info = get_provider_and_url_by_model_sync(deep_model, req_deep_provider)
```

约 1325-1332 的 `create_analysis_config(...)` 调用,在末尾参数加:

```python
                market_type=market_type,
                quick_provider=req_quick_provider,
                deep_provider=req_deep_provider
            )
```

> 推荐切换分支(约 1281-1286 的 `recommend_models_for_depth`)在 Task 5 处理 —— 那里会改用带 provider 的推荐方法并相应设置 `req_quick_provider`/`req_deep_provider`。本 task 先让"前端指定模型"路径完整工作,自动推荐分支 provider 暂为 None(两级回退兜底,不劣化)。

- [ ] **Step 4: import 冒烟 + 现有 oauth 注入测试不破坏**

Run: `.venv/bin/python -c "import app.services.simple_analysis_service; print('OK')" && .venv/bin/pytest tests/unit/test_simple_analysis_oauth_inject.py -q`
Expected: `OK` + 现有 oauth 测试通过(若该文件存在;不存在则跳过此句)

- [ ] **Step 5: Commit**

```bash
git add app/services/simple_analysis_service.py
git commit -m "feat(model-select): live 路径透传 quick/deep provider 到解析与 config"
```

---

### Task 4: worker 三方法块(analysis_service)透传 provider

**Files:**
- Modify: `app/services/analysis_service.py`(`_execute_analysis_sync_with_progress` 约 163,内联块 190-261、反查 240;`_execute_analysis_sync` 约 317,内联块 335-398、反查 380;`execute_analysis_task` 约 708,内联 734/744、反查 754)

- [ ] **Step 1: 加模块级匹配辅助(复用 Task 1 语义,避免跨模块耦合)**

在 `app/services/analysis_service.py` 顶部(import 之后)加一个轻量判据函数:

```python
def _cfg_matches(cfg: dict, model_name: str, provider: str = None) -> bool:
    """worker 内联模型配置读取的匹配判据:provider 给了精确配对、未给按 model_name。

    注意:这是“是否匹配”的布尔判据(用于内联循环过滤);跨厂家精确未命中的
    两级回退由 get_provider_and_url_by_model_sync 负责,此处仅决定读取哪条
    模型完整配置(max_tokens 等),取首个满足判据者。
    """
    if cfg.get("model_name") != model_name:
        return False
    return (not provider) or str(cfg.get("provider") or "").lower() == provider.lower()
```

- [ ] **Step 2: `_execute_analysis_sync_with_progress`(约 180-261)取 provider + 改内联匹配 + 反查透传**

在 `quick_model = ... ` / `deep_model = ...`(约 180-181)之后加:

```python
            quick_provider = getattr(task.parameters, 'quick_analysis_provider', None)
            deep_provider = getattr(task.parameters, 'deep_analysis_provider', None)
```

内联循环(约 203-225)改判据 —— 把:

```python
                    for llm_config in llm_configs:
                        if llm_config.get("model_name") == quick_model:
                            quick_model_config = { ... }
                        if llm_config.get("model_name") == deep_model:
                            deep_model_config = { ... }
```

改为(只取首个满足判据者,避免同名覆盖):

```python
                    for llm_config in llm_configs:
                        if quick_model_config is None and _cfg_matches(llm_config, quick_model, quick_provider):
                            quick_model_config = { ... }  # 内部字段不变
                        if deep_model_config is None and _cfg_matches(llm_config, deep_model, deep_provider):
                            deep_model_config = { ... }   # 内部字段不变
```

反查(约 240)`get_provider_by_model_name_sync(quick_model)` → `get_provider_by_model_name_sync(quick_model, quick_provider)`。

`create_analysis_config(...)` 调用(约 252-261)末尾加 `quick_provider=quick_provider, deep_provider=deep_provider`。

- [ ] **Step 3: `_execute_analysis_sync`(约 335-398)同样处理**

同 Step 2 模式,作用于第二个方法块:取 `quick_provider`/`deep_provider`(在该方法取 quick_model/deep_model 之后)、内联循环改 `_cfg_matches` + `is None` 守卫、反查(约 380)透传、`create_analysis_config` 加 provider 参数。

- [ ] **Step 4: `execute_analysis_task`(异步,约 720-766)同样处理**

取 `quick_provider`/`deep_provider`;内联 734/744 改 `_cfg_matches` + `is None` 守卫;反查(约 754)`await get_provider_by_model_name(quick_model)` → `await get_provider_by_model_name(quick_model, quick_provider)`;`create_analysis_config` 加 provider 参数。

- [ ] **Step 5: import 冒烟**

Run: `.venv/bin/python -c "import app.services.analysis_service; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/services/analysis_service.py
git commit -m "feat(model-select): worker 三方法块透传 provider(精确匹配 + 反查)"
```

---

### Task 5: 推荐产出端补全 provider

**Files:**
- Modify: `app/services/model_capability_service.py`(`recommend_models_for_depth` 约 312、`_get_default_models` 约 395)
- Modify: `app/routers/model_capabilities.py`(`recommend_models` 约 172-228)
- Modify: `app/services/simple_analysis_service.py`(live 路径推荐切换分支约 1263/1275-1286)
- Test: `tests/unit/test_provider_aware_resolution.py`(追加)

- [ ] **Step 1: 写失败测试 —— 推荐带 provider**

在 `tests/unit/test_provider_aware_resolution.py` 追加:

```python
def test_recommend_with_providers_keeps_candidate_provider(monkeypatch):
    from app.services.model_capability_service import get_model_capability_service
    svc = get_model_capability_service()

    class _M:
        def __init__(self, name, provider, level=3):
            self.model_name = name
            self.provider = provider
            self.enabled = True
            self.capability_level = level
            self.suitable_roles = []
            self.features = []
            self.performance_metrics = {}

    # 让 recommend_models_for_depth 选到带 provider 的候选
    monkeypatch.setattr(svc, "recommend_models_for_depth",
                        lambda depth: ("gpt-4o", "gpt-4o"))
    # with_providers 包装:必须能从某处带出 provider —— 见实现
    q_model, q_provider, d_model, d_provider = svc.recommend_models_with_providers("标准")
    assert q_model == "gpt-4o" and d_model == "gpt-4o"
    # provider 可能为 None(取决于候选),但方法必须返回 4 元组结构
    assert isinstance(q_model, str)
```

> 说明:此测试锁定**返回 4 元组的契约**;provider 具体值依赖候选数据,核心是结构与"不丢弃"。

- [ ] **Step 2: 运行,确认失败**

Run: `.venv/bin/pytest tests/unit/test_provider_aware_resolution.py::test_recommend_with_providers_keeps_candidate_provider -q`
Expected: FAIL — `AttributeError: ... has no attribute 'recommend_models_with_providers'`

- [ ] **Step 3: 实现 `recommend_models_with_providers`(不改旧方法,零破坏)**

在 `model_capability_service.py` 的 `recommend_models_for_depth` 之后新增方法,复用其筛选逻辑但保留候选对象的 `.provider`。最小实现:重构出一个返回候选对象的内部方法,新旧两个公开方法都基于它。

把 `recommend_models_for_depth`(约 312-393)的候选筛选与排序逻辑抽到 `_recommend_candidates(research_depth)`(返回 `(quick_obj_or_None, deep_obj_or_None)`),然后:

```python
    def recommend_models_for_depth(self, research_depth: str):
        """(保持旧签名)返回 (quick_model, deep_model) 纯名字。"""
        q, p_q, d, p_d = self.recommend_models_with_providers(research_depth)
        return q, d

    def recommend_models_with_providers(self, research_depth: str):
        """返回 (quick_model, quick_provider, deep_model, deep_provider)。

        候选对象本身带 .provider,不再丢弃;落默认时 provider 为 None,走两级回退。
        """
        quick_obj, deep_obj = self._recommend_candidates(research_depth)
        if quick_obj is None or deep_obj is None:
            q, d = self._get_default_models()
            return q, None, d, None
        return (
            quick_obj.model_name, getattr(quick_obj, "provider", None),
            deep_obj.model_name, getattr(deep_obj, "provider", None),
        )
```

`_recommend_candidates` 内含原 340-385 的筛选/排序,返回 `(quick_candidates[0] if quick_candidates else None, deep_candidates[0] if deep_candidates else None)`,并保留"获取配置失败 / 无启用模型 → 返回 (None, None)" 的早退(让上层落默认)。

- [ ] **Step 4: 运行测试,确认通过**

Run: `.venv/bin/pytest tests/unit/test_provider_aware_resolution.py -q`
Expected: PASS(全部)

- [ ] **Step 5: 推荐接口响应加 provider 字段**

`app/routers/model_capabilities.py` 约 183 改为用新方法:

```python
        quick_model, quick_provider, deep_model, deep_provider = \
            capability_service.recommend_models_with_providers(request.research_depth)
```

约 218-224 的 `response_data` 加两字段:

```python
        response_data = {
            "quick_model": quick_model,
            "deep_model": deep_model,
            "quick_provider": quick_provider,
            "deep_provider": deep_provider,
            "quick_model_info": quick_info,
            "deep_model_info": deep_info,
            "reason": reason
        }
```

- [ ] **Step 6: live 路径推荐切换分支带 provider**

`app/services/simple_analysis_service.py` 约 1263 与 1275 两处 `quick_model, deep_model = capability_service.recommend_models_for_depth(research_depth)` 改为:

```python
                    quick_model, req_quick_provider, deep_model, req_deep_provider = \
                        capability_service.recommend_models_with_providers(research_depth)
```

这样自动切换/推荐后,`req_quick_provider`/`req_deep_provider` 带上推荐候选的 provider(而非置 None 再猜),Task 3 的解析调用据此精确匹配。

- [ ] **Step 7: 冒烟 + import**

Run: `.venv/bin/pytest tests/unit/test_provider_aware_resolution.py -q && .venv/bin/python -c "import app.routers.model_capabilities, app.services.model_capability_service, app.services.simple_analysis_service; print('OK')"`
Expected: PASS + `OK`

- [ ] **Step 8: Commit**

```bash
git add app/services/model_capability_service.py app/routers/model_capabilities.py app/services/simple_analysis_service.py tests/unit/test_provider_aware_resolution.py
git commit -m "feat(model-select): 推荐产出端补全 provider(新增 recommend_models_with_providers)"
```

---

### Task 6: 后端全量回归

- [ ] **Step 1: 跑全量 unit + 记录基线差异**

Run: `.venv/bin/pytest tests/unit/ -q > /tmp/p2_unit.txt 2>&1; tail -5 /tmp/p2_unit.txt`
Expected: 新增测试通过;失败数与 `origin/main` 基线一致(该仓库有预存 collection error / 失败,需区分:本次新引入的失败为 0)。若有新失败,定位修复后重跑。

- [ ] **Step 2: 确认无新引入失败**

对比基线:`git stash` 后跑同一命令记基线失败集,`git stash pop` 后再跑,diff 失败集。本次改动文件相关的失败必须为 0。

---

## 前端

### Task 7: API 类型加可选 provider 字段

**Files:**
- Modify: `frontend/src/api/analysis.ts:35-36`

- [ ] **Step 1: 加字段**

把 `frontend/src/api/analysis.ts` 的:

```typescript
    quick_analysis_model?: string
    deep_analysis_model?: string
```

改为:

```typescript
    quick_analysis_model?: string
    deep_analysis_model?: string
    quick_analysis_provider?: string
    deep_analysis_provider?: string
```

- [ ] **Step 2: type-check 基线对比(改动前)**

Run: `cd frontend && git stash && yarn type-check > /tmp/tc_base.txt 2>&1; git stash pop`
记录基线错误数(约 242)。

- [ ] **Step 3: type-check(改动后)**

Run: `cd frontend && yarn type-check > /tmp/tc_now.txt 2>&1; grep -c "error TS" /tmp/tc_now.txt`
Expected: 与基线相同(本字段是纯增量,不应新增错误)。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/analysis.ts
git commit -m "feat(model-select): 分析参数类型加可选 quick/deep_analysis_provider"
```

---

### Task 8: 复合键工具 + 系统设置页

**Files:**
- Modify: `frontend/src/views/Settings/ConfigManagement.vue`(下拉 587/609、`enabledModels` 约 1304、保存提交、回填)

- [ ] **Step 1: 加复合键工具函数(就近定义,供本组件用)**

在 `<script setup>` 内加:

```typescript
const PROVIDER_MODEL_SEP = '::'
const makeModelKey = (provider: string, model: string) => `${provider}${PROVIDER_MODEL_SEP}${model}`
const splitModelKey = (key: string): { provider: string; model: string } => {
  const i = key.indexOf(PROVIDER_MODEL_SEP)  // 只按第一个分隔符拆
  if (i < 0) return { provider: '', model: key }  // 兼容裸值
  return { provider: key.slice(0, i), model: key.slice(i + PROVIDER_MODEL_SEP.length) }
}
// 回填:把存量裸 model_name 解析为复合键(多命中优先 default_provider 再首个)
const resolveModelKey = (model: string, provider: string | undefined, defaultProvider: string): string => {
  if (!model) return ''
  if (provider) return makeModelKey(provider, model)
  const matches = enabledModels.value.filter(m => m.model_name === model)
  if (matches.length === 0) return makeModelKey('', model)
  if (matches.length === 1) return makeModelKey(matches[0].provider, model)
  const preferred = matches.find(m => m.provider === defaultProvider) || matches[0]
  return makeModelKey(preferred.provider, model)
}
```

- [ ] **Step 2: 下拉改复合 value + 本地复合值绑定**

加两个本地 ref:`const quickModelKey = ref('')`、`const deepModelKey = ref('')`。
把 587/609 两个下拉的 `v-model` 改为绑 `quickModelKey`/`deepModelKey`,`el-option` 的 `:value` 改为 `makeModelKey(model.provider, model.model_name)`(`:key` 已是 `${model.provider}/${model.model_name}`,保留)。

- [ ] **Step 3: 加载时回填(把已存裸名解析为复合键)**

在加载 systemSettings 完成、且 `enabledModels` 可用之后(loadSystemSettings 内赋值 systemSettings 之后),加:

```typescript
  quickModelKey.value = resolveModelKey(
    systemSettings.quick_analysis_model,
    systemSettings.quick_analysis_provider,
    systemSettings.default_provider
  )
  deepModelKey.value = resolveModelKey(
    systemSettings.deep_analysis_model,
    systemSettings.deep_analysis_provider,
    systemSettings.default_provider
  )
```

- [ ] **Step 4: 保存时拆分回两个字段**

在保存 systemSettings 的提交前(saveSystemSettings 内构造 payload 处),把复合键拆回:

```typescript
  const q = splitModelKey(quickModelKey.value)
  const d = splitModelKey(deepModelKey.value)
  systemSettings.quick_analysis_model = q.model
  systemSettings.quick_analysis_provider = q.provider || undefined
  systemSettings.deep_analysis_model = d.model
  systemSettings.deep_analysis_provider = d.provider || undefined
```

(若 systemSettings 是 reactive 对象,直接赋值;若提交独立 payload,则写入 payload 同名字段。)

- [ ] **Step 5: type-check**

Run: `cd frontend && yarn type-check 2>&1 | grep -c "error TS"`
Expected: 与基线相同(无新增)。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Settings/ConfigManagement.vue
git commit -m "feat(model-select): 系统设置页下拉复合键携带 provider + 回填/提交拆分"
```

---

### Task 9: 子组件 option 复合键(ModelConfig / DeepModelSelector)

**Files:**
- Modify: `frontend/src/views/Settings/components/ModelConfig.vue`(option `:value`/`:key` 约 17/19、59/61)
- Modify: `frontend/src/views/Analysis/components/DeepModelSelector.vue`(实际路径以 `grep -rl "DeepModelSelector" frontend/src` 为准;option `:value`/`:key`)

- [ ] **Step 1: 定位子组件实际路径**

Run: `grep -rn "el-option" frontend/src/views/Settings/components/ModelConfig.vue; find frontend/src -name DeepModelSelector.vue`
记录两个文件里 `el-option` 的 `:value`/`:key` 行号。

- [ ] **Step 2: ModelConfig.vue option 改复合键**

把两处 `el-option` 的 `:value="model.model_name"` 改为 `:value="\`${model.provider}::${model.model_name}\`"`,`:key` 同样带 provider(若原 `:key` 仅 `model_name`)。v-model 仍是单字符串(承载复合值),由父组件 `SingleAnalysis.vue` split(Task 10)。

- [ ] **Step 3: DeepModelSelector.vue option 改复合键**

同 Step 2:`:value` 与 `:key` 都改为 `${model.provider}::${model.model_name}`(修掉原先仅用 `model_name` 的重复 key 隐患)。

- [ ] **Step 4: type-check**

Run: `cd frontend && yarn type-check 2>&1 | grep -c "error TS"`
Expected: 与基线相同。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Settings/components/ModelConfig.vue frontend/src/views/Analysis/components/DeepModelSelector.vue
git commit -m "feat(model-select): ModelConfig/DeepModelSelector option 改复合键"
```

---

### Task 10: 单股分析页

**Files:**
- Modify: `frontend/src/views/Analysis/SingleAnalysis.vue`(`modelSettings` 约 798、`availableModels`、提交 payload 约 962-963、推荐回填 `applyRecommendedModels` 约 2175、加载默认 约 1900-1901)

- [ ] **Step 1: modelSettings 加 provider 字段 + 复合键工具**

`modelSettings`(约 798)加 `quickAnalysisProvider: ''`、`deepAnalysisProvider: ''`。在 script 内加同 Task 8 的 `makeModelKey`/`splitModelKey`/`resolveModelKey`(若可抽公共 util 更好;否则就近复制,保持一致)。
下拉(约 370 的 `quickAnalysisModel` select、412 的 `DeepModelSelector`)的 v-model 改绑复合键 ref(`quickModelKey`/`deepModelKey`),option `:value` 用 `makeModelKey`。

- [ ] **Step 2: 加载默认模型时回填复合键**

约 1900-1901 设置 `modelSettings.quickAnalysisModel`/`deepAnalysisModel` 处,改为同时解析复合键:

```typescript
    quickModelKey.value = resolveModelKey(defaultModels.quick_analysis_model, defaultModels.quick_analysis_provider, /* default_provider 若可得 */ '')
    deepModelKey.value = resolveModelKey(defaultModels.deep_analysis_model, defaultModels.deep_analysis_provider, '')
```

(`getDefaultModels` 目前无 provider,故首次按名字猜;一致行为。)

- [ ] **Step 3: 提交 payload 加 provider**

约 962-963 的:

```typescript
        quick_analysis_model: modelSettings.value.quickAnalysisModel,
        deep_analysis_model: modelSettings.value.deepAnalysisModel
```

改为(从复合键拆出):

```typescript
        quick_analysis_model: splitModelKey(quickModelKey.value).model,
        quick_analysis_provider: splitModelKey(quickModelKey.value).provider || undefined,
        deep_analysis_model: splitModelKey(deepModelKey.value).model,
        deep_analysis_provider: splitModelKey(deepModelKey.value).provider || undefined
```

- [ ] **Step 4: 推荐回填带 provider(消费 Task 5 的新响应字段)**

`applyRecommendedModels`(约 2175)从 `modelRecommendation.value` 读 `quickProvider`/`deepProvider`(在设置 `modelRecommendation` 的约 2132/2149/2166 处,把接口返回的 `quick_provider`/`deep_provider` 一并存入)。应用时:

```typescript
  quickModelKey.value = makeModelKey(modelRecommendation.value.quickProvider || '', modelRecommendation.value.quickModel)
  deepModelKey.value = makeModelKey(modelRecommendation.value.deepProvider || '', modelRecommendation.value.deepModel)
```

`modelRecommendation` 的 ref 类型(约 806)加可选 `quickProvider?: string; deepProvider?: string`。

- [ ] **Step 5: type-check**

Run: `cd frontend && yarn type-check 2>&1 | grep -c "error TS"`
Expected: 与基线相同。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Analysis/SingleAnalysis.vue
git commit -m "feat(model-select): 单股分析页复合键 + payload provider + 推荐回填带 provider"
```

---

### Task 11: 批量分析页

**Files:**
- Modify: `frontend/src/views/Analysis/BatchAnalysis.vue`(`modelSettings`、下拉、提交 payload、加载默认 约 377-378)

- [ ] **Step 1: 同单股页改造(批量共用一套 parameters)**

`modelSettings` 加 provider 字段;加复合键工具(或复用公共 util);下拉 v-model 绑复合键 + option `:value` 用 `makeModelKey`;加载默认(约 377-378)回填复合键;提交 payload(批量请求构造处,约 502-516 附近)加 `quick_analysis_provider`/`deep_analysis_provider`(从复合键拆出)。批量是单一共享 parameters,一套选择套全批,无 per-symbol 分歧。

- [ ] **Step 2: type-check**

Run: `cd frontend && yarn type-check 2>&1 | grep -c "error TS"`
Expected: 与基线相同。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Analysis/BatchAnalysis.vue
git commit -m "feat(model-select): 批量分析页复合键 + payload provider"
```

---

### Task 12: 推送 + 更新 PR #24

- [ ] **Step 1: 交叉核验本地分支**

Run: `git log --oneline origin/main..HEAD | head -20; git status --porcelain | grep -v '.claude/'`
Expected: 看到本计划的后端+前端提交;工作区干净。

- [ ] **Step 2: 推送**

Run: `git push origin fix/global-model-dropdown`
然后 cross-verify:`git rev-parse HEAD`、`git rev-parse @{u}` 一致,`git cat-file -t HEAD` = commit。

- [ ] **Step 3: 在 PR #24 追加说明**

用 `gh pr comment 24` 补充:本次叠加了 provider-aware 模型选择(P2 修复),并注明**真实跨厂家同名手测仍待部署后验证**(配两家 gpt-4o 各选一家,验证打到正确 endpoint/key)。

---

## Self-Review

**Spec 覆盖核对:**
- 两级回退契约 → Task 1(`_match_llm_config` + 防劣化测试)✓
- 命中后用存储 provider 查厂家 → Task 1 Step 5 注释明确 ✓
- schema 新字段 → Task 2 ✓
- live 路径透传 → Task 3 ✓
- worker 三方法块(含 execute_analysis_task)→ Task 4 三个 Step ✓
- 推荐产出端补 provider(不破坏旧方法)→ Task 5 ✓
- config_bridge → spec 列为低优先;**本计划未单列 task**(解析器两级回退已保证不劣化,deep provider 取自全局设置属增强,留作后续)。已在此显式说明,非遗漏。
- 前端复合键 + 子组件 option 改 + 回填 → Task 8/9/10/11 ✓
- 测试(后端单测 + 前端 type-check 基线)→ Task 1/5/6/7 各步 ✓
- 并行边界 → 不碰 graph/model_usage,全程未涉及 ✓

**占位符扫描:** 无 TBD/TODO;子组件路径在 Task 9 Step 1 用命令定位(非占位,是"先查实际行号"的合理步骤)。✓

**类型/命名一致性:** `_match_llm_config`(Task1)、`_cfg_matches`(Task4,布尔判据,职责不同已注明)、`recommend_models_with_providers` 返回 4 元组(Task5 定义、Task5 Step6 消费一致)、`makeModelKey`/`splitModelKey`/`resolveModelKey`(Task8 定义,Task10/11 复用)、`req_quick_provider`/`req_deep_provider`(Task3 引入、Task5 Step6 赋值一致)。✓

**已知留待实现期确认的点(非占位,是诚实标注):**
- `create_analysis_config` 内联块字段、各 worker 方法的精确行号会因前序提交微移 —— 实现时以 grep 定位为准。
- config_bridge deep provider 增强未做。
