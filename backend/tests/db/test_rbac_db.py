"""F2 на настоящем PostgreSQL: каталог ролей из миграции 0004, назначения в БД Альмы,
API ролей, защита от потери управления, область B2B/B2C и «только свои».

Изменения workflow (строгий V-7, уровни конструктора, переходы своих ролей в движке) —
волна 2; здесь проверяется то, что уже переведено на права: заявки, отчёты, экспорт,
поиск, пользователи, /auth/me, кэш каталога.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access import ensure_role_catalog, reset_role_catalog
from app.core.rbac_catalog import PERMISSIONS, SYSTEM_ROLES, WORKFLOW_TYPES
from app.core.security import sync_app_user
from app.models import AppUser, AuditLog, WorkflowTransition
from app.models.access import AppPermission, AppRole, AppRolePermission
from app.modules.access import service as access_service
from app.modules.access.bootstrap import seed_rbac_catalog
from app.tools.seed import seed_demo_roles

from . import factories
from .rbac_factories import KAM_PERMISSIONS, make_role

# --------------------------------------------------------------------------------------
# Помощники
# --------------------------------------------------------------------------------------


async def _role_id(session: AsyncSession, code: str) -> uuid.UUID:
    return (await session.execute(sa.select(AppRole.id).where(AppRole.code == code))).scalar_one()


async def _role_version(api, admin: AppUser, role_id: uuid.UUID) -> int:  # noqa: ANN001
    response = await api.as_user(admin).get(f"/api/v1/admin/roles/{role_id}")
    assert response.status_code == 200, response.text
    return response.json()["version"]


async def _audit(session: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        (await session.execute(sa.select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------------------
# 1. Каталог: миграция 0004 совпадает с кодом
# --------------------------------------------------------------------------------------


def _catalog_from_rows(rows: dict) -> tuple[set, dict[str, set[str]]]:  # noqa: ANN001
    permissions = {(r["code"], r["group_code"], r["is_scoped"]) for r in rows["app_permission"]}
    role_codes = {r["id"]: r["code"] for r in rows["app_role"]}
    by_role: dict[str, set[str]] = {code: set() for code in role_codes.values()}
    for link in rows["app_role_permission"]:
        by_role[role_codes[link["role_id"]]].add(link["permission_code"])
    return permissions, by_role


def test_migration_catalog_matches_code(migrated_db) -> None:  # noqa: ANN001
    permissions, by_role = _catalog_from_rows(migrated_db.seed_rows)
    assert permissions == {(p.code, p.group, p.scoped) for p in PERMISSIONS}
    assert by_role == {code: set(role.permissions) for code, role in SYSTEM_ROLES.items()}
    roles = {r["code"]: r for r in migrated_db.seed_rows["app_role"]}
    assert roles["admin"]["is_locked"] and all(r["is_system"] for r in roles.values())
    assert all(r["workflow_types"] == ["b2b", "b2c"] for r in roles.values())
    assert all(r["record_scope"] == "all" for r in roles.values())


async def test_seed_rbac_catalog_is_idempotent(db_session: AsyncSession, migrated_db) -> None:  # noqa: ANN001
    await seed_rbac_catalog(db_session)
    await seed_rbac_catalog(db_session)
    rows = {
        "app_permission": [
            {"code": p.code, "group_code": p.group_code, "is_scoped": p.is_scoped}
            for p in (await db_session.execute(sa.select(AppPermission))).scalars()
        ],
        "app_role": [
            {"id": r.id, "code": r.code}
            for r in (await db_session.execute(sa.select(AppRole))).scalars()
        ],
        "app_role_permission": [
            {"role_id": link.role_id, "permission_code": link.permission_code}
            for link in (await db_session.execute(sa.select(AppRolePermission))).scalars()
        ],
    }
    assert _catalog_from_rows(rows) == _catalog_from_rows(migrated_db.seed_rows)


async def test_seed_restores_locked_admin(db_session: AsyncSession) -> None:
    admin_id = await _role_id(db_session, "admin")
    await db_session.execute(
        sa.delete(AppRolePermission).where(
            AppRolePermission.role_id == admin_id,
            AppRolePermission.permission_code == "admin:roles",
        )
    )
    await seed_rbac_catalog(db_session)
    catalog = await ensure_role_catalog(db_session, force=True)
    assert "admin:roles" in catalog.roles["admin"].permissions


async def test_migration_cleans_unknown_role_codes(db_session: AsyncSession) -> None:
    # шаг 3 миграции 0004 на строке с мусорными кодами (сам UPDATE из upgrade())
    user = await factories.make_user(db_session, roles=("kam", "svc_notification", "ghost"))
    await db_session.execute(
        sa.text(
            "UPDATE app_user SET roles = ARRAY(SELECT r FROM unnest(roles) r "
            "WHERE r IN (SELECT code FROM app_role) ORDER BY r) WHERE id = :id"
        ),
        {"id": user.id},
    )
    await db_session.refresh(user)
    assert user.roles == ["kam"]


# --------------------------------------------------------------------------------------
# 2. Вход: роли — из БД Альмы
# --------------------------------------------------------------------------------------


async def test_new_user_gets_start_roles_from_token(db_session: AsyncSession, api) -> None:  # noqa: ANN001
    ghost = AppUser(id=uuid.uuid4(), keycloak_sub=uuid.uuid4(), username="newcomer@test", roles=[])
    response = await api.as_user(ghost, token_roles=["kam", "svc_integration"]).get(
        "/api/v1/auth/me"
    )
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["kam"]
    stored = (
        await db_session.execute(sa.select(AppUser).where(AppUser.username == "newcomer@test"))
    ).scalar_one()
    assert stored.roles == ["kam"]


async def test_existing_user_roles_not_overwritten_by_token(
    db_session: AsyncSession,
    api,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    user = await factories.make_user(db_session, roles=("kam_b2c",))
    response = await api.as_user(user, token_roles=["kam"]).get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["kam_b2c"]
    await db_session.refresh(user)
    assert user.roles == ["kam_b2c"]


async def test_user_without_roles_is_mirrored_but_forbidden(
    db_session: AsyncSession,
    api,
    admin_user,  # noqa: ANN001
) -> None:
    nobody = AppUser(id=uuid.uuid4(), keycloak_sub=uuid.uuid4(), username="nobody@test", roles=[])
    response = await api.as_user(nobody, token_roles=[]).get("/api/v1/auth/me")
    assert response.status_code == 403
    assert "не назначены роли" in response.json()["error"]["message"]
    users = await api.as_user(admin_user).get(
        "/api/v1/admin/users", params={"search": "nobody@test"}
    )
    assert users.status_code == 200
    (item,) = users.json()["items"]
    assert item["no_roles"] is True and item["roles"] == []
    only_empty = await api.as_user(admin_user).get(
        "/api/v1/admin/users", params={"no_roles": "true"}
    )
    assert [i["username"] for i in only_empty.json()["items"]] == ["nobody@test"]


async def test_service_account_token_creates_no_user(db_session: AsyncSession, api) -> None:  # noqa: ANN001
    bot = AppUser(id=uuid.uuid4(), keycloak_sub=uuid.uuid4(), username="svc-bot", roles=[])
    response = await api.as_user(bot, token_roles=["svc_notification"]).get("/api/v1/auth/me")
    assert response.status_code == 403
    count = (
        await db_session.execute(
            sa.select(sa.func.count()).select_from(AppUser).where(AppUser.username == "svc-bot")
        )
    ).scalar_one()
    assert count == 0


async def test_sync_app_user_keeps_roles(db_session: AsyncSession) -> None:
    from app.core.security import TokenClaims

    user = await factories.make_user(db_session, roles=("observer",))
    claims = TokenClaims(
        sub=str(user.keycloak_sub),
        username=user.username,
        email="new@mail.test",
        full_name="Новое Имя",
        roles=("admin",),
    )
    synced = await sync_app_user(db_session, claims)
    assert synced.id == user.id
    assert synced.roles == ["observer"]
    assert synced.full_name == "Новое Имя"


# --------------------------------------------------------------------------------------
# 3. API ролей
# --------------------------------------------------------------------------------------

ANALYST = {
    "code": "analyst",
    "name": "Аналитик (заказчик)",
    "permissions": ["reports:read", "reports:all", "export:run"],
    "workflow_types": ["b2b", "b2c"],
    "record_scope": "all",
}


async def test_create_role_and_audit(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    response = await api.as_user(admin_user).post("/api/v1/admin/roles", json=ANALYST)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "analyst" and body["is_system"] is False
    assert body["permissions"] == ["reports:read", "reports:all", "export:run"]
    assert body["permissions_count"] == 3 and body["users_count"] == 0
    (entry,) = await _audit(db_session, "role.created")
    assert entry.after["code"] == "analyst"
    # каталог для всех пользователей
    catalog = await api.as_user(admin_user).get("/api/v1/roles")
    items = {item["code"]: item for item in catalog.json()["items"]}
    assert items["analyst"]["transition_workflow_types"] == []
    assert items["kam"]["transition_workflow_types"] == ["b2b", "b2c"]
    assert list(items)[:4] == ["admin", "head_kam", "kam", "observer"]


@pytest.mark.parametrize(
    ("patch", "status", "code", "field"),
    [
        ({"code": "kam"}, 409, "role_exists", "code"),
        ({"name": "КАМ"}, 409, "role_exists", "name"),
        ({"name": "кам"}, 409, "role_exists", "name"),
        ({"permissions": ["reports:read", "nope:x"]}, 400, "unknown_permission", "permissions"),
        ({"permissions": ["workflow:approve"]}, 400, "permission_dependency", "permissions"),
        ({"code": "svc_reports"}, 400, "reserved", "code"),
        ({"code": "Bad Role!"}, 400, "pattern", "code"),
        ({"workflow_types": []}, 400, "too_short", "workflow_types"),
    ],
)
async def test_create_role_validation(
    api,  # noqa: ANN001
    admin_user,  # noqa: ANN001
    patch: dict,
    status: int,
    code: str,
    field: str,
) -> None:
    response = await api.as_user(admin_user).post("/api/v1/admin/roles", json={**ANALYST, **patch})
    assert response.status_code == status, response.text
    error = response.json()["error"]
    details = error.get("details") or []
    assert error["code"] == code or any(
        d["code"] == code and d["field"] == field for d in details
    ), error


async def test_patch_role_versioning_and_audit(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    created = (await api.as_user(admin_user).post("/api/v1/admin/roles", json=ANALYST)).json()
    url = f"/api/v1/admin/roles/{created['id']}"
    stale = await api.as_user(admin_user).patch(url, json={"name": "X-роль", "version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_version"
    response = await api.as_user(admin_user).patch(
        url,
        json={
            "permissions": ["reports:read", "export:run"],
            "workflow_types": ["b2c"],
            "version": created["version"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["workflow_types"] == ["b2c"]
    assert response.json()["version"] != created["version"]
    (entry,) = await _audit(db_session, "role.updated")
    assert entry.after["permissions_removed"] == ["reports:all"]
    assert entry.before["workflow_types"] == ["b2b", "b2c"]


async def test_admin_role_is_locked(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    role_id = await _role_id(db_session, "admin")
    version = await _role_version(api, admin_user, role_id)
    response = await api.as_user(admin_user).patch(
        f"/api/v1/admin/roles/{role_id}",
        json={"permissions": ["admin:users", "admin:roles"], "version": version},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "role_locked"
    reset = await api.as_user(admin_user).post(
        f"/api/v1/admin/roles/{role_id}/reset", json={"version": version}
    )
    assert reset.status_code == 409 and reset.json()["error"]["code"] == "role_locked"


async def test_reset_system_role(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    role_id = await _role_id(db_session, "observer")
    version = await _role_version(api, admin_user, role_id)
    changed = await api.as_user(admin_user).patch(
        f"/api/v1/admin/roles/{role_id}",
        json={"permissions": ["requests:read", "reports:read"], "version": version},
    )
    assert changed.status_code == 200, changed.text
    detail = await api.as_user(admin_user).get(f"/api/v1/admin/roles/{role_id}")
    assert detail.json()["differs_from_default"] is True
    reset = await api.as_user(admin_user).post(
        f"/api/v1/admin/roles/{role_id}/reset", json={"version": changed.json()["version"]}
    )
    assert reset.status_code == 200, reset.text
    assert set(reset.json()["permissions"]) == SYSTEM_ROLES["observer"].permissions
    detail = await api.as_user(admin_user).get(f"/api/v1/admin/roles/{role_id}")
    assert detail.json()["differs_from_default"] is False
    assert len(await _audit(db_session, "role.reset")) == 1


async def test_delete_roles(
    db_session: AsyncSession,
    api,
    admin_user,
    workflows,  # noqa: ANN001
) -> None:
    system = await api.as_user(admin_user).delete(
        f"/api/v1/admin/roles/{await _role_id(db_session, 'kam')}"
    )
    assert system.status_code == 409 and system.json()["error"]["code"] == "role_system"

    busy = await make_role(db_session, code="busy_role")
    await factories.make_user(db_session, roles=("busy_role",))
    in_transitions = await make_role(db_session, code="wf_role")
    wf, _stages = workflows["b2c"]
    transition = (
        await db_session.execute(
            sa.select(WorkflowTransition).where(WorkflowTransition.workflow_id == wf.id).limit(1)
        )
    ).scalar_one()
    transition.allowed_roles = [*transition.allowed_roles, "wf_role"]
    free = await make_role(db_session, code="free_role")
    await db_session.flush()

    response = await api.as_user(admin_user).delete(f"/api/v1/admin/roles/{busy.id}")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "role_in_use"
    assert [d["field"] for d in error["details"]] == ["users"]
    assert error["details"][0]["count"] == 1

    response = await api.as_user(admin_user).delete(f"/api/v1/admin/roles/{in_transitions.id}")
    assert response.status_code == 409
    (detail,) = response.json()["error"]["details"]
    assert detail["field"] == "transitions" and "(B2C)" in detail["items"][0]

    response = await api.as_user(admin_user).delete(f"/api/v1/admin/roles/{free.id}")
    assert response.status_code == 204
    assert (await db_session.get(AppRole, free.id)) is None
    (entry,) = await _audit(db_session, "role.deleted")
    assert entry.before["code"] == "free_role"


async def test_role_coverage(db_session: AsyncSession, api, admin_user, workflows) -> None:  # noqa: ANN001
    role = await make_role(db_session, code="cover_role")
    response = await api.as_user(admin_user).get(f"/api/v1/admin/roles/{role.id}")
    coverage = response.json()["coverage"]
    # сид: все переходы обеих воронок ограничены ролями, новой роли в них нет
    assert coverage["b2b"]["restricted"] == 10 and coverage["b2b"]["allowed"] == 0
    assert coverage["b2c"]["restricted"] == 9 and coverage["b2c"]["allowed"] == 0
    kam = await api.as_user(admin_user).get(
        f"/api/v1/admin/roles/{await _role_id(db_session, 'kam')}"
    )
    assert kam.json()["coverage"]["b2b"] == {"restricted": 10, "allowed": 9}


async def test_permissions_catalog(api, admin_user) -> None:  # noqa: ANN001
    response = await api.as_user(admin_user).get("/api/v1/admin/permissions")
    groups = response.json()["groups"]
    assert [g["code"] for g in groups][0] == "requests"
    flat = {p["code"]: p for g in groups for p in g["permissions"]}
    assert len(flat) == 62
    assert flat["workflow:approve"]["requires"] == ["workflow:change"]
    assert flat["requests:read"]["scoped"] is True


async def test_roles_api_requires_admin_roles(db_session: AsyncSession, api) -> None:  # noqa: ANN001
    head = await factories.make_user(db_session, roles=("head_kam",))
    for path in ("/api/v1/admin/roles", "/api/v1/admin/permissions"):
        response = await api.as_user(head).get(path)
        assert response.status_code == 403
        assert "Роли и права" in response.json()["error"]["message"]
    assert (await api.as_user(head).get("/api/v1/roles")).status_code == 200


# --------------------------------------------------------------------------------------
# 4. Назначение ролей и защита от потери управления
# --------------------------------------------------------------------------------------


async def test_set_user_roles(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",), name="КАМ B2C")
    kam2 = await factories.make_user(db_session, roles=("kam",))
    url = f"/api/v1/admin/users/{kam2.id}/roles"
    unknown = await api.as_user(admin_user).put(url, json={"roles": ["ghost"]})
    assert unknown.status_code == 400
    assert unknown.json()["error"]["details"][0]["code"] == "unknown_role"
    duplicate = await api.as_user(admin_user).put(url, json={"roles": ["kam", "kam"]})
    assert duplicate.status_code == 400
    stale = await api.as_user(admin_user).put(url, json={"roles": ["kam_b2c"], "version": 1})
    assert stale.status_code == 409

    response = await api.as_user(admin_user).put(url, json={"roles": ["kam_b2c"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["roles"] == ["kam_b2c"] and body["role_names"] == ["КАМ B2C"]
    (entry,) = await _audit(db_session, "user.roles_changed")
    assert entry.before == {"roles": ["kam"]} and entry.after == {"roles": ["kam_b2c"]}
    # назначение действует на следующем же запросе пользователя
    me = await api.as_user(kam2).get("/api/v1/auth/me")
    assert me.json()["access"]["workflow_types"] == ["b2c"]


async def test_last_admin_cannot_remove_own_admin(api, admin_user) -> None:  # noqa: ANN001
    response = await api.as_user(admin_user).put(
        f"/api/v1/admin/users/{admin_user.id}/roles", json={"roles": ["kam"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "access_lockout"


async def test_two_admins(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    second = await factories.make_user(db_session, roles=("admin",))
    # A снимает admin с B — можно
    response = await api.as_user(admin_user).put(
        f"/api/v1/admin/users/{second.id}/roles", json={"roles": ["observer"]}
    )
    assert response.status_code == 200, response.text
    # вернуть B admin и попытаться снять с себя при живом B — нельзя
    await api.as_user(admin_user).put(
        f"/api/v1/admin/users/{second.id}/roles", json={"roles": ["admin"]}
    )
    response = await api.as_user(admin_user).put(
        f"/api/v1/admin/users/{admin_user.id}/roles", json={"roles": ["kam"]}
    )
    assert response.status_code == 409
    assert "с себя" in response.json()["error"]["message"]


async def test_custom_role_cannot_drop_last_admin_roles_holder(
    db_session: AsyncSession,
    api,  # noqa: ANN001
) -> None:
    security = await make_role(
        db_session, code="security", permissions=("admin:users", "admin:roles")
    )
    officer = await factories.make_user(db_session, roles=("security",))
    version = await _role_version(api, officer, security.id)
    response = await api.as_user(officer).patch(
        f"/api/v1/admin/roles/{security.id}",
        json={"permissions": ["admin:users"], "version": version},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "access_lockout"


async def test_concurrent_admins_cannot_remove_each_other(
    db_committed_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_committed_sessionmaker() as session:
        first = await factories.make_user(session, roles=("admin",), username="a1@test")
        second = await factories.make_user(session, roles=("admin",), username="a2@test")
        await session.commit()

    async def remove(actor: AppUser, target: AppUser) -> int:
        async with db_committed_sessionmaker() as session:
            try:
                await access_service.set_user_roles(
                    session,
                    actor=actor,
                    user_id=target.id,
                    roles=["kam"],
                    version=None,
                    ip=None,
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return getattr(exc, "status_code", 500)
            return 200

    results = await asyncio.gather(remove(first, second), remove(second, first))
    assert sorted(results) == [200, 409]
    async with db_committed_sessionmaker() as session:
        admins = (
            await session.execute(sa.select(AppUser.username).where(AppUser.roles.any("admin")))
        ).scalars()
        assert len(list(admins)) == 1


async def test_cli_grant_admin(
    db_committed_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from app.tools import rbac

    async with db_committed_sessionmaker() as session:
        await factories.make_user(session, roles=("observer",), username="rescue@test")
        await session.commit()
    assert await rbac.grant_admin("rescue@test") == 0
    assert await rbac.grant_admin("missing@test") == 1
    async with db_committed_sessionmaker() as session:
        user = (
            await session.execute(sa.select(AppUser).where(AppUser.username == "rescue@test"))
        ).scalar_one()
        assert user.roles == ["admin", "observer"]
        (entry,) = await _audit(session, "user.roles_changed")
        assert entry.after == {"roles": ["admin", "observer"], "source": "cli"}
        assert entry.actor_user_id is None
    assert await rbac.list_admins() == 0


# --------------------------------------------------------------------------------------
# 5–7. Область B2B/B2C и «только свои»
# --------------------------------------------------------------------------------------


@pytest.fixture
async def scoped_world(db_session: AsyncSession, workflows):  # noqa: ANN001, ANN201
    """Роли kam_b2b/kam_b2c/kam_own, их пользователи и заявки: в каждом процессе своя
    (kam_own) и чужая; у пользователя kam_b2c — по заявке в B2C и в B2B (назначена
    до смены роли: ответственный, но процесс вне области)."""
    await make_role(db_session, code="kam_b2b", workflow_types=("b2b",), name="КАМ B2B")
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",), name="КАМ B2C")
    await make_role(db_session, code="kam_own", record_scope="own", name="КАМ свои")
    users = {
        "b2c": await factories.make_user(db_session, roles=("kam_b2c",)),
        "both": await factories.make_user(db_session, roles=("kam_b2b", "kam_b2c")),
        "own": await factories.make_user(db_session, roles=("kam_own",)),
        "other": await factories.make_user(db_session, roles=("kam",)),
        "head": await factories.make_user(db_session, roles=("head_kam",)),
    }
    deals = {}
    for wf_code in WORKFLOW_TYPES:
        wf, stages = workflows[wf_code]
        deals[f"{wf_code}_own"] = await factories.make_deal(
            db_session,
            workflow=wf,
            stages=stages,
            responsible=users["own"],
            title=f"Скоуп {wf_code} своя",
        )
        deals[f"{wf_code}_other"] = await factories.make_deal(
            db_session,
            workflow=wf,
            stages=stages,
            responsible=users["other"],
            title=f"Скоуп {wf_code} чужая",
        )
        deals[f"{wf_code}_mine"] = await factories.make_deal(
            db_session,
            workflow=wf,
            stages=stages,
            responsible=users["b2c"],
            title=f"Скоуп {wf_code} КАМ B2C",
        )
    reset_role_catalog()
    return users, deals


async def test_b2c_role_scope(api, scoped_world, memory_storage) -> None:  # noqa: ANN001
    users, deals = scoped_world
    client = api.as_user(users["b2c"])

    listed = await client.get("/api/v1/requests", params={"limit": 100})
    assert listed.status_code == 200
    assert {item["workflow_type"] for item in listed.json()["items"]} == {"b2c"}
    assert listed.json()["total"] == 3

    board = await client.get("/api/v1/requests/board", params={"workflow_type": "b2b"})
    assert board.status_code == 403
    assert "B2B недоступен" in board.json()["error"]["message"]
    default_board = await client.get("/api/v1/requests/board")
    assert default_board.status_code == 200 and default_board.json()["workflow_type"] == "b2c"

    assert (await client.get(f"/api/v1/requests/{deals['b2b_other'].id}")).status_code == 404
    # ответственный, но процесс вне области — тоже 404
    assert (await client.get(f"/api/v1/requests/{deals['b2b_mine'].id}")).status_code == 404
    assert (await client.get(f"/api/v1/requests/{deals['b2c_other'].id}")).status_code == 200
    for suffix in ("history", "comments", "transitions"):
        response = await client.get(f"/api/v1/requests/{deals['b2b_other'].id}/{suffix}")
        assert response.status_code == 404, suffix

    created = await client.post(
        "/api/v1/requests",
        json={
            "workflow_type": "b2b",
            "title": "Чужой процесс",
            "university_id": str(deals["b2b_other"].university_id),
        },
    )
    assert created.status_code == 403
    assert "B2B недоступно" in created.json()["error"]["message"]

    export = await client.post(
        "/api/v1/export/jobs", json={"entity_type": "requests", "format": "json"}
    )
    assert export.status_code == 201, export.text
    (payload,) = [data for data, _type in memory_storage.objects.values()]
    assert b"b2b" not in payload and payload.count(b'"b2c"') == 3

    funnel = await client.get("/api/v1/reports/funnel", params={"workflow_type": "b2b"})
    assert funnel.status_code == 200
    assert sum(item["value"] for item in funnel.json()["series"]) == 0
    # права КАМа: отчёты без reports:all — только свои заявки, процесс по умолчанию — B2C
    funnel_default = await client.get("/api/v1/reports/funnel")
    assert sum(item["value"] for item in funnel_default.json()["series"]) == 1

    dashboard = await client.get("/api/v1/reports/dashboard")
    assert dashboard.json()["kpi"]["active"]["value"] == 1

    search = await client.get("/api/v1/search", params={"q": "Скоуп"})
    assert {item["workflow_type"] for item in search.json()["requests"]} == {"b2c"}


async def test_own_records_scope(api, scoped_world) -> None:  # noqa: ANN001
    users, deals = scoped_world
    client = api.as_user(users["own"])
    listed = await client.get("/api/v1/requests", params={"limit": 100})
    titles = {item["title"] for item in listed.json()["items"]}
    assert titles == {"Скоуп b2b своя", "Скоуп b2c своя"}
    assert (await client.get(f"/api/v1/requests/{deals['b2b_other'].id}")).status_code == 404
    assert (await client.get(f"/api/v1/requests/{deals['b2b_own'].id}")).status_code == 200
    # отчёты — тоже только свои
    dashboard = await client.get("/api/v1/reports/dashboard")
    assert dashboard.json()["kpi"]["active"]["value"] == 2


async def test_union_of_two_process_roles(api, scoped_world) -> None:  # noqa: ANN001
    users, _deals = scoped_world
    client = api.as_user(users["both"])
    listed = await client.get("/api/v1/requests", params={"limit": 100})
    assert {item["workflow_type"] for item in listed.json()["items"]} == {"b2b", "b2c"}
    assert listed.json()["total"] == 6
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["access"]["workflow_types"] == ["b2b", "b2c"]
    assert me["access"]["transition_roles"] == {"b2b": ["kam_b2b"], "b2c": ["kam_b2c"]}


async def test_base_roles_see_everything(api, scoped_world) -> None:  # noqa: ANN001
    users, _deals = scoped_world
    listed = await api.as_user(users["head"]).get("/api/v1/requests", params={"limit": 100})
    assert listed.json()["total"] == 6
    board = await api.as_user(users["other"]).get("/api/v1/requests/board")
    assert board.status_code == 200 and board.json()["workflow_type"] == "b2b"


async def test_list_filters_accept_repeated_ids(api, scoped_world) -> None:  # noqa: ANN001
    """Переход с дашборда на доску: несколько КАМов / вузов одним запросом (IN), один id —
    как раньше; область записей роли при этом не расширяется."""
    users, deals = scoped_world
    own, other = str(users["own"].id), str(users["other"].id)
    head = api.as_user(users["head"])

    def titles(response) -> set[str]:  # noqa: ANN001
        assert response.status_code == 200, response.text
        return {item["title"] for item in response.json()["items"]}

    both = await head.get("/api/v1/requests", params=[("assignee_id", own), ("assignee_id", other)])
    assert titles(both) == {
        "Скоуп b2b своя",
        "Скоуп b2c своя",
        "Скоуп b2b чужая",
        "Скоуп b2c чужая",
    }
    single = await head.get("/api/v1/requests", params={"assignee_id": own})
    assert titles(single) == {"Скоуп b2b своя", "Скоуп b2c своя"}

    universities = [str(deals["b2b_own"].university_id), str(deals["b2b_other"].university_id)]
    by_university = await head.get(
        "/api/v1/requests", params=[("university_id", u) for u in universities]
    )
    assert titles(by_university) == {"Скоуп b2b своя", "Скоуп b2b чужая"}

    # роль «только свои»: чужой id в списке не открывает чужие заявки
    scoped = await api.as_user(users["own"]).get(
        "/api/v1/requests", params=[("assignee_id", own), ("assignee_id", other)]
    )
    assert titles(scoped) == {"Скоуп b2b своя", "Скоуп b2c своя"}

    bad = await head.get("/api/v1/requests", params={"assignee_id": "не-uuid"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------------------
# 8. Переходы своей роли: доступные переходы фильтруются по ролям процесса
# --------------------------------------------------------------------------------------


async def test_available_transitions_for_custom_role(
    db_session: AsyncSession,
    api,
    workflows,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    user = await factories.make_user(db_session, roles=("kam_b2c",))
    wf, stages = workflows["b2c"]
    deal = await factories.make_deal(db_session, workflow=wf, stages=stages, responsible=user)
    client = api.as_user(user)
    before = await client.get(f"/api/v1/requests/{deal.id}/transitions")
    assert before.status_code == 200 and before.json()["items"] == []

    await seed_demo_roles(db_session, workflows)
    after = await client.get(f"/api/v1/requests/{deal.id}/transitions")
    assert after.json()["items"], "после сида демо-ролей kam_b2c указан в переходах B2C"


async def test_seed_demo_roles_idempotent(db_session: AsyncSession, workflows) -> None:  # noqa: ANN001
    await seed_demo_roles(db_session, workflows)
    await seed_demo_roles(db_session, workflows)
    catalog = await ensure_role_catalog(db_session, force=True)
    assert catalog.roles["kam_b2b"].workflow_types == {"b2b"}
    assert catalog.roles["kam_b2c"].permissions == KAM_PERMISSIONS
    for wf_code, demo in (("b2b", "kam_b2b"), ("b2c", "kam_b2c")):
        wf, _stages = workflows[wf_code]
        rows = (
            (
                await db_session.execute(
                    sa.select(WorkflowTransition.allowed_roles).where(
                        WorkflowTransition.workflow_id == wf.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for allowed in rows:
            assert ("kam" in allowed) == (demo in allowed)
            assert allowed.count(demo) <= 1


# --------------------------------------------------------------------------------------
# 10–12. Кэш каталога, пользователи по праву, /auth/me
# --------------------------------------------------------------------------------------


async def test_role_change_applies_without_ttl(db_session: AsyncSession, api, admin_user) -> None:  # noqa: ANN001
    analyst_role = await make_role(
        db_session, code="analyst", permissions=("export:run",), workflow_types=("b2b",)
    )
    analyst = await factories.make_user(db_session, roles=("analyst",))
    assert (await api.as_user(analyst).get("/api/v1/reports/funnel")).status_code == 403
    version = await _role_version(api, admin_user, analyst_role.id)
    patched = await api.as_user(admin_user).patch(
        f"/api/v1/admin/roles/{analyst_role.id}",
        json={"permissions": ["export:run", "reports:read"], "version": version},
    )
    assert patched.status_code == 200, patched.text
    assert (await api.as_user(analyst).get("/api/v1/reports/funnel")).status_code == 200


async def test_users_by_permission_and_kam_workload(
    db_session: AsyncSession,
    api,
    admin_user,  # noqa: ANN001
) -> None:
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",))
    kam = await factories.make_user(db_session, roles=("kam",), full_name="Ка Обычный")
    kam_b2c = await factories.make_user(db_session, roles=("kam_b2c",), full_name="Ка Бэтуси")
    await factories.make_user(db_session, roles=("head_kam",))
    response = await api.as_user(admin_user).get(
        "/api/v1/admin/users", params={"permission": "requests:kam", "limit": 100}
    )
    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {str(kam.id), str(kam_b2c.id)}
    bad = await api.as_user(admin_user).get("/api/v1/admin/users", params={"permission": "nope:x"})
    assert bad.status_code == 400

    workload = await api.as_user(admin_user).get("/api/v1/reports/kam-workload")
    assert {row["kam_id"] for row in workload.json()["rows"]} == {str(kam.id), str(kam_b2c.id)}
    b2b_only = await api.as_user(admin_user).get(
        "/api/v1/reports/kam-workload", params={"workflow_type": "b2b"}
    )
    assert {row["kam_id"] for row in b2b_only.json()["rows"]} == {str(kam.id)}


async def test_auth_me_payload(db_session: AsyncSession, api) -> None:  # noqa: ANN001
    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",), name="КАМ B2C")
    user = await factories.make_user(db_session, roles=("kam_b2c",))
    me = (await api.as_user(user).get("/api/v1/auth/me")).json()
    assert me["roles"] == ["kam_b2c"]
    assert me["role_details"] == [{"code": "kam_b2c", "name": "КАМ B2C", "is_system": False}]
    assert set(me["permissions"]) == KAM_PERMISSIONS
    access = me["access"]
    assert access["workflow_types"] == ["b2c"]
    assert access["records"] == {"b2c": "all"}
    assert "requests:write" in access["by_workflow"]["b2c"] and "b2b" not in access["by_workflow"]
    assert access["transition_roles"] == {"b2c": ["kam_b2c"]}
    assert "requests:read" in access["scoped_permissions"]


@pytest.mark.parametrize("role", ["admin", "head_kam", "kam", "observer"])
async def test_auth_me_base_roles(db_session: AsyncSession, api, role: str) -> None:  # noqa: ANN001
    user = await factories.make_user(db_session, roles=(role,))
    me = (await api.as_user(user).get("/api/v1/auth/me")).json()
    assert set(me["permissions"]) == SYSTEM_ROLES[role].permissions
    assert me["access"]["workflow_types"] == ["b2b", "b2c"]
    assert me["access"]["records"] == {"b2b": "all", "b2c": "all"}


async def test_bot_respects_access(db_session: AsyncSession, api) -> None:  # noqa: ANN001
    from app.core.security import TokenClaims, get_token_claims

    await make_role(db_session, code="kam_b2c", workflow_types=("b2c",), name="КАМ B2C")
    linked = await factories.make_user(db_session, roles=("kam_b2c",), tg_chat_id=777001)
    no_roles = await factories.make_user(db_session, roles=(), tg_chat_id=777002)
    bot_claims = TokenClaims(
        sub=str(uuid.uuid4()),
        username="service-account-crm-notification",
        email=None,
        full_name=None,
        roles=("svc_notification",),
    )
    api.app.dependency_overrides[get_token_claims] = lambda: bot_claims
    client = api.anonymous()
    summary = await client.get("/api/v1/internal/bot/users/777001/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["role_labels"] == ["КАМ B2C"]
    binding = await client.get("/api/v1/internal/bot/bindings/777001")
    assert binding.json()["role_labels"] == ["КАМ B2C"] and binding.json()["roles"] == ["kam_b2c"]
    forbidden = await client.get("/api/v1/internal/bot/users/777002/summary")
    assert forbidden.status_code == 403
    assert linked.id != no_roles.id


async def test_assignee_must_see_process(api, scoped_world) -> None:  # noqa: ANN001
    users, deals = scoped_world
    head = api.as_user(users["head"])
    deal = deals["b2b_other"]
    response = await head.post(
        f"/api/v1/requests/{deal.id}/assign", json={"assignee_id": str(users["b2c"].id)}
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["code"] == "workflow_not_allowed"
    ok = await head.post(
        f"/api/v1/requests/{deal.id}/assign", json={"assignee_id": str(users["both"].id)}
    )
    assert ok.status_code == 200, ok.text
    # без assign_any — только себе; чужая заявка КАМа не «свободна»
    taken = await api.as_user(users["b2c"]).post(
        f"/api/v1/requests/{deals['b2c_other'].id}/assign",
        json={"assignee_id": str(users["b2c"].id)},
    )
    assert taken.status_code == 403
    assert "другому KAM" in taken.json()["error"]["message"]
