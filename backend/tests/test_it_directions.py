"""ИТ-направления (P1-1) без БД: правило «направление записи» в SQL, параметры отчётов,
синонимы импорта."""

from __future__ import annotations

import re
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.errors import ApiError
from app.models import Activity, Deal, Program, Student
from app.modules.crm.directions import direction_match, direction_names
from app.modules.import_export.mapping import (
    REQUEST_B2B_FIELDS,
    REQUEST_B2C_FIELDS,
    STUDENT_FIELDS,
    suggest_mapping,
)
from app.modules.reporting.interactions import COLUMNS_BY_KEY, DEFINITIONS, InteractionsParams
from app.modules.reporting.kam_activity import KamActivityParams, resolve_params
from app.modules.reporting.service import ReportParams


def _sql(stmt) -> str:  # noqa: ANN001
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestDirectionSql:
    @pytest.mark.parametrize(
        ("entity", "program", "product"),
        [
            (Deal, Deal.program_id, Deal.product_id),
            (Student, Student.program_id, Student.product_id),
            (Activity, Activity.program_id, None),
            (Program, Program.id, Program.product_id),
        ],
    )
    def test_correlated_without_cross_join(self, entity, program, product) -> None:  # noqa: ANN001
        """Вложенный подзапрос «продукт программы» коррелирует с внешней записью на любой
        глубине: внешняя таблица не попадает во FROM подзапросов (иначе — декартово
        произведение и «more than one row returned by a subquery»)."""
        table = entity.__table__.name
        stmt = select(entity.id, direction_names(program, product).label("d")).where(
            direction_match(program, product, [uuid.uuid4()])
        )
        sql = _sql(stmt)
        assert not re.search(rf", {table}\b(?! AS)", sql)  # нет «FROM x AS y, deal»
        assert len(re.findall(rf"FROM {table}\b(?! AS)", sql)) == 1  # только внешний FROM
        assert "string_agg" in sql and "ORDER BY" in sql

    def test_program_direction_has_priority(self) -> None:
        sql = _sql(
            select(Deal.id).where(direction_match(Deal.program_id, Deal.product_id, [uuid.uuid4()]))
        )
        # своё направление программы решает; продукт — только если у программы его нет
        assert "IS NULL AND (EXISTS" in sql
        assert "coalesce(deal.product_id" in sql


class TestReportParams:
    def test_dashboard_params_accept_direction_lists(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        params = ReportParams(direction_id=[b, a, str(a)])
        assert params.direction_ids == tuple(sorted({a, b}, key=str))
        assert params.as_json()["direction_id"] == sorted([str(a), str(b)])
        # без фильтра ключа нет: прежние ключи кэша и эхо параметров не меняются
        assert "direction_id" not in ReportParams().as_json()
        assert ReportParams.from_filters({"direction_id": str(a)}).direction_ids == (a,)
        with pytest.raises(ApiError):
            ReportParams(direction_id=[uuid.uuid4() for _ in range(51)])

    def test_kam_activity_params(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        params = KamActivityParams.from_filters({"direction_id": [str(a), str(b), str(a)]})
        resolved = resolve_params(params, today=date(2026, 10, 20))
        assert resolved.direction_ids == tuple(sorted({a, b}, key=str))
        assert resolved.as_json()["direction_id"] == sorted([str(a), str(b)])
        assert "direction_id" not in KamActivityParams().as_json()
        with pytest.raises(ApiError):
            KamActivityParams.from_filters({"direction": "не-uuid"})

    def test_interactions_params_inherit_direction(self) -> None:
        a = uuid.uuid4()
        params = InteractionsParams.from_filters({"direction_id": [str(a)]})
        assert params.base.direction_ids == (a,)
        assert params.as_json()["direction_id"] == [str(a)]

    def test_interactions_column_is_documented(self) -> None:
        assert COLUMNS_BY_KEY["it_direction"].default is True
        assert not any("появится" in text for text in DEFINITIONS)


class TestImportSynonyms:
    @pytest.mark.parametrize("spec", [REQUEST_B2B_FIELDS, REQUEST_B2C_FIELDS])
    def test_direction_header_maps_to_it_direction(self, spec) -> None:  # noqa: ANN001
        for header in ("Направление", "ИТ-направление", "IT-направление", "Направление обучения"):
            suggestion = suggest_mapping([header], spec)
            assert suggestion and suggestion[0]["target_field"] == "it_direction", header

    def test_product_no_longer_takes_direction(self) -> None:
        columns = ["Вуз", "Направление", "ИТ-продукт", "Программа"]
        mapped = {
            s["source_column"]: s["target_field"]
            for s in suggest_mapping(columns, REQUEST_B2B_FIELDS)
        }
        assert mapped == {0: "university", 1: "it_direction", 2: "product", 3: "program"}

    def test_students_keep_program_synonym(self) -> None:
        # у студентов «направление подготовки» — по-прежнему программа
        mapped = suggest_mapping(["Направление подготовки"], STUDENT_FIELDS)
        assert mapped[0]["target_field"] == "program"
