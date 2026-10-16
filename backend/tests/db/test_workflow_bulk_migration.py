"""Массовый перенос заявок при удалении этапа на настоящем PostgreSQL
(workflow-engine.md §6.3–6.4, §6.8): корректность, история, аудит, отчёт, сводные
уведомления, эквивалентность получателей прежнему поштучному пути, повторное применение
и атомарность при сбое посреди переноса.

N — env `ALMA_MIGRATION_TEST_N` (по умолчанию 10 000). Тесты идут в `db_session`:
`commit()` сервиса фиксирует SAVEPOINT, после теста всё откатывается.
"""

from __future__ import annotations

import os
import uuid
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.events import EVENT_REQUEST_STUCK, EVENT_REQUEST_TRANSITIONED
from app.models import AppUser, Deal, Workflow, WorkflowChangeRequest, WorkflowStage
from app.modules.workflow import migration, service
from app.modules.workflow.migration import migration_comment, ru_requests
from app.modules.workflow.schemas import Operation
from app.tools.bench_migration import legacy_migrate_deleted_stages

from . import factories
from .workflow_factories import bulk_insert_open_deals, delete_first_contact_ops, raw_operations

N = int(os.environ.get("ALMA_MIGRATION_TEST_N", "10000"))

Stages = dict[str, WorkflowStage]


async def _scalar(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(sa.text(sql), params)).scalar()


async def _create_change(
    session: AsyncSession, workflow: Workflow, author: AppUser, operations: list[Operation]
) -> WorkflowChangeRequest:
    return await service.create_change(
        session,
        workflow_id=workflow.id,
        author=author,
        operations=operations,
        raw_operations=raw_operations(operations),
        comment=None,
    )


async def _kams(session: AsyncSession, count: int, *, tg_base: int = 1000) -> list[AppUser]:
    return [
        await factories.make_user(session, roles=("kam",), tg_chat_id=tg_base + i)
        for i in range(count)
    ]


def _two_stage_ops(stages: Stages) -> list[Operation]:
    """Удаление двух этапов: «Первичный контакт» → «Новая», «Подготовка договора» →
    «Переговоры» (+ рёбра, без которых схема невалидна)."""
    return service.parse_operations(
        [
            {
                "op": "add_transition",
                "transition": {
                    "from_status_id": str(stages["new"].id),
                    "to_status_id": str(stages["negotiation"].id),
                    "name": "К переговорам",
                },
            },
            {
                "op": "add_transition",
                "transition": {
                    "from_status_id": str(stages["negotiation"].id),
                    "to_status_id": str(stages["contract_signed"].id),
                    "name": "Договор подписан",
                },
            },
            {
                "op": "delete_status",
                "status_id": str(stages["first_contact"].id),
                "migrate_to_status_id": str(stages["new"].id),
                "confirm_name": stages["first_contact"].name,
            },
            {
                "op": "delete_status",
                "status_id": str(stages["contract_prep"].id),
                "migrate_to_status_id": str(stages["negotiation"].id),
                "confirm_name": stages["contract_prep"].name,
            },
        ]
    )


async def _side_effect_counts(session: AsyncSession) -> dict[str, int]:
    return {
        "history": await _scalar(
            session, "SELECT count(*) FROM deal_stage_history WHERE kind = 'system_migration'"
        ),
        "notifications": await _scalar(
            session,
            "SELECT count(*) FROM notification WHERE event_type = :e",
            e=EVENT_REQUEST_TRANSITIONED,
        ),
        "audit": await _scalar(
            session, "SELECT count(*) FROM audit_log WHERE action = 'workflow.change_applied'"
        ),
    }


# --------------------------------------------------------------------------------------
# 1. Перенос N заявок: заявки, история, аудит, отчёт, мягкое удаление, I-2, LMS
# --------------------------------------------------------------------------------------


async def test_bulk_migration_moves_all_open_deals(
    db_session: AsyncSession, workflows, admin_user: AppUser, api
) -> None:
    wf, stages = workflows["b2b"]
    first_contact, new = stages["first_contact"], stages["new"]
    kams = await _kams(db_session, 20)
    university = await factories.make_university(db_session)
    await factories.make_rule(
        db_session, event_type=EVENT_REQUEST_TRANSITIONED, recipient_type="responsible"
    )
    common = {"workflow_id": wf.id, "university_id": university.id}
    await bulk_insert_open_deals(
        db_session,
        **common,
        stage_id=first_contact.id,
        n=N,
        responsible_ids=[k.id for k in kams],
        entered_days_ago=5,
    )
    await bulk_insert_open_deals(
        db_session,
        **common,
        stage_id=first_contact.id,
        n=3,
        status="archived",
        responsible_ids=[kams[0].id],
        title_prefix="Архив",
    )
    await bulk_insert_open_deals(
        db_session,
        **common,
        stage_id=new.id,
        n=5,
        responsible_ids=[kams[1].id],
        title_prefix="Уже новая",
    )
    # тот же пакет делает цель LMS-триггером: перенос всё равно не передаёт заявки в LMS
    operations = [
        *delete_first_contact_ops(stages),
        *service.parse_operations(
            [
                {
                    "op": "update_status",
                    "status_id": str(new.id),
                    "patch": {"triggers_lms_handover": True},
                }
            ]
        ),
    ]
    wcr = await _create_change(db_session, wf, admin_user, operations)
    assert wcr.status == "pending" and wcr.impact["affected_requests_total"] == N

    fingerprint_sql = (
        "SELECT md5(string_agg(id::text || '@' || stage_entered_at::text, ',' ORDER BY id)) "
        "FROM deal WHERE title LIKE 'Заявка %'"
    )
    entered_before = await _scalar(db_session, fingerprint_sql)
    activity_before = await _scalar(
        db_session, "SELECT max(last_activity_at) FROM deal WHERE title LIKE 'Заявка %'"
    )

    client = api.as_user(admin_user)
    response = await client.post(f"/api/v1/workflows/changes/{wcr.id}/approve")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "applied"
    report = body["migration_report"]
    assert report["migrated_requests"] == N
    assert report["by_status"] == [{"from": "Первичный контакт", "to": "Новая", "count": N}]
    assert report["digest_notifications"] == 20
    timings = report["timings"]
    assert set(timings) == {"lock_wait_ms", "apply_ms", "migrate_ms", "lock_held_ms"}
    assert 0 <= timings["migrate_ms"] <= timings["apply_ms"] <= timings["lock_held_ms"]
    # в цепочке approve заявки в ORM не загружаются — bulk UPDATE не оставил устаревших
    assert not [obj for obj in db_session.identity_map.values() if isinstance(obj, Deal)]

    # заявки: все открытые переехали, архивные не тронуты, срок на этапе сохранён
    sql = "SELECT count(*) FROM deal WHERE stage_id = :s AND status = :st"
    assert await _scalar(db_session, sql, s=first_contact.id, st="open") == 0
    assert await _scalar(db_session, sql, s=new.id, st="open") == N + 5
    assert await _scalar(db_session, sql, s=first_contact.id, st="archived") == 3
    assert await _scalar(db_session, fingerprint_sql) == entered_before
    activity_after = await _scalar(
        db_session, "SELECT min(last_activity_at) FROM deal WHERE title LIKE 'Заявка %'"
    )
    assert activity_after > activity_before

    # история: ровно N строк system_migration со снимками имён и прежним текстом
    history = (
        await db_session.execute(
            sa.text(
                "SELECT count(*), count(DISTINCT deal_id), "
                "count(*) FILTER (WHERE reason = 'stage_deleted' AND actor_user_id IS NULL "
                "  AND from_stage_id = :fc AND to_stage_id = :new AND is_return = false "
                "  AND transition_id IS NULL AND from_stage_name = 'Первичный контакт' "
                "  AND to_stage_name = 'Новая' AND comment = :comment) "
                "FROM deal_stage_history WHERE kind = 'system_migration'"
            ),
            {
                "fc": first_contact.id,
                "new": new.id,
                "comment": migration_comment("Первичный контакт", "Новая"),
            },
        )
    ).one()
    assert tuple(history) == (N, N, N)

    # аудит: одна запись на пакет
    audit = (
        (
            await db_session.execute(
                sa.text(
                    "SELECT after FROM audit_log WHERE action = 'workflow.change_applied' "
                    "AND after->>'change_request_id' = :id"
                ),
                {"id": str(wcr.id)},
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert audit[0]["migrated_requests"] == N
    assert audit[0]["by_status"] == [{"from": "Первичный контакт", "to": "Новая", "count": N}]
    assert audit[0]["digest_notifications"] == 20

    # пакет: applied, отчёт сохранён в impact; карточка отдаёт его отдельным полем
    stored = await _scalar(
        db_session,
        "SELECT impact->'migration_report' FROM workflow_change_request WHERE id = :id",
        id=wcr.id,
    )
    assert stored["migrated_requests"] == N and "lock_wait_ms" in stored["timings"]
    card = await client.get(f"/api/v1/workflows/changes/{wcr.id}")
    assert card.status_code == 200, card.text
    card_body = card.json()
    assert card_body["status"] == "applied"
    assert "migration_report" not in card_body["impact"]
    assert card_body["impact"]["affected_requests_total"] == N
    assert card_body["migration_report"]["migrated_requests"] == N
    assert card_body["migration_report"]["by_status"][0] == {
        "from": "Первичный контакт",
        "to": "Новая",
        "count": N,
    }
    assert card_body["migration_report"]["timings"]["lock_held_ms"] is None

    # этап и смежные рёбра мягко удалены; I-2: открытых заявок на удалённых этапах нет
    assert (
        await _scalar(
            db_session, "SELECT deleted_at FROM workflow_stage WHERE id = :id", id=first_contact.id
        )
        is not None
    )
    assert (
        await _scalar(
            db_session,
            "SELECT count(*) FROM workflow_transition WHERE deleted_at IS NULL "
            "AND (from_stage_id = :id OR to_stage_id = :id)",
            id=first_contact.id,
        )
        == 0
    )
    assert (
        await _scalar(
            db_session,
            "SELECT count(*) FROM deal d JOIN workflow_stage s ON s.id = d.stage_id "
            "WHERE s.deleted_at IS NOT NULL AND d.status = 'open'",
        )
        == 0
    )
    # административная перестройка не запускает LMS handover (цель — LMS-триггер)
    assert (
        await _scalar(
            db_session, "SELECT triggers_lms_handover FROM workflow_stage WHERE id = :id", id=new.id
        )
        is True
    )
    assert await _scalar(db_session, "SELECT count(*) FROM integration_event") == 0


# --------------------------------------------------------------------------------------
# 2. Сводки: одна на КАМа, count — число его заявок
# --------------------------------------------------------------------------------------


async def test_digest_one_per_kam(db_session: AsyncSession, workflows, admin_user) -> None:
    wf, stages = workflows["b2b"]
    kams = await _kams(db_session, 20)
    no_telegram = await factories.make_user(db_session, roles=("kam",), tg_chat_id=None)
    silent = await factories.make_user(db_session, roles=("kam",), tg_chat_id=99)
    await factories.set_notify_prefs(
        db_session, user=silent, doc={"events": {EVENT_REQUEST_TRANSITIONED: {"enabled": False}}}
    )
    university = await factories.make_university(db_session)
    await factories.make_rule(
        db_session, event_type=EVENT_REQUEST_TRANSITIONED, recipient_type="responsible"
    )
    everyone = [*kams, no_telegram, silent]
    await bulk_insert_open_deals(
        db_session,
        workflow_id=wf.id,
        stage_id=stages["first_contact"].id,
        university_id=university.id,
        responsible_ids=[u.id for u in everyone],
        n=N,
    )
    per_kam = dict(
        (
            await db_session.execute(
                sa.text(
                    "SELECT responsible_user_id, count(*) FROM deal "
                    "WHERE title LIKE 'Заявка %' GROUP BY 1"
                )
            )
        ).all()
    )
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    _, report = await service.apply_change(db_session, change_id=wcr.id, approver=admin_user)
    assert report["migrated_requests"] == N
    assert report["digest_notifications"] == 21

    rows = (
        await db_session.execute(
            sa.text(
                "SELECT recipient_user_id, status, last_error, payload, entity_type, entity_id, "
                "dedup_key, subject, body, address FROM notification WHERE event_type = :e"
            ),
            {"e": EVENT_REQUEST_TRANSITIONED},
        )
    ).all()
    by_user = {row.recipient_user_id: row for row in rows}
    assert len(rows) == len(by_user) == 21  # ровно одна строка на получателя
    assert silent.id not in by_user  # событие выключено — строки нет
    assert by_user[no_telegram.id].status == "skipped"
    assert by_user[no_telegram.id].last_error == "Адрес доставки не настроен у получателя"
    assert sum(row.payload["count"] for row in rows) == N - per_kam[silent.id]
    for kam in kams:
        row = by_user[kam.id]
        assert row.status == "pending" and row.address == str(kam.tg_chat_id)
        assert row.payload["digest"] is True and row.payload["migrated"] is True
        assert row.payload["count"] == per_kam[kam.id]
        assert row.entity_type == "workflow_change_request" and row.entity_id == wcr.id
        assert row.dedup_key == f"wfmig:{wcr.id}:{kam.id}:telegram"
        assert row.subject == "Заявки перенесены"
        assert f"«Первичный контакт» удалён — {ru_requests(per_kam[kam.id])}" in row.body
        assert row.payload["url"] == (f"/board?wf=b2b&kam={kam.id}&status={stages['new'].id}")


# --------------------------------------------------------------------------------------
# 3. Эквивалентность получателей прежнему поштучному пути
# --------------------------------------------------------------------------------------


async def _recipient_counts(session: AsyncSession) -> Counter[tuple[str, str, str]]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT coalesce(recipient_user_id::text, address), channel, status, payload "
                "FROM notification WHERE event_type = :e"
            ),
            {"e": EVENT_REQUEST_TRANSITIONED},
        )
    ).all()
    counts: Counter[tuple[str, str, str]] = Counter()
    for recipient, channel, status, payload in rows:
        counts[(recipient, channel, status)] += int((payload or {}).get("count", 1))
    return counts


async def test_digest_equivalent_to_legacy(
    db_session: AsyncSession, workflows, admin_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf, stages = workflows["b2b"]
    b2c_wf, _ = workflows["b2c"]
    make_user = factories.make_user
    head = await make_user(db_session, roles=("head_kam",), tg_chat_id=500, email="head@t")
    head2 = await make_user(db_session, roles=("head_kam", "kam"), tg_chat_id=501)
    await make_user(db_session, roles=("head_kam",), is_active=False)  # роль, но неактивен
    kam1 = await make_user(db_session, manager=head, tg_chat_id=601)
    kam2 = await make_user(db_session, manager=head, tg_chat_id=602)  # telegram выключен
    kam3 = await make_user(db_session, manager=head2, tg_chat_id=None)  # нет адреса
    kam4 = await make_user(db_session, tg_chat_id=604)  # событие выключено
    kam5 = await make_user(db_session, tg_chat_id=605, email="")  # без руководителя
    gone = await make_user(db_session, roles=("kam",), tg_chat_id=606, is_active=False)
    await factories.set_notify_prefs(
        db_session,
        user=head,
        doc={"events": {EVENT_REQUEST_TRANSITIONED: {"only_mine": False}}},
    )
    await factories.set_notify_prefs(
        db_session, user=kam2, doc={"channels": {"telegram": {"enabled": False}}}
    )
    await factories.set_notify_prefs(
        db_session, user=kam4, doc={"events": {EVENT_REQUEST_TRANSITIONED: {"enabled": False}}}
    )
    await factories.set_notify_prefs(
        db_session, user=kam1, doc={"events": {EVENT_REQUEST_TRANSITIONED: {"only_mine": False}}}
    )
    rule = factories.make_rule
    event = EVENT_REQUEST_TRANSITIONED
    await rule(db_session, event_type=event, recipient_type="responsible")
    await rule(db_session, event_type=event, recipient_type="manager_of_responsible")
    await rule(
        db_session,
        event_type=event,
        recipient_type="role",
        recipient_value="head_kam",
        channel="email",
    )
    await rule(
        db_session,
        event_type=event,
        recipient_type="user",
        recipient_value=str(kam1.id),
        channel="max",
    )  # канал max выключен по умолчанию → skipped
    await rule(db_session, event_type=event, recipient_type="user", recipient_value=str(gone.id))
    await rule(db_session, event_type=event, recipient_type="user", recipient_value=str(kam1.id))
    await rule(
        db_session, event_type=event, recipient_type="tg_username", recipient_value="@alma_sales"
    )
    await rule(
        db_session,
        event_type=event,
        recipient_type="email",
        recipient_value="ops@alma.test",
        channel="email",
        stage=stages["negotiation"],
    )
    await rule(
        db_session, event_type=event, recipient_type="responsible", channel="email", workflow=b2c_wf
    )  # другая воронка
    await rule(
        db_session, event_type=event, recipient_type="responsible", channel="max", is_active=False
    )
    await rule(
        db_session,
        event_type=event,
        recipient_type="responsible",
        channel="email",
        stage=stages["first_contact"],
    )  # этап-источник: после переноса не совпадает
    await rule(db_session, event_type=EVENT_REQUEST_STUCK, recipient_type="responsible")

    university = await factories.make_university(db_session)
    placement = [
        ("first_contact", kam1, 9),
        ("first_contact", kam2, 8),
        ("first_contact", kam3, 7),
        ("first_contact", kam4, 6),
        ("first_contact", kam5, 5),
        ("first_contact", head2, 4),
        ("contract_prep", kam1, 6),
        ("contract_prep", kam3, 5),
        ("contract_prep", head2, 4),
        ("contract_prep", gone, 6),
    ]
    for code, user, count in placement:
        await bulk_insert_open_deals(
            db_session,
            workflow_id=wf.id,
            stage_id=stages[code].id,
            university_id=university.id,
            responsible_ids=[user.id],
            n=count,
            title_prefix=f"{code}-{user.username}",
        )
    total = sum(count for *_, count in placement)
    assert total == 60

    wcr = await _create_change(db_session, wf, admin_user, _two_stage_ops(stages))
    change_id = wcr.id
    await db_session.commit()

    # прежний путь: поштучный цикл с emit_event_notifications на каждую заявку — в SAVEPOINT
    live = await service.load_graph(db_session, wf.id)
    operations = service.parse_operations(wcr.operations)
    nested = await db_session.begin_nested()
    with monkeypatch.context() as patch:
        patch.setattr(migration, "migrate_deleted_stages", legacy_migrate_deleted_stages)
        legacy_report = await service._execute_plan(db_session, wcr, admin_user, live, operations)
    legacy = await _recipient_counts(db_session)
    await nested.rollback()
    # откат SAVEPOINT истёк изменённые в нём объекты; заявки legacy-пути выкидываем из
    # identity map (новый путь их не загружает), актора перечитываем
    for obj in list(db_session.identity_map.values()):
        if isinstance(obj, Deal):
            db_session.expunge(obj)
    await db_session.refresh(admin_user)
    assert legacy_report["migrated_requests"] == total
    assert await _scalar(db_session, "SELECT count(*) FROM notification") == 0

    # новый путь: сводки
    _, report = await service.apply_change(db_session, change_id=change_id, approver=admin_user)
    assert report["migrated_requests"] == total
    digests = await _recipient_counts(db_session)
    rows_per_key = Counter(
        (str(r[0]), r[1], r[2])
        for r in (
            await db_session.execute(
                sa.text(
                    "SELECT coalesce(recipient_user_id::text, address), channel, status "
                    "FROM notification WHERE event_type = :e"
                ),
                {"e": EVENT_REQUEST_TRANSITIONED},
            )
        ).all()
    )

    assert digests == legacy  # те же получатели, каналы, статусы и число заявок
    assert set(rows_per_key.values()) == {1}  # по одной сводке на получателя
    assert report["digest_notifications"] == len(legacy)

    # содержательные ожидания — набор правил и настроек действительно покрыт
    def got(user_or_address: Any, channel: str, status: str = "pending") -> int:
        key = str(getattr(user_or_address, "id", user_or_address))
        return digests.get((key, channel, status), 0)

    assert got(head, "email") == 60  # роль head_kam, «все заявки»
    assert got(head2, "email") == 8  # роль head_kam, «только мои» (по умолчанию)
    assert got(head, "telegram") == 9 + 6 + 8  # руководитель kam1 и kam2
    assert got(head2, "telegram") == 8  # ответственный; как руководитель kam3 — «только мои»
    assert got("@alma_sales", "telegram") == 60  # прямой адрес — все заявки
    assert got("ops@alma.test", "email") == 21  # правило этапа «Переговоры» — только эти
    assert got(kam1, "telegram") == 60  # user-правило + «все» — свои 15 не дважды
    assert got(kam1, "max", "skipped") == 60  # канал max выключен по умолчанию
    assert got(kam2, "telegram", "skipped") == 8  # telegram выключен в настройках
    assert got(kam3, "telegram", "skipped") == 12  # нет tg_chat_id
    assert got(kam5, "telegram") == 5
    assert got(gone, "telegram") == 6  # неактивный ответственный — как раньше; «только мои»
    assert not [key for key in digests if key[0] == str(kam4.id)]  # событие выключено


# --------------------------------------------------------------------------------------
# 4. Повторное применение того же пакета
# --------------------------------------------------------------------------------------


async def _simple_setup(
    session: AsyncSession, workflows, *, n: int, kams: int = 5
) -> tuple[Workflow, Stages, list[AppUser], uuid.UUID]:
    wf, stages = workflows["b2b"]
    users = await _kams(session, kams)
    university = await factories.make_university(session)
    await factories.make_rule(
        session, event_type=EVENT_REQUEST_TRANSITIONED, recipient_type="responsible"
    )
    await bulk_insert_open_deals(
        session,
        workflow_id=wf.id,
        stage_id=stages["first_contact"].id,
        university_id=university.id,
        responsible_ids=[u.id for u in users],
        n=n,
    )
    return wf, stages, users, university.id


async def test_reapply_same_package_is_safe(db_session, workflows, admin_user, api) -> None:
    wf, stages, kams, _ = await _simple_setup(db_session, workflows, n=500)
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    client = api.as_user(admin_user)
    first = await client.post(f"/api/v1/workflows/changes/{wcr.id}/approve")
    assert first.status_code == 200, first.text
    after_first = await _side_effect_counts(db_session)
    assert after_first == {"history": 500, "notifications": len(kams), "audit": 1}

    second = await client.post(f"/api/v1/workflows/changes/{wcr.id}/approve")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    with pytest.raises(ApiError) as caught:
        await service.apply_change(db_session, change_id=wcr.id, approver=admin_user)
    assert caught.value.code == "conflict"
    assert await _side_effect_counts(db_session) == after_first
    assert (
        await _scalar(
            db_session, "SELECT status FROM workflow_change_request WHERE id = :id", id=wcr.id
        )
        == "applied"
    )


# --------------------------------------------------------------------------------------
# 5. Сбои применения: всё откатывается, пакет failed, уведомлений нет
# --------------------------------------------------------------------------------------


def _ids(stages: Stages) -> dict[str, uuid.UUID]:
    """id этапов заранее: после отката внутри сервиса ORM-объекты истекают, а ленивая
    подгрузка атрибутов в async-тесте невозможна."""
    return {code: stage.id for code, stage in stages.items()}


async def _assert_nothing_applied(
    session: AsyncSession,
    wcr_id: uuid.UUID,
    stage_ids: dict[str, uuid.UUID],
    expected: dict[str, int],
) -> None:
    """Заявки на исходных этапах, схема не тронута, истории/сводок/аудита нет."""
    assert await _side_effect_counts(session) == {"history": 0, "notifications": 0, "audit": 0}
    for code, count in expected.items():
        assert (
            await _scalar(
                session,
                "SELECT count(*) FROM deal WHERE stage_id = :s AND status = 'open'",
                s=stage_ids[code],
            )
            == count
        ), code
    assert (
        await _scalar(
            session,
            "SELECT count(*) FROM workflow_stage s JOIN workflow_stage n "
            "ON n.workflow_id = s.workflow_id WHERE n.id = :new AND s.deleted_at IS NOT NULL",
            new=stage_ids["new"],
        )
        == 0
    )
    assert (
        await _scalar(
            session, "SELECT count(*) FROM workflow_transition WHERE name = 'К переговорам'"
        )
        == 0
    )
    row = (
        await session.execute(
            sa.text(
                "SELECT status, decision_comment, impact ? 'migration_report' AS has_report "
                "FROM workflow_change_request WHERE id = :id"
            ),
            {"id": wcr_id},
        )
    ).one()
    assert row.status == "failed"
    assert row.decision_comment.startswith("Ошибка применения:")
    assert row.has_report is False


async def test_failed_apply_rolls_back_everything(db_session, workflows, admin_user) -> None:
    """Между созданием пакета и approve другой применённый пакет переименовал удаляемый
    этап: рубеж 2 «защиты от дурака» (confirm_name под локом) → 422, пакет failed."""
    wf, stages, _, _ = await _simple_setup(db_session, workflows, n=300)
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    change_id, ids = wcr.id, _ids(stages)
    rename = service.parse_operations(
        [
            {
                "op": "rename_status",
                "status_id": str(stages["first_contact"].id),
                "new_name": "Первое касание",
                "confirm_name": "Первичный контакт",
            }
        ]
    )
    renamer = await _create_change(db_session, wf, admin_user, rename)
    await service.apply_change(db_session, change_id=renamer.id, approver=admin_user)
    audit_before = await _scalar(
        db_session, "SELECT count(*) FROM audit_log WHERE action = 'workflow.change_applied'"
    )

    with pytest.raises(ApiError) as caught:
        await service.apply_change(db_session, change_id=change_id, approver=admin_user)
    assert caught.value.status_code == 422
    assert "Первое касание" in caught.value.message  # подсказка: актуальное название

    assert audit_before == 1  # применён только переименовывающий пакет
    await db_session.execute(
        sa.text("DELETE FROM audit_log WHERE action = 'workflow.change_applied'")
    )
    await _assert_nothing_applied(db_session, change_id, ids, {"first_contact": 300})


async def test_forced_failure_mid_migration_rolls_back_everything(
    db_session, workflows, admin_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ПРИНУДИТЕЛЬНЫЙ СБОЙ посреди переноса: первый этап (N заявок) уже перенесён и записан
    в историю, на заявке второго этапа триггер роняет INSERT истории. Транзакция откатывается
    целиком — ни одной перенесённой заявки, строки истории, сводки или записи аудита."""
    wf, stages, kams, university_id = await _simple_setup(db_session, workflows, n=N)
    await bulk_insert_open_deals(
        db_session,
        workflow_id=wf.id,
        stage_id=stages["contract_prep"].id,
        university_id=university_id,
        responsible_ids=[k.id for k in kams],
        n=50,
        title_prefix="Договор",
    )
    victim = await _scalar(
        db_session,
        "SELECT id FROM deal WHERE stage_id = :s ORDER BY title OFFSET 25 LIMIT 1",
        s=stages["contract_prep"].id,
    )
    await db_session.execute(
        sa.text(
            f"""
            CREATE FUNCTION alma_test_fail_history() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.deal_id = '{victim}'::uuid THEN
                    RAISE EXCEPTION 'принудительный сбой посреди переноса';
                END IF;
                RETURN NEW;
            END $$
            """
        )
    )
    await db_session.execute(
        sa.text(
            "CREATE TRIGGER alma_test_fail_history BEFORE INSERT ON deal_stage_history "
            "FOR EACH ROW EXECUTE FUNCTION alma_test_fail_history()"
        )
    )
    wcr = await _create_change(db_session, wf, admin_user, _two_stage_ops(stages))
    change_id, ids = wcr.id, _ids(stages)
    inside: dict[str, int] = {}
    original = migration.migrate_deleted_stages

    async def stage_by_stage(session: AsyncSession, *, plan: Any, **kwargs: Any) -> Any:
        # первый удаляемый этап — отдельно, чтобы увидеть промежуточное состояние транзакции
        first, rest = plan.deleted_stages[:1], plan.deleted_stages[1:]
        outcome = await original(session, plan=replace(plan, deleted_stages=first), **kwargs)
        inside["moved"] = outcome.migrated_total
        inside["history"] = await _scalar(
            session, "SELECT count(*) FROM deal_stage_history WHERE kind = 'system_migration'"
        )
        await original(session, plan=replace(plan, deleted_stages=rest), **kwargs)  # упадёт
        raise AssertionError("триггер должен был уронить перенос второго этапа")

    monkeypatch.setattr(migration, "migrate_deleted_stages", stage_by_stage)
    with pytest.raises(DBAPIError) as caught:
        await service.apply_change(db_session, change_id=change_id, approver=admin_user)
    assert "принудительный сбой" in str(caught.value)
    # перед сбоем первый этап уже был перенесён внутри транзакции применения
    assert inside == {"moved": N, "history": N}
    await _assert_nothing_applied(
        db_session, change_id, ids, {"first_contact": N, "contract_prep": 50, "new": 0}
    )
    assert not [obj for obj in db_session.identity_map.values() if isinstance(obj, Deal)]


async def test_failure_after_digests_rolls_back_notifications(
    db_session, workflows, admin_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой уже ПОСЛЕ переноса и записи сводок в outbox: внутри транзакции были N строк
    истории и сводки КАМам — после отката нет ни того, ни другого (уведомления живут в той
    же транзакции, relay их не увидел), пакет failed."""
    wf, stages, kams, _ = await _simple_setup(db_session, workflows, n=N, kams=7)
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    change_id, ids = wcr.id, _ids(stages)
    inside: dict[str, int] = {}
    original = migration.emit_migration_digests

    async def failing(session: AsyncSession, **kwargs: Any) -> int:
        inside["created"] = await original(session, **kwargs)
        inside["notifications"] = await _scalar(
            session, "SELECT count(*) FROM notification WHERE entity_id = :id", id=change_id
        )
        inside["history"] = await _scalar(
            session, "SELECT count(*) FROM deal_stage_history WHERE kind = 'system_migration'"
        )
        inside["moved"] = await _scalar(
            session,
            "SELECT count(*) FROM deal WHERE stage_id = :s AND status = 'open'",
            s=ids["new"],
        )
        raise RuntimeError("принудительный сбой после записи сводок")

    monkeypatch.setattr(migration, "emit_migration_digests", failing)
    with pytest.raises(RuntimeError, match="принудительный сбой"):
        await service.apply_change(db_session, change_id=change_id, approver=admin_user)

    assert inside == {"created": 7, "notifications": 7, "history": N, "moved": N}
    await _assert_nothing_applied(db_session, change_id, ids, {"first_contact": N, "new": 0})
    comment = await _scalar(
        db_session,
        "SELECT decision_comment FROM workflow_change_request WHERE id = :id",
        id=change_id,
    )
    assert "изменения отменены" in comment


async def test_invariant_violation_rolls_back(db_session, workflows, admin_user, monkeypatch):
    """Инвариант I-9 (строк истории столько же, сколько перенесено) проверяется самим
    оператором; нарушение — RuntimeError и полный откат."""
    wf, stages, _, _ = await _simple_setup(db_session, workflows, n=200)
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    change_id, ids = wcr.id, _ids(stages)
    broken = sa.text(
        str(migration.MIGRATE_STAGE_SQL).replace(
            "(SELECT count(*) FROM hist)", "(SELECT count(*) - 1 FROM hist)"
        )
    ).bindparams(*migration.MIGRATE_STAGE_SQL._bindparams.values())
    monkeypatch.setattr(migration, "MIGRATE_STAGE_SQL", broken)
    with pytest.raises(RuntimeError, match="I-9"):
        await service.apply_change(db_session, change_id=change_id, approver=admin_user)
    await _assert_nothing_applied(db_session, change_id, ids, {"first_contact": 200})


# --------------------------------------------------------------------------------------
# 6. Срок на этапе сохраняется — «зависание» не сбрасывается
# --------------------------------------------------------------------------------------


async def test_stuck_clock_preserved(db_session, workflows, admin_user) -> None:
    wf, stages = workflows["b2b"]
    kam = await factories.make_user(db_session, tg_chat_id=4242)
    university = await factories.make_university(db_session)
    await factories.make_rule(
        db_session, event_type=EVENT_REQUEST_STUCK, recipient_type="responsible"
    )
    await bulk_insert_open_deals(
        db_session,
        workflow_id=wf.id,
        stage_id=stages["first_contact"].id,
        university_id=university.id,
        responsible_ids=[kam.id],
        n=3,
        entered_days_ago=30,
        title_prefix="Давняя",
    )
    entered = dict(
        (await db_session.execute(sa.text("SELECT id, stage_entered_at FROM deal"))).all()
    )
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    await service.apply_change(db_session, change_id=wcr.id, approver=admin_user)
    after = dict((await db_session.execute(sa.text("SELECT id, stage_entered_at FROM deal"))).all())
    assert after == entered

    result = await service.check_stuck_requests(db_session)
    assert result["stuck_found"] == 3 and result["notifications_sent"] == 3
    stuck = (
        await db_session.execute(
            sa.text("SELECT entity_id, dedup_key FROM notification WHERE event_type = :e"),
            {"e": EVENT_REQUEST_STUCK},
        )
    ).all()
    assert {row.entity_id for row in stuck} == set(entered)
    # порог «Новой» — 7 дней: заявка висит 30 дней на новом этапе, а не 0
    assert all(f":{stages['new'].id}:" in row.dedup_key for row in stuck)


# --------------------------------------------------------------------------------------
# Ограниченное ожидание применения: 409 workflow_busy + Retry-After, пакет pending
# --------------------------------------------------------------------------------------


async def test_approve_busy_returns_retry_after_and_keeps_pending(
    db_session, db_engine, workflows, admin_user, api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.workflow import locks

    wf, stages, _, _ = await _simple_setup(db_session, workflows, n=50)
    wcr = await _create_change(db_session, wf, admin_user, delete_first_contact_ops(stages))
    change_id, workflow_id = wcr.id, wf.id
    monkeypatch.setattr(locks, "APPLY_LOCK_TIMEOUT_MS", 200)
    client = api.as_user(admin_user)
    async with db_engine.connect() as other:
        # «хвост перехода»: shared-лок воронки в другой открытой транзакции
        await other.execute(
            sa.text("SELECT pg_advisory_xact_lock_shared(hashtextextended('wf:' || :w, 0))"),
            {"w": str(workflow_id)},
        )
        busy = await client.post(f"/api/v1/workflows/changes/{change_id}/approve")
        await other.rollback()
    assert busy.status_code == 409, busy.text
    assert busy.json()["error"]["code"] == "workflow_busy"
    assert busy.headers["Retry-After"] == "2"
    assert "остаётся на согласовании" in busy.json()["error"]["message"]
    assert (
        await _scalar(
            db_session, "SELECT status FROM workflow_change_request WHERE id = :id", id=change_id
        )
        == "pending"
    )
    assert await _side_effect_counts(db_session) == {"history": 0, "notifications": 0, "audit": 0}

    retry = await client.post(f"/api/v1/workflows/changes/{change_id}/approve")
    assert retry.status_code == 200, retry.text
    assert retry.json()["migration_report"]["migrated_requests"] == 50
