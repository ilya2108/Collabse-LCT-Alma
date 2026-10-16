"""Все модели SQLAlchemy (35 таблиц по data-model.md §13)."""

from app.models.access import AppPermission, AppRole, AppRolePermission
from app.models.base import Base
from app.models.crm import (
    Contract,
    ContractProduct,
    Counterparty,
    InteractionType,
    Product,
    Program,
    University,
    UniversityContact,
)
from app.models.deal import Deal, DealComment, DealStageHistory
from app.models.idempotency import IdempotencyKey
from app.models.service import (
    AppSetting,
    AuditLog,
    ExternalLink,
    FileObject,
    ImportRowError,
    ImportSession,
    IntegrationEvent,
    Notification,
    NotificationRule,
)
from app.models.talent import (
    Activity,
    ActivitySchedule,
    FederalProject,
    Student,
    StudentActivity,
    StudentStatusHistory,
)
from app.models.user import AppUser
from app.models.workflow import (
    Workflow,
    WorkflowChangeRequest,
    WorkflowStage,
    WorkflowTransition,
)

__all__ = [
    "Base",
    "AppUser",
    "AppPermission",
    "AppRole",
    "AppRolePermission",
    "University",
    "UniversityContact",
    "Counterparty",
    "InteractionType",
    "Product",
    "Program",
    "Contract",
    "ContractProduct",
    "Workflow",
    "WorkflowStage",
    "WorkflowTransition",
    "WorkflowChangeRequest",
    "Deal",
    "DealStageHistory",
    "DealComment",
    "FederalProject",
    "Student",
    "StudentStatusHistory",
    "Activity",
    "ActivitySchedule",
    "StudentActivity",
    "FileObject",
    "ImportSession",
    "ImportRowError",
    "IntegrationEvent",
    "ExternalLink",
    "NotificationRule",
    "Notification",
    "AppSetting",
    "AuditLog",
    "IdempotencyKey",
]
