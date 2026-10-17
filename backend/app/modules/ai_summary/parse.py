"""Разбор ответа модели: JSON из текста (порт `extract_json` «Орбиты») и схема ответа.

`AiSummaryLlmOut` — плоская JSON Schema (без `$defs`): её же получает GigaChat в
`response_format` и в системной инструкции. Длинные списки обрезаются до лимитов
разделов, а не валят разбор; нестроковые пункты пропускаются.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# лимиты разделов (промпт просит столько же)
SECTION_LIMITS: dict[str, int] = {"going_well": 4, "lagging": 4, "attention": 5, "questions": 4}
HEADLINE_MAX = 400

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(?P<body>.*?)```", re.DOTALL)
_DECODER = json.JSONDecoder()


class ParseError(Exception):
    """В ответе нет корректного JSON по схеме; `message` можно вернуть модели для повтора."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def extract_json(text: str | None) -> Any:
    """JSON из ответа модели: целиком, из первого ограждения ``` или от первой `{`/`[`.

    Поддерживает обрамляющий текст («Вот ответ: {...}») и мусор после объекта.
    """
    if text is None:
        raise ParseError("пустой ответ модели")
    candidate = text.strip()
    if not candidate:
        raise ParseError("пустой ответ модели")
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    fence = _FENCE_RE.search(candidate)
    if fence is not None:
        body = fence.group("body").strip()
        try:
            return json.loads(body)
        except ValueError:
            candidate = body or candidate
    last_error: str | None = None
    for index, char in enumerate(candidate):
        if char not in "{[":
            continue
        try:
            value, _ = _DECODER.raw_decode(candidate, index)
        except ValueError as exc:
            if last_error is None:
                last_error = str(exc)
            continue
        return value
    raise ParseError("в ответе нет корректного JSON" + (f": {last_error}" if last_error else ""))


def _items(value: Any, limit: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        return value  # pydantic сообщит о неверном типе
    result = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return result[:limit]


class AiSummaryLlmOut(BaseModel):
    """Ответ модели: пять разделов сводки (лишние ключи игнорируются)."""

    model_config = ConfigDict(extra="ignore")

    headline: str = Field(
        min_length=1,
        max_length=HEADLINE_MAX,
        description="1–2 предложения: главный итог периода",
    )
    going_well: list[str] = Field(
        default_factory=list,
        max_length=SECTION_LIMITS["going_well"],
        description="Что идёт хорошо",
    )
    lagging: list[str] = Field(
        default_factory=list,
        max_length=SECTION_LIMITS["lagging"],
        description="Где отставание и задержки",
    )
    attention: list[str] = Field(
        default_factory=list,
        max_length=SECTION_LIMITS["attention"],
        description="Кто или что требует внимания",
    )
    questions: list[str] = Field(
        default_factory=list,
        max_length=SECTION_LIMITS["questions"],
        description="Вопросы для разбора, каждый заканчивается «?»",
    )

    @field_validator("headline", mode="before")
    @classmethod
    def _strip_headline(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("going_well", "lagging", "attention", "questions", mode="before")
    @classmethod
    def _truncate(cls, value: Any, info: Any) -> Any:
        return _items(value, SECTION_LIMITS[info.field_name])


def response_schema() -> dict[str, Any]:
    """JSON Schema ответа (плоская: только строки и списки строк)."""
    return AiSummaryLlmOut.model_json_schema()


def _validation_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:8]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


def parse_llm_output(text: str | None) -> AiSummaryLlmOut:
    """Текст ответа → разделы сводки; ошибка — `ParseError` с текстом для повтора."""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ParseError("ожидался JSON-объект, получен " + type(data).__name__)
    try:
        return AiSummaryLlmOut.model_validate(data)
    except ValidationError as exc:
        raise ParseError("ответ не соответствует схеме: " + _validation_text(exc)) from exc
