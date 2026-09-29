"""RBAC: матрица permissions, проверки ролей, внутренний токен, audience."""

from __future__ import annotations

from app.core.security import (
    KNOWN_ROLES,
    check_roles,
    parse_token_payload,
    validate_audience,
    verify_internal_token,
)
from app.modules.auth.permissions import ROLE_PERMISSIONS, permissions_for_roles

# permissions kam из примера /auth/me (api-contract.md §2.1) — фронт завязан на эти строки
KAM_CONTRACT_PERMISSIONS = {
    "requests:read",
    "requests:write",
    "requests:transition",
    "universities:read",
    "universities:write",
    "contracts:read",
    "contracts:write",
    "programs:read",
    "programs:priority",
    "students:read",
    "students:write",
    "reports:read",
    "import:run",
    "export:run",
    "workflow:read",
    "comments:write",
    "notifications:self",
}


class TestPermissionsMatrix:
    def test_kam_contains_contract_permissions(self) -> None:
        assert KAM_CONTRACT_PERMISSIONS <= set(ROLE_PERMISSIONS["kam"])

    def test_observer_is_read_only(self) -> None:
        writes = {
            p
            for p in ROLE_PERMISSIONS["observer"]
            if p.endswith((":write", ":delete", ":transition", ":approve"))
            or p in ("import:run", "workflow:change")
        }
        assert writes == set()

    def test_observer_has_no_pii_reveal(self) -> None:
        assert "students:reveal" not in ROLE_PERMISSIONS["observer"]

    def test_hierarchy_is_monotonic(self) -> None:
        # admin ⊇ head_kam ⊇ kam: у старшей роли не может быть меньше прав
        assert set(ROLE_PERMISSIONS["kam"]) <= set(ROLE_PERMISSIONS["head_kam"])
        assert set(ROLE_PERMISSIONS["head_kam"]) <= set(ROLE_PERMISSIONS["admin"])

    def test_workflow_approve_is_admin_only(self) -> None:
        for role in ("head_kam", "kam", "observer"):
            assert "workflow:approve" not in ROLE_PERMISSIONS[role]
        assert "workflow:approve" in ROLE_PERMISSIONS["admin"]

    def test_union_for_multiple_roles(self) -> None:
        merged = permissions_for_roles(["kam", "observer"])
        assert set(merged) == set(ROLE_PERMISSIONS["kam"]) | set(ROLE_PERMISSIONS["observer"])
        assert merged == sorted(merged)

    def test_unknown_role_gives_nothing(self) -> None:
        assert permissions_for_roles(["svc_notification", "unknown"]) == []


class TestRoleChecks:
    def test_check_roles_intersection(self) -> None:
        assert check_roles(["kam"], ("admin", "head_kam", "kam"))
        assert not check_roles(["observer"], ("admin", "head_kam", "kam"))
        assert not check_roles([], ("admin",))

    def test_parse_token_filters_unknown_realm_roles(self) -> None:
        claims = parse_token_payload(
            {
                "sub": "3f0e6d20-0000-0000-0000-000000000001",
                "preferred_username": "kam1@demo",
                "realm_access": {
                    "roles": ["kam", "offline_access", "uma_authorization", "default-roles-crm"]
                },
            }
        )
        assert claims.roles == ("kam",)
        assert set(claims.roles) <= KNOWN_ROLES


class TestInternalToken:
    def test_valid_token(self) -> None:
        assert verify_internal_token("secret-token", "secret-token")

    def test_invalid_or_missing(self) -> None:
        assert not verify_internal_token("wrong", "secret-token")
        assert not verify_internal_token(None, "secret-token")
        assert not verify_internal_token("", "secret-token")
        # пустой ожидаемый токен никогда не пропускает (защита от пустого Secret)
        assert not verify_internal_token("", "")


class TestAudience:
    def test_aud_string_match(self) -> None:
        assert validate_audience({"aud": "account"}, ["account", "crm-frontend"])

    def test_aud_list_match(self) -> None:
        assert validate_audience({"aud": ["other", "crm-frontend"]}, ["account", "crm-frontend"])

    def test_azp_fallback(self) -> None:
        assert validate_audience({"azp": "crm-frontend"}, ["account", "crm-frontend"])

    def test_mismatch_rejected(self) -> None:
        assert not validate_audience({"aud": "evil"}, ["account", "crm-frontend"])
