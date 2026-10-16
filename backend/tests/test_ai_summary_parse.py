"""Разбор ответа модели: JSON из текста, обрезка длинных списков, ремонт со второй попытки."""

from __future__ import annotations

import json
import time
import uuid

import pytest

from app.modules.ai_summary import service
from app.modules.ai_summary.facts import build_team_facts
from app.modules.ai_summary.parse import (
    AiSummaryLlmOut,
    ParseError,
    extract_json,
    parse_llm_output,
)
from app.modules.ai_summary.providers import LlmReply

from . import ai_summary_samples as samples

GOOD = {
    "headline": "На 16.10.2026 в работе 47 заявок, просрочено 9 (19,1%).",
    "going_well": ["Просроченных на срез меньше, чем на начало периода: 9 против 11."],
    "lagging": ["Заявок больше 30 дней сверх нормы: 1."],
    "attention": ["КАМ-02: просрочено 6 из 20 (30%)."],
    "questions": ["Что задерживает заявки на этапе «Согласование договора»?"],
}


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(GOOD, ensure_ascii=False),
        "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```",
        "Вот сводка: " + json.dumps(GOOD, ensure_ascii=False) + " Надеюсь, помог.",
        json.dumps(GOOD, ensure_ascii=False) + "\n}}",
    ],
)
def test_extract_json_variants(text: str) -> None:
    assert extract_json(text) == GOOD


@pytest.mark.parametrize("text", [None, "", "   ", "нет json", "{оборван"])
def test_extract_json_errors(text: str | None) -> None:
    with pytest.raises(ParseError):
        extract_json(text)


def test_long_lists_are_truncated_not_rejected() -> None:
    data = {**GOOD, "lagging": [f"Пункт {i}." for i in range(9)], "extra": 1}
    parsed = parse_llm_output(json.dumps(data, ensure_ascii=False))
    assert len(parsed.lagging) == 4
    assert parsed.questions == GOOD["questions"]


def test_non_string_items_dropped_and_none_is_empty() -> None:
    parsed = AiSummaryLlmOut.model_validate(
        {"headline": "  Итог.  ", "going_well": [1, "  ", "Ок."], "attention": None}
    )
    assert parsed.headline == "Итог."
    assert parsed.going_well == ["Ок."] and parsed.attention == []


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ([1, 2], "ожидался JSON-объект"),
        ({"going_well": []}, "headline"),
        ({"headline": ""}, "headline"),
        ({"headline": "x" * 401}, "headline"),
    ],
)
def test_schema_errors_are_readable(data: object, needle: str) -> None:
    with pytest.raises(ParseError) as exc:
        parse_llm_output(json.dumps(data))
    assert needle in exc.value.message


class _ScriptedProvider:
    name = "gigachat"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages, *, schema, purpose, request_id):  # noqa: ANN001, ANN201
        self.calls.append(list(messages))
        return LlmReply(
            text=self.replies.pop(0),
            model="GigaChat-2-Pro",
            tokens_in=100,
            tokens_out=50,
            latency_ms=10,
        )


async def test_second_attempt_repairs_json(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = build_team_facts(samples.team_summary(), samples.team_sla())
    provider = _ScriptedProvider(["Сводка готова, но без JSON", json.dumps(GOOD)])
    monkeypatch.setattr(service, "get_provider", lambda name: provider)
    run = service._Run()
    result = await service._generate(
        run,
        job_id=uuid.uuid4(),
        kind="team",
        provider_name="gigachat",
        facts=facts.facts,
        aliases=facts.aliases,
        started=time.monotonic(),
    )
    assert run.attempts == 2 and run.tokens_in == 200 and run.tokens_out == 100
    second = provider.calls[1]
    assert second[-2] == {"role": "assistant", "content": "Сводка готова, но без JSON"}
    assert "не прошёл проверку" in second[-1]["content"]
    assert result["source"] == "gigachat" and result["model"] == "GigaChat-2-Pro"
    assert result["checks"]["sentences_removed"] == 0


async def test_no_second_attempt_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = build_team_facts(samples.team_summary(), samples.team_sla())
    provider = _ScriptedProvider(["мусор", json.dumps(GOOD)])
    monkeypatch.setattr(service, "get_provider", lambda name: provider)
    run = service._Run()
    with pytest.raises(service.ProviderError) as exc:
        await service._generate(
            run,
            job_id=uuid.uuid4(),
            kind="team",
            provider_name="gigachat",
            facts=facts.facts,
            aliases=facts.aliases,
            started=time.monotonic() - 500,
        )
    assert exc.value.code == "llm_bad_output" and run.attempts == 1
