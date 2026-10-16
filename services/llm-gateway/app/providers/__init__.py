"""Провайдеры модели: GigaChat (httpx) и stub без сети."""
from __future__ import annotations

import httpx

from app.config import Settings
from app.providers.base import Provider, ProviderError, ProviderReply, ProviderRequest
from app.providers.gigachat import GigaChatProvider
from app.providers.stub import StubProvider


def build_provider(settings: Settings, http: httpx.AsyncClient) -> Provider:
    if settings.llm_provider == "stub":
        return StubProvider()
    return GigaChatProvider(settings, http)


__all__ = [
    "GigaChatProvider",
    "Provider",
    "ProviderError",
    "ProviderReply",
    "ProviderRequest",
    "StubProvider",
    "build_provider",
]
