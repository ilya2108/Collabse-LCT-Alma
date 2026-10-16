"""Сводка по правилам: тексты из фактов проходят проверку без удалений, без советов."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.modules.ai_summary.facts import build_kam_facts, build_team_facts
from app.modules.ai_summary.rules import rules_sections
from app.modules.ai_summary.verify import verify
from app.modules.reporting.sla import NO_ADVICE_RE

from . import ai_summary_samples as samples


def _team() -> Any:
    return build_team_facts(samples.team_summary(), samples.team_sla())


def _kam(team: bool = True) -> Any:
    return build_kam_facts(samples.KAM_B, samples.kam_activity(), samples.kam_sla(team=team))


def _empty() -> Any:
    summary, sla = samples.team_summary(), samples.team_sla()
    for block in (summary["kpi"], sla["kpi"]):
        for value in block.values():
            if isinstance(value.get("value"), int | float):
                value.update(value=0, prev=0, delta=0)
    sla["kams"], summary["by_kam"] = [], []
    sla["attention"]["items"] = []
    for rows in sla["stages"].values():
        for row in rows:
            row.update(active=0, overdue=0, decided=0)
    summary["untouched_assigned"] = []
    return build_team_facts(summary, sla)


def _all_phrases(sections: dict[str, Any]) -> list[str]:
    keys = ("going_well", "lagging", "attention", "questions")
    return [sections["headline"]] + [item for key in keys for item in sections[key]]


@pytest.mark.parametrize("build", [_team, _kam, _empty], ids=["team", "kam", "empty"])
def test_rules_pass_verification_without_removals(build) -> None:  # noqa: ANN001
    result = build()
    sections = rules_sections(result.facts)
    verified = verify(sections, result.facts, result.aliases, enforce=False)
    assert verified.checks["sentences_removed"] == 0, verified
    assert verified.checks["numbers_total"] == verified.checks["numbers_matched"]
    assert verified.sections == sections
    for phrase in _all_phrases(sections):
        assert not NO_ADVICE_RE.search(phrase), phrase
        assert ".." not in phrase, phrase
    for question in sections["questions"]:
        assert question.endswith("?")


def test_team_rules_content() -> None:
    sections = rules_sections(_team().facts)
    assert sections["headline"] == (
        "На 16.10.2026 в работе 47 заявок, просрочено 9 (19,1%); "
        "за период создано 12, выиграно 3, отказов 1."
    )
    assert sections["going_well"][0] == (
        "Уложились в норму: 71,4% решённых этапов, к прошлому периоду +10,3 п.п."
    )
    assert sections["lagging"][0] == (
        "Больше всего просрочек на этапе «Согласование договора» (B2B): 3 из 8, "
        "медиана сверх нормы 6 дн."
    )
    assert sections["attention"] == [
        "КАМ-02: просрочено 6 из 20 (30%), собственных 5, больше 30 дней сверх нормы — 1."
    ]
    assert sections["questions"][1] == "КАМ-02: какие заявки просрочены больше чем на 30 дней?"
    # verify с порогами тоже проходит
    verify(sections, _team().facts, _team().aliases)


def test_kam_rules_are_impersonal_and_compare_with_team() -> None:
    sections = rules_sections(_kam().facts)
    assert all("КАМ-" not in phrase for phrase in _all_phrases(sections))
    assert sections["headline"].startswith("На 16.10.2026 в работе 20 заявок, просрочено 6 (30%)")
    assert sections["attention"][0] == "Доля просроченных 30% при 19,1% по команде."
    assert "Медиана задержки сверх нормы 5 дн. при 4,5 дн. по команде." in sections["attention"]
    # «только свои»: сравнения с командой нет
    own = rules_sections(_kam(team=False).facts)
    assert not any("по команде" in phrase for phrase in own["attention"])


def test_empty_period() -> None:
    sections = rules_sections(_empty().facts)
    assert sections["headline"] == "За период 16.09.2026–16.10.2026 заявок в работе нет."
    assert sections["attention"] == [] and sections["questions"] == []


def test_rules_are_deterministic() -> None:
    facts = _team().facts
    assert rules_sections(copy.deepcopy(facts)) == rules_sections(facts)


def test_plural_in_headline() -> None:
    facts = copy.deepcopy(_team().facts)
    facts["overdue"]["kpi"]["active"]["value"] = 1
    assert "в работе 1 заявка," in rules_sections(facts)["headline"]
    facts["overdue"]["kpi"]["active"]["value"] = 3
    assert "в работе 3 заявки," in rules_sections(facts)["headline"]
