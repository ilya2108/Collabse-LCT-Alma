"""Граф workflow в памяти: применение пакета операций и валидации V-1..V-8.

workflow-engine.md §6: операции применяются к копии живой схемы, целевой граф
проверяется валидациями (400 `workflow_invalid_schema` со списком кодов);
миграция заявок удаляемых этапов проверяется V-8. Модуль чистый (без БД) —
инварианты покрываются юнит-тестами.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from app.modules.workflow.conditions import validate_condition_syntax
from app.modules.workflow.schemas import (
    TRANSITION_ROLES,
    AddStatusOp,
    AddTransitionOp,
    DeleteStatusOp,
    DeleteTransitionOp,
    Operation,
    RenameStatusOp,
    UpdateStatusOp,
    UpdateTransitionOp,
)

# --- коды ошибок валидаций (workflow-engine.md §6.7) --------------------------------------

E_INITIAL_COUNT = "E_INITIAL_COUNT"
E_NO_TERMINAL = "E_NO_TERMINAL"
E_UNREACHABLE_STAGE = "E_UNREACHABLE_STAGE"
E_NO_PATH_TO_TERMINAL = "E_NO_PATH_TO_TERMINAL"
E_BAD_TRANSITION = "E_BAD_TRANSITION"
E_TERMINAL_OUTGOING = "E_TERMINAL_OUTGOING"
E_STAGE_DUP = "E_STAGE_DUP"
E_BAD_CONDITION = "E_BAD_CONDITION"
E_UNKNOWN_ROLE = "E_UNKNOWN_ROLE"
E_MAPPING_REQUIRED = "E_MAPPING_REQUIRED"
E_MAPPING_INVALID = "E_MAPPING_INVALID"
E_CONFIRM_MISMATCH = "E_CONFIRM_MISMATCH"


class OperationConflict(Exception):
    """Операция ссылается на несуществующий/изменённый объект схемы → 409 conflict
    (двое правили схему одновременно, автор пересоздаёт пакет — §6.6)."""


class OperationUnprocessable(Exception):
    """Семантически недопустимая операция (удаление initial и т.п.) → 422."""


class ConfirmNameMismatch(Exception):
    """`confirm_name` не совпал с актуальным названием этапа → 422 (E_CONFIRM_MISMATCH)."""

    def __init__(self, message: str, expected_name: str) -> None:
        super().__init__(message)
        self.expected_name = expected_name


# --- модель графа -------------------------------------------------------------------------


@dataclass
class GraphStage:
    id: uuid.UUID
    code: str
    name: str
    sort_order: int = 0
    is_initial: bool = False
    is_terminal: bool = False
    terminal_outcome: str | None = None
    stuck_threshold_days: int | None = None
    triggers_lms_handover: bool = False
    color: str | None = None
    description: str | None = None
    ui_position: dict[str, Any] | None = None
    is_new: bool = False


@dataclass
class GraphTransition:
    id: uuid.UUID
    from_stage_id: uuid.UUID
    to_stage_id: uuid.UUID
    name: str
    is_return: bool = False
    requires_comment: bool = False
    allowed_roles: list[str] = field(default_factory=list)
    condition: dict[str, Any] | None = None
    sort_order: int = 0
    is_new: bool = False


@dataclass
class WorkflowGraph:
    workflow_id: uuid.UUID
    stages: dict[uuid.UUID, GraphStage] = field(default_factory=dict)
    transitions: dict[uuid.UUID, GraphTransition] = field(default_factory=dict)

    def clone(self) -> WorkflowGraph:
        return WorkflowGraph(
            workflow_id=self.workflow_id,
            stages={k: replace(v) for k, v in self.stages.items()},
            transitions={k: replace(v) for k, v in self.transitions.items()},
        )

    def stage_by_code(self, code: str) -> GraphStage | None:
        lowered = code.lower()
        for stage in self.stages.values():
            if stage.code.lower() == lowered:
                return stage
        return None

    def outgoing(self, stage_id: uuid.UUID) -> list[GraphTransition]:
        return [t for t in self.transitions.values() if t.from_stage_id == stage_id]

    def incoming(self, stage_id: uuid.UUID) -> list[GraphTransition]:
        return [t for t in self.transitions.values() if t.to_stage_id == stage_id]

    def neighbors(self, stage_id: uuid.UUID) -> set[uuid.UUID]:
        """Соседи по ребру в ЛЮБОМ направлении (для правила миграции V-8)."""
        result: set[uuid.UUID] = set()
        for t in self.transitions.values():
            if t.from_stage_id == stage_id:
                result.add(t.to_stage_id)
            elif t.to_stage_id == stage_id:
                result.add(t.from_stage_id)
        return result

    @property
    def initial_stages(self) -> list[GraphStage]:
        return [s for s in self.stages.values() if s.is_initial]

    @property
    def terminal_stages(self) -> list[GraphStage]:
        return [s for s in self.stages.values() if s.is_terminal]


# --- план применения (исполняется сервисом поверх ORM) ------------------------------------


@dataclass
class DeleteStagePlanItem:
    stage_id: uuid.UUID
    stage_name: str  # живое имя на момент формирования (для истории/отчёта)
    migrate_to_status_id: uuid.UUID | None
    migrate_to_name: str | None
    deleted_transition_ids: list[uuid.UUID]


@dataclass
class ApplyPlan:
    """Резолюция пакета: новые объекты получают UUID здесь, ORM использует их же."""

    new_stages: list[GraphStage] = field(default_factory=list)
    new_transitions: list[GraphTransition] = field(default_factory=list)
    stage_patches: list[tuple[uuid.UUID, dict[str, Any]]] = field(default_factory=list)
    renames: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)  # id, old, new
    transition_patches: list[tuple[uuid.UUID, dict[str, Any]]] = field(default_factory=list)
    deleted_transition_ids: list[uuid.UUID] = field(default_factory=list)
    deleted_stages: list[DeleteStagePlanItem] = field(default_factory=list)


def _require_stage(graph: WorkflowGraph, stage_id: uuid.UUID) -> GraphStage:
    stage = graph.stages.get(stage_id)
    if stage is None:
        raise OperationConflict(
            "Этап, указанный в операции, уже изменён или удалён — пересоздайте пакет"
        )
    return stage


def _require_transition(graph: WorkflowGraph, transition_id: uuid.UUID) -> GraphTransition:
    transition = graph.transitions.get(transition_id)
    if transition is None:
        raise OperationConflict(
            "Переход, указанный в операции, уже изменён или удалён — пересоздайте пакет"
        )
    return transition


def _resolve_endpoint(
    graph: WorkflowGraph, *, stage_id: uuid.UUID | None, code: str | None, side: str
) -> GraphStage:
    if stage_id is not None:
        return _require_stage(graph, stage_id)
    stage = graph.stage_by_code(code or "")
    if stage is None:
        raise OperationConflict(
            f"Этап с кодом «{code}» не найден в схеме ({side}) — пересоздайте пакет"
        )
    return stage


def apply_operations(
    live: WorkflowGraph, operations: list[Operation]
) -> tuple[WorkflowGraph, ApplyPlan]:
    """Применяет пакет к копии живой схемы; возвращает целевой граф и план для ORM.

    Ошибки ссылок → OperationConflict (409); структурные запреты → OperationUnprocessable
    (422). Валидации целевого графа (V-1..V-7) — отдельно, `validate_graph`.
    """
    target = live.clone()
    plan = ApplyPlan()

    for operation in operations:
        if isinstance(operation, AddStatusOp):
            spec = operation.status
            if target.stage_by_code(spec.code) is not None:
                raise OperationUnprocessable(f"Этап с кодом «{spec.code}» уже существует")
            stage = GraphStage(
                id=uuid.uuid4(),
                code=spec.code,
                name=spec.name,
                sort_order=spec.position,
                is_initial=False,
                is_terminal=spec.is_terminal,
                terminal_outcome=spec.terminal_outcome,
                stuck_threshold_days=spec.stuck_threshold_days,
                triggers_lms_handover=spec.triggers_lms_handover,
                color=spec.color,
                description=spec.description,
                ui_position=spec.ui_position,
                is_new=True,
            )
            target.stages[stage.id] = stage
            plan.new_stages.append(stage)
        elif isinstance(operation, UpdateStatusOp):
            stage = _require_stage(target, operation.status_id)
            patch: dict[str, Any] = {}
            p = operation.patch
            if p.color is not None:
                stage.color = p.color
                patch["color"] = p.color
            if p.description is not None:
                stage.description = p.description
                patch["description"] = p.description
            if p.position is not None:
                stage.sort_order = p.position
                patch["sort_order"] = p.position
            if p.reset_stuck_threshold:
                stage.stuck_threshold_days = None
                patch["stuck_threshold_days"] = None
            elif p.stuck_threshold_days is not None:
                stage.stuck_threshold_days = p.stuck_threshold_days
                patch["stuck_threshold_days"] = p.stuck_threshold_days
            if p.triggers_lms_handover is not None:
                stage.triggers_lms_handover = p.triggers_lms_handover
                patch["triggers_lms_handover"] = p.triggers_lms_handover
            if p.ui_position is not None:
                stage.ui_position = p.ui_position
                patch["ui_position"] = p.ui_position
            if patch:
                plan.stage_patches.append((stage.id, patch))
        elif isinstance(operation, RenameStatusOp):
            stage = _require_stage(target, operation.status_id)
            plan.renames.append((stage.id, stage.name, operation.new_name))
            stage.name = operation.new_name
        elif isinstance(operation, DeleteStatusOp):
            stage = _require_stage(target, operation.status_id)
            if stage.is_initial:
                raise OperationUnprocessable(
                    f"Начальный этап «{stage.name}» удалить нельзя"
                )
            adjacent = [
                t.id
                for t in target.transitions.values()
                if operation.status_id in (t.from_stage_id, t.to_stage_id)
            ]
            for transition_id in adjacent:
                del target.transitions[transition_id]
            migrate_to = target.stages.get(operation.migrate_to_status_id) \
                if operation.migrate_to_status_id else None
            plan.deleted_stages.append(
                DeleteStagePlanItem(
                    stage_id=stage.id,
                    stage_name=stage.name,
                    migrate_to_status_id=operation.migrate_to_status_id,
                    migrate_to_name=migrate_to.name if migrate_to else None,
                    deleted_transition_ids=adjacent,
                )
            )
            del target.stages[stage.id]
        elif isinstance(operation, AddTransitionOp):
            spec = operation.transition
            from_stage = _resolve_endpoint(
                target, stage_id=spec.from_status_id, code=spec.from_code, side="from"
            )
            to_stage = _resolve_endpoint(
                target, stage_id=spec.to_status_id, code=spec.to_code, side="to"
            )
            transition = GraphTransition(
                id=uuid.uuid4(),
                from_stage_id=from_stage.id,
                to_stage_id=to_stage.id,
                name=spec.name,
                is_return=spec.kind == "return",
                requires_comment=spec.effective_requires_comment,
                allowed_roles=list(spec.allowed_roles),
                condition=spec.condition,
                sort_order=spec.position,
                is_new=True,
            )
            target.transitions[transition.id] = transition
            plan.new_transitions.append(transition)
        elif isinstance(operation, UpdateTransitionOp):
            transition = _require_transition(target, operation.transition_id)
            patch = {}
            p = operation.patch
            if p.name is not None:
                transition.name = p.name
                patch["name"] = p.name
            if p.kind is not None:
                transition.is_return = p.kind == "return"
                patch["is_return"] = transition.is_return
            if p.requires_comment is not None:
                transition.requires_comment = p.requires_comment
                patch["requires_comment"] = p.requires_comment
            if transition.is_return and not transition.requires_comment:
                # возврат всегда с комментарием (CHECK в БД) — форсируем
                transition.requires_comment = True
                patch["requires_comment"] = True
            if p.allowed_roles is not None:
                transition.allowed_roles = list(p.allowed_roles)
                patch["allowed_roles"] = list(p.allowed_roles)
            if p.reset_condition:
                transition.condition = None
                patch["condition"] = None
            elif p.condition is not None:
                transition.condition = p.condition
                patch["condition"] = p.condition
            if p.position is not None:
                transition.sort_order = p.position
                patch["sort_order"] = p.position
            if patch:
                plan.transition_patches.append((transition.id, patch))
        elif isinstance(operation, DeleteTransitionOp):
            transition = _require_transition(target, operation.transition_id)
            del target.transitions[transition.id]
            plan.deleted_transition_ids.append(transition.id)
        else:  # pragma: no cover — union закрыт discriminator'ом
            raise OperationUnprocessable(f"Неизвестная операция: {operation!r}")

    return target, plan


def verify_confirm_names(live: WorkflowGraph, operations: list[Operation]) -> None:
    """Посимвольная сверка `confirm_name` опасных операций с АКТУАЛЬНЫМ именем этапа.

    Вызывается при создании пакета и повторно в транзакции применения («защита
    от дурака», два рубежа — workflow-engine.md §6.2).
    """
    for operation in operations:
        if isinstance(operation, (RenameStatusOp, DeleteStatusOp)):
            stage = _require_stage(live, operation.status_id)
            if operation.confirm_name != stage.name:
                raise ConfirmNameMismatch(
                    f"Подтверждение не совпало: введите точное название «{stage.name}»",
                    expected_name=stage.name,
                )


# --- валидации целевого графа V-1..V-7 ----------------------------------------------------


def _reachable_from_initial(graph: WorkflowGraph) -> set[uuid.UUID]:
    initials = graph.initial_stages
    if not initials:
        return set()
    seen: set[uuid.UUID] = set()
    queue = deque(s.id for s in initials)
    seen.update(s.id for s in initials)
    forward: dict[uuid.UUID, list[uuid.UUID]] = {}
    for t in graph.transitions.values():
        forward.setdefault(t.from_stage_id, []).append(t.to_stage_id)
    while queue:
        current = queue.popleft()
        for nxt in forward.get(current, ()):  # рёбра только по живым этапам
            if nxt in graph.stages and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _reaching_terminal(graph: WorkflowGraph) -> set[uuid.UUID]:
    seen = {s.id for s in graph.terminal_stages}
    queue = deque(seen)
    backward: dict[uuid.UUID, list[uuid.UUID]] = {}
    for t in graph.transitions.values():
        backward.setdefault(t.to_stage_id, []).append(t.from_stage_id)
    while queue:
        current = queue.popleft()
        for prev in backward.get(current, ()):
            if prev in graph.stages and prev not in seen:
                seen.add(prev)
                queue.append(prev)
    return seen


def validate_graph(graph: WorkflowGraph) -> list[dict[str, Any]]:
    """V-1..V-7; пустой список — схема валидна. Элемент: {code, message, status_id?}."""
    errors: list[dict[str, Any]] = []

    initials = graph.initial_stages
    if len(initials) != 1:
        errors.append(
            {
                "code": E_INITIAL_COUNT,
                "message": (
                    f"В схеме должен быть ровно один начальный этап (сейчас {len(initials)})"
                ),
            }
        )
    if not graph.terminal_stages:
        errors.append(
            {"code": E_NO_TERMINAL, "message": "В схеме нет ни одного терминального этапа"}
        )

    # V-5: петли и дубли рёбер
    seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for t in graph.transitions.values():
        if t.from_stage_id == t.to_stage_id:
            errors.append(
                {
                    "code": E_BAD_TRANSITION,
                    "message": f"Переход «{t.name}» замыкается сам на себя",
                }
            )
        pair = (t.from_stage_id, t.to_stage_id)
        if pair in seen_pairs:
            errors.append(
                {
                    "code": E_BAD_TRANSITION,
                    "message": f"Дублирующийся переход «{t.name}» между теми же этапами",
                }
            )
        seen_pairs.add(pair)
        if t.from_stage_id not in graph.stages or t.to_stage_id not in graph.stages:
            errors.append(
                {
                    "code": E_BAD_TRANSITION,
                    "message": f"Переход «{t.name}» ссылается на несуществующий этап",
                }
            )
        # V-7: роли и условие
        unknown_roles = set(t.allowed_roles) - set(TRANSITION_ROLES)
        if unknown_roles:
            errors.append(
                {
                    "code": E_UNKNOWN_ROLE,
                    "message": (
                        f"Переход «{t.name}»: недопустимые роли {sorted(unknown_roles)}; "
                        f"допустимы {list(TRANSITION_ROLES)}"
                    ),
                }
            )
        if not validate_condition_syntax(t.condition):
            errors.append(
                {
                    "code": E_BAD_CONDITION,
                    "message": f"Переход «{t.name}»: некорректное condition-условие",
                }
            )

    # V-6: у терминалов нет исходящих
    for stage in graph.terminal_stages:
        if graph.outgoing(stage.id):
            errors.append(
                {
                    "code": E_TERMINAL_OUTGOING,
                    "status_id": str(stage.id),
                    "message": (
                        f"Терминальный этап «{stage.name}» не может иметь исходящих переходов"
                    ),
                }
            )

    # V-7: уникальность code/name
    by_code: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for stage in graph.stages.values():
        by_code[stage.code.lower()] = by_code.get(stage.code.lower(), 0) + 1
        by_name[stage.name] = by_name.get(stage.name, 0) + 1
    for code, count in by_code.items():
        if count > 1:
            errors.append(
                {"code": E_STAGE_DUP, "message": f"Код этапа «{code}» используется {count} раза"}
            )
    for name, count in by_name.items():
        if count > 1:
            errors.append(
                {
                    "code": E_STAGE_DUP,
                    "message": f"Название этапа «{name}» используется {count} раза",
                }
            )

    # V-3 / V-4: достижимость (только при валидном initial, иначе BFS бессмыслен)
    if len(initials) == 1:
        reachable = _reachable_from_initial(graph)
        for stage in graph.stages.values():
            if stage.id not in reachable:
                errors.append(
                    {
                        "code": E_UNREACHABLE_STAGE,
                        "status_id": str(stage.id),
                        "message": f"Этап «{stage.name}» недостижим из начального",
                    }
                )
    if graph.terminal_stages:
        reaching = _reaching_terminal(graph)
        for stage in graph.stages.values():
            if not stage.is_terminal and stage.id not in reaching:
                errors.append(
                    {
                        "code": E_NO_PATH_TO_TERMINAL,
                        "status_id": str(stage.id),
                        "message": (
                            f"Из этапа «{stage.name}» недостижим ни один терминальный этап "
                            "(тупики запрещены)"
                        ),
                    }
                )
    return errors


# --- V-8: миграция заявок удаляемых этапов ------------------------------------------------


def default_migration_target(live: WorkflowGraph, stage_id: uuid.UUID) -> GraphStage | None:
    """Дефолт «лучше предыдущий»: сосед по ребру с максимальным sort_order меньше
    удаляемого; иначе — любой нетерминальный сосед (data-model.md §5.3 п.1)."""
    stage = live.stages.get(stage_id)
    if stage is None:
        return None
    candidates = [
        live.stages[n]
        for n in live.neighbors(stage_id)
        if n in live.stages and not live.stages[n].is_terminal
    ]
    previous = [c for c in candidates if c.sort_order < stage.sort_order]
    if previous:
        return max(previous, key=lambda s: s.sort_order)
    return min(candidates, key=lambda s: s.sort_order) if candidates else None


def validate_migration(
    live: WorkflowGraph,
    plan: ApplyPlan,
    open_deal_counts: dict[uuid.UUID, int],
) -> list[dict[str, Any]]:
    """V-8: цели переноса заявок удаляемых этапов (workflow-engine.md §6.3, §6.7)."""
    errors: list[dict[str, Any]] = []
    deleted_ids = {item.stage_id for item in plan.deleted_stages}
    for item in plan.deleted_stages:
        deals = open_deal_counts.get(item.stage_id, 0)
        target_id = item.migrate_to_status_id
        if target_id is None:
            if deals > 0:
                errors.append(
                    {
                        "code": E_MAPPING_REQUIRED,
                        "status_id": str(item.stage_id),
                        "message": (
                            f"Этап «{item.stage_name}» содержит {deals} заявок: укажите "
                            "migrate_to_status_id (по умолчанию — предыдущий соседний этап)"
                        ),
                    }
                )
            continue
        target = live.stages.get(target_id)
        problem: str | None = None
        if target is None:
            problem = "целевой этап не существует"
        elif target_id in deleted_ids:
            problem = "целевой этап удаляется этим же пакетом"
        elif target_id == item.stage_id:
            problem = "целевой этап совпадает с удаляемым"
        elif target.is_terminal:
            problem = "целевой этап терминальный"
        elif target_id not in live.neighbors(item.stage_id):
            problem = "целевой этап не связан ребром с удаляемым (не сосед)"
        if problem is not None:
            errors.append(
                {
                    "code": E_MAPPING_INVALID,
                    "status_id": str(item.stage_id),
                    "message": (
                        f"Перенос заявок этапа «{item.stage_name}» невозможен: {problem}"
                    ),
                }
            )
    return errors
