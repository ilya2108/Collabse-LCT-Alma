"""Юнит-тесты движка: валидации графа V-1..V-8, применение операций, миграция.

Инварианты — workflow-engine.md §6.7, §10 (I-1, I-2, I-7). Seed-графы обязаны
проходить тот же validate_graph, что и пользовательские пакеты. Роли переходов (V-7,
Р-F2-12): Pydantic проверяет только формат кода, строгий `check_operation_roles` — роли,
добавляемые пакетом, мягкий `validate_graph(known_roles=…)` — существование ролей.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.modules.workflow.graph import (
    E_BAD_CONDITION,
    E_BAD_TRANSITION,
    E_INITIAL_COUNT,
    E_MAPPING_INVALID,
    E_MAPPING_REQUIRED,
    E_NO_PATH_TO_TERMINAL,
    E_NO_TERMINAL,
    E_STAGE_DUP,
    E_TERMINAL_OUTGOING,
    E_UNKNOWN_ROLE,
    E_UNREACHABLE_STAGE,
    ConfirmNameMismatch,
    GraphStage,
    GraphTransition,
    OperationConflict,
    OperationUnprocessable,
    WorkflowGraph,
    apply_operations,
    check_operation_roles,
    default_migration_target,
    validate_graph,
    validate_migration,
    verify_confirm_names,
)
from app.modules.workflow.schemas import (
    DEFAULT_TRANSITION_ROLES,
    MAX_ALLOWED_ROLES,
    Operation,
)

SEEDS_DIR = Path(__file__).resolve().parents[1] / "app" / "modules" / "workflow" / "seeds"

_ops_adapter: TypeAdapter[list[Operation]] = TypeAdapter(list[Operation])


def parse_ops(raw: list[dict[str, Any]]) -> list[Operation]:
    return _ops_adapter.validate_python(raw)


def graph_from_seed(code: str) -> WorkflowGraph:
    spec = json.loads((SEEDS_DIR / f"{code}.json").read_text(encoding="utf-8"))
    graph = WorkflowGraph(workflow_id=uuid.uuid4())
    by_code: dict[str, uuid.UUID] = {}
    for stage_spec in spec["stages"]:
        stage = GraphStage(
            id=uuid.uuid4(),
            code=stage_spec["code"],
            name=stage_spec["name"],
            sort_order=stage_spec["sort_order"],
            is_initial=stage_spec["is_initial"],
            is_terminal=stage_spec["is_terminal"],
            terminal_outcome=stage_spec["terminal_outcome"],
            stuck_threshold_days=stage_spec["stuck_threshold_days"],
            triggers_lms_handover=stage_spec["triggers_lms_handover"],
        )
        graph.stages[stage.id] = stage
        by_code[stage.code] = stage.id
    for tr_spec in spec["transitions"]:
        transition = GraphTransition(
            id=uuid.uuid4(),
            from_stage_id=by_code[tr_spec["from"]],
            to_stage_id=by_code[tr_spec["to"]],
            name=tr_spec["name"],
            is_return=tr_spec["is_return"],
            requires_comment=tr_spec["requires_comment"],
            allowed_roles=list(tr_spec["allowed_roles"]),
            condition=tr_spec["condition"],
            sort_order=tr_spec["sort_order"],
        )
        graph.transitions[transition.id] = transition
    return graph


def codes(errors: list[dict[str, Any]]) -> set[str]:
    return {e["code"] for e in errors}


def stage_id(graph: WorkflowGraph, code: str) -> uuid.UUID:
    stage = graph.stage_by_code(code)
    assert stage is not None, f"нет этапа {code}"
    return stage.id


# --- seed-графы валидны (инвариант I-1) ---------------------------------------------------


@pytest.mark.parametrize("workflow_code", ["b2b", "b2c"])
def test_seed_graph_passes_validation(workflow_code: str) -> None:
    graph = graph_from_seed(workflow_code)
    assert validate_graph(graph) == []


# --- V-1, V-2 ------------------------------------------------------------------------------


def test_v1_two_initials_rejected() -> None:
    graph = graph_from_seed("b2b")
    graph.stages[stage_id(graph, "negotiation")].is_initial = True
    assert E_INITIAL_COUNT in codes(validate_graph(graph))


def test_v2_no_terminal_rejected() -> None:
    graph = graph_from_seed("b2b")
    for stage in graph.stages.values():
        stage.is_terminal = False
    assert E_NO_TERMINAL in codes(validate_graph(graph))


# --- V-3, V-4: достижимость ----------------------------------------------------------------


def test_v3_added_stage_without_incoming_is_unreachable() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {"op": "add_status", "status": {"code": "pilot", "name": "Пилот", "position": 3}},
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "pilot",
                    "to_code": "rejected",
                    "name": "Отказ",
                    "requires_comment": True,
                },
            },
        ]
    )
    target, _plan = apply_operations(graph, ops)
    assert E_UNREACHABLE_STAGE in codes(validate_graph(target))


def test_v4_dead_end_stage_rejected() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {"op": "add_status", "status": {"code": "pilot", "name": "Пилот", "position": 3}},
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "negotiation",
                    "to_code": "pilot",
                    "name": "Запустить пилот",
                },
            },
        ]
    )
    target, _plan = apply_operations(graph, ops)
    assert E_NO_PATH_TO_TERMINAL in codes(validate_graph(target))


def test_valid_branch_addition_passes() -> None:
    """Демо-сценарий защиты: новая ветка = этап + вход + выход к терминалу."""
    graph = graph_from_seed("b2c")
    ops = parse_ops(
        [
            {
                "op": "add_status",
                "status": {"code": "corp", "name": "Корпоративное обучение", "position": 2},
            },
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "consultation",
                    "to_code": "corp",
                    "name": "Корпоративный сценарий",
                },
            },
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "corp",
                    "to_code": "proposal",
                    "name": "Сформировать предложение",
                },
            },
        ]
    )
    target, plan = apply_operations(graph, ops)
    assert validate_graph(target) == []
    assert len(plan.new_stages) == 1 and len(plan.new_transitions) == 2


# --- V-5, V-6, V-7 -------------------------------------------------------------------------


def test_v5_duplicate_edge_rejected() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {
                "op": "add_transition",
                "transition": {"from_code": "new", "to_code": "first_contact", "name": "Дубль"},
            }
        ]
    )
    target, _plan = apply_operations(graph, ops)
    assert E_BAD_TRANSITION in codes(validate_graph(target))


def test_v5_self_loop_rejected() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {
                "op": "add_transition",
                "transition": {"from_code": "new", "to_code": "new", "name": "Петля"},
            }
        ]
    )
    target, _plan = apply_operations(graph, ops)
    assert E_BAD_TRANSITION in codes(validate_graph(target))


def test_v6_terminal_outgoing_rejected() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {
                "op": "add_transition",
                "transition": {"from_code": "completed", "to_code": "new", "name": "Назад"},
            }
        ]
    )
    target, _plan = apply_operations(graph, ops)
    assert E_TERMINAL_OUTGOING in codes(validate_graph(target))


def test_v7_duplicate_name_rejected() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {"op": "add_status", "status": {"code": "new2", "name": "Новая", "position": 9}},
            {
                "op": "add_transition",
                "transition": {"from_code": "negotiation", "to_code": "new2", "name": "Вбок"},
            },
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "new2",
                    "to_code": "rejected",
                    "name": "Отказ",
                    "requires_comment": True,
                },
            },
        ]
    )
    target, _plan = apply_operations(graph, ops)
    assert E_STAGE_DUP in codes(validate_graph(target))


def test_v7_duplicate_code_rejected_on_apply() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [{"op": "add_status", "status": {"code": "new", "name": "Ещё новая", "position": 9}}]
    )
    with pytest.raises(OperationUnprocessable):
        apply_operations(graph, ops)


def test_v7_unknown_role_and_bad_condition_on_live_rows() -> None:
    # битые данные могли попасть в БД мимо Pydantic — validate_graph ловит и их
    graph = graph_from_seed("b2b")
    transition = next(iter(graph.transitions.values()))
    transition.allowed_roles = ["superuser"]
    transition.condition = {"field": "nonexistent", "op": "eq", "value": 1}
    found = codes(validate_graph(graph))
    assert E_UNKNOWN_ROLE in found and E_BAD_CONDITION in found


# --- V-7: роли переходов из динамического каталога (Р-F2-12) --------------------------------

CAPABLE = frozenset({"admin", "head_kam", "kam"})  # requests:transition в процессе
KNOWN = CAPABLE | {"observer", "kam_b2c", "analyst"}  # все роли каталога


def _add_transition(roles: list[str], name: str = "Сразу в переговоры") -> dict[str, Any]:
    return {
        "op": "add_transition",
        "transition": {
            "from_code": "new",
            "to_code": "negotiation",
            "name": name,
            "allowed_roles": roles,
        },
    }


def test_default_transition_roles() -> None:
    assert DEFAULT_TRANSITION_ROLES == ("admin", "head_kam", "kam")


def test_pydantic_accepts_any_role_code_format() -> None:
    # существование роли и её право на переходы — забота V-7, а не схемы запроса
    (op,) = parse_ops([_add_transition(["observer", "kam_b2c"])])
    assert op.transition.allowed_roles == ["observer", "kam_b2c"]


@pytest.mark.parametrize("bad", ["Bad Role!", "KAM", "1kam", "k", "x" * 41, ""])
def test_pydantic_rejects_bad_role_code(bad: str) -> None:
    with pytest.raises(ValidationError):
        parse_ops([_add_transition([bad])])


def test_pydantic_limits_and_dedupes_allowed_roles() -> None:
    too_many = [f"role_{i:02d}" for i in range(MAX_ALLOWED_ROLES + 1)]
    with pytest.raises(ValidationError):
        parse_ops([_add_transition(too_many)])
    (op,) = parse_ops([_add_transition(["kam", "head_kam", "kam"])])
    assert op.transition.allowed_roles == ["kam", "head_kam"]
    (patch_op,) = parse_ops(
        [
            {
                "op": "update_transition",
                "transition_id": str(uuid.uuid4()),
                "patch": {"allowed_roles": ["kam", "kam"]},
            }
        ]
    )
    assert patch_op.patch.allowed_roles == ["kam"]


def test_strict_v7_rejects_role_without_transition_right() -> None:
    # прежний запрет «observer в allowed_roles» — теперь строгий V-7 по каталогу
    ops = parse_ops([_add_transition(["kam", "observer"])])
    errors = check_operation_roles(ops, CAPABLE)
    assert codes(errors) == {E_UNKNOWN_ROLE}
    (error,) = errors
    assert error["role"] == "observer"
    assert error["message"] == (
        "Переход «Сразу в переговоры»: роль «observer» не может выполнять переходы в этом процессе"
    )


def test_strict_v7_rejects_unknown_and_foreign_process_roles() -> None:
    # kam_b2c существует, но в B2B переходов не выполняет; ghost — нет в каталоге
    ops = parse_ops([_add_transition(["kam_b2c", "ghost", "ghost"])])
    errors = check_operation_roles(ops, CAPABLE)
    assert [e["role"] for e in errors] == ["kam_b2c", "ghost"]  # дубль — одна ошибка
    assert check_operation_roles(ops, CAPABLE | {"kam_b2c", "ghost"}) == []


def test_strict_v7_accepts_capable_and_empty_roles() -> None:
    ops = parse_ops([_add_transition(["kam", "head_kam"]), _add_transition([], "Без ролей")])
    assert check_operation_roles(ops, CAPABLE) == []


def test_strict_v7_update_checks_only_added_roles() -> None:
    graph = graph_from_seed("b2b")
    transition = next(iter(graph.transitions.values()))
    transition.allowed_roles = ["kam", "observer"]  # observer уже в живой строке
    patch = {
        "op": "update_transition",
        "transition_id": str(transition.id),
        "patch": {"allowed_roles": ["observer"]},  # сняли kam — правка не блокируется
    }
    assert check_operation_roles(parse_ops([patch]), CAPABLE, live=graph) == []
    patch["patch"] = {"allowed_roles": ["observer", "analyst"]}
    errors = check_operation_roles(parse_ops([patch]), CAPABLE, live=graph)
    assert [e["role"] for e in errors] == ["analyst"]
    assert transition.name in errors[0]["message"]
    # без живой схемы — весь новый список
    errors = check_operation_roles(parse_ops([patch]), CAPABLE)
    assert [e["role"] for e in errors] == ["observer", "analyst"]
    assert errors[0]["message"].startswith("Роль «observer»")


def test_strict_v7_ignores_other_operations() -> None:
    graph = graph_from_seed("b2b")
    transition = next(iter(graph.transitions.values()))
    ops = parse_ops(
        [
            {
                "op": "update_transition",
                "transition_id": str(transition.id),
                "patch": {"name": "Новое имя"},
            },
            {"op": "delete_transition", "transition_id": str(transition.id)},
        ]
    )
    assert check_operation_roles(ops, frozenset(), live=graph) == []


def test_soft_v7_live_rows_need_only_existing_roles() -> None:
    graph = graph_from_seed("b2b")
    transition = next(iter(graph.transitions.values()))
    # роль есть в каталоге, но переходы не выполняет (право сняли) — схема не блокируется
    transition.allowed_roles = ["kam", "observer", "kam_b2c"]
    assert validate_graph(graph, known_roles=KNOWN) == []
    # удалённая из каталога роль — E_UNKNOWN_ROLE с её кодом
    transition.allowed_roles = ["kam", "gone_role"]
    errors = [e for e in validate_graph(graph, known_roles=KNOWN) if e["code"] == E_UNKNOWN_ROLE]
    (error,) = errors
    assert error["roles"] == ["gone_role"]
    assert "«gone_role»" in error["message"] and transition.name in error["message"]


def test_soft_v7_default_known_roles_are_system_transition_roles() -> None:
    graph = graph_from_seed("b2b")
    transition = next(iter(graph.transitions.values()))
    transition.allowed_roles = ["observer"]
    assert E_UNKNOWN_ROLE in codes(validate_graph(graph))
    assert validate_graph(graph, known_roles=KNOWN) == []


# --- операции: конфликты, confirm_name, запреты --------------------------------------------


def test_unknown_status_id_is_conflict() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [
            {
                "op": "rename_status",
                "status_id": str(uuid.uuid4()),
                "new_name": "X",
                "confirm_name": "Y",
            }
        ]
    )
    with pytest.raises(OperationConflict):
        apply_operations(graph, ops)


def test_delete_initial_stage_forbidden() -> None:
    graph = graph_from_seed("b2b")
    ops = parse_ops(
        [{"op": "delete_status", "status_id": str(stage_id(graph, "new")), "confirm_name": "Новая"}]
    )
    with pytest.raises(OperationUnprocessable):
        apply_operations(graph, ops)


def test_confirm_name_checked_charwise() -> None:
    graph = graph_from_seed("b2b")
    sid = str(stage_id(graph, "first_contact"))
    ok_ops = parse_ops(
        [
            {
                "op": "rename_status",
                "status_id": sid,
                "new_name": "Первый контакт",
                "confirm_name": "Первичный контакт",
            }
        ]
    )
    verify_confirm_names(graph, ok_ops)  # не бросает
    bad_ops = parse_ops(
        [
            {
                "op": "rename_status",
                "status_id": sid,
                "new_name": "Первый контакт",
                "confirm_name": "первичный контакт",
            }
        ]  # регистр не совпал — посимвольная сверка
    )
    with pytest.raises(ConfirmNameMismatch) as excinfo:
        verify_confirm_names(graph, bad_ops)
    assert excinfo.value.expected_name == "Первичный контакт"


def test_rename_applies_and_keeps_stage_id() -> None:
    graph = graph_from_seed("b2b")
    sid = stage_id(graph, "first_contact")
    ops = parse_ops(
        [
            {
                "op": "rename_status",
                "status_id": str(sid),
                "new_name": "Первый контакт",
                "confirm_name": "Первичный контакт",
            }
        ]
    )
    target, plan = apply_operations(graph, ops)
    assert target.stages[sid].name == "Первый контакт"  # UUID этапа не меняется (§2.2)
    assert plan.renames == [(sid, "Первичный контакт", "Первый контакт")]
    assert validate_graph(target) == []


def test_delete_stage_removes_adjacent_transitions() -> None:
    graph = graph_from_seed("b2b")
    sid = stage_id(graph, "first_contact")
    ops = parse_ops(
        [
            {
                "op": "delete_status",
                "status_id": str(sid),
                "migrate_to_status_id": str(stage_id(graph, "new")),
                "confirm_name": "Первичный контакт",
            },
            # без замыкания new → negotiation этап new стал бы тупиком (V-4)
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "new",
                    "to_code": "negotiation",
                    "name": "Сразу в переговоры",
                },
            },
        ]
    )
    target, plan = apply_operations(graph, ops)
    assert sid not in target.stages
    assert all(sid not in (t.from_stage_id, t.to_stage_id) for t in target.transitions.values())
    assert len(plan.deleted_stages) == 1
    assert len(plan.deleted_stages[0].deleted_transition_ids) == 3  # new→fc, fc→neg, fc→rej
    assert validate_graph(target) == []


# --- V-8: миграция заявок ------------------------------------------------------------------


def _delete_package(graph: WorkflowGraph, migrate_to: uuid.UUID | None) -> list[Operation]:
    raw: list[dict[str, Any]] = [
        {
            "op": "delete_status",
            "status_id": str(stage_id(graph, "first_contact")),
            "confirm_name": "Первичный контакт",
        },
        {
            "op": "add_transition",
            "transition": {"from_code": "new", "to_code": "negotiation", "name": "Вперёд"},
        },
    ]
    if migrate_to is not None:
        raw[0]["migrate_to_status_id"] = str(migrate_to)
    return parse_ops(raw)


def test_v8_mapping_required_when_deals_exist() -> None:
    graph = graph_from_seed("b2b")
    sid = stage_id(graph, "first_contact")
    _target, plan = apply_operations(graph, _delete_package(graph, None))
    errors = validate_migration(graph, plan, {sid: 7})
    assert E_MAPPING_REQUIRED in codes(errors)


def test_v8_mapping_not_required_without_deals() -> None:
    graph = graph_from_seed("b2b")
    _target, plan = apply_operations(graph, _delete_package(graph, None))
    assert validate_migration(graph, plan, {}) == []


def test_v8_terminal_target_invalid() -> None:
    graph = graph_from_seed("b2b")
    sid = stage_id(graph, "first_contact")
    _target, plan = apply_operations(graph, _delete_package(graph, stage_id(graph, "rejected")))
    errors = validate_migration(graph, plan, {sid: 1})
    assert E_MAPPING_INVALID in codes(errors)


def test_v8_non_neighbor_target_invalid() -> None:
    graph = graph_from_seed("b2b")
    sid = stage_id(graph, "first_contact")
    # contract_signed не связан ребром с first_contact
    _target, plan = apply_operations(
        graph, _delete_package(graph, stage_id(graph, "contract_signed"))
    )
    errors = validate_migration(graph, plan, {sid: 1})
    assert E_MAPPING_INVALID in codes(errors)


def test_v8_previous_neighbor_target_valid() -> None:
    graph = graph_from_seed("b2b")
    sid = stage_id(graph, "first_contact")
    _target, plan = apply_operations(graph, _delete_package(graph, stage_id(graph, "new")))
    assert validate_migration(graph, plan, {sid: 7}) == []
    item = plan.deleted_stages[0]
    assert item.migrate_to_name == "Новая"


def test_v8_target_deleted_in_same_package_invalid() -> None:
    graph = graph_from_seed("b2b")
    fc = stage_id(graph, "first_contact")
    neg = stage_id(graph, "negotiation")
    ops = parse_ops(
        [
            {
                "op": "delete_status",
                "status_id": str(neg),
                "migrate_to_status_id": str(fc),
                "confirm_name": "Переговоры",
            },
            {
                "op": "delete_status",
                "status_id": str(fc),
                "migrate_to_status_id": str(neg),
                "confirm_name": "Первичный контакт",
            },
            {
                "op": "add_transition",
                "transition": {
                    "from_code": "new",
                    "to_code": "contract_prep",
                    "name": "Сразу к договору",
                },
            },
        ]
    )
    target, plan = apply_operations(graph, ops)
    errors = validate_migration(graph, plan, {fc: 1, neg: 1})
    assert E_MAPPING_INVALID in codes(errors)


# --- дефолт «лучше предыдущий» -------------------------------------------------------------


def test_default_migration_target_prefers_previous_neighbor() -> None:
    graph = graph_from_seed("b2b")
    target = default_migration_target(graph, stage_id(graph, "first_contact"))
    assert target is not None and target.code == "new"
    target = default_migration_target(graph, stage_id(graph, "contract_prep"))
    assert target is not None and target.code == "negotiation"


def test_default_migration_target_forward_fallback() -> None:
    graph = graph_from_seed("b2b")
    # у initial предыдущих соседей нет — берётся ближайший следующий нетерминальный
    target = default_migration_target(graph, stage_id(graph, "new"))
    assert target is not None and target.code == "first_contact"
