"""Кольцо «отчёт → живой перевод схемы → отчёт цел» (P0-1) на настоящем PostgreSQL.

Перевод схемы (удаление этапа с переносом заявок, `system_migration`) и события LMS
(`integration`, этап не меняется) пишут строки истории, но срок на этапе не обнуляют:
`deal.stage_entered_at` не трогается. Отчёт «Активность КАМов» обязан показывать те же
«дней на этапе» и «зависла», что доска (`GET /requests/{id}`), до и после перевода — и для
текущего периода (по `stage_entered_at`), и для прошедшего (по строкам-входам в этап).

`NOW` = 20.10.2026 12:00 МСК (подмена `_now` отчёта и карточки заявки); время строк
перевода фиксируется относительно NOW, а не реального `now()` БД.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.models import AppUser, Deal, DealStageHistory
from app.modules.reporting import kam_activity as ka
from app.modules.reporting.kam_activity import KamActivityParams
from app.modules.requests import serializers
from app.modules.workflow import service as workflow_service

from . import factories
from .workflow_factories import delete_first_contact_ops, raw_operations

API = "/api/v1"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=ka.REPORT_TZ)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ka, "_now", lambda: NOW)
    monkeypatch.setattr(serializers, "_now", lambda: NOW)


def _request(report: dict[str, Any], deal: Deal) -> dict[str, Any] | None:
    for row in report["rows"]:
        for node in row["universities"]:
            for request in node["requests"]:
                if request["request_id"] == str(deal.id):
                    return request
    return None


async def _report_request(session, viewer: AppUser, deal: Deal, **kwargs: Any) -> dict[str, Any]:
    report = await ka.build_kam_activity(session, viewer, KamActivityParams(**kwargs))
    request = _request(report, deal)
    assert request is not None
    return request


async def _board(api, viewer: AppUser, deal: Deal) -> dict[str, Any]:
    response = await api.as_user(viewer).get(f"{API}/requests/{deal.id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _live_migration(session, workflow, admin: AppUser, stages, deal: Deal, at: datetime):
    """Живой перевод: пакет «удалить «Первичный контакт» с переносом в «Новая»» через
    сервис конструктора; время строки перевода — `at` (относительно NOW)."""
    operations = delete_first_contact_ops(stages)
    change = await workflow_service.create_change(
        session,
        workflow_id=workflow.id,
        author=admin,
        operations=operations,
        raw_operations=raw_operations(operations),
        comment=None,
    )
    await workflow_service.apply_change(session, change_id=change.id, approver=admin)
    moved = await session.execute(
        sa.update(DealStageHistory)
        .where(DealStageHistory.deal_id == deal.id, DealStageHistory.kind == "system_migration")
        .values(created_at=at)
        .returning(DealStageHistory.id)
    )
    assert len(moved.all()) == 1
    await session.refresh(deal)


async def _lms_event(session, deal: Deal, at: datetime) -> None:
    """Событие LMS: строка истории без смены этапа (from = to, kind='integration')."""
    stage_id = deal.stage_id
    session.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=stage_id,
            to_stage_id=stage_id,
            from_stage_name="—",
            to_stage_name="—",
            actor_user_id=None,
            kind="integration",
            reason="lms.progress",
            created_at=at,
        )
    )
    await session.flush()


@pytest.fixture
async def ring(db_session, workflows, admin_user) -> dict[str, Any]:
    """kam1: заявка на «Первичный контакт» с −40 д (создана −60 д) — зависла (порог 14)."""
    wf, stages = workflows["b2b"]
    head = await factories.make_user(db_session, roles=("head_kam",), full_name="Морозов Д. А.")
    kam1 = await factories.make_user(db_session, roles=("kam",), full_name="Петров К. А.")
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=kam1,
        title="МФТИ — переподготовка",
        created_at=ago(60),
        path=[("new", ago(60)), ("first_contact", ago(40))],
    )
    return {"wf": wf, "stages": stages, "head": head, "kam1": kam1, "deal": deal}


async def test_live_migration_keeps_days_on_stage_and_stuck(
    api, db_session, admin_user, ring
) -> None:
    head, deal = ring["head"], ring["deal"]

    before = await _report_request(db_session, head, deal)
    board_before = await _board(api, head, deal)
    assert before["stage"]["code"] == "first_contact"
    assert (before["days_on_stage"], before["is_stuck"]) == (40, True)
    assert (board_before["stuck_days"], board_before["is_stuck"]) == (40, True)

    await _lms_event(db_session, deal, ago(6))
    after_lms = await _report_request(db_session, head, deal)
    assert (after_lms["days_on_stage"], after_lms["is_stuck"]) == (40, True)

    await _live_migration(db_session, ring["wf"], admin_user, ring["stages"], deal, ago(5))
    await _lms_event(db_session, deal, ago(4))
    after = await _report_request(db_session, head, deal)
    board_after = await _board(api, head, deal)
    # этап — целевой этап перевода, срок на этапе не обнулился (как на доске и в боте)
    assert after["stage"]["code"] == "new"
    assert (after["days_on_stage"], after["is_stuck"]) == (40, True)
    assert (board_after["stuck_days"], board_after["is_stuck"]) == (40, True)
    assert after["stage_since"] == before["stage_since"]
    # строки перевода и LMS — не взаимодействия КАМа
    assert after["interactions"]["total"] == before["interactions"]["total"] == 0

    # то же через API отчёта (кэш KeyDB в тестах недоступен — читается из PG)
    response = await api.as_user(head).get(f"{API}/reports/kam-activity")
    assert response.status_code == 200
    api_request = _request(response.json(), deal)
    assert api_request is not None
    assert (api_request["days_on_stage"], api_request["is_stuck"]) == (40, True)


async def test_past_period_entry_ignores_migration_and_lms_rows(
    db_session, admin_user, ring
) -> None:
    """Прошедший период (as_of — его конец): вход в этап — по строкам-входам истории."""
    head, deal = ring["head"], ring["deal"]
    await _live_migration(db_session, ring["wf"], admin_user, ring["stages"], deal, ago(5))
    await _lms_event(db_session, deal, ago(4))

    date_to = (NOW - timedelta(days=2)).date()  # конец периода 19.10 00:00 МСК
    request = await _report_request(
        db_session, head, deal, date_from=date_to - timedelta(days=29), date_to=date_to
    )
    as_of = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), ka.REPORT_TZ)
    assert request["stage"]["code"] == "new"  # этап на конец периода — после перевода
    assert datetime.fromisoformat(request["stage_since"]) == ago(40)
    assert request["days_on_stage"] == (as_of - ago(40)).days == 38
    assert request["is_stuck"] is True

    summary = await ka.build_kam_activity_summary(
        db_session,
        head,
        KamActivityParams(date_from=date_to - timedelta(days=29), date_to=date_to),
    )
    stages = {stage["code"]: stage for stage in summary["stages"]["b2b"]}
    # «дошли»: только входы в этап (создание → «Новая», ручной переход → «Первичный
    # контакт»); строка перевода «Первичный контакт» → «Новая» заявку не «продвигает»
    assert stages["new"]["reached"] == 1 and stages["first_contact"]["reached"] == 1
    assert stages["first_contact"]["deleted"] is True
    # интервал не рвётся строками перевода и LMS: один интервал −40…конец периода,
    # отнесён к этапу на его конце («Новая») — как «дней на этапе» и доска; интервал
    # «Новая» −60…−40 закончился до периода и в среднее не входит
    assert stages["new"]["intervals"] == 1
    expected = (as_of - ago(40)).total_seconds() / 86400
    assert abs(stages["new"]["avg_days"] - expected) <= 0.1
    assert stages["first_contact"]["intervals"] == 0
    assert stages["new"]["stuck"] == 1 and stages["new"]["on_stage_at_end"] == 1


async def test_reopen_style_manual_entry_moves_stage_since(db_session, workflows, ring) -> None:
    """Ручной переход после перевода — новый вход в этап: срок считается от него."""
    wf, stages = workflows["b2b"]
    kam1, head = ring["kam1"], ring["head"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=kam1,
        created_at=ago(50),
        path=[
            ("new", ago(50)),
            ("first_contact", ago(45)),
            ("new", ago(10), None, "system_migration"),
            ("negotiation", ago(3)),
        ],
    )
    request = await _report_request(db_session, head, deal)
    assert request["stage"]["code"] == "negotiation"
    assert (request["days_on_stage"], request["is_stuck"]) == (3, False)
    assert request["interactions"]["transitions"] == 1  # ручной переход КАМа в периоде


async def test_history_without_creation_row_falls_back_to_stage_entered_at(
    db_session, workflows, ring
) -> None:
    """Заявка без строк-входов в истории (ручная правка БД) — `stage_entered_at`."""
    wf, stages = workflows["b2b"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=ring["kam1"],
        created_at=ago(80),
        path=[("first_contact", ago(80))],
    )
    await db_session.execute(sa.delete(DealStageHistory).where(DealStageHistory.deal_id == deal.id))
    deal.stage_entered_at = ago(33)
    await db_session.flush()
    date_to = (NOW - timedelta(days=1)).date()
    request = await _report_request(
        db_session,
        ring["head"],
        deal,
        date_from=date_to - timedelta(days=29),
        date_to=date_to,
    )
    assert datetime.fromisoformat(request["stage_since"]) == ago(33)
    assert uuid.UUID(request["stage"]["id"]) == stages["first_contact"].id
