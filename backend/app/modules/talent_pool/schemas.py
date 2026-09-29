"""Pydantic-схемы Talent Pool: студенты, воронка, активности (api-contract.md §8)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

FunnelStatus = Literal["candidate", "studying", "graduate", "talent_pool"]
ActivityType = Literal["course", "event", "internship", "hackathon", "other"]
ScheduleFormat = Literal["online", "offline", "hybrid"]


class _ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- студенты ---------------------------------------------------------------------------


class StudentCreate(_ApiModel):
    full_name: str = Field(min_length=2, max_length=300)
    email: str = Field(min_length=5, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=32)
    university_id: UUID | None = None
    product_id: UUID | None = None
    program_id: UUID | None = None
    counterparty_id: UUID | None = None
    federal_project_id: UUID | None = None
    funnel_status: FunnelStatus = "candidate"
    notes: str | None = None


class StudentUpdate(_ApiModel):
    version: int
    full_name: str | None = Field(default=None, min_length=2, max_length=300)
    email: str | None = Field(
        default=None, min_length=5, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    phone: str | None = Field(default=None, max_length=32)
    university_id: UUID | None = None
    product_id: UUID | None = None
    program_id: UUID | None = None
    counterparty_id: UUID | None = None
    federal_project_id: UUID | None = None
    notes: str | None = None


class StudentStatusBody(_ApiModel):
    to: FunnelStatus
    comment: str | None = None
    version: int


class StudentRevealOut(_ApiModel):
    id: UUID
    full_name: str
    email: str
    phone: str | None
    ttl_seconds: int = 60  # UI прячет значения через 60 с (Р-19)


class StudentHistoryItem(_ApiModel):
    id: UUID
    from_status: str | None
    to_status: str
    actor: dict | None = None
    reason: str | None
    created_at: datetime


# --- активности и сквозное расписание ---------------------------------------------------


class ScheduleSlot(_ApiModel):
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = None
    format: ScheduleFormat | None = None
    note: str | None = None


class ScheduleSlotOut(ScheduleSlot):
    id: UUID


class ActivityCreate(_ApiModel):
    name: str = Field(min_length=2, max_length=300)
    activity_type: ActivityType = "other"
    federal_project_id: UUID | None = None
    university_id: UUID | None = None
    program_id: UUID | None = None
    responsible_user_id: UUID | None = None
    description: str | None = None
    is_active: bool = True
    schedule: list[ScheduleSlot] = Field(default_factory=list)


class ActivityUpdate(_ApiModel):
    version: int
    name: str | None = Field(default=None, min_length=2, max_length=300)
    activity_type: ActivityType | None = None
    federal_project_id: UUID | None = None
    university_id: UUID | None = None
    program_id: UUID | None = None
    responsible_user_id: UUID | None = None
    description: str | None = None
    is_active: bool | None = None
    schedule: list[ScheduleSlot] | None = None  # None — не менять; список — заменить


class ActivityOut(_ApiModel):
    id: UUID
    name: str
    activity_type: str
    federal_project_id: UUID | None
    university_id: UUID | None
    program_id: UUID | None
    responsible_user_id: UUID | None
    description: str | None
    is_active: bool
    schedule: list[ScheduleSlotOut] = Field(default_factory=list)
    participants_count: int = 0
    # денормализованная подпись федпроекта (frontend Activity.federal_project)
    federal_project: dict[str, str] | None = None
    version: int = Field(validation_alias=AliasChoices("xmin", "version"))
    created_at: datetime
    updated_at: datetime


class ParticipantsBody(_ApiModel):
    student_ids: list[UUID] = Field(min_length=1)
