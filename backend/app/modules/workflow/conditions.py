"""JSON-DSL guard-условий переходов (workflow-engine.md §3.3, Р-4).

`condition` — декларативная ПОДСКАЗКА ветвления: `GET /requests/{id}/transitions`
аннотирует ею доступные переходы (available + причина), `POST` по ней НЕ отказывает.
Никакого eval: рекурсивный обход словаря с белым списком полей и операторов.
Неизвестное поле / несравнимые типы → условие ложно + warning в лог.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger("app.workflow.conditions")

# точечные пути по контексту заявки (workflow-engine.md §3.3)
CONDITION_FIELDS = frozenset(
    {
        "workflow_type",
        "client_kind",
        "university_id",
        "amount",
        "interaction_type_code",
        "responsible_user_id",
    }
)

CONDITION_OPS = frozenset(
    {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "is_set"}
)


def _coerce_pair(left: Any, right: Any) -> tuple[Any, Any] | None:
    """Приводит операнды к сравнимому виду; None — типы несравнимы."""
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        try:
            return Decimal(str(left)), Decimal(str(right))
        except (InvalidOperation, TypeError, ValueError):
            return None
    if isinstance(left, bool) or isinstance(right, bool):
        return (left, right) if isinstance(left, bool) and isinstance(right, bool) else None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left, right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            return float(left), float(right)
        except (TypeError, ValueError):
            return None
    return str(left), str(right)


def _eval_leaf(node: dict[str, Any], context: dict[str, Any]) -> bool:
    field = node.get("field")
    op = node.get("op")
    if field not in CONDITION_FIELDS or op not in CONDITION_OPS:
        logger.warning("condition: неизвестное поле/оператор %r/%r — условие ложно", field, op)
        return False
    actual = context.get(field)
    if op == "is_set":
        return actual is not None and actual != ""
    if actual is None:
        return False
    expected = node.get("value")
    if op in ("in", "not_in"):
        if not isinstance(expected, (list, tuple)):
            logger.warning("condition: значение %r для %s должно быть списком", expected, op)
            return False
        normalized = {str(item) for item in expected}
        contains = str(actual) in normalized
        return contains if op == "in" else not contains
    pair = _coerce_pair(actual, expected)
    if pair is None:
        logger.warning(
            "condition: несравнимые типы %r и %r (поле %s) — условие ложно",
            actual, expected, field,
        )
        return False
    left, right = pair
    if op == "eq":
        return left == right
    if op == "neq":
        return left != right
    try:
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
    except TypeError:
        logger.warning("condition: сравнение %r и %r невозможно", left, right)
        return False
    return False


def evaluate_condition(condition: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    """Вычисляет DSL над контекстом заявки. Пустое условие — переход подходит всегда."""
    if not condition:
        return True
    if not isinstance(condition, dict):
        logger.warning("condition: узел не является объектом: %r", condition)
        return False
    if "all" in condition:
        children = condition["all"]
        if not isinstance(children, list):
            return False
        return all(evaluate_condition(child, context) for child in children)
    if "any" in condition:
        children = condition["any"]
        if not isinstance(children, list):
            return False
        return any(evaluate_condition(child, context) for child in children)
    return _eval_leaf(condition, context)


def validate_condition_syntax(condition: Any) -> bool:
    """Синтаксическая проверка DSL для валидации схемы (V-7, E_BAD_CONDITION)."""
    if condition is None:
        return True
    if not isinstance(condition, dict) or not condition:
        return False
    if "all" in condition or "any" in condition:
        key = "all" if "all" in condition else "any"
        if set(condition.keys()) != {key} or not isinstance(condition[key], list):
            return False
        return all(validate_condition_syntax(child) for child in condition[key])
    if condition.get("field") not in CONDITION_FIELDS:
        return False
    op = condition.get("op")
    if op not in CONDITION_OPS:
        return False
    extra_keys = set(condition.keys()) - {"field", "op", "value"}
    if extra_keys:
        return False
    if op != "is_set" and "value" not in condition:
        return False
    if op in ("in", "not_in") and not isinstance(condition.get("value"), list):
        return False
    return True
