"""Сериализация схемы workflow в формат API (api-contract.md §5.1).

Терминологический мост (OVERVIEW.md §2.1): stage → status, sort_order → position,
is_return → kind: "return".
"""

from __future__ import annotations

from typing import Any

from app.models.workflow import Workflow, WorkflowStage, WorkflowTransition


def serialize_stage(stage: WorkflowStage) -> dict[str, Any]:
    return {
        "id": str(stage.id),
        "code": stage.code,
        "name": stage.name,
        "color": stage.color,
        "is_initial": stage.is_initial,
        "is_terminal": stage.is_terminal,
        "terminal_outcome": stage.terminal_outcome,
        "stuck_threshold_days": stage.stuck_threshold_days,
        "triggers_lms_handover": stage.triggers_lms_handover,
        "position": stage.sort_order,
        "ui_position": stage.ui_position,
    }


def serialize_transition(transition: WorkflowTransition) -> dict[str, Any]:
    return {
        "id": str(transition.id),
        "from_status_id": str(transition.from_stage_id),
        "to_status_id": str(transition.to_stage_id),
        "kind": "return" if transition.is_return else "forward",
        "name": transition.name,
        "requires_comment": transition.requires_comment,
        "allowed_roles": transition.allowed_roles,
        "condition": transition.condition,
    }


def serialize_workflow(
    workflow: Workflow,
    stages: list[WorkflowStage],
    transitions: list[WorkflowTransition],
) -> dict[str, Any]:
    return {
        "id": str(workflow.id),
        "type": workflow.code,
        "name": workflow.name,
        "description": workflow.description,
        "stuck_threshold_days": workflow.stuck_threshold_days,
        "updated_at": workflow.updated_at.isoformat(),
        "statuses": [serialize_stage(s) for s in sorted(stages, key=lambda s: s.sort_order)],
        "transitions": [serialize_transition(t) for t in transitions],
    }
