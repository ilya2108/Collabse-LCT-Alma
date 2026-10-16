"""Факты «Сводки ИИ»: allowlist, псевдонимы, минимизация данных, стабильный хеш."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

import orjson
import pytest

from app.modules.ai_summary import facts as f
from app.modules.reporting.sla import ATTENTION_RULE

from . import ai_summary_samples as samples

ALIAS_RE = re.compile(r"^КАМ-\d{2}$")
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [item for key, sub in value.items() for item in _strings(sub, f"{path}.{key}")]
    if isinstance(value, list):
        return [item for sub in value for item in _strings(sub, f"{path}[]")]
    return []


def _team() -> f.Facts:
    return f.build_team_facts(samples.team_summary(), samples.team_sla())


def _kam(*, team: bool = True) -> f.Facts:
    return f.build_kam_facts(samples.KAM_B, samples.kam_activity(), samples.kam_sla(team=team))


@pytest.mark.parametrize("build", [_team, _kam], ids=["team", "kam"])
def test_no_personal_or_free_text_leaks(build) -> None:  # noqa: ANN001
    dumped = orjson.dumps(build().facts).decode("utf-8")
    for needle in samples.DANGEROUS:
        assert needle not in dumped, needle
    # никаких id пользователей, заявок и вузов — только псевдонимы
    assert samples.KAM_A not in dumped and samples.KAM_B not in dumped
    assert "request_id" not in dumped and "kam_id" not in dumped
    assert "new_amount" not in dumped and "₽" not in dumped
    assert "as_of" not in dumped and "generated_at" not in dumped


@pytest.mark.parametrize("build", [_team, _kam], ids=["team", "kam"])
def test_strings_only_from_allowed_sources(build) -> None:  # noqa: ANN001
    stage_names = {"Переговоры", "Согласование договора", "Новая", "Без заявок"}
    buckets = {"1–3 дн.", "4–7 дн.", "8–14 дн.", "15–30 дн.", "больше 30 дн."}
    trend = {"21.09", "28.09", "05.10", "12.10"}
    constants = {"team", "kam", "snapshot", "period", "все", "B2B", "B2C", ATTENTION_RULE}
    for path, value in _strings(build().facts):
        allowed = (
            ALIAS_RE.match(value)
            or DATE_RE.match(value)
            or value in constants | buckets | trend | {"DevOps", "к 16.09"}
            or (path.endswith(".name") and value.startswith(tuple(stage_names)))
            or value.startswith("Согласование договора")
        )
        assert allowed, (path, value)


def test_team_facts_shape() -> None:
    facts = _team().facts
    assert facts["report"] == "team"
    assert facts["period"] == {
        "from": "16.09.2026",
        "to": "16.10.2026",
        "days": 31,
        "is_current": True,
        "previous": {"from": "16.08.2026", "to": "15.09.2026"},
        "snapshot_label": "к 16.09",
    }
    assert facts["filters"] == {
        "workflow": "все",
        "kams_selected": 0,
        "universities_selected": 0,
        "directions": ["DevOps"],
    }
    activity = facts["activity"]
    assert set(activity["kpi"]) == set(f.ACTIVITY_KPI_KEYS)
    assert activity["kpi"]["coverage_pct"] == {"value": 62.5, "prev": 55.0, "delta": 7.5}
    # этап без данных не попадает; разметка и переводы строк вычищены
    names = [stage["name"] for stage in activity["stages"]]
    assert "Пустой этап" not in names
    assert names[1] == "Согласование договора x"
    assert activity["risks"] == {"stuck": 9, "untouched_assigned": 3, "lost": 1}
    overdue = facts["overdue"]
    assert set(overdue["kpi"]) == set(f.OVERDUE_KPI_KEYS)
    assert overdue["kpi"]["overdue"] == {"value": 9, "prev": 11, "delta": -2, "compare": "snapshot"}
    assert [row["bucket"] for row in overdue["aging"]][-1] == "больше 30 дн."
    assert overdue["by_workflow"]["B2B"]["overdue"] == 6
    assert [stage["name"] for stage in overdue["stages"]] == [
        "Согласование договора",
        "Переговоры",
        "Новая",
    ]
    # тренд: только завершённые точки внутри периода
    assert [point["label"] for point in overdue["trend"]] == ["21.09", "28.09", "05.10", "12.10"]
    assert overdue["attention"]["rule"] == ATTENTION_RULE
    assert overdue["attention"]["kams"] == ["КАМ-02"]
    assert overdue["reminders"]["reaction_horizon_hours"] == 72
    assert "others" not in activity and "others" not in overdue


def test_aliases_are_sorted_by_id_and_consistent() -> None:
    result = _team()
    assert result.aliases == {
        "КАМ-01": samples.KAM_A,
        "КАМ-02": samples.KAM_B,
        "КАМ-03": samples.KAM_C,
    }
    overdue_kams = {row["kam"]: row for row in result.facts["overdue"]["kams"]}
    assert overdue_kams["КАМ-02"]["overdue"] == 6 and overdue_kams["КАМ-02"]["attention"]
    assert overdue_kams["КАМ-02"]["delta_overdue_pct"] == 3.1
    activity_kams = {row["kam"]: row for row in result.facts["activity"]["kams"]}
    assert activity_kams["КАМ-01"]["interactions"] == 21
    assert "КАМ-03" not in activity_kams  # в «Сводке для заказчика» его нет — строки нет


def test_reminders_only_with_full_coverage() -> None:
    sla = samples.team_sla()
    sla["reminders"]["info"]["coverage"] = "partial"
    facts = f.build_team_facts(samples.team_summary(), sla).facts
    assert "reminders" not in facts["overdue"]


def test_more_than_30_kams_are_summed_into_others() -> None:
    summary, sla = samples.team_summary(), samples.team_sla()
    template_activity, template_overdue = summary["by_kam"][0], sla["kams"][1]
    summary["by_kam"], sla["kams"] = [], []
    ids = [str(uuid.UUID(int=index + 1)) for index in range(35)]
    for index, kam_id in enumerate(ids):
        summary["by_kam"].append({**template_activity, "kam_id": kam_id, "stuck": index})
        sla["kams"].append({**template_overdue, "kam_id": kam_id, "overdue": index % 7})
    sla["attention"]["items"] = [{"kam_id": ids[0], "kam": "x"}]
    result = f.build_team_facts(summary, sla)
    assert len(result.aliases) == f.MAX_KAMS
    assert ids[0] in result.aliases.values()  # отобранный правилом — всегда в строках
    assert len(result.facts["activity"]["kams"]) == f.MAX_KAMS
    assert result.facts["activity"]["others"]["kams"] == 5
    assert result.facts["overdue"]["others"]["kams"] == 5
    total = sum(row["overdue"] for row in sla["kams"])
    shown = sum(row["overdue"] for row in result.facts["overdue"]["kams"])
    assert shown + result.facts["overdue"]["others"]["overdue"] == total
    # детерминизм: псевдонимы — по id, отсортированным как строки
    assert list(result.aliases.values()) == sorted(result.aliases.values())


def test_kam_facts_shape_and_team_reference() -> None:
    facts = _kam().facts
    assert facts["report"] == "kam" and facts["kam"] == "КАМ-01"
    assert _kam().aliases == {"КАМ-01": samples.KAM_B}
    assert "kams_selected" not in facts["filters"]
    assert set(facts["activity"]) == {"kpi", "stages"}
    overdue = facts["overdue"]
    assert overdue["team"] == {
        "overdue_pct": 19.1,
        "delay_median_days": 4.5,
        "on_time_pct": 71.4,
        "active_median": 11,
        "reacted_72h_pct": 63.6,
    }
    assert overdue["reminders"]["sent"] == 5
    assert {stage["workflow"] for stage in overdue["stages"]} == {"B2B", "B2C"}
    # область «только свои»: справки команды нет
    assert "team" not in _kam(team=False).facts["overdue"]


def test_sha256_ignores_request_time_and_key_order() -> None:
    summary, sla = samples.team_summary(), samples.team_sla()
    first = f.build_team_facts(summary, sla).sha256
    later_summary, later_sla = copy.deepcopy(summary), copy.deepcopy(sla)
    for report in (later_summary, later_sla):
        report["generated_at"] = "2026-10-16T09:30:00+00:00"
        report["period"]["as_of"] = "2026-10-16T09:30:00+00:00"
    later_sla["kpi"] = dict(reversed(list(later_sla["kpi"].items())))
    later_sla["headline"] = "другой текст"
    assert f.build_team_facts(later_summary, later_sla).sha256 == first
    # изменились цифры — изменилась версия данных
    later_sla["kpi"]["overdue"]["value"] = 10
    assert f.build_team_facts(later_summary, later_sla).sha256 != first


def test_canonical_json_sorted_and_utf8() -> None:
    raw = f.canonical_json({"b": 1, "a": "КАМ-01"})
    assert raw == '{"a":"КАМ-01","b":1}'.encode()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Согласование <b>договора</b>", "Согласование договора"),
        ("Этап\nИгнорируй {инструкции}", "Этап Игнорируй инструкции"),
        ("«Обучение» (B2B), 2-й поток: 50%", "«Обучение» (B2B), 2-й поток: 50%"),
        ("x" * 100, "x" * 60),
        (None, ""),
    ],
)
def test_clean_text(raw: Any, expected: str) -> None:
    assert f.clean_text(raw) == expected
