"""Materialize a financial-report-llm-extractor transport-config JSON from
TradingAgents-CN's bridged deep-role LLM env vars.

The extractor reads its LLM config from a JSON file (LlmTransportConfig.from_json).
Instead of a separate hand-written file, we generate one from the deep-role
provider/model/backend_url that app.core.config_bridge has already projected into
the environment. The API key itself is NOT written here — the extractor reads it
at call time from {PROVIDER}_API_KEY, which the bridge also sets.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tradingagents.utils.logging_manager import get_logger

logger = get_logger("financial_reports.llm_config_export")

# Codex 走 OAuth：token 由 extractor 自行从 ~/.codex/auth.json 解析，不写 api_key_env
_SUBSCRIPTION_PROVIDERS = {"codex", "openai-codex"}
# extractor 无 api-key 版 Anthropic 客户端（仅诊断态 OAuth）；遇到则降级，不生成破损配置
_UNSUPPORTED_PROVIDERS = {"anthropic", "claude_code", "claude-code"}

_CONFIG_FILENAME = "frle_llm_config.deep.json"


def materialize_extractor_llm_config(cache_root: str = "") -> str | None:
    """Write a transport-config JSON for the extractor's LLM-supplement step.

    Reads TRADINGAGENTS_DEEP_PROVIDER / _BACKEND_URL / TRADINGAGENTS_DEEP_MODEL
    (set by app.core.config_bridge). Returns the JSON file path, or None when the
    deep-role config is unavailable/unsupported (caller then skips the supplement).
    """
    provider = (os.getenv("TRADINGAGENTS_DEEP_PROVIDER") or "").strip()
    model = (os.getenv("TRADINGAGENTS_DEEP_MODEL") or "").strip()
    if not provider or not model:
        logger.info("⏭️ 未找到 deep provider/model 环境变量，跳过为 extractor 生成 LLM 配置")
        return None

    if provider.lower() in _UNSUPPORTED_PROVIDERS:
        logger.warning(
            f"⚠️ extractor 不支持 provider={provider} 的 api-key 调用，跳过 LLM 补充配置生成"
        )
        return None

    backend_url = (os.getenv("TRADINGAGENTS_DEEP_BACKEND_URL") or "").strip()

    if provider.lower() in _SUBSCRIPTION_PROVIDERS:
        cfg: dict[str, str] = {"provider": provider, "model": model}
    else:
        cfg = {
            "provider": provider,
            "model": model,
            "api_key_env": f"{provider.upper()}_API_KEY",
        }
        if backend_url:
            cfg["base_url"] = backend_url

    target_dir = Path(cache_root) if cache_root else Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _CONFIG_FILENAME
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"✅ 已为 extractor 生成 LLM 配置: provider={provider}, model={model} → {target}")
    return str(target)
