"""Общие типы провайдеров: запрос, ответ, ошибки с HTTP-кодом шлюза."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderRequest:
    messages: list[dict[str, str]]
    model: str
    temperature: float
    max_tokens: int
    response_schema: dict[str, Any] | None
    request_id: str


@dataclass
class ProviderReply:
    text: str
    model: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    structured: bool
    attempts: int
    # токены всех ответов этого вызова, включая отклонённые (для дневного лимита)
    spent_tokens: int = 0


class ProviderError(Exception):
    """Ошибка вызова модели: `code` и `http_status` — из контракта шлюза (README §коды).

    `message` — короткий русский текст без содержимого запроса и без секретов.
    `attempts` и `spent_tokens` дописывает провайдер перед выходом (лог и дневной лимит).
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 502,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after
        self.attempts = 0
        self.spent_tokens = 0

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class Provider(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    @property
    def structured_supported(self) -> bool | None: ...

    @property
    def token_cached(self) -> bool: ...

    async def chat(self, request: ProviderRequest) -> ProviderReply: ...

    async def aclose(self) -> None: ...
