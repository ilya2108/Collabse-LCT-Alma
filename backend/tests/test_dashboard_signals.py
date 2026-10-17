"""Карточка «Сигналы» дашборда: чистые части без БД — правило «вузы без активности» на
дереве отчёта КАМов, перевод фильтров панели, SQL «без ответственного КАМа» и лицензий,
форма ответа для OpenAPI."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.core.access import static_catalog
from app.modules.reporting import kam_activity as ka
from app.modules.reporting import signals
from app.modules.reporting.kam_activity import ActivityStat, PersonRef, UniversityRef
from app.modules.reporting.schemas import DashboardSignalsOut
from app.modules.reporting.service import ReportParams
from app.modules.requests.service import unassigned_condition

MSK = ka.REPORT_TZ
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=MSK)
TODAY = NOW.date()
KAM1, KAM2 = uuid.uuid4(), uuid.uuid4()
U_TOUCHED, U_SILENT, U_OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PEOPLE = {
    KAM1: PersonRef(KAM1, "Петров Константин Андреевич", ("kam",)),
    KAM2: PersonRef(KAM2, "Иванова Мария Сергеевна", ("kam",)),
}


def _sql(clause: Any) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _tree() -> dict[str, Any]:
    params = ka.resolve_params(ka.KamActivityParams(), today=TODAY)
    bounds = ka.resolve_bounds(params, now=NOW)
    assigned = [
        UniversityRef(U_TOUCHED, "Томский политехнический университет", "ТПУ", None, KAM1),
        UniversityRef(U_SILENT, "Университет ИТМО", "ИТМО", None, KAM1),
        UniversityRef(U_OTHER, "Томский государственный университет", None, None, KAM2),
    ]
    # с вузом КАМа 1 в периоде работал КАМ 2 — вуз «тронут» (кто угодно из выборки)
    activities = [
        ActivityStat(KAM2, U_TOUCHED, "Томский политехнический университет", "ТПУ", None, 1, NOW),
        ActivityStat(KAM1, U_SILENT, "Университет ИТМО", "ИТМО", None, 0, NOW - timedelta(days=45)),
    ]
    return ka.assemble_tree([], activities, assigned, PEOPLE, bounds, include_requests=False)


class TestUntouchedRule:
    def test_refs_and_names_are_one_rule(self) -> None:
        tree = _tree()
        refs = ka.untouched_assigned_refs(tree)
        assert [ref["name"] for ref in refs] == ["ИТМО", "Томский государственный университет"]
        assert ka.untouched_assigned(tree) == [ref["name"] for ref in refs]
        silent, other = refs
        assert silent["university_id"] == str(U_SILENT) and silent["kam_id"] == str(KAM1)
        assert silent["kam"] == "Петров Константин Андреевич"
        # давнее мероприятие — последний контакт до периода, взаимодействием в периоде не стало
        assert silent["last_contact_at"] == (NOW - timedelta(days=45)).isoformat()
        assert other["last_contact_at"] is None and other["kam_id"] == str(KAM2)

    def test_period_is_kam_report_default(self) -> None:
        params = ka.resolve_params(signals.kam_params(ReportParams()), today=TODAY)
        assert (params.date_from, params.date_to) == (TODAY - timedelta(days=29), TODAY)


def test_panel_filters_map_to_kam_report() -> None:
    kam, uni, direction, product = (uuid.uuid4() for _ in range(4))
    params = ReportParams(
        workflow_type="b2b",
        date_from=date(2026, 9, 1),
        kam_id=[kam],
        university_id=str(uni),
        direction_id=[direction],
        product_id=[product],
    )
    mapped = signals.kam_params(params)
    # период панели не переносится: сигнал — всегда последние 30 дней
    assert (mapped.date_from, mapped.date_to) == (None, None)
    assert mapped.workflow_type == "b2b"
    assert (mapped.kam_ids, mapped.university_ids, mapped.direction_ids) == (
        (kam,),
        (uni,),
        (direction,),
    )


class TestUnassignedCondition:
    def test_kam_roles_per_process(self) -> None:
        sql = _sql(unassigned_condition())
        assert "deal.status = 'open'" in sql
        assert "deal.pipeline = 'b2b'" in sql and "deal.pipeline = 'b2c'" in sql
        # своя таблица пользователей — не коррелирует с app_user внешнего запроса
        assert "unassigned_owner.is_active IS true" in sql
        assert "unassigned_owner.roles && ARRAY['kam']" in sql
        for role in ("head_kam", "admin", "observer"):  # не КАМы: их заявки не распределены
            assert f"'{role}'" not in sql

    def test_process_without_kam_roles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        catalog = static_catalog()

        class OnlyB2b:
            def role_codes_with(
                self, permission: str, workflow_type: str | None = None
            ) -> tuple[str, ...]:
                if workflow_type != "b2b":
                    return ()
                return catalog.role_codes_with(permission, workflow_type)

        monkeypatch.setattr("app.modules.requests.service.get_catalog_snapshot", lambda: OnlyB2b())
        sql = _sql(unassigned_condition())
        # в процессе без ролей КАМа любая открытая заявка — без ответственного КАМа
        assert "OR deal.pipeline = 'b2c'" in sql


class TestLicenseSql:
    def test_horizon_and_exclusions(self) -> None:
        sql = _sql(signals.license_counts_stmt(ReportParams(), None, TODAY))
        horizon = TODAY + timedelta(days=signals.LICENSE_EXPIRING_DAYS)
        assert signals.LICENSE_EXPIRING_DAYS == 30
        assert f"contract_product.license_valid_to <= '{horizon.isoformat()}'" in sql
        assert f"contract_product.license_valid_to < '{TODAY.isoformat()}'" in sql
        assert "contract.status = 'active'" in sql
        assert "transfer_status IS DISTINCT FROM 'cancelled'" in sql
        assert "JOIN university" in sql  # только договоры с вузами (B2B)

    def test_panel_filters_and_own_scope(self) -> None:
        kam, uni, product, program = (uuid.uuid4() for _ in range(4))
        own = uuid.uuid4()
        params = ReportParams(
            kam_id=[kam], university_id=[uni], product_id=[product], program_id=[program]
        )
        sql = _sql(signals.license_items_stmt(params, own, TODAY, expired=False))
        assert f"university.kam_user_id IN ('{kam}')" in sql
        assert f"university.id IN ('{uni}')" in sql
        assert f"contract_product.product_id IN ('{product}')" in sql
        assert "SELECT program.product_id" in sql  # программа — через её продукт
        assert f"university.kam_user_id = '{own}'" in sql
        assert "LIMIT 50" in sql


def test_response_shape_matches_openapi_model() -> None:
    body = {
        "report": "signals",
        "generated_at": NOW.isoformat(),
        "params": ReportParams().as_json(),
        "as_of": NOW.isoformat(),
        "workflow_types": ["b2b", "b2c"],
        "stuck": {"value": 3, "by_workflow": {"b2b": 2, "b2c": 1}},
        "unassigned": {"value": 0, "by_workflow": {"b2b": 0, "b2c": 0}},
        "licenses": {
            "expiring": 1,
            "expired": 0,
            "horizon_days": 30,
            "items": [
                {
                    "contract_id": str(uuid.uuid4()),
                    "contract_number": "Д-2026/041",
                    "university_id": str(U_SILENT),
                    "university": "ИТМО",
                    "kam_id": str(KAM1),
                    "kam": "Петров Константин Андреевич",
                    "product_id": str(uuid.uuid4()),
                    "product": "ДПО: Data Science",
                    "valid_to": (TODAY + timedelta(days=8)).isoformat(),
                    "days_left": 8,
                    "state": "expiring",
                    "transfer_status": "transferred",
                }
            ],
            "truncated": False,
        },
        "untouched_universities": {
            "value": 2,
            "period": ka.resolve_bounds(
                ka.resolve_params(ka.KamActivityParams(), today=TODAY), now=NOW
            ).as_json(),
            "items": ka.untouched_assigned_refs(_tree()),
            "truncated": False,
        },
    }
    DashboardSignalsOut.model_validate(body)
    DashboardSignalsOut.model_validate({**body, "licenses": None, "untouched_universities": None})
