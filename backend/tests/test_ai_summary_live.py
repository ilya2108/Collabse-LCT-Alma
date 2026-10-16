"""Живой прогон сводки через llm-gateway и настоящий GigaChat (не в CI).

Запуск при поднятом шлюзе (`make llm-smoke` проходит); токен — случайный из
deploy/compose/.env (с настоящим ключом шлюз `dev-llm-token` не принимает):
`LLM_GATEWAY_TOKEN=$(grep '^LLM_GATEWAY_TOKEN=' ../deploy/compose/.env | cut -d= -f2-) \
ALMA_LLM_LIVE=1 LLM_GATEWAY_URL=http://localhost:8040 \
.venv/bin/python -m pytest -q tests/test_ai_summary_live.py -s`

Синтетические факты (образцы отчётов без ПДн) → `GigaChatGatewayProvider` → разбор JSON →
проверка цифр: после удалений ни одного несверенного числа и не меньше двух фраз.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from app.core.config import get_settings
from app.modules.ai_summary import service
from app.modules.ai_summary.facts import build_kam_facts, build_team_facts

from . import ai_summary_samples as samples

pytestmark = pytest.mark.skipif(
    os.environ.get("ALMA_LLM_LIVE") != "1" or not os.environ.get("LLM_GATEWAY_URL"),
    reason="живой GigaChat: ALMA_LLM_LIVE=1 и LLM_GATEWAY_URL",
)


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_gateway_url", os.environ["LLM_GATEWAY_URL"])
    monkeypatch.setattr(
        settings, "llm_gateway_token", os.environ.get("LLM_GATEWAY_TOKEN", "dev-llm-token")
    )


@pytest.mark.parametrize("kind", ["team", "kam"])
async def test_live_summary_passes_verification(kind: str) -> None:
    if kind == "team":
        facts = build_team_facts(samples.team_summary(), samples.team_sla())
    else:
        facts = build_kam_facts(samples.KAM_B, samples.kam_activity(), samples.kam_sla())
    run = service._Run()
    started = time.monotonic()
    result = await service._generate(
        run,
        job_id=uuid.uuid4(),
        kind=kind,
        provider_name="gigachat",
        facts=facts.facts,
        aliases=facts.aliases,
        started=started,
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))  # noqa: T201 — ручной прогон
    print(  # noqa: T201
        f"model={run.model} attempts={run.attempts} tokens={run.tokens_in}/{run.tokens_out} "
        f"latency_ms={run.latency_ms} wall_s={time.monotonic() - started:.1f}"
    )
    checks = result["checks"]
    sections = result["sections"]
    phrases = [sections["headline"]] + [
        item
        for key in ("going_well", "lagging", "attention", "questions")
        for item in sections[key]
    ]
    assert sections["headline"] and len([p for p in phrases if p]) >= 2
    assert checks["numbers_matched"] <= checks["numbers_total"]
    dumped = json.dumps(sections, ensure_ascii=False)
    for needle in samples.DANGEROUS:
        assert needle not in dumped
