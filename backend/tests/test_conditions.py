"""Юнит-тесты JSON-DSL guard-условий (workflow-engine.md §3.3, Р-4)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.workflow.conditions import (
    evaluate_condition,
    validate_condition_syntax,
)

CONTEXT = {
    "workflow_type": "b2c",
    "client_kind": "company",
    "university_id": None,
    "amount": Decimal("150000.00"),
    "interaction_type_code": "corporate_training",
    "responsible_user_id": "9e7cf5a2-0000-0000-0000-000000000001",
}


class TestLeafOperators:
    def test_eq_neq(self) -> None:
        assert evaluate_condition({"field": "client_kind", "op": "eq", "value": "company"}, CONTEXT)
        assert not evaluate_condition(
            {"field": "client_kind", "op": "eq", "value": "person"}, CONTEXT
        )
        assert evaluate_condition(
            {"field": "client_kind", "op": "neq", "value": "person"}, CONTEXT
        )

    def test_numeric_comparison_coerces_decimal(self) -> None:
        assert evaluate_condition({"field": "amount", "op": "gte", "value": 100000}, CONTEXT)
        assert evaluate_condition(
            {"field": "amount", "op": "lt", "value": "200000.50"}, CONTEXT
        )
        assert not evaluate_condition({"field": "amount", "op": "gt", "value": 150000}, CONTEXT)

    def test_in_not_in(self) -> None:
        node = {"field": "interaction_type_code", "op": "in",
                "value": ["corporate_training", "event"]}
        assert evaluate_condition(node, CONTEXT)
        assert not evaluate_condition(
            {"field": "interaction_type_code", "op": "not_in", "value": ["corporate_training"]},
            CONTEXT,
        )

    def test_is_set(self) -> None:
        assert evaluate_condition({"field": "amount", "op": "is_set"}, CONTEXT)
        assert not evaluate_condition({"field": "university_id", "op": "is_set"}, CONTEXT)

    def test_null_value_never_matches_comparisons(self) -> None:
        assert not evaluate_condition(
            {"field": "university_id", "op": "eq", "value": "x"}, CONTEXT
        )


class TestNesting:
    def test_contract_example_all_any(self) -> None:
        # пример из workflow-engine.md §3.3
        condition = {
            "all": [
                {"field": "client_kind", "op": "eq", "value": "company"},
                {
                    "any": [
                        {"field": "amount", "op": "gte", "value": 100000},
                        {"field": "interaction_type_code", "op": "eq",
                         "value": "corporate_training"},
                    ]
                },
            ]
        }
        assert evaluate_condition(condition, CONTEXT)
        assert not evaluate_condition(
            condition, {**CONTEXT, "client_kind": "person"}
        )

    def test_empty_condition_always_true(self) -> None:
        assert evaluate_condition(None, CONTEXT)
        assert evaluate_condition({}, CONTEXT)


class TestRobustness:
    """Неизвестное поле / несравнимые типы → условие ложно, не исключение."""

    def test_unknown_field_false(self) -> None:
        assert not evaluate_condition({"field": "bogus", "op": "eq", "value": 1}, CONTEXT)

    def test_unknown_op_false(self) -> None:
        assert not evaluate_condition(
            {"field": "amount", "op": "matches", "value": 1}, CONTEXT
        )

    def test_incomparable_types_false(self) -> None:
        assert not evaluate_condition(
            {"field": "amount", "op": "gte", "value": "не число"}, CONTEXT
        )

    def test_in_requires_list(self) -> None:
        assert not evaluate_condition(
            {"field": "client_kind", "op": "in", "value": "company"}, CONTEXT
        )


class TestSyntaxValidation:
    def test_valid_syntax(self) -> None:
        assert validate_condition_syntax(None)
        assert validate_condition_syntax(
            {"all": [{"field": "amount", "op": "is_set"}]}
        )

    def test_invalid_syntax(self) -> None:
        assert not validate_condition_syntax({"field": "bogus", "op": "eq", "value": 1})
        assert not validate_condition_syntax({"all": "не список"})
        assert not validate_condition_syntax({"field": "amount", "op": "eq"})  # нет value
        assert not validate_condition_syntax(
            {"field": "amount", "op": "in", "value": "строка"}
        )
        assert not validate_condition_syntax({"field": "amount"})
        assert not validate_condition_syntax([])
