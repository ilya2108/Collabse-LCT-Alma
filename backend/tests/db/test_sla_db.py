"""Вкладка «Просрочки» (`/reports/sla`) на настоящем PostgreSQL.

`NOW` = 20.10.2026 12:00 МСК — подмена `_now` и отчёта, и `kam_activity` (сравнение
срезов); «30 дней» = 21.09–20.10, срез T = NOW, срез прошлого периода T_prev = 21.09 00:00
МСК (= NOW − 29,5 сут). Данные — относительно NOW общими фабриками; KeyDB в тестах
недоступен — отчёт читается из PG. Сравнение с фильтром доски `?stuck=true` идёт по
реальному времени: доска считает в SQL через `now()`.
"""

from __future__ import annotations

import io
import os
import time as clock
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.access import DealScope, reset_role_catalog
from app.core.errors import ApiError
from app.models import (
    AppSetting,
    AppUser,
    AuditLog,
    Deal,
    DealStageHistory,
    Notification,
    WorkflowStage,
)
from app.modules.reporting import kam_activity as ka
from app.modules.reporting import sla
from app.modules.reporting.kam_activity import KamActivityParams
from app.modules.reporting.sla_schemas import SlaKamReportOut, SlaReportOut
from app.modules.workflow import service as workflow_service

from . import factories
from .rbac_factories import make_role
from .workflow_factories import delete_first_contact_ops, raw_operations

API = "/api/v1"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=ka.REPORT_TZ)
T_PREV = datetime(2026, 9, 21, 0, 0, tzinfo=ka.REPORT_TZ)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sla, "_now", lambda: NOW)
    monkeypatch.setattr(ka, "_now", lambda: NOW)


@pytest.fixture
async def people(db_session) -> dict[str, AppUser]:
    make = factories.make_user
    head = await make(db_session, roles=("head_kam",), full_name="Морозов Дмитрий Александрович")
    return {
        "head": head,
        "kam1": await make(
            db_session, roles=("kam",), full_name="Петров Константин Андреевич", manager=head
        ),
        "kam2": await make(
            db_session, roles=("kam",), full_name="Иванова Мария Сергеевна", manager=head
        ),
    }


async def _report(session, viewer: AppUser, **kwargs: Any) -> dict[str, Any]:
    report = await sla.build_sla(session, viewer, KamActivityParams(**kwargs))
    SlaReportOut.model_validate(report)  # форма ответа = схема OpenAPI
    return report


async def _kam(session, viewer: AppUser, kam: AppUser, **kwargs: Any) -> dict[str, Any]:
    report = await sla.build_sla_kam(session, viewer, kam.id, KamActivityParams(**kwargs))
    SlaKamReportOut.model_validate(report)
    return report


def _item(report: dict[str, Any], deal: Deal) -> dict[str, Any] | None:
    return next((i for i in report["overdue"]["items"] if i["request_id"] == str(deal.id)), None)


def _row(report: dict[str, Any], user: AppUser) -> dict[str, Any]:
    return next(row for row in report["kams"] if row["kam_id"] == str(user.id))


async def _deal(session, workflows, kam: AppUser, path: list[tuple], **kwargs: Any) -> Deal:
    wf, stages = workflows[kwargs.pop("pipeline", "b2b")]
    return await factories.make_deal(
        session,
        workflow=wf,
        stages=stages,
        responsible=kam,
        created_at=kwargs.pop("created_at", path[0][1]),
        path=path,
        **kwargs,
    )


async def _transfer(session, deal: Deal, src: AppUser, dst: AppUser, at: datetime, actor) -> None:
    """Передача заявки как в ручке: ответственный и аудит `request.assigned`."""
    deal.responsible_user_id = dst.id
    session.add(
        AuditLog(
            actor_user_id=actor.id,
            action="request.assigned",
            entity_type="deal",
            entity_id=deal.id,
            before={"assignee_id": str(src.id)},
            after={"assignee_id": str(dst.id), "reason": "portfolio_transfer"},
            created_at=at,
        )
    )
    await session.flush()


async def _history(session, deal: Deal, stage: WorkflowStage, at: datetime, kind: str) -> None:
    session.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=deal.stage_id,
            to_stage_id=stage.id,
            from_stage_name="—",
            to_stage_name=stage.name,
            actor_user_id=None,
            kind=kind,
            created_at=at,
        )
    )
    if kind != "integration":
        deal.stage_id = stage.id
    if kind == "manual":
        deal.stage_entered_at = at
    await session.flush()


# --- 1. каскад норм ---------------------------------------------------------------------


async def test_norm_cascade_stage_then_workflow(db_session, workflows, people) -> None:
    wf, stages = workflows["b2b"]
    stages["negotiation"].stuck_threshold_days = None  # наследует процесс (14)
    await db_session.flush()
    kam1 = people["kam1"]
    on_new = await _deal(db_session, workflows, kam1, [("new", ago(10))])
    on_neg = await _deal(
        db_session,
        workflows,
        kam1,
        [("new", ago(30)), ("first_contact", ago(26)), ("negotiation", ago(20))],
    )
    report = await _report(db_session, people["head"])
    new_item, neg_item = _item(report, on_new), _item(report, on_neg)
    assert (new_item["norm_days"], new_item["norm_source"], new_item["delay_days"]) == (
        7,
        "stage",
        3.0,
    )
    assert (neg_item["norm_days"], neg_item["norm_source"], neg_item["delay_days"]) == (
        14,
        "workflow",
        6.0,
    )
    assert neg_item["bucket"] == "d4_7" and new_item["bucket"] == "d1_3"
    rows = {row["code"]: row for row in report["stages"]["b2b"]}
    assert rows["negotiation"]["norm_source"] == "workflow"
    assert rows["new"]["norm_source"] == "stage"
    assert rows["negotiation"]["overdue"] == 1 and rows["new"]["overdue"] == 1
    assert "completed" not in rows  # терминальные этапы не выводятся
    assert report["kpi"]["overdue"]["value"] == 2
    assert report["definition"] == sla.DEFINITION
    assert [d["term"] for d in report["definitions"]][0] == "Просрочено"


# --- 2–5. переводы схемы, LMS, возврат ---------------------------------------------------


async def _live_migration(session, workflow, admin: AppUser, stages, deal: Deal, at: datetime):
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
    await session.execute(
        sa.update(DealStageHistory)
        .where(DealStageHistory.deal_id == deal.id, DealStageHistory.kind == "system_migration")
        .values(created_at=at)
    )
    await session.refresh(deal)


async def test_system_migration_keeps_clock_and_matches_kam_activity_prev(
    db_session, workflows, people, admin_user
) -> None:
    wf, stages = workflows["b2b"]
    deal = await _deal(
        db_session,
        workflows,
        people["kam1"],
        [("new", ago(60)), ("first_contact", ago(50))],
        title="МФТИ — переподготовка",
    )
    await _live_migration(db_session, wf, admin_user, stages, deal, ago(5))

    report = await _report(db_session, people["head"])
    item = _item(report, deal)
    # время на этапе не обнулилось; на T — этап «Новая» и её норма (7)
    assert item["stage"]["code"] == "new" and item["norm_days"] == 7
    assert datetime.fromisoformat(item["stage_since"]) == ago(50)
    assert item["delay_days"] == 43.0 and item["bucket"] == "d31_plus"
    # срез прошлого периода: «Первичный контакт (удалён)» и его норма — как kam_activity
    prev_from, prev_to = date(2026, 8, 22), date(2026, 9, 20)
    detail = await ka.build_kam_activity(
        db_session, people["head"], KamActivityParams(date_from=prev_from, date_to=prev_to)
    )
    request = next(
        r
        for row in detail["rows"]
        for node in row["universities"]
        for r in node["requests"]
        if r["request_id"] == str(deal.id)
    )
    assert request["stage"]["name"] == "Первичный контакт (удалён)"
    assert request["is_stuck"] is True and request["threshold_days"] == 14
    assert report["kpi"]["overdue"]["prev"] == detail["totals"]["stuck"] == 1
    data = await sla.load_dataset(
        db_session,
        KamActivityParams(),
        DealScope.unrestricted(),
        ka.resolve_bounds(
            KamActivityParams(date_from=date(2026, 9, 21), date_to=date(2026, 10, 20)), now=NOW
        ),
    )
    (prev_item,) = [i for i in data.snap_prev if i.visit.deal_id == deal.id]
    assert data.stages[prev_item.stage_id].label == "Первичный контакт (удалён)"
    assert prev_item.delay == (T_PREV - ago(50)) - timedelta(days=14)
    # оговорка о переводе — факт без оценки
    assert any("перенесены на «Новая»" in note for note in report["notes"])


async def test_migration_to_terminal_ends_visit(db_session, workflows, people) -> None:
    _wf, stages = workflows["b2b"]
    kam1 = people["kam1"]
    overdue_before = await _deal(db_session, workflows, kam1, [("negotiation", ago(50))])
    await _history(db_session, overdue_before, stages["rejected"], ago(10), "system_migration")
    early = await _deal(db_session, workflows, kam1, [("negotiation", ago(10))])
    await _history(db_session, early, stages["rejected"], ago(5), "system_migration")

    report = await _report(db_session, people["head"])
    assert report["kpi"]["overdue"]["value"] == 0  # визит завершён переводом
    assert report["kpi"]["overdue"]["prev"] == 1  # прошлый срез просрочку сохраняет
    assert report["kpi"]["decided"]["value"] == 0  # перевод в «решённые» не попадает
    assert report["kpi"]["decided"]["prev"] == 1  # срок истёк ещё в прошлом периоде
    assert report["kpi"]["completed"]["value"] == 0


async def test_migration_to_terminal_and_back_counts_like_board(
    api, db_session, workflows, people, monkeypatch
) -> None:
    """Перевод схемы увёл открытую заявку на терминальный этап, следующий вернул на рабочий:
    «Просрочено» совпадает с «зависшими» отчёта КАМов и фильтром доски `?stuck=true`."""
    real_now = factories.utcnow()
    monkeypatch.setattr(sla, "_now", lambda: real_now)
    monkeypatch.setattr(ka, "_now", lambda: real_now)
    _wf, stages = workflows["b2b"]
    deal = await _deal(
        db_session, workflows, people["kam1"], [("negotiation", real_now - timedelta(days=30))]
    )
    await _history(
        db_session, deal, stages["rejected"], real_now - timedelta(days=10), "system_migration"
    )
    await _history(
        db_session, deal, stages["contract_prep"], real_now - timedelta(days=5), "system_migration"
    )

    report = await _report(db_session, people["head"])
    item = _item(report, deal)
    assert item is not None and item["stage"]["code"] == "contract_prep"
    assert datetime.fromisoformat(item["stage_since"]) == real_now - timedelta(days=30)
    detail = await ka.build_kam_activity(db_session, people["head"], KamActivityParams())
    assert report["kpi"]["overdue"]["value"] == detail["totals"]["stuck"] == 1
    board = await api.as_user(people["head"]).get(f"{API}/requests", params={"stuck": "true"})
    assert board.status_code == 200 and board.json()["total"] == 1


async def test_lms_integration_row_does_not_split_visit(db_session, workflows, people) -> None:
    _wf, stages = workflows["b2b"]
    deal = await _deal(db_session, workflows, people["kam1"], [("negotiation", ago(20))])
    await _history(db_session, deal, stages["negotiation"], ago(10), "integration")
    report = await _report(db_session, people["head"])
    item = _item(report, deal)
    assert datetime.fromisoformat(item["stage_since"]) == ago(20)
    assert item["delay_days"] == 6.0
    assert report["kpi"]["completed"]["value"] == 0


async def test_return_starts_new_visit_and_completes_previous(
    db_session, workflows, people
) -> None:
    kam1 = people["kam1"]
    deal = await _deal(
        db_session,
        workflows,
        kam1,
        [
            ("new", ago(30)),
            ("first_contact", ago(25)),
            ("negotiation", ago(20)),
            ("first_contact", ago(3), kam1, "manual", True),
        ],
    )
    report = await sla.build_sla(
        db_session, people["head"], KamActivityParams(), include_late=True, use_cache=False
    )
    assert _item(report, deal) is None  # новый визит на «Первичном контакте» ещё в норме
    late = report["late_closed"]["items"]
    assert [item["request_id"] for item in late] == [str(deal.id)]
    assert late[0]["delay_days"] == 3.0 and late[0]["exited_to"]["is_return"] is True
    assert late[0]["stage"]["code"] == "negotiation"
    kpi = report["kpi"]
    assert kpi["late_closed"]["value"] == 1
    assert kpi["completed"]["value"] == 3  # «Новая», «Первичный контакт», «Переговоры»
    # «не уложились» — в момент истечения нормы (−6 сут); два первых этапа — в норме
    assert kpi["decided"]["value"] == 3 and kpi["on_time"]["value"] == 2


# --- 6. совпадение с отчётом КАМов и доской ---------------------------------------------


async def test_overdue_equals_kam_activity_stuck_and_board_filter(
    api, db_session, workflows, people, monkeypatch
) -> None:
    real_now = factories.utcnow()
    monkeypatch.setattr(sla, "_now", lambda: real_now)
    monkeypatch.setattr(ka, "_now", lambda: real_now)

    def back(days: float) -> datetime:
        return real_now - timedelta(days=days)

    kam1, kam2 = people["kam1"], people["kam2"]
    plan = [
        (kam1, [("new", back(10.5))]),  # просрочена (7)
        (kam1, [("new", back(3.5))]),
        (kam1, [("new", back(40)), ("negotiation", back(20.5))]),  # просрочена (14)
        (kam2, [("new", back(30)), ("first_contact", back(14.5))]),  # просрочена
        (kam2, [("new", back(30)), ("first_contact", back(13.5))]),
        (kam2, [("new", back(60)), ("completed", back(40))]),  # закрыта
    ]
    for kam, path in plan:
        await _deal(db_session, workflows, kam, path)
    await _deal(db_session, workflows, kam2, [("new", back(9.5))], pipeline="b2c")

    report = await _report(db_session, people["head"])
    detail = await ka.build_kam_activity(db_session, people["head"], KamActivityParams())
    stuck = {row["kam_id"]: row["stuck"] for row in detail["rows"]}
    for user in (kam1, kam2):
        assert _row(report, user)["overdue"] == stuck[str(user.id)]
    assert report["kpi"]["overdue"]["value"] == detail["totals"]["stuck"] == 4

    board = await api.as_user(people["head"]).get(
        f"{API}/requests", params={"stuck": "true", "limit": 200}
    )
    assert board.status_code == 200, board.text
    assert board.json()["total"] == report["kpi"]["overdue"]["value"]


# --- 7–8. область и пустые данные -------------------------------------------------------


async def test_scope(api, db_session, workflows, people) -> None:
    kam1, kam2 = people["kam1"], people["kam2"]
    await _deal(db_session, workflows, kam1, [("new", ago(10))])
    await _deal(db_session, workflows, kam2, [("new", ago(12))])
    await _deal(db_session, workflows, kam2, [("new", ago(12))], pipeline="b2c")

    # КАМ: reports:read без reports:all — «только свои»
    own = api.as_user(kam1)
    response = await own.get(f"{API}/reports/sla")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"]["own_only"] is True
    assert [row["kam_id"] for row in body["kams"]] == [str(kam1.id)]
    assert body["attention"]["items"] == [] and body["team"] == {"kams_total": 1}
    assert body["kpi"]["overdue"]["value"] == 1
    assert (await own.get(f"{API}/reports/sla", params={"kam_id": str(kam2.id)})).status_code == 403
    foreign = await own.get(f"{API}/reports/sla/kams/{kam2.id}")
    assert foreign.status_code == 403
    assert foreign.json()["error"]["message"] == "КАМ видит отчёт только по своим заявкам"
    mine = await own.get(f"{API}/reports/sla/kams/{kam1.id}")
    assert mine.status_code == 200 and mine.json()["team"] is None

    # своя роль «КАМ B2C»: процесс b2b вне области — 403
    await make_role(db_session, code="kam_b2c_sla", workflow_types=("b2c",))
    kam_c = await factories.make_user(db_session, roles=("kam_b2c_sla",))
    reset_role_catalog()
    denied = await api.as_user(kam_c).get(f"{API}/reports/sla", params={"workflow_type": "b2b"})
    assert denied.status_code == 403
    assert denied.json()["error"]["message"] == "Процесс B2B недоступен вашей роли"

    # руководитель только B2B (reports:all в одном процессе)
    from app.core.rbac_catalog import SYSTEM_ROLES

    await make_role(
        db_session,
        code="head_b2b_sla",
        permissions=SYSTEM_ROLES["head_kam"].permissions,
        workflow_types=("b2b",),
    )
    head_b2b = await factories.make_user(db_session, roles=("head_b2b_sla",))
    reset_role_catalog()  # каталог уже загружен запросами выше — перечитать с новыми ролями
    scoped = (await api.as_user(head_b2b).get(f"{API}/reports/sla")).json()
    assert list(scoped["stages"]) == ["b2b"] and list(scoped["by_workflow"]) == ["b2b"]
    assert scoped["kpi"]["overdue"]["value"] == 2
    assert scoped["team"]["kams_total"] == 2
    head = (await api.as_user(people["head"]).get(f"{API}/reports/sla")).json()
    assert head["kpi"]["overdue"]["value"] == 3 and head["team"]["kams_total"] == 2
    kam_view = await api.as_user(people["head"]).get(f"{API}/reports/sla/kams/{kam2.id}")
    assert kam_view.json()["team"]["overdue_pct"] == 100.0

    with pytest.raises(ApiError) as excinfo:
        await sla.build_sla(
            db_session,
            people["head"],
            KamActivityParams(workflow_type="b2b"),
            scope=DealScope(rules=(("b2c", None),)),
        )
    assert excinfo.value.status_code == 403


async def test_empty_period_no_data(api, db_session, workflows, people) -> None:
    report = await _report(db_session, people["head"])
    kpi = report["kpi"]
    assert kpi["overdue"]["value"] == 0 and kpi["active"]["value"] == 0
    assert kpi["overdue_pct"]["value"] is None and kpi["on_time_pct"]["value"] is None
    assert kpi["delay_median_days"]["value"] is None
    assert report["headline"] == "Заявок в работе нет."
    assert report["overdue"] == {"total": 0, "truncated": False, "items": []}
    assert report["reminders"]["info"]["coverage"] == "none"
    assert report["reminders"]["team"] is None
    assert all(point["active"] == 0 for point in report["trend"]["points"])
    head = api.as_user(people["head"])
    empty = await head.get(f"{API}/reports/sla/kams/{people['kam1'].id}")
    assert empty.status_code == 200, empty.text
    assert empty.json()["kpi"]["active"]["value"] == 0
    assert empty.json()["headline"] == "У Петрова К. А.: заявок в работе нет."
    missing = await head.get(f"{API}/reports/sla/kams/{uuid.uuid4()}")
    assert missing.status_code == 404
    # не КАМ процессов области и без данных (администратор): 404, как у несуществующего, —
    # ФИО и роли не раскрываются ни разбором, ни строкой фильтра kam_id
    admin = await factories.make_user(
        db_session, roles=("admin",), full_name="Соколова Анна Викторовна"
    )
    hidden = await head.get(f"{API}/reports/sla/kams/{admin.id}")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]
    filtered = await head.get(f"{API}/reports/sla", params={"kam_id": str(admin.id)})
    assert filtered.status_code == 200 and filtered.json()["kams"] == []
    assert "Соколова" not in filtered.text
    bad = await head.get(f"{API}/reports/sla", params={"from": "2026-10-10", "to": "2026-10-01"})
    assert bad.status_code == 400


# --- 9–11. корзины, тренд, решения ------------------------------------------------------


async def test_aging_buckets_from_db(db_session, workflows, people) -> None:
    kam1 = people["kam1"]
    for delay in (2, 5, 10, 20, 40):  # норма «Переговоров» — 14
        await _deal(db_session, workflows, kam1, [("negotiation", ago(14 + delay + 0.25))])
    report = await _report(db_session, people["head"])
    assert {row["key"]: row["count"] for row in report["aging"]} == dict.fromkeys(
        sla.BUCKET_KEYS, 1
    )
    row = _row(report, kam1)
    assert row["aging"] == dict.fromkeys(sla.BUCKET_KEYS, 1)
    assert row["overdue_30_plus"] == 1 and row["delay_max_days"] == 40.2
    assert row["delay_median_days"] == 10.2


async def test_trend_weekly_snapshots(db_session, workflows, people) -> None:
    kam1 = people["kam1"]
    entries = [ago(40), ago(70), ago(20)]
    deals = [await _deal(db_session, workflows, kam1, [("negotiation", at)]) for at in entries]
    await _history(db_session, deals[1], workflows["b2b"][1]["contract_prep"], ago(45), "manual")
    report = await _report(db_session, people["head"])
    trend = report["trend"]
    assert trend["granularity"] == "week" and trend["min_n"] == 5
    for point in trend["points"]:
        moment = datetime.fromisoformat(point["snapshot_at"])
        active = overdue = 0
        # «Переговоры»: −70 (до −45, затем «Подготовка договора» с нового входа), −40, −20
        visits = [(ago(70), ago(45)), (ago(45), None), (ago(40), None), (ago(20), None)]
        for start, end in visits:
            if start <= moment and (end is None or moment < end):
                active += 1
                overdue += moment - start > timedelta(days=14)
        assert (point["active"], point["overdue"]) == (active, overdue), point["label"]
    assert trend["points"][-1]["complete"] is False
    assert trend["points"][-1]["overdue"] == report["kpi"]["overdue"]["value"]


async def test_decided_on_time_and_closed_late(db_session, workflows, people) -> None:
    kam1 = people["kam1"]
    lost = await _deal(
        db_session,
        workflows,
        kam1,
        [("new", ago(25)), ("negotiation", ago(20)), ("rejected", ago(2))],
    )
    await _deal(db_session, workflows, kam1, [("new", ago(10)), ("first_contact", ago(5))])
    report = await sla.build_sla(
        db_session, people["head"], KamActivityParams(), include_late=True, use_cache=False
    )
    kpi = report["kpi"]
    # решения в периоде: «Новая» −25…−20 (в норме), «Переговоры» (срок истёк −6),
    # «Новая» −10…−5 (в норме)
    assert (kpi["decided"]["value"], kpi["on_time"]["value"]) == (3, 2)
    assert kpi["on_time_pct"]["value"] == 66.7
    assert kpi["completed"]["value"] == 3 and kpi["late_closed"]["value"] == 1
    (late,) = report["late_closed"]["items"]
    assert late["request_id"] == str(lost.id) and late["delay_days"] == 4.0
    assert late["exited_to"] == {"name": "Отказ", "is_return": False, "outcome": "lost"}
    assert kpi["late_delay_avg_days"]["value"] == 4.0


# --- 12–13. передача портфеля и прошлый период -------------------------------------------


async def test_transfer_attribution(api, db_session, workflows, people) -> None:
    kam1, kam2, head = people["kam1"], people["kam2"], people["head"]
    inherited = await _deal(
        db_session, workflows, kam1, [("new", ago(56)), ("negotiation", ago(50))]
    )
    await _transfer(db_session, inherited, kam1, kam2, ago(10), head)
    closed = await _deal(db_session, workflows, kam1, [("negotiation", ago(30))])
    await _transfer(db_session, closed, kam1, kam2, ago(8), head)
    await _history(db_session, closed, workflows["b2b"][1]["contract_prep"], ago(3), "manual")

    report = await sla.build_sla(
        db_session, head, KamActivityParams(), include_late=True, use_cache=False
    )
    item = _item(report, inherited)
    # текущая просрочка — у нового владельца, «получена просроченной»
    assert item["kam_id"] == str(kam2.id) and item["inherited"] is True
    assert datetime.fromisoformat(item["with_kam_since"]) == ago(10)
    row1, row2 = _row(report, kam1), _row(report, kam2)
    assert (row2["overdue"], row2["own_overdue"], row2["inherited"]) == (1, 0, 1)
    # срез прошлого периода — у прежнего владельца
    assert row1["prev"]["overdue"] == 1 and row2["prev"]["overdue"] == 0
    # «не уложились» до передачи — у прежнего владельца (срок closed истёк −16 у kam1)
    assert row1["decided"] == 1 and row1["on_time"] == 0
    # закрыта с опозданием у нового владельца — с флагом «получена просроченной»
    (late,) = report["late_closed"]["items"]
    assert late["kam_id"] == str(kam2.id) and late["inherited"] is True
    assert row2["late_closed"] == 1 and row2["late_inherited"] == 1
    assert sla.TRANSFER_NOTE in report["notes"]
    # «Поступило»: обе заявки созданы до периода; передачи kam1 → kam2 внутри команды
    # командный показатель не меняют, у самого kam2 — это два поступления
    assert report["kpi"]["inflow"]["value"] == 0
    assert (row1["inflow"], row2["inflow"]) == (0, 2)
    # фильтр kam_id=kam1 находит заявки, которые сейчас у kam2
    filtered = await _report(db_session, head, kam_ids=(kam1.id,))
    assert [row["kam_id"] for row in filtered["kams"]] == [str(kam1.id)]
    assert filtered["kpi"]["decided"]["value"] == 1
    assert filtered["kpi"]["overdue"]["prev"] == 1 and filtered["kpi"]["overdue"]["value"] == 0
    # правило отбора: унаследованная не считается
    assert row2["attention"] is False
    facts_view = await _kam(db_session, head, kam2)
    assert facts_view["overdue"]["items"][0]["inherited"] is True


async def test_previous_period_delta(db_session, workflows, people) -> None:
    kam1 = people["kam1"]
    await _deal(db_session, workflows, kam1, [("negotiation", ago(60))])  # просрочена везде
    await _deal(db_session, workflows, kam1, [("negotiation", ago(20))])  # только на T
    await _deal(  # закрыта до T, на T_prev — просрочена
        db_session,
        workflows,
        kam1,
        [("negotiation", ago(50)), ("contract_prep", ago(10))],
    )
    report = await _report(db_session, people["head"])
    overdue = report["kpi"]["overdue"]
    assert (overdue["value"], overdue["prev"], overdue["delta"], overdue["compare"]) == (
        2,
        2,
        0,
        "snapshot",
    )
    assert report["kpi"]["decided"]["compare"] == "period"
    assert report["comparison"] == {"snapshot_label": "к 21.09", "period_label": "к 22.08–20.09"}
    assert report["previous_period"]["from"] == "2026-08-22"
    assert "на 21.09 — 2 из 2, 100 %" in report["headline"]
    aging = {row["key"]: (row["count"], row["prev"]) for row in report["aging"]}
    assert aging["d31_plus"] == (1, 0)


# --- 14–16. напоминания, последнее действие, архив ---------------------------------------


async def _notify(
    session,
    deal: Deal,
    recipient: AppUser,
    at: datetime,
    *,
    level: str = "responsible",
    status: str = "sent",
    bucket: int = 5,
    suffix: bool = False,
) -> None:
    key = f"stuck:{deal.id}:{deal.stage_id}:{level}:{bucket}"
    session.add(
        Notification(
            event_type="request.stuck",
            entity_type="deal",
            entity_id=deal.id,
            recipient_user_id=recipient.id,
            channel="telegram",
            address="1",
            body="Заявка зависла",
            payload={"escalation": True} if level == "manager" else {},
            dedup_key=f"{key}:{recipient.id}" if suffix else key,
            status=status,
            sent_at=at + timedelta(minutes=1) if status == "sent" else None,
            created_at=at,
        )
    )
    await session.flush()


async def test_reminders_and_reaction(db_session, workflows, people) -> None:
    kam1, head = people["kam1"], people["head"]
    reacted = await _deal(db_session, workflows, kam1, [("negotiation", ago(25))])
    report = await _report(db_session, head)
    assert report["reminders"]["info"]["coverage"] == "none"
    assert _row(report, kam1)["reminders"] is None

    await _notify(db_session, reacted, kam1, ago(10), suffix=True)
    await _notify(db_session, reacted, kam1, ago(7), bucket=6, status="skipped")
    await _notify(db_session, reacted, head, ago(4), level="manager", bucket=7, suffix=True)
    delivered_at = ago(10) + timedelta(minutes=1)
    await factories.make_comment(
        db_session, deal=reacted, author=kam1, at=delivered_at + timedelta(hours=5)
    )
    early = await _deal(db_session, workflows, kam1, [("negotiation", ago(60))])
    await _notify(db_session, early, kam1, ago(40))  # первое напоминание — раньше периода
    await _notify(db_session, early, kam1, ago(20), bucket=9)

    report = await _report(db_session, head)
    info = report["reminders"]["info"]
    assert (info["coverage"], info["prev_coverage"]) == ("full", "partial")
    assert info["journal_from"] == ago(40).date().isoformat()
    assert info["check_enabled"] is True and info["note"] == sla.REMINDERS_NOTE
    stats = _row(report, kam1)["reminders"]
    assert stats["episodes"] == 1 and stats["reacted_72h"] == 1
    assert stats["reaction_median_hours"] == 5.0 and stats["reacted_72h_pct"] == 100.0
    assert stats["visits_reminded"] == 2 and (stats["sent"], stats["delivered"]) == (3, 2)
    assert stats["visits_escalated"] == 1  # эскалация — владельцу заявки на момент отправки
    item = _item(report, reacted)
    assert (item["reminders"], item["delivered"], item["escalated"]) == (2, 1, True)
    kam_view = await _kam(db_session, head, kam1)
    (episode,) = kam_view["reminders"]["items"]
    assert episode["status"] == "reacted_72h" and episode["reaction_kind"] == "comment"
    assert kam_view["reminders"]["prev"]["episodes"] == 1  # эпизод early — прошлый период
    assert kam_view["team"]["reaction_median_hours"] == 5.0

    setting = await db_session.get(AppSetting, "stuck_check_enabled")
    if setting is None:
        db_session.add(AppSetting(key="stuck_check_enabled", value={"value": False}))
    else:
        setting.value = {"value": False}
    await db_session.flush()
    report = await _report(db_session, head)
    assert report["reminders"]["info"]["check_enabled"] is False
    assert any(note.startswith("Проверка зависших выключена") for note in report["notes"])


async def test_last_action_older_than_window(db_session, workflows, people) -> None:
    kam1, kam2 = people["kam1"], people["kam2"]
    acted = await _deal(
        db_session, workflows, kam1, [("negotiation", ago(200))], created_at=ago(200)
    )
    await factories.make_comment(db_session, deal=acted, author=kam2, at=ago(150))
    silent = await _deal(db_session, workflows, kam1, [("negotiation", ago(200))])
    await factories.make_comment(db_session, deal=silent, author=kam1, at=ago(190))
    report = await _report(db_session, people["head"])
    item = _item(report, acted)
    assert item["last_action"]["kind"] == "comment"
    assert item["last_action"]["days_ago"] == 150.0
    assert item["last_action"]["actor"] == "Иванова М. С."
    assert item["no_action_after_due"] is False  # действие любого автора после срока
    assert _item(report, silent)["no_action_after_due"] is True
    assert report["kpi"]["overdue_no_action"]["value"] == 1


async def test_archived_excluded(db_session, workflows, people) -> None:
    await _deal(
        db_session, workflows, people["kam1"], [("negotiation", ago(60))], status="archived"
    )
    report = await _report(db_session, people["head"])
    assert report["kpi"]["overdue"]["value"] == 0 and report["kpi"]["overdue"]["prev"] == 0


async def test_cache_key_depends_on_scope_and_kam(people) -> None:
    params = KamActivityParams(date_from=date(2026, 10, 1), date_to=date(2026, 10, 20))
    wide = DealScope.unrestricted()
    own = DealScope(rules=(("b2b", people["kam1"].id), ("b2c", people["kam1"].id)))
    keys = {
        ka._cache_key(sla.CODE, params, wide),
        ka._cache_key(sla.CODE, params, own),
        ka._cache_key(sla.CODE_KAM, KamActivityParams(kam_ids=(people["kam1"].id,)), wide),
        ka._cache_key(sla.CODE_KAM, KamActivityParams(kam_ids=(people["kam2"].id,)), wide),
    }
    assert len(keys) == 4
    assert all(key.startswith(("dash:sla:", "dash:sla-kam:")) for key in keys)


# --- 18. выгрузка -----------------------------------------------------------------------


async def test_build_sla_file_xlsx_and_pdf(
    api, db_session, workflows, people, memory_storage, monkeypatch
) -> None:
    import openpyxl

    from app.modules.reporting.sla_export import SHEET_NAMES, build_sla_file

    kam1, head = people["kam1"], people["head"]
    deal = await _deal(db_session, workflows, kam1, [("negotiation", ago(25))], title="МЭИ — ИИ")
    await _notify(db_session, deal, kam1, ago(9))
    filters = {"from": "2026-09-21", "to": "2026-10-20"}
    data, rows, name = await build_sla_file(
        db_session, user=head, filters=filters, export_format="xlsx", options={}
    )
    assert (rows, name) == (1, "overdue_20260921-20261020.xlsx")
    book = openpyxl.load_workbook(io.BytesIO(data))
    assert tuple(book.sheetnames) == SHEET_NAMES
    assert book["Сводка"]["A1"].value.startswith("На 20.10.2026 дольше нормы — 1 из 1")
    titles = [cell.value for cell in book["Просрочено сейчас"][1]]
    assert "Сверх нормы, дн." in titles
    assert any(c.value == "МЭИ — ИИ" for row in book["Просрочено сейчас"].iter_rows() for c in row)

    pdf, _rows, pdf_name = await build_sla_file(
        db_session, user=head, filters=filters, export_format="pdf", options={"by_kam": True}
    )
    assert pdf.startswith(b"%PDF") and pdf_name.endswith(".pdf")
    csv, csv_rows, csv_name = await build_sla_file(
        db_session, user=head, filters=filters, export_format="csv", options={}
    )
    assert csv_rows == 1 and csv_name == "overdue_20260921-20261020.csv"
    assert "МЭИ — ИИ" in csv.decode("utf-8-sig")

    with pytest.raises(ApiError) as excinfo:
        await build_sla_file(
            db_session,
            user=people["kam2"],
            filters={"kam_id": str(kam1.id)},
            export_format="xlsx",
            options={},
        )
    assert excinfo.value.status_code == 403

    # через механизм выгрузок: 201, имя файла; options.by_kam доходит до PDF — «По КАМам»
    # с именами, без него — «Сводка без имён» (ни КАМов, ни автора выгрузки)
    from app.modules.reporting import sla_export

    rendered: list[tuple[bool, list[str]]] = []
    original = sla_export.render_sla_pdf

    def spy(report, meta, *, by_kam=False):  # noqa: ANN001, ANN202
        texts = sla_export.section_texts(sla_export.pdf_sections(report, by_kam, meta))
        rendered.append((by_kam, texts))
        return original(report, meta, by_kam=by_kam)

    monkeypatch.setattr(sla_export, "render_sla_pdf", spy)
    client = api.as_user(head)
    job = await client.post(
        f"{API}/export/jobs",
        json={"entity_type": "report:sla", "format": "pdf", "filters": filters},
    )
    assert job.status_code == 201, job.text
    assert job.json()["file"]["file_name"] == "overdue_20260921-20261020.pdf"
    assert job.json()["rows"] == 1
    named = await client.post(
        f"{API}/export/jobs",
        json={
            "entity_type": "report:sla",
            "format": "pdf",
            "filters": filters,
            "options": {"by_kam": True},
        },
    )
    assert named.status_code == 201, named.text
    (plain, plain_texts), (with_names, named_texts) = rendered
    assert (plain, with_names) == (False, True)
    surnames = ("Петров", "Морозов")  # КАМ и автор выгрузки
    assert not any(name in text for text in plain_texts for name in surnames)
    assert any("Петров" in text for text in named_texts)
    bad = await client.post(
        f"{API}/export/jobs",
        json={"entity_type": "report:sla", "format": "xlsx", "filters": {"kam_id": "x"}},
    )
    assert bad.status_code == 422
    own = await api.as_user(people["kam2"]).post(
        f"{API}/export/jobs",
        json={"entity_type": "report:sla", "format": "pdf", "filters": {"kam_id": str(kam1.id)}},
    )
    assert own.status_code == 403


# --- производительность -----------------------------------------------------------------

_BULK_SQL = sa.text(
    """
WITH ins AS (
    INSERT INTO deal (pipeline, workflow_id, stage_id, title, university_id,
                      responsible_user_id, status, stage_entered_at, last_activity_at,
                      created_at)
    SELECT 'b2b', :wf, :stage, 'Замер ' || lpad(g::text, 6, '0'), :uni,
           (:resp)[1 + (g - 1) % cardinality(:resp)], 'open',
           :now - make_interval(secs => (g % 90) * 86400 + 3600),
           :now - make_interval(secs => (g % 90) * 86400 + 3600),
           :now - make_interval(secs => (g % 90) * 86400 + 20 * 86400)
      FROM generate_series(1, :n) AS g
    RETURNING id, stage_entered_at, created_at
)
INSERT INTO deal_stage_history (deal_id, from_stage_id, to_stage_id, from_stage_name,
                                to_stage_name, kind, created_at)
SELECT id, NULL, :first, 'Новая', 'Новая', 'manual', created_at FROM ins
UNION ALL
SELECT id, :first, :stage, 'Новая', 'Переговоры', 'manual', stage_entered_at FROM ins
"""
)


_BULK_EXTRAS = (
    # передачи — у 5 % заявок, журнал напоминаний и комментарии — у 30 %
    """
    INSERT INTO audit_log (actor_user_id, action, entity_type, entity_id, before, after,
                           created_at)
    SELECT d.responsible_user_id, 'request.assigned', 'deal', d.id,
           jsonb_build_object('assignee_id', CAST(:first_kam AS text)),
           jsonb_build_object('assignee_id', d.responsible_user_id::text),
           d.stage_entered_at + interval '1 day'
      FROM deal d WHERE d.title LIKE 'Замер %' AND (hashtext(d.id::text) % 20) = 0
    """,
    """
    INSERT INTO notification (recipient_user_id, event_type, entity_type, entity_id, channel,
                              address, status, dedup_key, payload, created_at, sent_at, body)
    SELECT d.responsible_user_id, 'request.stuck', 'deal', d.id, 'telegram', '1', 'sent',
           'stuck:' || d.id || ':' || d.stage_id || ':responsible:' || g, '{}'::jsonb,
           d.stage_entered_at + make_interval(days => 15 + g * 3),
           d.stage_entered_at + make_interval(days => 15 + g * 3), 'b'
      FROM deal d, generate_series(0, 2) g
     WHERE d.title LIKE 'Замер %' AND (hashtext(d.id::text) % 10) < 3
    """,
    """
    INSERT INTO deal_comment (deal_id, author_user_id, body, created_at)
    SELECT d.id, d.responsible_user_id, 'комментарий', d.stage_entered_at + interval '2 days'
      FROM deal d WHERE d.title LIKE 'Замер %' AND (hashtext(d.id::text) % 10) < 3
    """,
)


async def _bulk(db_session, workflows, people, count: int) -> None:  # noqa: ANN001
    wf, stages = workflows["b2b"]
    university = await factories.make_university(db_session, kam=people["kam1"])
    kams = [people["kam1"], people["kam2"]] + [
        await factories.make_user(db_session, roles=("kam",)) for _ in range(18)
    ]
    await db_session.execute(
        _BULK_SQL.bindparams(
            sa.bindparam("wf", type_=sa.Uuid()),
            sa.bindparam("stage", type_=sa.Uuid()),
            sa.bindparam("first", type_=sa.Uuid()),
            sa.bindparam("uni", type_=sa.Uuid()),
            sa.bindparam("resp", type_=postgresql.ARRAY(sa.Uuid())),
            sa.bindparam("now", type_=sa.TIMESTAMP(timezone=True)),
            sa.bindparam("n", type_=sa.Integer()),
        ),
        {
            "wf": wf.id,
            "stage": stages["negotiation"].id,
            "first": stages["new"].id,
            "uni": university.id,
            "resp": [kam.id for kam in kams],
            "now": NOW,
            "n": count,
        },
    )
    for statement in _BULK_EXTRAS:
        await db_session.execute(sa.text(statement), {"first_kam": str(people["kam1"].id)})
    for table in ("deal", "deal_stage_history", "audit_log", "notification", "deal_comment"):
        await db_session.execute(sa.text(f"ANALYZE {table}"))


async def _timed(coro) -> tuple[Any, float, float]:  # noqa: ANN001
    """(результат, секунды, самая долгая остановка цикла событий): расчёт не должен
    держать цикл — остальные запросы воркера в это время обслуживаются."""
    import asyncio

    stalls: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = clock.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = clock.perf_counter()
            stalls.append(now - last - 0.01)
            last = now

    task = asyncio.create_task(ticker())
    started = clock.perf_counter()
    try:
        result = await coro
    finally:
        elapsed = clock.perf_counter() - started
        stop.set()
        await task
    return result, elapsed, max(stalls, default=0.0)


class _MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get_json(self, key: str) -> Any:
        return self.store.get(key)

    async def set_json(self, key: str, value: Any, ttl: int) -> bool:
        self.store[key] = value
        return True


async def _check_performance(  # noqa: ANN001
    db_session, workflows, people, monkeypatch, count: int, limits: tuple[float, float, float]
) -> None:
    await _bulk(db_session, workflows, people, count)
    head = people["head"]
    report, elapsed, stall = await _timed(
        sla.build_sla(db_session, head, KamActivityParams(), use_cache=False)
    )
    # разбор КАМа: командные значения — из кэша вкладки, набор — с предфильтром по КАМу
    cache = _MemoryCache()
    monkeypatch.setattr(sla, "get_cache", lambda: cache)
    await sla.build_sla(db_session, head, KamActivityParams())
    kam, kam_elapsed, _kam_stall = await _timed(
        sla.build_sla_kam(db_session, head, people["kam2"].id, KamActivityParams())
    )
    print(  # noqa: T201
        f"\nsla: {count} заявок — команда {elapsed:.2f} с (остановка цикла {stall:.2f} с), "
        f"разбор КАМа {kam_elapsed:.2f} с"
    )
    assert report["kpi"]["active"]["value"] == count
    assert report["overdue"]["truncated"] is True
    assert len(report["overdue"]["items"]) == sla.MAX_OVERDUE_ITEMS
    assert kam["team"]["overdue_pct"] == report["kpi"]["overdue_pct"]["value"]
    assert len(kam["overdue"]["items"]) <= sla.MAX_KAM_ITEMS
    team_limit, stall_limit, kam_limit = limits
    assert elapsed < team_limit and stall < stall_limit and kam_elapsed < kam_limit


async def test_performance_10k_requests(db_session, workflows, people, monkeypatch) -> None:
    await _check_performance(db_session, workflows, people, monkeypatch, 10_000, (10, 1.0, 3))


@pytest.mark.skipif(
    not os.environ.get("ALMA_SLA_PERF_50K"),
    reason="замер на 50 000 заявок (~40 с) — по ALMA_SLA_PERF_50K=1",
)
async def test_performance_50k_requests(db_session, workflows, people, monkeypatch) -> None:
    # замер 16.10.2026 на ноутбуке разработчика: команда 5,5–6,7 с (было 9–11 с), самая
    # долгая остановка цикла событий 0,3 с (было — весь расчёт), разбор КАМа при командном
    # отчёте в кэше 0,3–0,5 с (было 8–9,6 с)
    await _check_performance(db_session, workflows, people, monkeypatch, 50_000, (20, 1.5, 3))
