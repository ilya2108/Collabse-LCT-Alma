"""Журнал аудита только дополняется (миграция 0011) — на настоящем PostgreSQL.

INSERT и чтение работают как раньше; UPDATE, DELETE и TRUNCATE (в том числе каскадный и
через ORM) отклоняет триггер с SQLSTATE 42501. Сброс стенда журнал не трогает, а если его
когда-нибудь включат в очистку — откатится целиком. Тестовая очистка `truncate_and_restore`
выключает TRUNCATE-триггер только в своей транзакции и возвращает его включённым.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.models import AppUser, AuditLog
from app.models.talent import Student
from app.modules.workflow.locks import _sqlstate
from app.tools import seed
from tests.db import factories

INSUFFICIENT_PRIVILEGE = "42501"
TRIGGERS = {"trg_audit_log_append_only", "trg_audit_log_no_truncate"}
_MARK = "audit.append_only.committed"
API = "/api/v1"


async def _count(session: AsyncSession, *where: sa.ColumnElement[bool]) -> int:
    stmt = sa.select(sa.func.count()).select_from(AuditLog)
    for clause in where:
        stmt = stmt.where(clause)
    return (await session.execute(stmt)).scalar_one()


async def _rejected(session: AsyncSession, action: Callable[[], Awaitable[object]]) -> str:
    """Выполняет действие в SAVEPOINT и возвращает текст отказа БД (тест падает, если прошло)."""
    try:
        async with session.begin_nested():
            await action()
            await session.flush()
    except sa.exc.DBAPIError as exc:
        assert _sqlstate(exc) == INSUFFICIENT_PRIVILEGE, exc
        return str(exc)
    raise AssertionError("журнал аудита изменён — триггер 0011 не сработал")


async def _audit_row(session: AsyncSession, actor: AppUser) -> AuditLog:
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="request.transitioned",
        entity_type="deal",
        entity_id=None,
        after={"to": "Переговоры"},
        ip="10.0.0.1",
    )
    await session.flush()
    return (
        (
            await session.execute(
                sa.select(AuditLog).where(AuditLog.actor_user_id == actor.id).order_by(AuditLog.id)
            )
        )
        .scalars()
        .first()
    )


async def test_insert_and_read_allowed(db_session) -> None:  # noqa: ANN001
    actor = await factories.make_user(db_session, roles=("kam",))
    row = await _audit_row(db_session, actor)
    # прямой INSERT с явной датой (как сид передач портфеля) тоже разрешён
    await db_session.execute(
        sa.text(
            "INSERT INTO audit_log (actor_user_id, action, entity_type, created_at) "
            "VALUES (:a, 'request.assigned', 'deal', now() - interval '3 days')"
        ),
        {"a": actor.id},
    )
    assert await _count(db_session, AuditLog.actor_user_id == actor.id) == 2
    assert row.after == {"to": "Переговоры"}


async def test_update_rejected_sql_and_orm(db_session) -> None:  # noqa: ANN001
    actor = await factories.make_user(db_session, roles=("kam",))
    row = await _audit_row(db_session, actor)
    row_id = row.id

    message = await _rejected(
        db_session,
        lambda: db_session.execute(
            sa.text("UPDATE audit_log SET action = 'forged' WHERE id = :id"), {"id": row_id}
        ),
    )
    assert "append-only" in message and "UPDATE" in message
    await _rejected(
        db_session,
        lambda: db_session.execute(
            sa.text(
                "UPDATE audit_log SET created_at = created_at - interval '1 day' WHERE id = :id"
            ),
            {"id": row_id},
        ),
    )

    async def orm_update() -> None:
        loaded = await db_session.get(AuditLog, row_id)
        loaded.after = {"to": "Подменено"}

    await _rejected(db_session, orm_update)
    stored = (
        await db_session.execute(
            sa.text("SELECT action, after FROM audit_log WHERE id = :id"), {"id": row_id}
        )
    ).one()
    assert stored.action == "request.transitioned" and stored.after == {"to": "Переговоры"}


async def test_delete_rejected_sql_and_orm(db_session) -> None:  # noqa: ANN001
    actor = await factories.make_user(db_session, roles=("kam",))
    row_id = (await _audit_row(db_session, actor)).id

    message = await _rejected(
        db_session,
        lambda: db_session.execute(sa.text("DELETE FROM audit_log WHERE id = :id"), {"id": row_id}),
    )
    assert "DELETE" in message

    async def orm_delete() -> None:
        await db_session.delete(await db_session.get(AuditLog, row_id))

    await _rejected(db_session, orm_delete)
    assert await _count(db_session, AuditLog.id == row_id) == 1


async def test_truncate_rejected_direct_and_cascade(db_session) -> None:  # noqa: ANN001
    actor = await factories.make_user(db_session, roles=("kam",))
    await _audit_row(db_session, actor)
    rows = await _count(db_session)

    message = await _rejected(db_session, lambda: db_session.execute(sa.text("TRUNCATE audit_log")))
    assert "TRUNCATE" in message
    # каскад от app_user доходит до audit_log по FK автора — и тоже отклоняется
    await _rejected(db_session, lambda: db_session.execute(sa.text("TRUNCATE app_user CASCADE")))
    assert await _count(db_session) == rows
    assert await db_session.get(AppUser, actor.id) is not None


async def test_actor_fk_is_no_action(db_session) -> None:  # noqa: ANN001
    """Удаление автора записи отклоняет FK (23503), а не обнуляет автора в журнале."""
    rules = (
        (
            await db_session.execute(
                sa.text(
                    "SELECT confdeltype::text FROM pg_constraint "
                    "WHERE conrelid = 'audit_log'::regclass AND contype = 'f'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert rules == ["a"]  # NO ACTION

    actor = await factories.make_user(db_session, roles=("kam",))
    row_id = (await _audit_row(db_session, actor)).id
    try:
        async with db_session.begin_nested():
            await db_session.execute(
                sa.text("DELETE FROM app_user WHERE id = :id"), {"id": actor.id}
            )
    except sa.exc.DBAPIError as exc:
        assert _sqlstate(exc) == "23503", exc
    else:  # pragma: no cover — защита сломана
        raise AssertionError("автор записи аудита удалён")
    stored = await db_session.execute(
        sa.text("SELECT actor_user_id FROM audit_log WHERE id = :id"), {"id": row_id}
    )
    assert stored.scalar_one() == actor.id


async def test_migration_installs_enabled_guard(db_session, migrated_db) -> None:  # noqa: ANN001
    triggers = (
        await db_session.execute(
            sa.text(
                "SELECT tgname, tgenabled::text FROM pg_trigger "
                "WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal"
            )
        )
    ).all()
    assert {(name, enabled) for name, enabled in triggers} == {(t, "O") for t in TRIGGERS}
    function = await db_session.execute(
        sa.text("SELECT count(*) FROM pg_proc WHERE proname = 'audit_log_append_only'")
    )
    assert function.scalar_one() == 1
    assert "audit_log" not in migrated_db.seed_tables  # миграции журнал не заполняют


async def test_demo_reset_cannot_wipe_audit(db_session, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """Сброс стенда журнал сохраняет; если его уберут из сохраняемых — откат целиком."""
    assert "audit_log" in seed.RESET_KEEP_TABLES
    actor = await factories.make_user(db_session, roles=("kam",))
    await _audit_row(db_session, actor)
    rows, users = (
        await _count(db_session),
        await db_session.scalar(sa.select(sa.func.count()).select_from(AppUser)),
    )
    monkeypatch.setattr(seed, "RESET_KEEP_TABLES", seed.RESET_KEEP_TABLES - {"audit_log"})

    await _rejected(db_session, lambda: seed.truncate_demo_data(db_session))
    assert await _count(db_session) == rows
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(AppUser)) == users


# Пара по порядку файла (как в test_db_smoke): закоммиченная запись стирается очисткой
# `truncate_and_restore`, а триггер после неё снова включён.


async def test_committed_audit_row(db_committed_sessionmaker) -> None:  # noqa: ANN001
    async with db_committed_sessionmaker() as session:
        actor = await factories.make_user(session, roles=("kam",))
        await write_audit(
            session,
            actor_user_id=actor.id,
            action=_MARK,
            entity_type="deal",
            entity_id=None,
        )
        await session.commit()


async def test_committed_audit_cleaned_and_guard_enabled(db_engine) -> None:  # noqa: ANN001
    async with db_engine.connect() as conn:
        left = await conn.execute(sa.select(AuditLog.id).where(AuditLog.action == _MARK))
        assert left.first() is None
        triggers = await conn.execute(
            sa.text(
                "SELECT tgname, tgenabled::text FROM pg_trigger "
                "WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal"
            )
        )
        assert {(name, enabled) for name, enabled in triggers} == {(t, "O") for t in TRIGGERS}


async def test_student_audit_keeps_only_masked_name(api, db_session, admin_user) -> None:  # noqa: ANN001
    """Журнал неизменяем, поэтому ФИО студента в нём — только маской (152-ФЗ, §8.5):
    после удаления по праву на забвение псевдоним «Фамилия И.О.» не остаётся навсегда."""
    n = uuid.uuid4().hex
    student = Student(
        full_name="Петрова Анна Сергеевна",
        email=f"{n[:10]}@student.test",
        email_hmac=(n * 2)[:64],
        display_name="Петрова А.С.",
    )
    db_session.add(student)
    await db_session.flush()
    client = api.as_user(admin_user)

    revealed = await client.post(f"{API}/students/{student.id}/reveal")
    assert revealed.status_code == 200, revealed.text
    deleted = await client.delete(f"{API}/students/{student.id}")
    assert deleted.status_code == 200, deleted.text

    rows = (
        await db_session.execute(
            sa.select(AuditLog.action, AuditLog.before, AuditLog.after)
            .where(AuditLog.entity_id == student.id)
            .order_by(AuditLog.id)
        )
    ).all()
    assert [action for action, _b, _a in rows] == ["student.pii_revealed", "student.deleted"]
    assert rows[0].after == {"display_name": "П***а А.С."}
    assert rows[1].before == {"display_name": "П***а А.С."}
    assert "Петрова" not in str([tuple(row) for row in rows])
