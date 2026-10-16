"""F2 × workflow на настоящем PostgreSQL (волна 2): роли и права в конструкторе и движке.

- переходы своей роли в движке (`transition_deal`): код роли в `allowed_roles` и процесс
  роли — kam_b2c на переходе только для системных ролей → 403, после добавления кода → 200;
- уровни конструктора по процессу: `workflow:change` без `approve` → пакет на согласование,
  с `approve` → безопасный пакет применён сразу, без `change` → 403, право только в B2B →
  403 в B2C; история (`workflow:history`) и согласование (`workflow:approve`) — в процессе
  пакета; схема чужого процесса — 404, список процессов — только видимые;
- V-7: строгий для ролей, которые добавляет пакет (роль есть и может выполнять переходы в
  процессе), мягкий для живых строк (роль просто существует);
- гонка «пакет добавляет роль ↔ роль удаляют»: роли читаются свежим SELECT под локом
  воронки, `delete_role` берёт те же локи (RBAC → b2b → b2c), таймаут — 409 workflow_busy;
- получатели правил `role` (`outbox.role_rule_members`): процесс и «только свои»,
  эквивалентность сводок переноса и поштучного пути для своих ролей.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access import ensure_role_catalog, reset_role_catalog
from app.core.errors import ApiError
from app.core.events import EVENT_REQUEST_TRANSITIONED
from app.core.outbox import _resolve_rule_users, role_rule_members
from app.core.rbac_catalog import SYSTEM_ROLES
from app.models import AppUser, Deal, WorkflowChangeRequest, WorkflowTransition
from app.models.access import AppRole
from app.modules.access import service as access_service
from app.modules.workflow import locks, migration, service
from app.modules.workflow import router as workflow_router
from app.modules.workflow.graph import E_UNKNOWN_ROLE
from app.tools.bench_migration import legacy_migrate_deleted_stages
from app.tools.seed import seed_workflows

from . import factories
from .rbac_factories import KAM_PERMISSIONS, make_role
from .workflow_factories import bulk_insert_open_deals, delete_first_contact_ops, raw_operations

OBSERVER = SYSTEM_ROLES["observer"].permissions
EDITOR = KAM_PERMISSIONS | {"workflow:history", "workflow:change"}
PUBLISHER = EDITOR | {"workflow:approve"}

Maker = async_sessionmaker[AsyncSession]
SCENARIO_TIMEOUT = 30


# --------------------------------------------------------------------------------------
# Помощники
# --------------------------------------------------------------------------------------


def _wf_id(workflows, code: str) -> uuid.UUID:  # noqa: ANN001
    return workflows[code][0].id


def _safe_op(workflows, code: str = "b2b", color: str = "#3366ff") -> dict[str, Any]:  # noqa: ANN001
    _wf, stages = workflows[code]
    return {"op": "update_status", "status_id": str(stages["new"].id), "patch": {"color": color}}


def _add_transition(stages, from_code: str, to_code: str, roles: list[str]) -> dict[str, Any]:  # noqa: ANN001
    return {
        "op": "add_transition",
        "transition": {
            "from_status_id": str(stages[from_code].id),
            "to_status_id": str(stages[to_code].id),
            "name": f"Тест {from_code} → {to_code}",
            "allowed_roles": roles,
        },
    }


async def _post_change(client, workflow_id: uuid.UUID, *ops: dict[str, Any]):  # noqa: ANN001, ANN202
    return await client.post(
        f"/api/v1/workflows/{workflow_id}/changes", json={"comment": "тест", "operations": ops}
    )


def _error_codes(response) -> set[str]:  # noqa: ANN001
    return {detail["code"] for detail in response.json()["error"].get("details") or []}


async def _user_with(session: AsyncSession, *roles: str) -> AppUser:
    user = await factories.make_user(session, roles=roles)
    reset_role_catalog()  # свои роли созданы в этом тесте — каталог перечитается
    return user


# --------------------------------------------------------------------------------------
# 1. Движок: переход своей роли
# --------------------------------------------------------------------------------------


async def test_engine_transition_for_custom_role(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    user = await _user_with(db_session, "kam_b2c")
    wf, stages = workflows["b2c"]
    deal = await factories.make_deal(db_session, workflow=wf, stages=stages, responsible=user)
    client = api.as_user(user)
    card = await client.get(f"/api/v1/requests/{deal.id}")
    assert card.status_code == 200, card.text
    body = {"to_status_id": str(stages["consultation"].id), "version": card.json()["version"]}

    denied = await client.post(f"/api/v1/requests/{deal.id}/transitions", json=body)
    assert denied.status_code == 403
    assert denied.json()["error"]["message"] == "Ваша роль не может выполнить этот переход"

    transition = (
        await db_session.execute(
            sa.select(WorkflowTransition).where(
                WorkflowTransition.from_stage_id == stages["new"].id,
                WorkflowTransition.to_stage_id == stages["consultation"].id,
            )
        )
    ).scalar_one()
    transition.allowed_roles = [*transition.allowed_roles, "kam_b2c"]
    await db_session.flush()

    allowed = await client.post(f"/api/v1/requests/{deal.id}/transitions", json=body)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"]["id"] == str(stages["consultation"].id)


async def test_engine_rejects_process_outside_role(
    db_session: AsyncSession,
    workflows,  # noqa: ANN001
) -> None:
    # движок перепроверяет право и без роутера (заявка назначена до смены роли)
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    user = await _user_with(db_session, "kam_b2c")
    await ensure_role_catalog(db_session, force=True)
    wf, stages = workflows["b2b"]
    deal = await factories.make_deal(db_session, workflow=wf, stages=stages, responsible=user)
    version = await db_session.scalar(
        sa.text("SELECT xmin::text::bigint FROM deal WHERE id = :id"), {"id": deal.id}
    )
    with pytest.raises(ApiError) as excinfo:
        await service.transition_deal(
            db_session,
            deal_id=deal.id,
            to_status_id=stages["first_contact"].id,
            actor=user,
            comment=None,
            version=int(version),
        )
    assert excinfo.value.status_code == 403
    assert "процесса B2B недоступен" in excinfo.value.message


# --------------------------------------------------------------------------------------
# 2. Уровни конструктора по процессу
# --------------------------------------------------------------------------------------


async def test_change_without_approve_goes_to_approval(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="wf_editor", permissions=EDITOR)
    editor = await _user_with(db_session, "wf_editor")
    response = await _post_change(
        api.as_user(editor), _wf_id(workflows, "b2b"), _safe_op(workflows)
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"


async def test_change_with_approve_applies_safe_package_now(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="wf_publisher", permissions=PUBLISHER)
    publisher = await _user_with(db_session, "wf_publisher")
    client = api.as_user(publisher)
    response = await _post_change(client, _wf_id(workflows, "b2b"), _safe_op(workflows))
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "applied"
    # опасный пакет (переименование) — всегда на согласование, даже с правом публикации
    _wf, stages = workflows["b2b"]
    rename = {
        "op": "rename_status",
        "status_id": str(stages["negotiation"].id),
        "new_name": "Переговоры с вузом",
        "confirm_name": stages["negotiation"].name,
    }
    dangerous = await _post_change(client, _wf_id(workflows, "b2b"), rename)
    assert dangerous.status_code == 201, dangerous.text
    assert dangerous.json()["status"] == "pending"


async def test_base_roles_keep_constructor_levels(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
) -> None:
    head = await factories.make_user(db_session, roles=("head_kam",))
    kam = await factories.make_user(db_session, roles=("kam",))
    wf_id = _wf_id(workflows, "b2b")
    applied = await _post_change(api.as_user(admin_user), wf_id, _safe_op(workflows))
    assert applied.json()["status"] == "applied"
    pending = await _post_change(api.as_user(head), wf_id, _safe_op(workflows, color="#000000"))
    assert pending.json()["status"] == "pending"
    preview = await api.as_user(kam).post(
        f"/api/v1/workflows/{wf_id}/impact-preview", json={"operations": [_safe_op(workflows)]}
    )
    assert preview.status_code == 403
    assert (await _post_change(api.as_user(kam), wf_id, _safe_op(workflows))).status_code == 403


async def test_change_right_only_in_b2b(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="wf_editor_b2b", permissions=EDITOR, workflow_types=("b2b",))
    editor = await _user_with(db_session, "wf_editor_b2b")
    client = api.as_user(editor)
    b2c = await _post_change(client, _wf_id(workflows, "b2c"), _safe_op(workflows, "b2c"))
    assert b2c.status_code == 403
    assert "в процессе B2C" in b2c.json()["error"]["message"]
    preview = await client.post(
        f"/api/v1/workflows/{_wf_id(workflows, 'b2c')}/impact-preview",
        json={"operations": [_safe_op(workflows, "b2c")]},
    )
    assert preview.status_code == 403
    b2b = await _post_change(client, _wf_id(workflows, "b2b"), _safe_op(workflows))
    assert b2b.status_code == 201, b2b.text


async def test_history_in_process(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    head = await factories.make_user(db_session, roles=("head_kam",))
    observer = await factories.make_user(db_session, roles=("observer",))
    kam = await factories.make_user(db_session, roles=("kam",))
    await make_role(db_session, code="obs_b2c", permissions=OBSERVER, workflow_types=("b2c",))
    obs_b2c = await _user_with(db_session, "obs_b2c")
    b2b_change = await _post_change(
        api.as_user(head), _wf_id(workflows, "b2b"), _safe_op(workflows)
    )
    b2c_change = await _post_change(
        api.as_user(head), _wf_id(workflows, "b2c"), _safe_op(workflows, "b2c")
    )
    b2b_id, b2c_id = b2b_change.json()["id"], b2c_change.json()["id"]

    listed = await api.as_user(observer).get(
        f"/api/v1/workflows/{_wf_id(workflows, 'b2b')}/changes"
    )
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert (
        await api.as_user(kam).get(f"/api/v1/workflows/{_wf_id(workflows, 'b2b')}/changes")
    ).status_code == 403

    client = api.as_user(obs_b2c)
    foreign = await client.get(f"/api/v1/workflows/{_wf_id(workflows, 'b2b')}/changes")
    assert foreign.status_code == 403
    assert (await client.get(f"/api/v1/workflows/changes/{b2b_id}")).status_code == 404
    own = await client.get(f"/api/v1/workflows/changes/{b2c_id}")
    assert own.status_code == 200 and own.json()["id"] == b2c_id


async def test_approve_in_process_of_package(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    head = await factories.make_user(db_session, roles=("head_kam",))
    await make_role(db_session, code="approver_b2c", permissions=PUBLISHER, workflow_types=("b2c",))
    # КАМ в обоих процессах (видит схему B2B) + согласование только в B2C
    approver = await _user_with(db_session, "kam", "approver_b2c")
    b2b_change = await _post_change(
        api.as_user(head), _wf_id(workflows, "b2b"), _safe_op(workflows)
    )
    b2c_change = await _post_change(
        api.as_user(head), _wf_id(workflows, "b2c"), _safe_op(workflows, "b2c")
    )
    assert b2b_change.json()["status"] == b2c_change.json()["status"] == "pending"

    head_approve = await api.as_user(head).post(
        f"/api/v1/workflows/changes/{b2b_change.json()['id']}/approve"
    )
    assert head_approve.status_code == 403

    client = api.as_user(approver)
    denied = await client.post(f"/api/v1/workflows/changes/{b2b_change.json()['id']}/approve")
    assert denied.status_code == 403
    assert "в процессе B2B" in denied.json()["error"]["message"]
    rejected = await client.post(
        f"/api/v1/workflows/changes/{b2b_change.json()['id']}/reject", json={"reason": "нет"}
    )
    assert rejected.status_code == 403
    approved = await client.post(f"/api/v1/workflows/changes/{b2c_change.json()['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"
    unknown = await client.post(f"/api/v1/workflows/changes/{uuid.uuid4()}/approve")
    assert unknown.status_code == 404


# --------------------------------------------------------------------------------------
# 3. Видимость схем процессов
# --------------------------------------------------------------------------------------


async def test_workflow_list_and_schema_by_process(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    # доска строится по схеме: requests:read без workflow:read всё равно видит схему
    await make_role(
        db_session, code="board_reader", permissions={"requests:read"}, workflow_types=("b2b",)
    )
    await make_role(db_session, code="analyst", permissions={"reports:read"})
    b2c_kam = await factories.make_user(db_session, roles=("kam_b2c",))
    reader = await factories.make_user(db_session, roles=("board_reader",))
    analyst = await _user_with(db_session, "analyst")

    client = api.as_user(b2c_kam)
    listed = await client.get("/api/v1/workflows")
    assert [item["type"] for item in listed.json()] == ["b2c"]
    assert (await client.get(f"/api/v1/workflows/{_wf_id(workflows, 'b2b')}")).status_code == 404
    schema = await client.get(f"/api/v1/workflows/{_wf_id(workflows, 'b2c')}")
    assert schema.status_code == 200 and schema.json()["type"] == "b2c"

    reader_list = await api.as_user(reader).get("/api/v1/workflows")
    assert [item["type"] for item in reader_list.json()] == ["b2b"]
    reader_schema = await api.as_user(reader).get(f"/api/v1/workflows/{_wf_id(workflows, 'b2b')}")
    assert reader_schema.status_code == 200

    assert (await api.as_user(analyst).get("/api/v1/workflows")).json() == []
    assert (
        await api.as_user(analyst).get(f"/api/v1/workflows/{_wf_id(workflows, 'b2c')}")
    ).status_code == 404


class _MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get_json(self, suffix: str) -> Any | None:
        return self.data.get(suffix)

    async def set_json(self, suffix: str, value: Any, ttl_seconds: int) -> bool:
        self.data[suffix] = value
        return True


async def test_cached_schema_still_checks_process(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _MemoryCache()
    monkeypatch.setattr(workflow_router, "get_cache", lambda: cache)
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    b2c_kam = await _user_with(db_session, "kam_b2c")
    b2b_id = _wf_id(workflows, "b2b")
    assert (await api.as_user(admin_user).get(f"/api/v1/workflows/{b2b_id}")).status_code == 200
    assert f"wf:{b2b_id}" in cache.data  # схема B2B в кэше
    assert (await api.as_user(b2c_kam).get(f"/api/v1/workflows/{b2b_id}")).status_code == 404


# --------------------------------------------------------------------------------------
# 4. V-7: строгий для операций пакета, мягкий для живых строк
# --------------------------------------------------------------------------------------


async def test_strict_v7_rejects_roles_that_cannot_transition(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    await make_role(db_session, code="analyst", permissions={"reports:read"})
    reset_role_catalog()
    client = api.as_user(admin_user)
    _b2b, b2b_stages = workflows["b2b"]
    _b2c, b2c_stages = workflows["b2c"]
    b2b_id, b2c_id = _wf_id(workflows, "b2b"), _wf_id(workflows, "b2c")

    for role in ("observer", "analyst", "ghost_role", "kam_b2c"):
        op = _add_transition(b2b_stages, "new", "negotiation", ["kam", role])
        response = await _post_change(client, b2b_id, op)
        assert response.status_code == 400, (role, response.text)
        error = response.json()["error"]
        assert error["code"] == "workflow_invalid_schema"
        (detail,) = error["details"]
        assert detail["code"] == E_UNKNOWN_ROLE and detail["role"] == role
        assert "не может выполнять переходы в этом процессе" in detail["message"]
        preview = await client.post(
            f"/api/v1/workflows/{b2b_id}/impact-preview", json={"operations": [op]}
        )
        assert preview.status_code == 400 and E_UNKNOWN_ROLE in _error_codes(preview)

    # своя роль процесса B2C — в переходе B2C допустима, пакет админа применён сразу
    ok = await _post_change(
        client, b2c_id, _add_transition(b2c_stages, "new", "proposal", ["kam", "kam_b2c"])
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "applied"
    stored = (
        await db_session.execute(
            sa.select(WorkflowTransition.allowed_roles).where(
                WorkflowTransition.from_stage_id == b2c_stages["new"].id,
                WorkflowTransition.to_stage_id == b2c_stages["proposal"].id,
            )
        )
    ).scalar_one()
    assert stored == ["kam", "kam_b2c"]


async def test_soft_v7_does_not_block_live_rows(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
) -> None:
    # роль в живом переходе потеряла право на переходы (observer — никогда не имел):
    # схема не блокируется, правка других полей этого перехода тоже проходит
    _wf, stages = workflows["b2b"]
    transition = (
        await db_session.execute(
            sa.select(WorkflowTransition).where(
                WorkflowTransition.from_stage_id == stages["new"].id,
                WorkflowTransition.to_stage_id == stages["first_contact"].id,
            )
        )
    ).scalar_one()
    transition.allowed_roles = ["kam", "observer"]
    await db_session.flush()
    client = api.as_user(admin_user)
    wf_id = _wf_id(workflows, "b2b")

    safe = await _post_change(client, wf_id, _safe_op(workflows))
    assert safe.status_code == 201 and safe.json()["status"] == "applied"
    rename = {
        "op": "update_transition",
        "transition_id": str(transition.id),
        "patch": {"name": "Связались", "allowed_roles": ["observer"]},
    }
    renamed = await _post_change(client, wf_id, rename)
    assert renamed.status_code == 201, renamed.text
    # а добавить новую роль без права на переходы — нельзя (строгий V-7)
    adding = {
        "op": "update_transition",
        "transition_id": str(transition.id),
        "patch": {"allowed_roles": ["observer", "kam", "head_kam", "analyst_x"]},
    }
    response = await _post_change(client, wf_id, adding)
    assert response.status_code == 400
    (detail,) = response.json()["error"]["details"]
    assert detail["role"] == "analyst_x" and "«Связались»" in detail["message"]


async def test_apply_reads_roles_fresh_under_lock(
    db_session: AsyncSession,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
) -> None:
    """Роль удалена после создания пакета, кэш каталога процесса её ещё помнит: применение
    под локом читает роли свежим SELECT → E_UNKNOWN_ROLE, пакет failed, переход не создан."""
    head = await factories.make_user(db_session, roles=("head_kam",))
    await make_role(db_session, code="temp_role", workflow_types=("b2b",))
    reset_role_catalog()
    wf, stages = workflows["b2b"]
    operations = service.parse_operations(
        [_add_transition(stages, "new", "negotiation", ["kam", "temp_role"])]
    )
    wcr = await service.create_change(
        db_session,
        workflow_id=wf.id,
        author=head,
        operations=operations,
        raw_operations=raw_operations(operations),
        comment=None,
    )
    assert wcr.status == "pending"
    catalog = await ensure_role_catalog(db_session, force=True)
    assert "temp_role" in catalog.roles
    # удаление мимо сервиса ролей: кэш каталога не сброшен
    await db_session.execute(sa.delete(AppRole).where(AppRole.code == "temp_role"))
    await db_session.commit()

    with pytest.raises(ApiError) as excinfo:
        await service.apply_change(db_session, change_id=wcr.id, approver=admin_user)
    assert excinfo.value.code == "workflow_invalid_schema"
    assert [d["role"] for d in excinfo.value.details or []] == ["temp_role"]
    status = await db_session.scalar(
        sa.select(WorkflowChangeRequest.status).where(WorkflowChangeRequest.id == wcr.id)
    )
    assert status == "failed"
    created = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(WorkflowTransition)
        .where(WorkflowTransition.allowed_roles.contains(["temp_role"]))
    )
    assert created == 0


async def test_role_in_applied_transition_cannot_be_deleted(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
) -> None:
    role = await make_role(db_session, code="temp_role", workflow_types=("b2b",))
    reset_role_catalog()
    _wf, stages = workflows["b2b"]
    client = api.as_user(admin_user)
    applied = await _post_change(
        client,
        _wf_id(workflows, "b2b"),
        _add_transition(stages, "new", "negotiation", ["temp_role"]),
    )
    assert applied.json()["status"] == "applied"
    response = await client.delete(f"/api/v1/admin/roles/{role.id}")
    assert response.status_code == 409
    (detail,) = response.json()["error"]["details"]
    assert detail["field"] == "transitions" and "(B2B)" in detail["items"][0]


# --------------------------------------------------------------------------------------
# 5. Гонка с удалением роли: локи воронок (настоящие COMMIT)
# --------------------------------------------------------------------------------------


async def _committed_world(maker: Maker) -> dict[str, Any]:
    """Админ, head_kam, воронки из сида, своя роль temp_role (переходы в B2B) и пакет
    head_kam «добавить переход с temp_role» на согласовании — всё закоммичено."""
    async with maker() as session:
        admin = await factories.make_user(session, roles=("admin",), username="race-admin@test")
        head = await factories.make_user(session, roles=("head_kam",), username="race-head@test")
        workflows = await seed_workflows(session, admin)
        role = await make_role(session, code="temp_role", workflow_types=("b2b",))
        await session.commit()
        wf, stages = workflows["b2b"]
        reset_role_catalog()
        operations = service.parse_operations(
            [_add_transition(stages, "new", "negotiation", ["kam", "temp_role"])]
        )
        wcr = await service.create_change(
            session,
            workflow_id=wf.id,
            author=head,
            operations=operations,
            raw_operations=raw_operations(operations),
            comment=None,
        )
        assert wcr.status == "pending"
        return {
            "admin_id": admin.id,
            "role_id": role.id,
            "change_id": wcr.id,
            "b2b_id": wf.id,
        }


async def test_apply_waits_for_role_delete_and_sees_it_gone(
    db_committed_sessionmaker: Maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    world = await _committed_world(maker)
    locked = asyncio.Event()
    taken: list[uuid.UUID] = []
    marks: dict[str, float] = {}
    original = access_service.acquire_exclusive_workflow_lock

    async def holding(session: AsyncSession, workflow_id: uuid.UUID, **kwargs: Any) -> None:
        await original(session, workflow_id, **kwargs)
        taken.append(workflow_id)
        if len(taken) == 2:  # b2b, затем b2c — держим оба
            locked.set()
            await asyncio.sleep(0.3)

    monkeypatch.setattr(access_service, "acquire_exclusive_workflow_lock", holding)

    async def delete() -> None:
        async with maker() as session:
            admin = await session.get(AppUser, world["admin_id"])
            await access_service.delete_role(
                session, actor=admin, role_id=world["role_id"], ip=None
            )

    async def apply() -> ApiError:
        await locked.wait()
        marks["apply_started"] = time.perf_counter()
        async with maker() as session:
            admin = await session.get(AppUser, world["admin_id"])
            try:
                await service.apply_change(session, change_id=world["change_id"], approver=admin)
            except ApiError as exc:
                marks["applied"] = time.perf_counter()
                return exc
        raise AssertionError("пакет с удалённой ролью применён")

    _, error = await asyncio.wait_for(asyncio.gather(delete(), apply()), SCENARIO_TIMEOUT)
    assert taken[0] == world["b2b_id"]  # порядок локов: b2b → b2c
    assert marks["applied"] - marks["apply_started"] >= 0.2  # применение ждало удаления
    assert error.code == "workflow_invalid_schema"
    assert [d["role"] for d in error.details or []] == ["temp_role"]
    async with maker() as session:
        assert await session.get(AppRole, world["role_id"]) is None
        status = await session.scalar(
            sa.select(WorkflowChangeRequest.status).where(
                WorkflowChangeRequest.id == world["change_id"]
            )
        )
        assert status == "failed"


async def test_role_delete_waits_for_apply_and_sees_transition(
    db_committed_sessionmaker: Maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    world = await _committed_world(maker)
    locked = asyncio.Event()
    original = service.acquire_exclusive_workflow_lock

    async def holding(session: AsyncSession, workflow_id: uuid.UUID, **kwargs: Any) -> None:
        await original(session, workflow_id, **kwargs)
        locked.set()
        await asyncio.sleep(0.3)

    monkeypatch.setattr(service, "acquire_exclusive_workflow_lock", holding)

    async def apply() -> None:
        async with maker() as session:
            admin = await session.get(AppUser, world["admin_id"])
            await service.apply_change(session, change_id=world["change_id"], approver=admin)

    async def delete() -> ApiError:
        await locked.wait()
        async with maker() as session:
            admin = await session.get(AppUser, world["admin_id"])
            try:
                await access_service.delete_role(
                    session, actor=admin, role_id=world["role_id"], ip=None
                )
            except ApiError as exc:
                return exc
        raise AssertionError("роль удалена, хотя пакет добавил её в переход")

    _, error = await asyncio.wait_for(asyncio.gather(apply(), delete()), SCENARIO_TIMEOUT)
    assert error.status_code == 409 and error.code == "role_in_use"
    assert [d["field"] for d in error.details or []] == ["transitions"]


async def test_role_delete_busy_workflow_returns_409(
    db_committed_sessionmaker: Maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    world = await _committed_world(maker)
    monkeypatch.setattr(locks, "APPLY_LOCK_TIMEOUT_MS", 200)
    async with maker() as holder, maker() as session:
        # применение пакета B2B «держит» воронку
        await locks.acquire_exclusive_workflow_lock(holder, world["b2b_id"], timeout_ms=1000)
        admin = await session.get(AppUser, world["admin_id"])
        with pytest.raises(ApiError) as excinfo:
            await access_service.delete_role(
                session, actor=admin, role_id=world["role_id"], ip=None
            )
        await session.rollback()
        await holder.rollback()
    error = excinfo.value
    assert error.status_code == 409 and error.code == "workflow_busy"
    assert error.headers == {"Retry-After": "2"}
    assert "роль не удалена" in error.message
    async with maker() as session:
        assert await session.get(AppRole, world["role_id"]) is not None


# --------------------------------------------------------------------------------------
# 6. Получатели правил `role`: процесс и «только свои»
# --------------------------------------------------------------------------------------


async def test_role_rule_members_respect_process_and_own_scope(
    db_session: AsyncSession,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    await make_role(db_session, code="kam_own", record_scope="own")
    b2c_member = await factories.make_user(db_session, roles=("kam_b2c",))
    await factories.make_user(db_session, roles=("kam_b2c",), is_active=False)
    own1 = await factories.make_user(db_session, roles=("kam_own",))
    own2 = await factories.make_user(db_session, roles=("kam_own",))
    other = await _user_with(db_session, "kam")

    def ids(users: list[AppUser]) -> set[uuid.UUID]:
        return {user.id for user in users}

    assert ids(await role_rule_members(db_session, "kam_b2c", pipeline=None)) == {b2c_member.id}
    assert await role_rule_members(db_session, "kam_b2c", pipeline="b2b") == []
    assert ids(await role_rule_members(db_session, "kam_b2c", pipeline="b2c")) == {b2c_member.id}
    assert await role_rule_members(db_session, "", pipeline=None) == []
    own_members = await role_rule_members(db_session, "kam_own", pipeline="b2b")
    assert ids(own_members) == {own1.id, own2.id}
    assert [u.id for u in own_members] == sorted(u.id for u in own_members)
    mine = await role_rule_members(
        db_session, "kam_own", pipeline="b2b", responsible_user_id=own1.id
    )
    assert ids(mine) == {own1.id}
    assert (
        await role_rule_members(db_session, "kam_own", pipeline="b2b", responsible_user_id=other.id)
        == []
    )

    # поштучный путь: правило по роли и заявка события
    rule = await factories.make_rule(
        db_session,
        event_type=EVENT_REQUEST_TRANSITIONED,
        recipient_type="role",
        recipient_value="kam_b2c",
    )
    b2b_wf, b2b_stages = workflows["b2b"]
    b2c_wf, b2c_stages = workflows["b2c"]
    b2b_deal = await factories.make_deal(
        db_session, workflow=b2b_wf, stages=b2b_stages, responsible=other
    )
    b2c_deal = await factories.make_deal(
        db_session, workflow=b2c_wf, stages=b2c_stages, responsible=other
    )
    assert await _resolve_rule_users(db_session, rule, b2b_deal) == []
    assert ids(await _resolve_rule_users(db_session, rule, b2c_deal)) == {b2c_member.id}
    assert ids(await _resolve_rule_users(db_session, rule, None)) == {b2c_member.id}


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


async def test_digest_equivalent_to_legacy_for_custom_roles(
    db_session: AsyncSession,
    workflows,  # noqa: ANN001
    admin_user,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сводки переноса и прежний поштучный путь дают одинаковых получателей и для своих
    ролей: «КАМ B2C» не получает о переносе в B2B, «только свои» — лишь свои заявки."""
    wf, stages = workflows["b2b"]
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    await make_role(db_session, code="kam_own", record_scope="own")
    own1 = await factories.make_user(db_session, roles=("kam_own",), tg_chat_id=701)
    own2 = await factories.make_user(db_session, roles=("kam_own",), tg_chat_id=702)
    b2c_member = await factories.make_user(db_session, roles=("kam_b2c",), tg_chat_id=703)
    other = await factories.make_user(db_session, roles=("kam",), tg_chat_id=704)
    reset_role_catalog()
    for user in (own1, own2, b2c_member):  # «все заявки», а не «только мои» по умолчанию
        await factories.set_notify_prefs(
            db_session,
            user=user,
            doc={"events": {EVENT_REQUEST_TRANSITIONED: {"only_mine": False}}},
        )
    for code in ("kam_own", "kam_b2c"):
        await factories.make_rule(
            db_session,
            event_type=EVENT_REQUEST_TRANSITIONED,
            recipient_type="role",
            recipient_value=code,
        )
    university = await factories.make_university(db_session)
    for user, count in ((own1, 5), (other, 3)):
        await bulk_insert_open_deals(
            db_session,
            workflow_id=wf.id,
            stage_id=stages["first_contact"].id,
            university_id=university.id,
            responsible_ids=[user.id],
            n=count,
            title_prefix=f"fc-{user.username}",
        )
    operations = delete_first_contact_ops(stages)
    wcr = await service.create_change(
        db_session,
        workflow_id=wf.id,
        author=admin_user,
        operations=operations,
        raw_operations=raw_operations(operations),
        comment=None,
    )
    change_id, own1_id = wcr.id, own1.id  # откат SAVEPOINT ниже истечёт объекты сессии
    await db_session.commit()

    live = await service.load_graph(db_session, wf.id)
    nested = await db_session.begin_nested()
    with monkeypatch.context() as patch:
        patch.setattr(migration, "migrate_deleted_stages", legacy_migrate_deleted_stages)
        legacy_report = await service._execute_plan(
            db_session, wcr, admin_user, live, service.parse_operations(wcr.operations)
        )
    legacy = await _recipient_counts(db_session)
    await nested.rollback()
    for obj in list(db_session.identity_map.values()):
        if isinstance(obj, Deal):
            db_session.expunge(obj)
    await db_session.refresh(admin_user)
    assert legacy_report["migrated_requests"] == 8

    _, report = await service.apply_change(db_session, change_id=change_id, approver=admin_user)
    assert report["migrated_requests"] == 8
    digests = await _recipient_counts(db_session)
    assert digests == legacy
    assert digests == Counter({(str(own1_id), "telegram", "pending"): 5})
