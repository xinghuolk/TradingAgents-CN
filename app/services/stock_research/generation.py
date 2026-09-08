"""Persisted, user-scoped AI originals; credentials live only during invocation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Protocol
from uuid import uuid4

from app.models.config import LLMConfig
from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import GenerationTask, Reference, utc_now
from app.services.stock_research.prompts import PROMPT_VERSION, build_prompts
from app.services.stock_research.references import ReferenceService
from app.services.stock_research.storage import StockResearchRepository


logger = logging.getLogger(__name__)
GENERATION_ERRORS = {
    "INVALID_GENERATION_MODEL": "selected model is unavailable",
    "INVALID_GENERATION_SETTINGS": "selected reasoning effort is unsupported",
    "GENERATION_CREDENTIALS_UNAVAILABLE": "selected model credentials are unavailable",
    "GENERATION_FAILED": "research draft generation failed",
}


def _error(code: str) -> ResearchError:
    return ResearchError(code, GENERATION_ERRORS[code])


class ResearchTextGenerator(Protocol):
    async def generate(
        self,
        *,
        user_id: str,
        provider: str,
        model_name: str,
        reasoning_effort: str | None,
        system_prompt: str,
        user_prompt: str,
    ) -> str: ...


class ConfiguredResearchTextGenerator:
    def __init__(self, db, *, llm_factory: Callable | None = None) -> None:
        self.db = db
        self.llm_factory = llm_factory

    async def _selected_config(self, provider: str, model_name: str) -> LLMConfig:
        # ConfigService.get_system_config creates defaults and falls back on errors.
        # Generation must read the exact saved selection without either side effect.
        try:
            documents = await (
                self.db["system_configs"].find({"is_active": True})
                .sort([("version", -1)]).limit(1).to_list(length=1)
            )
            for item in documents[0].get("llm_configs", []) if documents else []:
                if (
                    item.get("provider") == provider
                    and item.get("model_name") == model_name
                ):
                    config = LLMConfig(**item)
                    if config.enabled:
                        return config
        except Exception:
            raise _error("INVALID_GENERATION_MODEL") from None
        raise _error("INVALID_GENERATION_MODEL")

    async def validate_selection(
        self, *, provider: str, model_name: str, reasoning_effort: str | None,
    ) -> None:
        await self._selected_config(provider, model_name)
        if reasoning_effort is not None:
            # Codex's current effort path requests encrypted reasoning replay.
            # Only adapters supporting plain effort without that request qualify.
            if reasoning_effort not in {"low", "medium", "high"} or provider in {
                "codex", "claude_code", "anthropic", "google", "deepseek",
            }:
                raise _error("INVALID_GENERATION_SETTINGS")

    async def _credentials(
        self, user_id: str, provider: str, config: LLMConfig
    ) -> tuple[str, str]:
        try:
            if provider in {"codex", "claude_code"}:
                from app.routers.oauth import get_credentials_collection
                from app.services import oauth_service

                token = await oauth_service.resolve(
                    get_credentials_collection(), str(user_id), provider
                )
                if not token:
                    raise ValueError("missing credential")
                return token, ""

            from app.services.config_service import ConfigService

            helper = ConfigService()
            provider_config = await self.db["llm_providers"].find_one(
                {"name": provider}
            ) or {}
            api_key = (
                config.api_key or provider_config.get("api_key")
                or helper._get_env_api_key(provider)
            )
            api_base = config.api_base or provider_config.get("default_base_url")
            if not api_base or not helper._is_valid_api_key(api_key):
                raise ValueError("missing credential or endpoint")
            return api_key, api_base
        except Exception:
            raise _error("GENERATION_CREDENTIALS_UNAVAILABLE") from None

    async def generate(
        self,
        *,
        user_id: str,
        provider: str,
        model_name: str,
        reasoning_effort: str | None,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        await self.validate_selection(
            provider=provider, model_name=model_name, reasoning_effort=reasoning_effort
        )
        config = await self._selected_config(provider, model_name)
        api_key, api_base = await self._credentials(user_id, provider, config)
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            factory = self.llm_factory
            if factory is None:
                from tradingagents.graph.trading_graph import create_llm_by_provider

                factory = create_llm_by_provider
            llm = factory(
                provider=provider, model=model_name, backend_url=api_base,
                temperature=config.temperature, max_tokens=config.max_tokens,
                timeout=config.timeout, api_key=api_key,
            )
            kwargs = (
                {"reasoning_effort": reasoning_effort}
                if reasoning_effort is not None else {}
            )
            response = await llm.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                **kwargs,
            )
            content = response.content
            if isinstance(content, list):
                content = "\n".join(
                    part if isinstance(part, str) else part["text"]
                    for part in content
                    if isinstance(part, str) or (
                        isinstance(part, dict)
                        and part.get("type") in {"text", "output_text"}
                        and isinstance(part.get("text"), str)
                    )
                )
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty generated text")
            return content
        except Exception:
            raise _error("GENERATION_FAILED") from None


class ResearchGenerationService:
    def __init__(
        self, repository: StockResearchRepository, references: ReferenceService,
        generator: ResearchTextGenerator,
        *,
        selection_validator: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.repository = repository
        self.references = references
        self.generator = generator
        self.selection_validator = selection_validator

    async def submit(
        self, *, user_id: str, target_entry_id: str, draft_kind: str,
        provider: str, model_name: str, reasoning_effort: str | None,
        references: list[Reference],
    ) -> GenerationTask:
        entry = await self.repository.get_entry(user_id, target_entry_id)
        if entry is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        if (
            draft_kind not in {"note", "research", "decision", "review"}
            or draft_kind != entry.entry_type
        ):
            raise ResearchError("INVALID_ENTRY", "draft kind must match target entry")
        if entry.status != "draft":
            raise ResearchError(
                "RESEARCH_CONFLICT", "AI drafts require an editable draft entry"
            )
        if not provider.strip() or not model_name.strip():
            raise _error("INVALID_GENERATION_MODEL")
        if self.selection_validator:
            await self.selection_validator(
                provider=provider, model_name=model_name, reasoning_effort=reasoning_effort
            )
        resolved = []
        for reference in references:
            document = (await self.references.resolve(user_id, reference)).to_document()
            document.pop("user_id", None)
            resolved.append(document)
        theses = []
        security_ids = dict.fromkeys(
            (*entry.security_ids, *((entry.security_id,) if entry.security_id else ()))
        )
        for security_id in security_ids:
            workspace = await self.repository.get_workspace(user_id, security_id)
            if workspace:
                workspace_document = workspace.to_document()
                theses.append({key: workspace_document[key] for key in (
                    "security_id", "body", "assumptions", "risks",
                    "invalidation_conditions", "open_questions",
                )})
        entry_document = entry.to_document()
        context = {
            "target": {key: entry_document[key] for key in (
                "entry_type", "scope", "security_id", "security_ids", "title", "body",
                "topic", "conclusion", "decision_action", "decision_date",
                "planned_price", "target_allocation", "horizon", "review_kind", "decision_id",
            )},
            "theses": theses, "references": resolved,
        }
        task = GenerationTask(
            id=str(uuid4()), user_id=user_id, target_entry_id=target_entry_id,
            draft_kind=draft_kind,
            provider=provider, model_name=model_name, reasoning_effort=reasoning_effort,
            references=tuple(references),
            source_ids=tuple(dict.fromkeys(ref.source_id for ref in references)),
            context_snapshot=deepcopy(context), prompt_version=PROMPT_VERSION,
        )
        return await self.repository.insert_generation_task(task)

    async def get(self, user_id: str, task_id: str) -> GenerationTask:
        task = await self.repository.get_generation_task(user_id, task_id)
        if task is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "generation task not found")
        return task

    async def run(self, task_id: str, user_id: str) -> None:
        try:
            task = await self.repository.claim_generation_task(user_id, task_id)
            if task is None:
                return
            system_prompt, user_prompt = build_prompts(
                task.draft_kind, task.context_snapshot
            )
            content = await self.generator.generate(
                user_id=user_id, provider=task.provider, model_name=task.model_name,
                reasoning_effort=task.reasoning_effort,
                system_prompt=system_prompt, user_prompt=user_prompt,
            )
            if not isinstance(content, str) or not content.strip():
                raise _error("GENERATION_FAILED")
            await self.repository.complete_generation_task(task, content, utc_now())
        except (Exception, asyncio.CancelledError) as error:
            code = (
                error.code
                if isinstance(error, ResearchError) and error.code in GENERATION_ERRORS
                else "GENERATION_FAILED"
            )
            try:
                await self.repository.fail_generation_task(
                    user_id, task_id, code, GENERATION_ERRORS[code]
                )
            except Exception:
                logger.error("Research generation task storage unavailable")
            if isinstance(error, asyncio.CancelledError):
                raise
