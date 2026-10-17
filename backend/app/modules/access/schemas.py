"""Pydantic-схемы API ролей и прав (api-contract.md §10.5)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from app.core.rbac_catalog import RESERVED_ROLE_CODES, ROLE_CODE_PATTERN

_CODE_RE = re.compile(ROLE_CODE_PATTERN)

WorkflowType = Literal["b2b", "b2c"]
RecordScope = Literal["all", "own"]


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if len(value) < 2:
        raise PydanticCustomError("too_short", "Название роли — от 2 до 60 символов")
    return value


def _unique_types(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    return [wf for wf in ("b2b", "b2c") if wf in value]


class RoleCreate(BaseModel):
    code: str = Field(
        max_length=40,
        description="Латиница в нижнем регистре, цифры и _, начинается с буквы; неизменяем",
        examples=["analyst"],
    )
    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    workflow_types: list[WorkflowType] = Field(min_length=1)
    record_scope: RecordScope = "all"

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        value = value.strip()
        if not _CODE_RE.fullmatch(value):
            raise PydanticCustomError(
                "pattern",
                "Код роли: латинские буквы в нижнем регистре, цифры и «_», 2–40 символов, "
                "начинается с буквы",
            )
        if value in RESERVED_ROLE_CODES or value.startswith("svc_"):
            raise PydanticCustomError(
                "reserved", "Код зарезервирован за служебными ролями Keycloak"
            )
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _clean_name(value)  # type: ignore[return-value]

    @field_validator("workflow_types")
    @classmethod
    def _check_types(cls, value: list[str]) -> list[str]:
        return _unique_types(value)  # type: ignore[return-value]


class RolePatch(BaseModel):
    """Частичное изменение роли; `version` — обязательный optimistic lock (xmin)."""

    name: str | None = Field(default=None, min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = Field(default=None, max_length=100)
    workflow_types: list[WorkflowType] | None = Field(default=None, min_length=1)
    record_scope: RecordScope | None = None
    version: int

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return _clean_name(value)

    @field_validator("workflow_types")
    @classmethod
    def _check_types(cls, value: list[str] | None) -> list[str] | None:
        return _unique_types(value)


class RoleReset(BaseModel):
    version: int


class UserRolesPut(BaseModel):
    roles: list[str] = Field(max_length=10)
    version: int | None = None
