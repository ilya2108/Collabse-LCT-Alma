"""Самопроверка инфраструктуры tests/db: схема на head, изоляция, очистка, клиенты, миграции."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.models import AppUser, Deal, DealStageHistory

from . import factories
from .conftest import drop_database, ensure_database, run_alembic, run_async, script_head

_MARK = "smoke-committed@test"


async def test_schema_is_at_head(db_session, migrated_db) -> None:
    assert migrated_db.head == script_head()
    version = (
        await db_session.execute(sa.text("SELECT version_num FROM alembic_version"))
    ).scalar()
    assert version == migrated_db.head
    ext = await db_session.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'"))
    assert ext.scalar() == 1
    index = await db_session.execute(
        sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_notification_dedup'")
    )
    assert "WHERE (dedup_key IS NOT NULL)" in index.scalar()


async def test_commit_is_savepoint_and_invisible_outside(db_session, db_engine, user_factory):
    user = await user_factory(roles=("kam",))
    await db_session.commit()  # как в обработчике: фиксирует SAVEPOINT, не транзакцию теста
    assert await db_session.get(AppUser, user.id) is not None
    async with db_engine.connect() as other:
        seen = await other.execute(sa.select(AppUser.id).where(AppUser.id == user.id))
        assert seen.first() is None


async def test_rollback_inside_service_keeps_test_transaction(db_session, user_factory) -> None:
    keep_id = (await user_factory()).id
    await db_session.commit()
    lost_id = (await user_factory()).id
    await db_session.rollback()  # откат до SAVEPOINT; объекты сессии после него expired
    assert await db_session.get(AppUser, keep_id) is not None
    assert (
        await db_session.execute(sa.select(AppUser).where(AppUser.id == lost_id))
    ).first() is None


async def test_deal_factory_history_and_status(db_session, workflows) -> None:
    wf, stages = workflows["b2b"]
    kam = await factories.make_user(db_session, roles=("kam",))
    deal = await factories.make_deal(
        db_session,
        workflow=wf,
        stages=stages,
        responsible=kam,
        created_at=factories.days_ago(30),
        path=[
            ("new", factories.days_ago(30)),
            ("first_contact", factories.days_ago(20)),
            ("completed", factories.days_ago(5), None, "system_migration"),
        ],
    )
    assert deal.status == "won" and deal.university_id is not None
    rows = (
        (
            await db_session.execute(
                sa.select(DealStageHistory)
                .where(DealStageHistory.deal_id == deal.id)
                .order_by(DealStageHistory.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [r.from_stage_id is None for r in rows] == [True, False, False]
    assert rows[-1].actor_user_id is None and rows[-1].kind == "system_migration"
    b2c_wf, b2c_stages = workflows["b2c"]
    b2c = await factories.make_deal(db_session, workflow=b2c_wf, stages=b2c_stages, responsible=kam)
    assert b2c.counterparty_id is not None and b2c.status == "open"
    assert (await db_session.get(Deal, b2c.id)).pipeline == "b2c"


async def test_committed_mode_writes_for_real(db_committed_sessionmaker, db_engine) -> None:
    async with db_committed_sessionmaker() as session:
        await factories.make_user(session, username=_MARK)
        await session.commit()
    async with db_engine.connect() as other:
        found = await other.execute(sa.select(AppUser.id).where(AppUser.username == _MARK))
        assert found.first() is not None


async def test_committed_mode_was_cleaned_and_seed_restored(db_engine, migrated_db) -> None:
    # выполняется после предыдущего (порядок файла): TRUNCATE + восстановление снимка
    async with db_engine.connect() as conn:
        left = await conn.execute(sa.select(AppUser.id).where(AppUser.username == _MARK))
        assert left.first() is None
        for table in migrated_db.seed_tables:
            count = await conn.execute(sa.text(f'SELECT count(*) FROM public."{table}"'))
            assert count.scalar() == len(migrated_db.seed_rows[table])


async def test_api_client_as_user(api, user_factory) -> None:
    kam = await user_factory(roles=("kam",), full_name="Петров Пётр Петрович")
    client = api.as_user(kam)
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["username"] == kam.username
    anonymous = await api.anonymous().get("/api/v1/auth/me")
    assert anonymous.status_code == 401


async def test_memory_storage(memory_storage, db_session, user_factory) -> None:
    from app.modules.files.service import store_bytes

    user = await user_factory()
    file_object = await store_bytes(
        db_session,
        data=b"%PDF-test",
        original_name="КП.pdf",
        content_type="application/pdf",
        bucket="crm-files",
        entity_type="deal",
        entity_id=user.id,
        uploaded_by=user.id,
    )
    assert memory_storage.objects[(file_object.s3_bucket, file_object.s3_key)][0] == b"%PDF-test"


def test_migrations_roundtrip(migrated_db) -> None:
    """upgrade head → downgrade base → upgrade head на отдельной базе: миграции обратимы."""
    url = make_url(migrated_db.url)
    name = migrated_db.name
    scratch = url.set(database=f"{name}_mig" if name.startswith("crm_test") else f"{name}_mig_test")
    scratch_url = scratch.render_as_string(hide_password=False)
    run_async(lambda: drop_database(scratch_url))
    run_async(lambda: ensure_database(scratch_url))
    try:
        run_alembic(scratch_url, "upgrade", "head")
        run_alembic(scratch_url, "downgrade", "base")
        run_alembic(scratch_url, "upgrade", "head")
    finally:
        run_async(lambda: drop_database(scratch_url))
