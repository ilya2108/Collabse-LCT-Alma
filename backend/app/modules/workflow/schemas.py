"""Pydantic-схемы конструктора workflow (api-contract.md §5.2–5.4).

Пакет операций — discriminated union по полю `op`. Опасные операции
(`rename_status`, `delete_status`) несут `confirm_name` — посимвольно введённое
текущее название этапа; на impact-preview оно опционально, при создании пакета
и применении сверяется с актуальным именем (422 при несовпадении).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.workflow.conditions import validate_condition_syntax

# роли, допустимые в allowed_roles ребра (V-7, E_UNKNOWN_ROLE)
TRANSITION_ROLES = ("admin", "head_kam", "kam")

DANGEROUS_OPS = frozenset({"rename_status", "delete_status"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_condition(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is not None and not validate_condition_syntax(value):
        raise ValueError(
            "Некорректное condition-условие: ожидается JSON-DSL "
            "{all|any: [...]} или {field, op, value}"
        )
    return value


# --- операции над этапами ---------------------------------------------------------------


class StatusSpec(_Model):
    """Новый этап (`add_status`). `position` — мост API ↔ колонка sort_order."""

    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=200)
    color: str | None = Field(default=None, max_length=20)
    description: str | None = None
    position: int = Field(default=0, ge=0)
    is_terminal: bool = False
    terminal_outcome: Literal["won", "lost"] | None = None
    stuck_threshold_days: int | None = Field(default=None, ge=1, le=60)
    triggers_lms_handover: bool = False
    ui_position: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_terminal(self) -> StatusSpec:
        if self.is_terminal and self.terminal_outcome is None:
            raise ValueError("Терминальный этап обязан иметь terminal_outcome (won|lost)")
        if not self.is_terminal and self.terminal_outcome is not None:
            raise ValueError("terminal_outcome допустим только у терминального этапа")
        return self


class StatusPatch(_Model):
    """`update_status` — безопасные правки: цвет, порог, позиция, координаты, LMS-флаг."""

    color: str | None = Field(default=None, max_length=20)
    description: str | None = None
    position: int | None = Field(default=None, ge=0)
    stuck_threshold_days: int | None = Field(default=None, ge=1, le=60)
    reset_stuck_threshold: bool = False  # явный сброс порога в NULL (наследовать workflow)
    triggers_lms_handover: bool | None = None
    ui_position: dict[str, Any] | None = None


class AddStatusOp(_Model):
    op: Literal["add_status"]
    status: StatusSpec


class UpdateStatusOp(_Model):
    op: Literal["update_status"]
    status_id: UUID
    patch: StatusPatch


class RenameStatusOp(_Model):
    op: Literal["rename_status"]
    status_id: UUID
    new_name: str = Field(min_length=1, max_length=200)
    confirm_name: str | None = None


class DeleteStatusOp(_Model):
    op: Literal["delete_status"]
    status_id: UUID
    migrate_to_status_id: UUID | None = None
    confirm_name: str | None = None


# --- операции над переходами -------------------------------------------------------------


class TransitionSpec(_Model):
    """Новое ребро (`add_transition`); этапы адресуются кодом или id (новые — кодом)."""

    from_status_id: UUID | None = None
    to_status_id: UUID | None = None
    from_code: str | None = None
    to_code: str | None = None
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["forward", "return"] = "forward"
    requires_comment: bool = False
    allowed_roles: list[Literal["admin", "head_kam", "kam"]] = Field(default_factory=list)
    condition: dict[str, Any] | None = None
    position: int = Field(default=0, ge=0)

    _validate_condition = field_validator("condition")(_check_condition)

    @model_validator(mode="after")
    def _check_endpoints(self) -> TransitionSpec:
        if (self.from_status_id is None) == (self.from_code is None):
            raise ValueError("Укажите ровно одно из from_status_id / from_code")
        if (self.to_status_id is None) == (self.to_code is None):
            raise ValueError("Укажите ровно одно из to_status_id / to_code")
        return self

    @property
    def effective_requires_comment(self) -> bool:
        # возврат всегда требует комментарий (CHECK ck_transition_return_comment)
        return True if self.kind == "return" else self.requires_comment


class TransitionPatch(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["forward", "return"] | None = None
    requires_comment: bool | None = None
    allowed_roles: list[Literal["admin", "head_kam", "kam"]] | None = None
    condition: dict[str, Any] | None = None
    reset_condition: bool = False  # явный сброс condition в NULL
    position: int | None = Field(default=None, ge=0)

    _validate_condition = field_validator("condition")(_check_condition)


class AddTransitionOp(_Model):
    op: Literal["add_transition"]
    transition: TransitionSpec


class UpdateTransitionOp(_Model):
    op: Literal["update_transition"]
    transition_id: UUID
    patch: TransitionPatch


class DeleteTransitionOp(_Model):
    op: Literal["delete_transition"]
    transition_id: UUID


Operation = Annotated[
    AddStatusOp
    | UpdateStatusOp
    | RenameStatusOp
    | DeleteStatusOp
    | AddTransitionOp
    | UpdateTransitionOp
    | DeleteTransitionOp,
    Field(discriminator="op"),
]


def is_dangerous(operation: Operation) -> bool:
    return operation.op in DANGEROUS_OPS


# --- тела запросов/ответов ---------------------------------------------------------------


class ImpactPreviewBody(_Model):
    operations: list[Operation] = Field(min_length=1)


class ChangeCreateBody(_Model):
    comment: str | None = Field(default=None, max_length=2000)
    operations: list[Operation] = Field(min_length=1)


class RejectBody(_Model):
    reason: str = Field(min_length=1, max_length=2000)


class UserRef(BaseModel):
    id: UUID
    full_name: str | None = None


class ImpactByStatus(BaseModel):
    status_id: UUID
    status_name: str
    count: int
    migrate_to: dict[str, Any] | None = None  # {"id": ..., "name": ...}


class ImpactOut(BaseModel):
    affected_requests_total: int
    by_status: list[ImpactByStatus]


class ChangeRequestOut(BaseModel):
    id: UUID
    workflow_id: UUID
    status: str
    comment: str | None = None
    author: UserRef | None = None
    decided_by: UserRef | None = None
    decision_comment: str | None = None
    operations: list[dict[str, Any]]
    impact: dict[str, Any] | None = None
    created_at: datetime
    decided_at: datetime | None = None
    applied_at: datetime | None = None


class MigrationReportByStatus(BaseModel):
    from_status: str = Field(serialization_alias="from")
    to_status: str = Field(serialization_alias="to")
    count: int


class MigrationReport(BaseModel):
    migrated_requests: int
    by_status: list[MigrationReportByStatus]


class ApplyResultOut(BaseModel):
    id: UUID
    status: str
    approved_by: UserRef | None = None
    applied_at: datetime | None = None
    migration_report: MigrationReport
