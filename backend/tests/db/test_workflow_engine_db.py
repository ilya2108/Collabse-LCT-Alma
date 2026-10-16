"""Движок заявок на настоящем PostgreSQL: главный путь демо через API и сервис.

- B2C: «Оплачено» → «Обучение» ставит событие передачи в LMS (`crm.request.handed_over`,
  pending в outbox), «Обучение» → «Завершена» закрывает заявку как won, и хук Talent Pool
  переводит связанного студента в graduate с записью в истории статусов;
- недопустимое ребро → 409 `workflow_invalid_transition` со списком допустимых переходов
  из текущего этапа (из терминала — «переходов нет»), заявка не меняется;
- обязательный комментарий: без него и из одних пробелов → 400 с деталью по полю
  `comment` (у возврата — своя формулировка), с комментарием → 200 и исход lost;
- чужая заявка: 403 отсекает и роутер, и сам движок (`transition_deal`) — проверка
  не держится на одном слое;
- переоткрытие (`reopen_deal`): только из терминала, не в терминал, только с
  `requests:reopen`, история с причиной reopen и аудит;
- отклонение пакета изменений схемы (`reject_change`): статус, решение и аудит, схема
  не меняется, повторное отклонение → 409.
"""

from __future__ import annotations

import re
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.events import INTG_CRM_REQUEST_HANDED_OVER
from app.models import (
    AuditLog,
    Deal,
    DealStageHistory,
    IntegrationEvent,
    WorkflowChangeRequest,
    WorkflowStage,
)
from app.models.talent import Student, StudentStatusHistory
from app.modules.workflow import service

from . import factories
from .factories import days_ago

# --------------------------------------------------------------------------------------
# Помощники
# --------------------------------------------------------------------------------------


async def _version(client, deal: Deal) -> int:  # noqa: ANN001
    response = await client.get(f"/api/v1/requests/{deal.id}")
    assert response.status_code == 200, response.text
    return response.json()["version"]


async def _transition(client, deal: Deal, stage: WorkflowStage, **extra):  # noqa: ANN001, ANN202
    body = {"to_status_id": str(stage.id), "version": await _version(client, deal), **extra}
    return await client.post(f"/api/v1/requests/{deal.id}/transitions", json=body)


async def _make_student(
    session: AsyncSession, *, counterparty_id: uuid.UUID, status: str = "studying"
) -> Student:
    n = uuid.uuid4().hex
    student = Student(
        full_name="Студентов Студент",
        email=f"{n[:12]}@student.test",
        email_hmac=(n * 2)[:64],
        display_name="Студентов С.",
        funnel_status=status,
        counterparty_id=counterparty_id,
    )
    session.add(student)
    await session.flush()
    return student


async def _history(session: AsyncSession, deal: Deal) -> list[DealStageHistory]:
    rows = await session.execute(
        sa.select(DealStageHistory)
        .where(DealStageHistory.deal_id == deal.id)
        .order_by(DealStageHistory.created_at)
    )
    return list(rows.scalars())


async def _audit(session: AsyncSession, entity_id: uuid.UUID, action: str) -> list[AuditLog]:
    rows = await session.execute(
        sa.select(AuditLog).where(AuditLog.entity_id == entity_id, AuditLog.action == action)
    )
    return list(rows.scalars())


def _error(response) -> dict:  # noqa: ANN001
    return response.json()["error"]


# --------------------------------------------------------------------------------------
# B2C: передача в LMS → завершение, студент становится выпускником
# --------------------------------------------------------------------------------------


async def test_b2c_lms_handover_then_won_graduates_student(db_session, api, workflows) -> None:
    kam = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2c"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        path=[("new", days_ago(5)), ("paid", days_ago(1))],
    )
    student = await _make_student(db_session, counterparty_id=deal.counterparty_id)
    client = api.as_user(kam)

    handover = await _transition(client, deal, st["in_learning"])
    assert handover.status_code == 200, handover.text
    assert handover.json()["status"]["id"] == str(st["in_learning"].id)
    events = (
        (
            await db_session.execute(
                sa.select(IntegrationEvent).where(
                    IntegrationEvent.entity_id == deal.id, IntegrationEvent.system == "lms"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(e.event_type, e.direction, e.status) for e in events] == [
        (INTG_CRM_REQUEST_HANDED_OVER, "outbound", "pending")
    ]
    await db_session.refresh(student)
    assert student.funnel_status == "studying"  # передача в обучение студента не двигает

    done = await _transition(client, deal, st["completed"])
    assert done.status_code == 200, done.text
    await db_session.refresh(deal)
    assert deal.status == "won"
    assert deal.stage_id == st["completed"].id
    await db_session.refresh(student)
    assert student.funnel_status == "graduate"
    status_history = (
        (
            await db_session.execute(
                sa.select(StudentStatusHistory).where(StudentStatusHistory.student_id == student.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(h.from_status, h.to_status, h.reason, h.actor_user_id) for h in status_history] == [
        ("studying", "graduate", "b2c_request_completed", kam.id)
    ]

    # строки движка — с ребром схемы; created_at у обеих — время внешней транзакции теста,
    # поэтому сверяем множеством, а не порядком
    engine_rows = [h for h in await _history(db_session, deal) if h.transition_id is not None]
    assert {(h.from_stage_id, h.to_stage_id, h.actor_user_id) for h in engine_rows} == {
        (st["paid"].id, st["in_learning"].id, kam.id),
        (st["in_learning"].id, st["completed"].id, kam.id),
    }
    assert len(await _audit(db_session, deal.id, "request.transitioned")) == 2
    # LMS-событие ставится только на этапе с передачей: завершение второго не добавляет
    total_lms = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(IntegrationEvent)
        .where(IntegrationEvent.entity_id == deal.id, IntegrationEvent.system == "lms")
    )
    assert total_lms == 1


# --------------------------------------------------------------------------------------
# Недопустимое ребро → 409 со списком допустимых переходов
# --------------------------------------------------------------------------------------


async def test_invalid_edge_is_409_with_available_transitions(db_session, api, workflows) -> None:
    kam = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2b"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        path=[("new", days_ago(3)), ("first_contact", days_ago(1))],
    )
    client = api.as_user(kam)
    version = await _version(client, deal)
    history_before = len(await _history(db_session, deal))

    response = await client.post(
        f"/api/v1/requests/{deal.id}/transitions",
        json={"to_status_id": str(st["completed"].id), "version": version},
    )
    assert response.status_code == 409, response.text
    error = _error(response)
    assert error["code"] == "workflow_invalid_transition"
    [detail] = error["details"]
    assert detail["field"] == "to_status_id"
    assert detail["message"].startswith("Допустимые переходы: ")
    # из «Первичного контакта» схема b2b ведёт в «Переговоры» и в «Отказ»
    assert set(re.findall(r"«([^»]+)»", detail["message"])) == {"Начать переговоры", "Отказ"}

    await db_session.refresh(deal)
    assert deal.stage_id == st["first_contact"].id and deal.status == "open"
    assert await _version(client, deal) == version
    assert len(await _history(db_session, deal)) == history_before


async def test_no_edges_from_terminal_stage(db_session, api, workflows) -> None:
    kam = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2b"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        path=[("new", days_ago(9)), ("in_learning", days_ago(4)), ("completed", days_ago(1))],
    )
    response = await _transition(api.as_user(kam), deal, st["new"])
    assert response.status_code == 409, response.text
    error = _error(response)
    assert error["code"] == "workflow_invalid_transition"
    assert error["details"][0]["message"] == "Из текущего этапа переходов нет"
    await db_session.refresh(deal)
    assert deal.status == "won"


# --------------------------------------------------------------------------------------
# Обязательный комментарий → 400
# --------------------------------------------------------------------------------------


async def test_required_comment_is_400_then_lost_with_comment(db_session, api, workflows) -> None:
    kam = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2b"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        path=[("new", days_ago(3)), ("first_contact", days_ago(1))],
    )
    client = api.as_user(kam)

    for extra in ({}, {"comment": "   "}):
        response = await _transition(client, deal, st["rejected"], **extra)
        assert response.status_code == 400, response.text
        error = _error(response)
        assert error["code"] == "validation_error"
        assert error["message"] == "Этот переход требует комментарий"
        assert [(d["field"], d["code"]) for d in error["details"]] == [("comment", "required")]
    await db_session.refresh(deal)
    assert deal.status == "open" and deal.stage_id == st["first_contact"].id

    lost = await _transition(client, deal, st["rejected"], comment="  нет бюджета  ")
    assert lost.status_code == 200, lost.text
    await db_session.refresh(deal)
    assert deal.status == "lost" and deal.stage_id == st["rejected"].id
    last = (await _history(db_session, deal))[-1]
    assert last.comment == "нет бюджета"  # движок сохраняет комментарий без краевых пробелов


async def test_return_edge_requires_comment_with_reason(db_session, api, workflows) -> None:
    kam = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2b"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        path=[("new", days_ago(6)), ("negotiation", days_ago(3)), ("contract_prep", days_ago(1))],
    )
    client = api.as_user(kam)

    response = await _transition(client, deal, st["negotiation"])
    assert response.status_code == 400, response.text
    message = _error(response)["message"]
    assert message == "Возврат на предыдущий этап требует комментарий с причиной"

    back = await _transition(client, deal, st["negotiation"], comment="правки в смете")
    assert back.status_code == 200, back.text
    last = (await _history(db_session, deal))[-1]
    assert last.is_return is True and last.to_stage_id == st["negotiation"].id


# --------------------------------------------------------------------------------------
# Чужая заявка → 403 в роутере и в движке
# --------------------------------------------------------------------------------------


async def test_foreign_deal_is_403_in_router_and_engine(db_session, api, workflows) -> None:
    owner = await factories.make_user(db_session, roles=("kam",))
    stranger = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2b"]
    deal = await factories.make_deal(db_session, workflow=wf, stages=st, responsible=owner)
    version = await _version(api.as_user(owner), deal)

    response = await api.as_user(stranger).post(
        f"/api/v1/requests/{deal.id}/transitions",
        json={"to_status_id": str(st["first_contact"].id), "version": version},
    )
    assert response.status_code == 403, response.text
    assert _error(response)["message"] == "Переход доступен только ответственному по заявке"

    # движок сам повторяет проверку: вызов в обход роутера (бот, фоновые задачи) тоже 403
    with pytest.raises(ApiError) as exc:
        await service.transition_deal(
            db_session,
            deal_id=deal.id,
            to_status_id=st["first_contact"].id,
            actor=stranger,
            comment=None,
            version=version,
        )
    assert exc.value.status_code == 403
    assert exc.value.message == "Переход доступен только ответственному по заявке"

    await db_session.refresh(deal)
    assert deal.stage_id == st["new"].id
    assert await _version(api.as_user(owner), deal) == version


# --------------------------------------------------------------------------------------
# Переоткрытие из терминала (reopen_deal)
# --------------------------------------------------------------------------------------


async def test_reopen_from_terminal(db_session, api, workflows, admin_user) -> None:
    kam = await factories.make_user(db_session, roles=("kam",))
    wf, st = workflows["b2b"]
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=st,
        responsible=kam,
        path=[("new", days_ago(9)), ("in_learning", days_ago(4)), ("completed", days_ago(1))],
    )
    assert deal.status == "won"
    admin = api.as_user(admin_user)
    url = f"/api/v1/requests/{deal.id}/reopen"

    # у КАМа нет права requests:reopen
    kam_try = await api.as_user(kam).post(
        url, json={"to_status_id": str(st["in_learning"].id), "comment": "ошибка"}
    )
    assert kam_try.status_code == 403, kam_try.text

    to_terminal = await admin.post(
        url, json={"to_status_id": str(st["rejected"].id), "comment": "x"}
    )
    assert to_terminal.status_code == 422, to_terminal.text
    assert _error(to_terminal)["message"] == "Переоткрытие в терминальный этап невозможно"

    foreign_stage = workflows["b2c"][1]["new"]
    other_workflow = await admin.post(
        url, json={"to_status_id": str(foreign_stage.id), "comment": "x"}
    )
    assert other_workflow.status_code == 422, other_workflow.text

    ok = await admin.post(
        url, json={"to_status_id": str(st["in_learning"].id), "comment": " закрыли по ошибке "}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"]["id"] == str(st["in_learning"].id)
    await db_session.refresh(deal)
    assert deal.status == "open" and deal.stage_id == st["in_learning"].id

    last = (await _history(db_session, deal))[-1]
    assert (last.reason, last.from_stage_id, last.to_stage_id, last.actor_user_id) == (
        "reopen",
        st["completed"].id,
        st["in_learning"].id,
        admin_user.id,
    )
    assert last.comment == "закрыли по ошибке"
    [audit] = await _audit(db_session, deal.id, "request.reopened")
    assert audit.before == {"stage_id": str(st["completed"].id), "status": "won"}
    assert audit.after == {"stage_id": str(st["in_learning"].id), "status": "open"}

    # открытую заявку переоткрыть нельзя
    again = await admin.post(url, json={"to_status_id": str(st["new"].id), "comment": "x"})
    assert again.status_code == 409, again.text
    assert _error(again)["message"] == "Переоткрыть можно только заявку в терминальном статусе"


# --------------------------------------------------------------------------------------
# Отклонение пакета изменений схемы (reject_change)
# --------------------------------------------------------------------------------------


async def test_reject_change(db_session, api, workflows, admin_user) -> None:
    head = await factories.make_user(db_session, roles=("head_kam",))
    wf, st = workflows["b2b"]
    color_before = st["new"].color
    pending = await api.as_user(head).post(
        f"/api/v1/workflows/{wf.id}/changes",
        json={
            "comment": "перекрасить этап",
            "operations": [
                {
                    "op": "update_status",
                    "status_id": str(st["new"].id),
                    "patch": {"color": "#3366ff"},
                }
            ],
        },
    )
    assert pending.status_code in (200, 201), pending.text
    assert pending.json()["status"] == "pending"
    change_id = pending.json()["id"]
    admin = api.as_user(admin_user)

    response = await admin.post(
        f"/api/v1/workflows/changes/{change_id}/reject", json={"reason": "не согласовано"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rejected"
    assert body["decision_comment"] == "не согласовано"
    assert body["decided_by"]["id"] == str(admin_user.id)

    change = await db_session.get(WorkflowChangeRequest, uuid.UUID(change_id))
    await db_session.refresh(change)
    assert (change.status, change.decided_by, change.decision_comment) == (
        "rejected",
        admin_user.id,
        "не согласовано",
    )
    assert change.decided_at is not None and change.applied_at is None
    [audit] = await _audit(db_session, change.id, "workflow.change_rejected")
    assert audit.actor_user_id == admin_user.id and audit.after == {"reason": "не согласовано"}
    # отклонённый пакет схему не трогает
    await db_session.refresh(st["new"])
    assert st["new"].color == color_before

    twice = await admin.post(
        f"/api/v1/workflows/changes/{change_id}/reject", json={"reason": "ещё раз"}
    )
    assert twice.status_code == 409, twice.text
    assert _error(twice)["message"] == "Пакет уже обработан"
