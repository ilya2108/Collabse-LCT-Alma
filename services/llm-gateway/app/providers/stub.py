"""Детерминированный провайдер без сети (LLM_PROVIDER=stub): CI, демо офлайн, отладка backend.

Со схемой — пустая сводка в формате backend (`headline` + четыре пустых списка), без схемы —
«ОК». Токены не тратит, дневной лимит не расходует.
"""
from __future__ import annotations

import json

from app.providers.base import ProviderReply, ProviderRequest

STUB_SUMMARY = {
    "headline": "Тестовый ответ llm-gateway (LLM_PROVIDER=stub).",
    "going_well": [],
    "lagging": [],
    "attention": [],
    "questions": [],
}


class StubProvider:
    name = "stub"
    configured = True
    structured_supported: bool | None = True
    token_cached = False

    async def chat(self, request: ProviderRequest) -> ProviderReply:
        if request.response_schema is not None:
            text = json.dumps(STUB_SUMMARY, ensure_ascii=False)
        else:
            text = "ОК"
        return ProviderReply(
            text=text,
            model="stub",
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            structured=request.response_schema is not None,
            attempts=1,
            spent_tokens=0,
        )

    async def aclose(self) -> None:
        return None
