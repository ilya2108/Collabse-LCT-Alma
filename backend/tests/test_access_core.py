"""Ядро прав F2 без БД: каталог прав, объединение ролей по процессу, область заявок.

Покрывает `core/rbac_catalog.py` (целостность, зависимости, наборы системных ролей),
`core/access.py` (compute_access, DealScope, кэш каталога и SSE-хук) и чистые проверки
сервиса ролей (защита от потери управления), схемы API ролей и `require_permission`.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.core import access as access_module
from app.core.access import (
    DealScope,
    RoleCatalog,
    RoleGrant,
    access_for_roles,
    compute_access,
    get_catalog_snapshot,
    reset_role_catalog,
    static_catalog,
)
from app.core.events import SSE_ACCESS_CHANGED
from app.core.rbac_catalog import (
    PERMISSION_CODES,
    PERMISSION_GROUP_CODES,
    PERMISSIONS,
    PERMISSIONS_BY_CODE,
    SCOPED_PERMISSIONS,
    SYSTEM_ROLE_CODES,
    SYSTEM_ROLES,
    WORKFLOW_TYPES,
    missing_requirements,
    with_requirements,
)
from app.core.security import PEOPLE_ROLES, require_permission
from app.core.sse import SseHub
from app.modules.access.schemas import RoleCreate, RolePatch
from app.modules.access.service import admin_holders_after
from app.modules.workflow.service import user_can_write_deal

KAM = SYSTEM_ROLES["kam"].permissions

# колонка S таблицы §4.1 спецификации F2 (+ requests:files)
EXPECTED_SCOPED = {
    "requests:read",
    "requests:write",
    "requests:write_any",
    "requests:transition",
    "requests:assign",
    "requests:assign_any",
    "requests:kam",
    "requests:reopen",
    "requests:delete",
    "requests:files",
    "comments:write",
    "comments:delete_any",
    "workflow:read",
    "workflow:history",
    "workflow:change",
    "workflow:approve",
    "reports:read",
    "reports:all",
}


def _grant(
    code: str,
    permissions: frozenset[str] | set[str],
    workflow_types: tuple[str, ...] = WORKFLOW_TYPES,
    record_scope: str = "all",
    *,
    is_system: bool = False,
) -> RoleGrant:
    return RoleGrant(
        code=code,
        name=code.upper(),
        is_system=is_system,
        permissions=frozenset(permissions),
        workflow_types=frozenset(workflow_types),
        record_scope=record_scope,  # type: ignore[arg-type]
    )


def _catalog(*grants: RoleGrant, version: int = 100) -> RoleCatalog:
    roles = dict(static_catalog().roles)
    roles.update({grant.code: grant for grant in grants})
    return RoleCatalog(roles=roles, version=version, loaded_at=0.0)


def _sql(clause) -> str:  # noqa: ANN001
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


DEMO = _catalog(
    _grant("kam_b2b", KAM, ("b2b",)),
    _grant("kam_b2c", KAM, ("b2c",)),
    _grant("kam_own", KAM, WORKFLOW_TYPES, "own"),
    _grant("analyst", {"reports:read", "reports:all", "export:run"}, ("b2c",)),
)


# --------------------------------------------------------------------------------------
# Каталог прав
# --------------------------------------------------------------------------------------


class TestCatalogIntegrity:
    def test_codes_unique_and_counted(self) -> None:
        codes = [p.code for p in PERMISSIONS]
        assert len(codes) == len(set(codes)) == 60

    def test_groups_known(self) -> None:
        assert {p.group for p in PERMISSIONS} <= PERMISSION_GROUP_CODES

    def test_requires_reference_existing(self) -> None:
        for definition in PERMISSIONS:
            assert set(definition.requires) <= PERMISSION_CODES, definition.code
            assert definition.code not in definition.requires

    def test_scoped_column(self) -> None:
        assert SCOPED_PERMISSIONS == EXPECTED_SCOPED

    @pytest.mark.parametrize("code", SYSTEM_ROLE_CODES)
    def test_defaults_closed_under_requires(self, code: str) -> None:
        permissions = SYSTEM_ROLES[code].permissions
        assert missing_requirements(permissions) == {}
        assert with_requirements(permissions) == permissions

    def test_role_sizes(self) -> None:
        sizes = {code: len(role.permissions) for code, role in SYSTEM_ROLES.items()}
        assert sizes == {"admin": 59, "head_kam": 40, "kam": 28, "observer": 14}

    def test_admin_has_everything_but_kam_flag(self) -> None:
        assert SYSTEM_ROLES["admin"].permissions == PERMISSION_CODES - {"requests:kam"}
        assert SYSTEM_ROLES["admin"].locked

    def test_requests_kam_only_for_kam(self) -> None:
        holders = {code for code, r in SYSTEM_ROLES.items() if "requests:kam" in r.permissions}
        assert holders == {"kam"}

    def test_people_roles_are_system_roles(self) -> None:
        assert PEOPLE_ROLES == SYSTEM_ROLE_CODES == ("admin", "head_kam", "kam", "observer")

    def test_auth_me_deltas_documented(self) -> None:
        # осознанные изменения /auth/me (спецификация F2 §4.1)
        assert {"requests:kam", "files:business", "clients:pii"} <= SYSTEM_ROLES["kam"].permissions
        assert "contracts:delete" not in SYSTEM_ROLES["head_kam"].permissions
        assert {"workflow:history", "reports:all"} <= SYSTEM_ROLES["observer"].permissions

    def test_requests_files_permission(self) -> None:
        definition = PERMISSIONS_BY_CODE["requests:files"]
        assert definition.scoped and definition.requires == ("requests:read",)
        holders = {c for c, r in SYSTEM_ROLES.items() if "requests:files" in r.permissions}
        assert holders == {"admin", "head_kam", "kam"}


class TestMissingRequirements:
    def test_reports_missing_dependency(self) -> None:
        assert missing_requirements({"workflow:approve"}) == {
            "workflow:approve": ("workflow:change",)
        }

    def test_chain_reported_per_link(self) -> None:
        missing = missing_requirements({"workflow:approve", "workflow:change"})
        assert missing == {"workflow:change": ("workflow:history",)}

    def test_complete_set_ok_and_unknown_ignored(self) -> None:
        assert missing_requirements({"requests:read", "requests:write", "nope:x"}) == {}

    def test_closure(self) -> None:
        assert with_requirements({"workflow:approve"}) == {
            "workflow:approve",
            "workflow:change",
            "workflow:history",
            "workflow:read",
        }


# --------------------------------------------------------------------------------------
# compute_access / has
# --------------------------------------------------------------------------------------


class TestComputeAccess:
    def test_union_of_system_roles(self) -> None:
        access = compute_access(["kam", "observer"], static_catalog())
        assert access.permissions == KAM | SYSTEM_ROLES["observer"].permissions
        assert access.role_codes == ("kam", "observer")

    def test_unknown_roles_ignored(self) -> None:
        access = compute_access(["svc_notification", "ghost"], static_catalog())
        assert access.role_codes == ()
        assert access.permissions == frozenset()
        assert not access.has("requests:read")

    def test_by_workflow_for_two_single_process_roles(self) -> None:
        access = compute_access(["kam_b2b", "kam_b2c"], DEMO)
        assert access.by_workflow["b2b"] == KAM & SCOPED_PERMISSIONS
        assert access.by_workflow["b2c"] == KAM & SCOPED_PERMISSIONS
        assert access.workflow_types == ("b2b", "b2c")

    def test_single_process_role(self) -> None:
        access = compute_access(["kam_b2c"], DEMO)
        assert access.has("requests:read", "b2c")
        assert not access.has("requests:read", "b2b")
        assert access.has("requests:read")  # хоть в одном процессе
        assert access.workflow_types == ("b2c",)
        # общие права действуют вне процессов
        assert access.has("universities:write") and access.has("universities:write", "b2b")

    def test_records_union_all_wins(self) -> None:
        own_only = compute_access(["kam_own"], DEMO)
        assert own_only.records == {"b2b": "own", "b2c": "own"}
        mixed = compute_access(["kam_own", "kam_b2c"], DEMO)
        assert mixed.records == {"b2b": "own", "b2c": "all"}

    def test_scoped_permissions_only_in_role_processes(self) -> None:
        access = compute_access(["kam_b2b", "analyst"], DEMO)
        assert access.has("reports:all", "b2c")
        assert not access.has("reports:all", "b2b")
        assert access.has("requests:write", "b2b")
        assert not access.has("requests:write", "b2c")

    def test_transition_roles(self) -> None:
        access = compute_access(["kam_b2b", "observer"], DEMO)
        assert access.transition_roles("b2b") == {"kam_b2b"}
        assert access.transition_roles("b2c") == frozenset()
        assert compute_access(["admin"], DEMO).transition_roles("b2c") == {"admin"}

    def test_me_payload(self) -> None:
        payload = compute_access(["kam_b2c"], DEMO).as_me_payload(DEMO)
        assert payload["workflow_types"] == ["b2c"]
        assert payload["records"] == {"b2c": "all"}
        assert "requests:write" in payload["by_workflow"]["b2c"]
        assert "b2b" not in payload["by_workflow"]
        assert payload["transition_roles"] == {"b2c": ["kam_b2c"]}
        assert set(payload["scoped_permissions"]) == SCOPED_PERMISSIONS


# --------------------------------------------------------------------------------------
# Область заявок
# --------------------------------------------------------------------------------------


class TestDealScope:
    uid = uuid.UUID("11111111-1111-1111-1111-111111111111")

    def test_all_processes_all_records_is_true(self) -> None:
        scope = compute_access(["admin"], DEMO).deal_scope("requests:read", self.uid)
        assert _sql(scope.clause()) == "true"
        assert scope.is_unrestricted and scope.cache_key() == "b2b:*|b2c:*"

    def test_single_process(self) -> None:
        scope = compute_access(["kam_b2c"], DEMO).deal_scope("requests:read", self.uid)
        assert _sql(scope.clause()) == "deal.pipeline = 'b2c'"
        assert scope.workflow_types == ("b2c",)

    def test_own_records(self) -> None:
        scope = compute_access(["kam_own"], DEMO).deal_scope("requests:read", self.uid)
        assert _sql(scope.clause()) == f"deal.responsible_user_id = '{self.uid}'"
        assert scope.own_user_id == self.uid

    def test_own_in_one_process(self) -> None:
        scope = DealScope(rules=(("b2c", self.uid),))
        assert (
            _sql(scope.clause())
            == f"deal.pipeline = 'b2c' AND deal.responsible_user_id = '{self.uid}'"
        )

    def test_mixed_rules(self) -> None:
        scope = compute_access(["kam_own", "kam_b2c"], DEMO).deal_scope("requests:read", self.uid)
        assert scope.rules == (("b2b", self.uid), ("b2c", None))
        assert _sql(scope.clause()) == (
            f"deal.pipeline = 'b2b' AND deal.responsible_user_id = '{self.uid}' "
            "OR deal.pipeline = 'b2c'"
        )
        assert scope.own_user_id is None
        assert scope.cache_key() == f"b2b:{self.uid}|b2c:*"

    def test_no_access_is_false(self) -> None:
        scope = compute_access(["analyst"], DEMO).deal_scope("requests:read", self.uid)
        assert _sql(scope.clause()) == "false"
        assert scope.is_empty and scope.cache_key() == "-"

    def test_allows(self) -> None:
        other = uuid.uuid4()
        scope = DealScope(rules=(("b2b", self.uid), ("b2c", None)))
        assert scope.allows("b2b", self.uid)
        assert not scope.allows("b2b", other)
        assert scope.allows("b2c", other)
        assert not DealScope(rules=(("b2c", None),)).allows("b2b", other)

    def test_apply_keeps_statement_when_unrestricted(self) -> None:
        from sqlalchemy import select

        from app.models.deal import Deal

        stmt = select(Deal.id)
        assert DealScope.unrestricted().apply(stmt) is stmt
        assert "WHERE deal.pipeline" in _sql(DealScope(rules=(("b2c", None),)).apply(stmt))

    def test_workflow_clause_ignores_owner(self) -> None:
        scope = DealScope(rules=(("b2c", self.uid),))
        assert _sql(scope.workflow_clause()) == "deal.pipeline IN ('b2c')"

    @pytest.mark.parametrize(
        ("role", "own"),
        [("admin", False), ("head_kam", False), ("kam", True), ("observer", False)],
    )
    def test_report_scope_matches_legacy(self, role: str, own: bool) -> None:
        # прежняя область отчётов по ролям: «широкие» роли a/h/o — все, kam — только свои
        scope = compute_access([role], static_catalog()).deal_scope(
            "reports:read", self.uid, wide_permission="reports:all"
        )
        assert scope.workflow_types == WORKFLOW_TYPES
        assert scope.own_user_id == (self.uid if own else None)

    def test_reports_own_when_role_records_own(self) -> None:
        catalog = _catalog(
            _grant("lead_own", SYSTEM_ROLES["head_kam"].permissions, WORKFLOW_TYPES, "own")
        )
        scope = compute_access(["lead_own"], catalog).deal_scope(
            "reports:read", self.uid, wide_permission="reports:all"
        )
        assert scope.own_user_id == self.uid

    def test_own_only(self) -> None:
        scope = compute_access(["admin"], DEMO).deal_scope("requests:read", self.uid, own_only=True)
        assert scope.own_user_id == self.uid


class TestDealActions:
    @pytest.mark.parametrize("role", SYSTEM_ROLE_CODES)
    @pytest.mark.parametrize("own", [True, False])
    def test_can_act_matches_legacy_write_rule(self, role: str, own: bool) -> None:
        user_id, other = uuid.uuid4(), uuid.uuid4()
        responsible = user_id if own else other
        access = compute_access([role], static_catalog())
        legacy = user_can_write_deal([role], user_id, responsible)
        for permission in ("requests:write", "requests:transition"):
            for pipeline in WORKFLOW_TYPES:
                assert (
                    access.can_act_on_deal(
                        permission,
                        pipeline=pipeline,
                        responsible_user_id=responsible,
                        user_id=user_id,
                    )
                    is legacy
                )

    def test_can_act_respects_process(self) -> None:
        user_id = uuid.uuid4()
        access = compute_access(["kam_b2c"], DEMO)
        kwargs = {"responsible_user_id": user_id, "user_id": user_id}
        assert access.can_act_on_deal("requests:write", pipeline="b2c", **kwargs)
        assert not access.can_act_on_deal("requests:write", pipeline="b2b", **kwargs)

    @pytest.mark.parametrize(
        ("role", "expected"),
        [("admin", {"admin"}), ("head_kam", {"head_kam"}), ("kam", {"kam"}), ("observer", set())],
    )
    def test_transition_roles_of_base_roles(self, role: str, expected: set[str]) -> None:
        access = compute_access([role], static_catalog())
        for pipeline in WORKFLOW_TYPES:
            assert access.transition_roles(pipeline) == expected


# --------------------------------------------------------------------------------------
# Кэш каталога процесса
# --------------------------------------------------------------------------------------


class TestCatalogCache:
    def test_static_without_loaded_catalog(self) -> None:
        reset_role_catalog()
        assert get_catalog_snapshot() is static_catalog()
        assert access_for_roles(["kam"]).has("requests:kam", "b2b")

    def test_lru_by_version_and_roles(self) -> None:
        reset_role_catalog()
        first = access_for_roles(["observer", "kam"])
        assert access_for_roles(["kam", "observer", "kam"]) is first

    def test_snapshot_used_and_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(access_module, "_snapshot", DEMO)
        assert access_for_roles(["kam_b2c"]).workflow_types == ("b2c",)
        reset_role_catalog()
        assert access_for_roles(["kam_b2c"]).role_codes == ()

    def test_sse_hook_resets_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(access_module, "_snapshot", DEMO)
        SseHub().publish_local(SSE_ACCESS_CHANGED, {"role_codes": ["kam_b2c"]})
        assert get_catalog_snapshot() is static_catalog()

    def test_other_topics_do_not_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(access_module, "_snapshot", DEMO)
        SseHub().publish_local("flags.updated", {})
        assert get_catalog_snapshot() is DEMO
        reset_role_catalog()

    def test_role_codes_with(self) -> None:
        assert DEMO.role_codes_with("requests:kam") == ("kam", "kam_b2b", "kam_b2c", "kam_own")
        assert DEMO.role_codes_with("requests:kam", "b2b") == ("kam", "kam_b2b", "kam_own")
        # общее право — процесс не важен
        assert DEMO.role_codes_with("admin:users", "b2c") == ("admin", "head_kam")
        assert DEMO.transition_workflow_types("kam_b2c") == ("b2c",)
        assert DEMO.transition_workflow_types("observer") == ()
        assert DEMO.names(["kam", "ghost"]) == ["КАМ"]


# --------------------------------------------------------------------------------------
# Защита от потери управления, схемы и зависимость права
# --------------------------------------------------------------------------------------


class TestAdminHolders:
    perms = {code: role.permissions for code, role in SYSTEM_ROLES.items()}

    def test_last_admin_removed(self) -> None:
        a = uuid.uuid4()
        assert admin_holders_after([(a, ["kam"])], self.perms) == set()

    def test_two_admins(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        assert admin_holders_after([(a, ["admin"]), (b, ["observer"])], self.perms) == {a}
        assert admin_holders_after([(a, ["admin"]), (b, ["admin"])], self.perms) == {a, b}

    def test_custom_role_with_admin_roles(self) -> None:
        a = uuid.uuid4()
        perms = {**self.perms, "security": frozenset({"admin:users", "admin:roles"})}
        assert admin_holders_after([(a, ["security"])], perms) == {a}
        perms["security"] = frozenset({"admin:users"})
        assert admin_holders_after([(a, ["security"])], perms) == set()


class TestRoleSchemas:
    def _body(self, **overrides: object) -> dict[str, object]:
        return {"code": "analyst", "name": "Аналитик", "workflow_types": ["b2b"], **overrides}

    @pytest.mark.parametrize("code", ["Analyst", "a", "1role", "ana-lyst", "x" * 41])
    def test_code_pattern(self, code: str) -> None:
        with pytest.raises(ValidationError) as caught:
            RoleCreate(**self._body(code=code))
        assert caught.value.errors()[0]["type"] in ("pattern", "string_too_long")

    @pytest.mark.parametrize("code", ["svc_notification", "svc_reports", "offline_access"])
    def test_reserved_codes(self, code: str) -> None:
        with pytest.raises(ValidationError) as caught:
            RoleCreate(**self._body(code=code))
        assert caught.value.errors()[0]["type"] == "reserved"

    def test_valid_body_normalized(self) -> None:
        body = RoleCreate(
            **self._body(name="  Аналитик   заказчика ", workflow_types=["b2c", "b2b", "b2c"])
        )
        assert body.name == "Аналитик заказчика"
        assert body.workflow_types == ["b2b", "b2c"]
        assert body.record_scope == "all"

    def test_workflow_types_required(self) -> None:
        with pytest.raises(ValidationError):
            RoleCreate(**self._body(workflow_types=[]))
        with pytest.raises(ValidationError):
            RoleCreate(**self._body(workflow_types=["b2x"]))

    def test_patch_requires_version(self) -> None:
        with pytest.raises(ValidationError):
            RolePatch(name="Новое имя")
        assert RolePatch(version=7).model_dump(exclude_unset=True) == {"version": 7}


class TestRequirePermission:
    def test_unknown_permission_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="nope:x"):
            require_permission("nope:x")

    def test_dependency_is_marked_and_cached(self) -> None:
        dependency = require_permission("requests:read")
        assert dependency.required_permission == "requests:read"
        assert require_permission("requests:read") is dependency
