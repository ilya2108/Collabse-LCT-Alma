"""Единый слой фильтров «Аналитики» без БД: параметры, query-контракт, SQL всех секций.

Замечания к дашборду: мультивыбор КАМов, вузов, продуктов молча превращался в одно
значение (скалярные query), часть секций фильтры игнорировала, выгрузка с фильтром-списком
падала с 500. Здесь — нормализация `ReportParams` (списки, строки, один UUID для старых
вызовов), 400 вместо 500 на плохих значениях, повторяемые query-параметры роутера и
контракт «каждый запрос к `deal` каждой секции дашборда несёт `IN` по всем измерениям».
Поведение на настоящих данных — tests/db/test_reporting_dashboard_db.py.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.access import DealScope
from app.core.errors import ApiError, register_error_handlers
from app.modules.reporting import service
from app.modules.reporting.router import report_params
from app.modules.reporting.service import (
    MAX_FILTER_IDS,
    ReportParams,
    _closed_between_stmt,
    _closed_weekly_stmt,
    _deal_filters,
    _student_dims,
    _Window,
    demand_index,
    dynamics_period,
    kpi_period,
    previous_period,
    programs_demand_stmt,
    sort_programs_demand,
)

A, B, C = (uuid.UUID(f"00000000-0000-0000-0000-00000000000{n}") for n in (1, 2, 3))


def _sql(stmt: Any) -> tuple[str, list[Any]]:
    compiled = stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    return str(compiled), list(compiled.params.values())


def _details(exc: ApiError) -> list[dict[str, Any]]:
    return list(exc.details or [])


# --- 1. ReportParams: нормализация и проверка ---------------------------------------------


class TestReportParams:
    def test_scalar_uuid_still_accepted(self) -> None:
        # прежние вызовы (выгрузка, тесты) передают один UUID
        params = ReportParams(kam_id=A, university_id=B)
        assert params.kam_ids == (A,)
        assert params.university_ids == (B,)
        assert params.product_ids == () and params.program_ids == ()

    def test_lists_and_strings_normalized(self) -> None:
        params = ReportParams(kam_id=[str(B), A, str(A)], product_id=(C,), program_id=str(C))
        assert params.kam_ids == (A, B)  # без повторов, в стабильном порядке
        assert params.product_ids == (C,)
        assert params.program_ids == (C,)

    def test_empty_values_mean_no_filter(self) -> None:
        params = ReportParams(kam_id=None, university_id=[], product_id="", program_id=["", None])
        assert params.kam_ids == params.university_ids == ()
        assert params.product_ids == params.program_ids == ()

    def test_invalid_uuid_is_400_not_500(self) -> None:
        with pytest.raises(ApiError) as caught:
            ReportParams(university_id=["не-uuid"])
        assert caught.value.status_code == 400
        assert _details(caught.value)[0]["field"] == "university_id"
        assert _details(caught.value)[0]["code"] == "invalid_uuid"

    def test_too_many_ids(self) -> None:
        ids = [uuid.uuid4() for _ in range(MAX_FILTER_IDS + 1)]
        with pytest.raises(ApiError) as caught:
            ReportParams(kam_id=ids)
        assert caught.value.status_code == 400
        assert _details(caught.value)[0]["code"] == "too_many_values"
        assert len(ReportParams(kam_id=ids[:MAX_FILTER_IDS]).kam_ids) == MAX_FILTER_IDS

    def test_inverted_period(self) -> None:
        with pytest.raises(ApiError) as caught:
            ReportParams(date_from=date(2026, 10, 2), date_to=date(2026, 10, 1))
        assert _details(caught.value)[0]["code"] == "period_inverted"

    def test_as_json_is_order_independent(self) -> None:
        # ключ кэша не должен зависеть от порядка чипов в фильтре
        first = ReportParams(kam_id=[A, B]).as_json()
        second = ReportParams(kam_id=[B, A]).as_json()
        assert first == second
        assert first["kam_id"] == [str(A), str(B)]
        assert first["university_id"] == []
        assert "from" in first and "to" in first

    def test_from_filters_accepts_dashboard_payload(self) -> None:
        # ровно то, что шлёт «Экспорт → XLSX» дашборда: списки строк
        params = ReportParams.from_filters(
            {
                "workflow_type": "b2c",
                "from": "2026-09-01",
                "to": "2026-09-30",
                "kam_id": [str(A), str(B)],
                "university_id": [str(C)],
                "product_id": str(A),
                "federal_project": str(B),
                "threshold_days": "7",
            }
        )
        assert params.workflow_type == "b2c"
        assert params.date_from == date(2026, 9, 1) and params.date_to == date(2026, 9, 30)
        assert params.kam_ids == (A, B)
        assert params.university_ids == (C,)
        assert params.product_ids == (A,)
        assert params.federal_project_id == B
        assert params.threshold_days == 7

    def test_from_filters_collects_all_errors(self) -> None:
        with pytest.raises(ApiError) as caught:
            ReportParams.from_filters(
                {
                    "workflow_type": "b2x",
                    "from": "01.09.2026",
                    "kam_id": ["x"],
                    "threshold_days": "99",
                    "federal_project": [str(A), str(B)],
                }
            )
        assert caught.value.status_code == 400
        fields = {item["field"] for item in _details(caught.value)}
        assert fields == {"workflow_type", "from", "kam_id", "threshold_days", "federal_project"}

    def test_from_filters_empty(self) -> None:
        assert ReportParams.from_filters({}) == ReportParams()


# --- 2. Роутер: повторяемые query-параметры, совместимость, 400 ----------------------------


@pytest.fixture
def params_client() -> TestClient:
    application = FastAPI()
    register_error_handlers(application)

    @application.get("/params")
    def echo(params: Annotated[ReportParams, Depends(report_params)]) -> dict[str, Any]:
        return params.as_json()

    return TestClient(application)


class TestRouterParams:
    def test_repeated_params_become_lists(self, params_client: TestClient) -> None:
        response = params_client.get(
            "/params",
            params=[
                ("kam_id", str(A)),
                ("kam_id", str(B)),
                ("university_id", str(C)),
                ("product_id", str(A)),
                ("program_id", str(B)),
                ("program_id", str(C)),
            ],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kam_id"] == [str(A), str(B)]
        assert body["university_id"] == [str(C)]
        assert body["product_id"] == [str(A)]
        assert body["program_id"] == [str(B), str(C)]

    def test_single_value_is_backward_compatible(self, params_client: TestClient) -> None:
        response = params_client.get("/params", params={"kam_id": str(A), "workflow_type": "b2b"})
        assert response.status_code == 200
        assert response.json()["kam_id"] == [str(A)]
        assert response.json()["workflow_type"] == "b2b"

    def test_too_many_ids_is_400(self, params_client: TestClient) -> None:
        ids = [("university_id", str(uuid.uuid4())) for _ in range(MAX_FILTER_IDS + 1)]
        response = params_client.get("/params", params=ids)
        assert response.status_code == 400
        assert response.json()["error"]["details"][0]["code"] == "too_many_values"

    def test_bad_uuid_and_inverted_period_are_400(self, params_client: TestClient) -> None:
        assert params_client.get("/params", params={"kam_id": "nope"}).status_code == 400
        inverted = params_client.get("/params", params={"from": "2026-10-02", "to": "2026-10-01"})
        assert inverted.status_code == 400

    def test_openapi_declares_array_params(self) -> None:
        application = FastAPI()
        from app.modules.reporting.router import router

        application.include_router(router)
        spec = application.openapi()
        operation = spec["paths"]["/reports/dashboard"]["get"]
        by_name = {p["name"]: p for p in operation["parameters"]}
        for name in ("kam_id", "university_id", "product_id", "program_id"):
            schema = by_name[name]["schema"]
            variants = schema.get("anyOf", [schema])
            assert any(v.get("type") == "array" for v in variants), name
        assert "400" in operation["responses"]


# --- 3. SQL: IN по всем измерениям -------------------------------------------------------

TWO = ReportParams(
    workflow_type="b2b",
    date_from=date(2026, 9, 1),
    date_to=date(2026, 9, 30),
    kam_id=[A, B],
    university_id=[B, C],
    product_id=[A, C],
    program_id=[A, B],
)


def _assert_in(sql: str, values: list[Any], column: str, ids: tuple[uuid.UUID, ...]) -> None:
    assert f"{column} IN (" in sql, column
    for item in ids:
        assert item in values, (column, item)


class TestDealFilters:
    def test_cohort_filters_all_dimensions(self) -> None:
        sql, values = _sql(
            _deal_filters(
                service.select(service.func.count()).select_from(service.Deal),
                TWO,
                DealScope.unrestricted(),
            )
        )
        _assert_in(sql, values, "deal.responsible_user_id", TWO.kam_ids)
        _assert_in(sql, values, "deal.university_id", TWO.university_ids)
        _assert_in(sql, values, "deal.product_id", TWO.product_ids)
        _assert_in(sql, values, "deal.program_id", TWO.program_ids)
        assert "deal.pipeline = " in sql
        assert "deal.created_at >= " in sql and "deal.created_at < " in sql
        # «=» по id остался бы признаком прежнего скалярного фильтра
        assert "deal.responsible_user_id = " not in sql

    def test_period_bounds_are_msk_midnight(self) -> None:
        window = _Window.of(date(2026, 9, 1), date(2026, 9, 30))
        assert window.start is not None and window.end is not None
        assert window.start.isoformat() == "2026-09-01T00:00:00+03:00"
        assert window.end.isoformat() == "2026-10-01T00:00:00+03:00"
        assert _Window.of(None, None).is_open

    def test_closed_between_uses_dims(self) -> None:
        sql, values = _sql(
            _closed_between_stmt("won", TWO.date_from, TWO.date_to, TWO, DealScope.unrestricted())
        )
        _assert_in(sql, values, "deal.responsible_user_id", TWO.kam_ids)
        _assert_in(sql, values, "deal.university_id", TWO.university_ids)
        _assert_in(sql, values, "deal.product_id", TWO.product_ids)
        _assert_in(sql, values, "deal.program_id", TWO.program_ids)
        assert "deal_stage_history.kind != " in sql  # перевод схемы — не закрытие
        assert "updated_at" not in sql

    def test_weekly_closed_same_definition_as_kpi(self) -> None:
        window = _Window.of(TWO.date_from, TWO.date_to)
        weekly, values = _sql(_closed_weekly_stmt("won", window, TWO, DealScope.unrestricted()))
        kpi, _ = _sql(
            _closed_between_stmt("won", TWO.date_from, TWO.date_to, TWO, DealScope.unrestricted())
        )
        # один и тот же момент закрытия: последний вход в терминальный этап с исходом
        for fragment in (
            "max(deal_stage_history.created_at)",
            "workflow_stage.terminal_outcome = ",
            "deal_stage_history.kind != ",
            "deal.stage_entered_at",
        ):
            assert fragment in weekly and fragment in kpi, fragment
        assert "date_trunc('week', timezone('Europe/Moscow'" in weekly
        assert "won" in values
        _assert_in(weekly, values, "deal.university_id", TWO.university_ids)

    def test_own_scope_and_filter_combine(self) -> None:
        own = uuid.uuid4()
        scope = DealScope(rules=(("b2b", own), ("b2c", own)))
        sql, values = _sql(_closed_between_stmt("lost", None, None, ReportParams(kam_id=A), scope))
        assert "deal.responsible_user_id = " in sql  # область «только свои»
        _assert_in(sql, values, "deal.responsible_user_id", (A,))


class TestProgramsAndStudentsSql:
    def test_programs_demand_three_indicators_and_filters(self) -> None:
        sql, values = _sql(programs_demand_stmt(TWO, DealScope.unrestricted()))
        # реестр программ — по продукту и программе
        _assert_in(sql, values, "program.product_id", TWO.product_ids)
        _assert_in(sql, values, "program.id", TWO.program_ids)
        # заявки и потоки — по КАМам и вузам; студенты — по вузам и вузам КАМов
        _assert_in(sql, values, "deal.responsible_user_id", TWO.kam_ids)
        _assert_in(sql, values, "deal.university_id", TWO.university_ids)
        _assert_in(sql, values, "student.university_id", TWO.university_ids)
        assert "university.kam_user_id IN (" in sql
        # обучающиеся — история статусов, потоки — история этапов (этап обучения)
        assert "student_status_history" in sql
        assert "workflow_stage.triggers_lms_handover" in sql
        assert "in_learning" in values and "studying" in values
        # без ручного топ-20 до сортировки: ни ORDER BY priority_rank, ни LIMIT в SQL
        assert "LIMIT" not in sql
        assert "ORDER BY" not in sql

    def test_programs_demand_without_period_is_snapshot(self) -> None:
        sql, _values = _sql(programs_demand_stmt(ReportParams(), DealScope.unrestricted()))
        assert "student_status_history" not in sql
        assert "deal_stage_history" not in sql

    def test_student_dims(self) -> None:
        stmt = _student_dims(service.select(service.func.count()).select_from(service.Student), TWO)
        sql, values = _sql(stmt)
        _assert_in(sql, values, "student.university_id", TWO.university_ids)
        _assert_in(sql, values, "student.program_id", TWO.program_ids)
        _assert_in(sql, values, "student.product_id", TWO.product_ids)
        assert "university.kam_user_id IN (" in sql


# --- 4. Секции дашборда целиком: каждый запрос к deal несёт IN -----------------------------


class _Result:
    def all(self) -> list[Any]:
        return []

    def scalar_one(self) -> int:
        return 0

    def scalar_one_or_none(self) -> None:
        return None

    def scalars(self) -> _Result:
        return self

    def __iter__(self):  # noqa: ANN204
        return iter([])


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.executed.append(stmt)
        return _Result()

    async def get(self, *_args: Any) -> None:
        return None


async def test_every_dashboard_query_on_deal_uses_in_filters() -> None:
    session = _RecordingSession()
    body = await service.report_dashboard(session, TWO, DealScope.unrestricted())  # type: ignore[arg-type]
    assert set(body) == {
        "kpi",
        "funnel",
        "dynamics",
        "kam_workload",
        "programs_demand",
        "talent_pool_funnel",
    }
    deal_queries = 0
    student_queries = 0
    for stmt in session.executed:
        sql, values = _sql(stmt)
        if "FROM deal" in sql or "JOIN deal" in sql:
            deal_queries += 1
            _assert_in(sql, values, "deal.responsible_user_id", TWO.kam_ids)
            _assert_in(sql, values, "deal.university_id", TWO.university_ids)
            if "program.id" not in sql:  # реестр программ фильтруется по самой программе
                _assert_in(sql, values, "deal.product_id", TWO.product_ids)
                _assert_in(sql, values, "deal.program_id", TWO.program_ids)
            assert "deal.pipeline = " in sql
        if "FROM student" in sql:
            student_queries += 1
            _assert_in(sql, values, "student.university_id", TWO.university_ids)
    # KPI: активные, зависшие, 4 закрытия; динамика: создано, завершено, отказ;
    # нагрузка: этапы, зависшие, вузы; программы; студенты (воронка без процесса в фейке)
    assert deal_queries >= 12
    assert student_queries >= 1


async def test_kam_workload_users_respect_kam_filter() -> None:
    session = _RecordingSession()
    await service.report_kam_workload(session, TWO, DealScope.unrestricted())  # type: ignore[arg-type]
    users_sql, values = _sql(session.executed[0])
    assert "FROM app_user" in users_sql
    _assert_in(users_sql, values, "app_user.id", TWO.kam_ids)


def test_cache_key_depends_on_ranking_flag_and_filter_values() -> None:
    scope = DealScope.unrestricted()
    params = ReportParams(kam_id=[A, B])
    on = service._cache_suffix("dashboard", params, scope, {"auto_ranking": True})
    off = service._cache_suffix("dashboard", params, scope, {"auto_ranking": False})
    assert on != off
    # прежняя ошибка: два КАМа и один КАМ давали разные данные под одним смыслом ключа
    single = service._cache_suffix("dashboard", ReportParams(kam_id=B), scope)
    assert single != service._cache_suffix("dashboard", params, scope)


# --- 5. Периоды и востребованность (чистые функции) ---------------------------------------


class TestPeriods:
    def test_kpi_default_is_last_30_days_inclusive(self) -> None:
        start, end, is_default = kpi_period(ReportParams(), date(2026, 10, 16))
        assert (start, end, is_default) == (date(2026, 9, 17), date(2026, 10, 16), True)
        assert previous_period(start, end) == (date(2026, 8, 18), date(2026, 9, 16))

    def test_kpi_explicit_period(self) -> None:
        params = ReportParams(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
        start, end, is_default = kpi_period(params, date(2026, 10, 16))
        assert (start, end, is_default) == (date(2026, 9, 1), date(2026, 9, 30), False)
        # та же длина, вплотную перед текущим
        assert previous_period(start, end) == (date(2026, 8, 2), date(2026, 8, 31))

    def test_dynamics_default_twelve_weeks(self) -> None:
        start, end = dynamics_period(ReportParams(), date(2026, 10, 16))
        assert end == date(2026, 10, 16) and (end - start).days == 84


def _row(name: str, requests: int, students: int, streams: int, rank: int) -> dict[str, Any]:
    return {
        "name": name,
        "requests_count": requests,
        "students_studying": students,
        "parallel_streams": streams,
        "priority_rank": rank,
        "computed_demand": None,
    }


class TestDemand:
    def test_index_averages_three_shares(self) -> None:
        rows = [_row("А", 10, 0, 1, 1), _row("Б", 5, 20, 2, 2), _row("В", 0, 0, 0, 3)]
        demand_index(rows)
        assert rows[0]["computed_demand"] == round(100 * (1 + 0 + 0.5) / 3, 1)
        assert rows[1]["computed_demand"] == round(100 * (0.5 + 1 + 1) / 3, 1)
        assert rows[2]["computed_demand"] == 0.0

    def test_index_all_zero(self) -> None:
        rows = [_row("А", 0, 0, 0, 1)]
        demand_index(rows)
        assert rows[0]["computed_demand"] == 0.0

    def test_sort_by_demand_not_manual_rank(self) -> None:
        # ручной приоритет — только при равенстве показателей
        rows = [_row("Топ-1 по приоритету", 1, 0, 0, 10), _row("Много заявок", 9, 0, 0, 1000)]
        rows.append(_row("Тоже 1 заявка", 1, 0, 0, 5))
        ordered = [row["name"] for row in sort_programs_demand(rows, by_index=False)]
        assert ordered == ["Много заявок", "Тоже 1 заявка", "Топ-1 по приоритету"]

    def test_sort_by_index_when_auto_ranking(self) -> None:
        rows = [_row("Заявки", 10, 0, 0, 1), _row("Всё понемногу", 6, 6, 6, 2)]
        demand_index(rows)
        ordered = [row["name"] for row in sort_programs_demand(rows, by_index=True)]
        assert ordered == ["Всё понемногу", "Заявки"]
