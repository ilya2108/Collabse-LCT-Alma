"""Сброс демо-стенда (`python -m app.tools.seed --reset`, make demo-reset) на PostgreSQL.

Сценарий «после репетиции»: kam2 стал «КАМ B2C», появилась роль «Аналитик», у КАМа сняли
право, этап удалили, цвет поменяли, флаг выключили, завели лишнюю заявку, онбординг
открыли. Сброс обязан вернуть эталон, не трогая app_user (id, keycloak_sub, Telegram) и
журнал аудита, а даты пересчитать от якоря.
"""

from __future__ import annotations

import json
from datetime import timedelta

import sqlalchemy as sa

from app.core.rbac_catalog import SYSTEM_ROLES
from app.models import (
    AppRole,
    AppRolePermission,
    AppSetting,
    AppUser,
    AuditLog,
    Deal,
    FileObject,
    University,
    Workflow,
    WorkflowStage,
)
from app.modules.ui.presets import presets_key
from app.tools import seed
from tests.db import factories


async def _user(session, username: str) -> AppUser:  # noqa: ANN001
    return (
        await session.execute(sa.select(AppUser).where(AppUser.username == username))
    ).scalar_one()


async def _count(session, model, *where) -> int:  # noqa: ANN001
    stmt = sa.select(sa.func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return (await session.execute(stmt)).scalar_one()


async def _onboarding(session, user: AppUser) -> dict:  # noqa: ANN001
    row = await session.get(AppSetting, presets_key(user.id))
    assert row is not None
    preset = next(
        item
        for item in row.value["items"]
        if item["screen"] == "onboarding" and item["name"] == "state"
    )
    return preset["state"]


async def test_demo_reset_restores_reference_stand(
    db_session_factory, memory_storage, monkeypatch
) -> None:  # noqa: ANN001
    anchor = factories.utcnow().replace(microsecond=0)
    monkeypatch.setattr(seed, "get_session_factory", lambda: db_session_factory)
    monkeypatch.setattr(seed, "_NOW", anchor)

    first = await seed.run_seed()
    assert first["deals"] >= 90

    # --- «репетиция» ------------------------------------------------------------------------
    async with db_session_factory() as session:
        users_before = {
            u.username: (u.id, u.keycloak_sub)
            for u in (await session.execute(sa.select(AppUser))).scalars()
        }
        deals_before = await _count(session, Deal)
        files_before = set(memory_storage.objects)
        assert files_before, "сид положил файлы заявок"

        kam1 = await _user(session, "kam1@demo")
        kam1.tg_chat_id, kam1.tg_username = 424242, "kam1_tg"
        kam2 = await _user(session, "kam2@demo")
        kam2.roles = ["kam_b2c"]
        analyst = AppRole(code="analyst", name="Аналитик", is_system=False)
        session.add(analyst)
        await session.flush()
        session.add(AppRolePermission(role_id=analyst.id, permission_code="reports:read"))
        observer = await _user(session, "observer@demo")
        observer.roles = ["observer", "analyst"]
        kam_role = (
            await session.execute(sa.select(AppRole).where(AppRole.code == "kam"))
        ).scalar_one()
        await session.execute(
            sa.delete(AppRolePermission).where(
                AppRolePermission.role_id == kam_role.id,
                AppRolePermission.permission_code == "requests:create",
            )
        )
        b2b = (
            await session.execute(sa.select(Workflow).where(Workflow.code == "b2b"))
        ).scalar_one()
        stages = {
            s.code: s
            for s in (
                await session.execute(
                    sa.select(WorkflowStage).where(WorkflowStage.workflow_id == b2b.id)
                )
            ).scalars()
        }
        stages["contract_signed"].deleted_at = anchor
        stages["negotiation"].color = "#13c2c2"
        b2b.stuck_threshold_days = 30
        flags = await session.get(AppSetting, "feature_flags")
        flags.value = {**flags.value, "report_pdf": False}
        head = await _user(session, "head@demo")
        await factories.make_deal(
            session,
            workflow=b2b,
            stages={code: s for code, s in stages.items() if code != "contract_signed"},
            responsible=head,
            title="Репетиция — заявка с демо",
        )
        row = await session.get(AppSetting, presets_key(head.id))
        for item in row.value["items"]:
            if item["screen"] == "onboarding":
                item["state"] = {**item["state"], "welcome_seen": False, "checklist_hidden": False}
        row.value = json.loads(json.dumps(row.value))
        await session.commit()

    # --- сброс ------------------------------------------------------------------------------
    await seed.run_seed(reset=True)

    async with db_session_factory() as session:
        users_after = {
            u.username: (u.id, u.keycloak_sub)
            for u in (await session.execute(sa.select(AppUser))).scalars()
        }
        assert users_after == users_before  # app_user не очищался: id и keycloak_sub те же
        kam1 = await _user(session, "kam1@demo")
        assert (kam1.tg_chat_id, kam1.tg_username) == (424242, "kam1_tg")
        assert (await _user(session, "kam2@demo")).roles == ["kam"]
        assert (await _user(session, "observer@demo")).roles == ["observer"]
        kam03 = await _user(session, "kam03@demo")
        assert kam03.roles == ["kam"] and kam03.manager_id == (await _user(session, "head@demo")).id

        codes = set((await session.execute(sa.select(AppRole.code))).scalars())
        assert codes == set(SYSTEM_ROLES) | {"kam_b2b", "kam_b2c"}
        kam_role = (
            await session.execute(sa.select(AppRole).where(AppRole.code == "kam"))
        ).scalar_one()
        kam_perms = set(
            (
                await session.execute(
                    sa.select(AppRolePermission.permission_code).where(
                        AppRolePermission.role_id == kam_role.id
                    )
                )
            ).scalars()
        )
        assert kam_perms == SYSTEM_ROLES["kam"].permissions

        b2b = (
            await session.execute(sa.select(Workflow).where(Workflow.code == "b2b"))
        ).scalar_one()
        assert b2b.stuck_threshold_days == 14
        rows = (
            (
                await session.execute(
                    sa.select(WorkflowStage).where(WorkflowStage.workflow_id == b2b.id)
                )
            )
            .scalars()
            .all()
        )
        b2b_json = json.loads((seed.SEEDS_DIR / "b2b.json").read_text(encoding="utf-8"))
        spec = {s["code"]: s for s in b2b_json["stages"]}
        assert {s.code for s in rows} == set(spec)  # удалённый этап восстановлен, «призраков» нет
        assert all(s.deleted_at is None for s in rows)
        for stage in rows:
            assert stage.color == spec[stage.code]["color"]
        assert {s.code: s.stuck_threshold_days for s in rows}["in_learning"] == 120

        assert await _count(session, Deal) == deals_before
        assert await _count(session, Deal, Deal.title == "Репетиция — заявка с демо") == 0
        assert await _count(session, Deal, Deal.source == "lms") == 0
        assert await _count(session, University) == 56
        closed_recently = await _count(
            session,
            Deal,
            Deal.status.in_(("won", "lost")),
            Deal.stage_entered_at.between(anchor - timedelta(days=20), anchor - timedelta(days=3)),
        )
        assert closed_recently >= 4

        flags = await session.get(AppSetting, "feature_flags")
        assert flags.value["report_pdf"] is True
        state = await _onboarding(session, await _user(session, "head@demo"))
        assert state["welcome_seen"] is True and state["checklist_hidden"] is True

        audit = (
            await session.execute(sa.select(AuditLog).where(AuditLog.action == "stand.demo_reset"))
        ).scalar_one()
        assert audit.after["removed_roles"] == ["analyst"]

        stored = {
            (bucket, key)
            for bucket, key in (
                await session.execute(sa.select(FileObject.s3_bucket, FileObject.s3_key))
            ).all()
        }
        assert stored and set(memory_storage.objects) == stored  # старые объекты стёрты
        assert not files_before & stored


async def test_demo_reset_refuses_when_keep_table_references_domain(db_session) -> None:  # noqa: ANN001
    """TRUNCATE без CASCADE: ссылка из сохраняемой таблицы на очищаемую — отказ целиком."""
    await db_session.execute(sa.text("ALTER TABLE app_user ADD COLUMN probe_university uuid"))
    await db_session.execute(
        sa.text(
            "ALTER TABLE app_user ADD CONSTRAINT fk_probe_university "
            "FOREIGN KEY (probe_university) REFERENCES university(id)"
        )
    )
    users = await _count(db_session, AppUser)
    try:
        async with db_session.begin_nested():
            await seed.truncate_demo_data(db_session)
    except sa.exc.DBAPIError as exc:
        assert "foreign key" in str(exc).lower() or "truncate" in str(exc).lower()
    else:  # pragma: no cover — защита сломана
        raise AssertionError("TRUNCATE прошёл бы каскадом через app_user")
    assert await _count(db_session, AppUser) == users
