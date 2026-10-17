"""Одно правило на всех экранах (PostgreSQL): «зависла» и «завершено».

1. «Зависла» — строго по точному времени: `now − stage_entered_at > N суток`. Заявка
   14,5 суток на этапе с нормой 14 зависла везде одинаково — бейдж карточки (список, доска,
   карточка), фильтр доски `?stuck=true`, дашборд и отчёт «Зависшие», «Активность КАМов»,
   «Просрочки» и бот `/stuck`; 13,5 суток — нигде. `stuck_days` — целые сутки для показа.
2. «Завершено» — вход в терминальный этап: событие LMS по уже закрытой заявке
   (`integration`, from = to) не «закрывает» её заново ни на дашборде, ни в отчёте КАМов.

Время — реальное: доска и отчёты считают в SQL через `now()`, у границ запас 12 часов.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.security import TokenClaims, get_token_claims
from app.models import AppUser, DealStageHistory, Workflow, WorkflowStage
from app.modules.reporting import kam_activity as ka
from app.modules.reporting import sla
from app.modules.reporting.kam_activity import KamActivityParams
from app.modules.reporting.service import REPORT_TZ

from . import factories

API = "/api/v1"
Workflows = dict[str, tuple[Workflow, dict[str, WorkflowStage]]]


def ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest.fixture
async def people(db_session) -> dict[str, AppUser]:  # noqa: ANN001
    head = await factories.make_user(
        db_session, roles=("head_kam",), full_name="Морозов Дмитрий Александрович"
    )
    kam = await factories.make_user(
        db_session,
        roles=("kam",),
        full_name="Черных Константин Андреевич",
        manager=head,
        tg_chat_id=880001,
    )
    return {"head": head, "kam": kam}


async def _deal(session, workflows: Workflows, kam: AppUser, path: list[tuple]) -> Any:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    return await factories.make_deal(
        session, workflow=wf, stages=stages, responsible=kam, created_at=path[0][1], path=path
    )


async def _get(client: Any, path: str, params: Any = None) -> dict[str, Any]:
    response = await client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- 1. «зависла»: граница нормы ---------------------------------------------------------


async def test_stuck_boundary_same_answer_everywhere(
    api, db_session, workflows: Workflows, people
) -> None:  # noqa: ANN001
    kam, head = people["kam"], people["head"]
    _, stages = workflows["b2b"]
    assert stages["first_contact"].stuck_threshold_days == 14  # фикстура нормы из сида
    assert stages["new"].stuck_threshold_days == 7

    over = await _deal(db_session, workflows, kam, [("new", ago(30)), ("first_contact", ago(14.5))])
    under = await _deal(
        db_session, workflows, kam, [("new", ago(30)), ("first_contact", ago(13.5))]
    )
    new_over = await _deal(db_session, workflows, kam, [("new", ago(7.5))])
    closed = await _deal(db_session, workflows, kam, [("new", ago(60)), ("completed", ago(40))])
    expected = {str(over.id), str(new_over.id)}
    every = {str(d.id) for d in (over, under, new_over, closed)}

    client = api.as_user(head)

    # бейдж: список заявок, доска, карточка
    listed = await _get(client, "/requests", {"limit": 200})
    badge = {item["id"]: item["is_stuck"] for item in listed["items"]}
    assert set(badge) == every
    assert {rid for rid, flag in badge.items() if flag} == expected
    days = {item["id"]: item["stuck_days"] for item in listed["items"]}
    assert days[str(over.id)] == 14 and days[str(under.id)] == 13  # показ — целые сутки

    board = await _get(client, "/requests/board", {"workflow_type": "b2b", "cards_limit": 100})
    cards = [card for column in board["columns"] for card in column["cards"]]
    assert {card["id"] for card in cards if card["is_stuck"]} == expected
    card = next(card for card in cards if card["id"] == str(over.id))
    assert card["stuck_days"] == 14

    detail = await _get(client, f"/requests/{over.id}")
    assert (detail["is_stuck"], detail["stuck_days"]) == (True, 14)
    detail_under = await _get(client, f"/requests/{under.id}")
    assert detail_under["is_stuck"] is False

    # фильтр доски: каждая строка `?stuck=true` с бейджем, `?stuck=false` — без
    stuck_rows = (await _get(client, "/requests", {"stuck": "true", "limit": 200}))["items"]
    assert {item["id"] for item in stuck_rows} == expected
    assert all(item["is_stuck"] for item in stuck_rows)
    rest = (await _get(client, "/requests", {"stuck": "false", "limit": 200}))["items"]
    assert {item["id"] for item in rest} == every - expected
    assert not any(item["is_stuck"] for item in rest)

    # отчёты: KPI дашборда и отчёт «Зависшие»
    dashboard = await _get(client, "/reports/dashboard")
    assert dashboard["kpi"]["stuck"]["value"] == len(expected)
    report = await _get(client, "/reports/stuck")
    assert {row["request_id"] for row in report["rows"]} == expected

    # «Активность КАМов» и «Просрочки» — тем же моментом «сейчас»
    detail_ka = await ka.build_kam_activity(db_session, head, KamActivityParams())
    assert detail_ka["totals"]["stuck"] == len(expected)
    overdue = await sla.build_sla(db_session, head, KamActivityParams())
    assert overdue["kpi"]["overdue"]["value"] == len(expected)
    assert {item["request_id"] for item in overdue["overdue"]["items"]} == expected

    # бот (/stuck и /my): тот же ответ; токен сервиса — последним, он подменяет claims
    bot_claims = TokenClaims(
        sub=str(uuid.uuid4()),
        username="service-account-crm-notification",
        email=None,
        full_name=None,
        roles=("svc_notification",),
    )
    api.app.dependency_overrides[get_token_claims] = lambda: bot_claims
    bot = api.anonymous()
    chat = kam.tg_chat_id
    stuck_items = (await _get(bot, f"/internal/bot/users/{chat}/requests", {"stuck": "true"}))[
        "items"
    ]
    assert {item["id"] for item in stuck_items} == expected
    assert all(item["is_stuck"] for item in stuck_items)
    mine = (await _get(bot, f"/internal/bot/users/{chat}/requests", {"limit": 50}))["items"]
    assert {item["id"] for item in mine if item["is_stuck"]} == expected
    assert next(item for item in mine if item["id"] == str(over.id))["stuck_days"] == 14
    summary = await _get(bot, f"/internal/bot/users/{chat}/summary")
    assert summary["stuck"] == len(expected)


# --- 2. «завершено»: событие LMS по уже закрытой заявке ----------------------------------


async def test_lms_event_after_close_is_not_a_new_completion(
    api, db_session, workflows: Workflows, people
) -> None:  # noqa: ANN001
    kam, head = people["kam"], people["head"]
    _, stages = workflows["b2b"]
    completed = stages["completed"]
    today = datetime.now(REPORT_TZ).date()
    period = {"from": (today - timedelta(days=30)).isoformat(), "to": today.isoformat()}
    client = api.as_user(head)

    await _deal(db_session, workflows, kam, [("new", ago(20)), ("completed", ago(5))])
    old = await _deal(db_session, workflows, kam, [("new", ago(200)), ("completed", ago(100))])

    async def completed_counts() -> tuple[int, int, int]:
        dashboard = await _get(client, "/reports/dashboard", period)
        (series,) = [s for s in dashboard["dynamics"]["series"] if s["key"] == "closed"]
        kam_report = await _get(client, "/reports/kam-activity", period)
        return (
            dashboard["kpi"]["completed"]["value"],
            sum(point["value"] for point in series["points"]),
            kam_report["totals"]["won"],
        )

    assert await completed_counts() == (1, 1, 1)

    # LMS прислала «обучение завершено» по заявке, закрытой 100 дней назад: этап не меняется
    db_session.add(
        DealStageHistory(
            deal_id=old.id,
            from_stage_id=completed.id,
            to_stage_id=completed.id,
            from_stage_name=completed.name,
            to_stage_name=completed.name,
            kind="integration",
            reason="lms.learning.completed",
            created_at=ago(1),
        )
    )
    await db_session.flush()

    assert await completed_counts() == (1, 1, 1)
