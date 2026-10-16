"""Проверка ответа модели: числа сверяются с фактами, советы и чужие псевдонимы вычищаются."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.ai_summary import verify as v
from app.modules.ai_summary.facts import build_team_facts

from . import ai_summary_samples as samples

FACTS: dict[str, Any] = {
    "report": "team",
    "period": {
        "from": "16.09.2026",
        "to": "16.10.2026",
        "days": 31,
        "previous": {"from": "16.08.2026", "to": "15.09.2026"},
        "snapshot_label": "к 16.09",
    },
    "overdue": {
        "kpi": {
            "overdue": {"value": 9, "prev": 11, "delta": -2},
            "active": {"value": 1234, "prev": 1200, "delta": 34},
            "overdue_pct": {"value": 19.14, "prev": 25.0, "delta": -5.9},
            "delay_median_days": {"value": 4.5},
        },
        "aging": [{"bucket": "больше 30 дн.", "count": 1}],
        "trend": [{"label": "05.10", "overdue": 7}],
        "attention": {"rule": "не меньше чем в 1,5 раза", "kams": ["КАМ-01"]},
        "stages": [{"workflow": "B2B", "name": "Этап 7", "overdue": 3}],
    },
}
ALIASES = {"КАМ-01": "a", "КАМ-02": "b"}


def _allowed() -> v.AllowedFacts:
    return v.allowed_values(FACTS)


@pytest.mark.parametrize(
    "phrase",
    [
        "Просрочено 9 из 1 234 заявок (19,1%).",
        "Просрочено 9 из 1 234 заявок, 19,14 %.",
        "Доля просроченных — 19%, к началу периода −5,9 п.п.",
        "Было 11, стало 9: на 2 меньше.",
        "На 16.10.2026 медиана задержки 4,5 дн., срез на 16.09.",
        "За 31 день периода 16.09.2026–16.10.2026 в 2026 году.",
        "На 05.10 просрочено 7.",
        "Больше 30 дней сверх нормы: 1 (порог — в 1,5 раза выше).",
        "КАМ-01: просрочено 3 на этапе «Этап 7» (B2B).",
        "Этап \"Этап 7\" и 'Этап 7': просрочено 3.",
        "Список из 1 КАМа требует внимания.",
        "Доля просроченных 19.14% (точка вместо запятой — дробь, а не дата).",
    ],
)
def test_numbers_that_match_facts_pass(phrase: str) -> None:
    result = v.check_phrase(phrase, section="lagging", aliases=ALIASES, allowed=_allowed())
    assert result.reason is None, (phrase, result)


@pytest.mark.parametrize(
    "phrase",
    [
        "Просрочено 10 из 1 234 заявок.",  # 10 нет в фактах
        "Доля просроченных — 19,3%.",  # дальше допуска 0,05
        "На 17.10.2026 просрочено 9.",  # дата не из фактов
        "Рост на 12,5 п.п.",
        "КАМ-01: просрочено 8.",
    ],
)
def test_invented_numbers_are_removed(phrase: str) -> None:
    result = v.check_phrase(phrase, section="lagging", aliases=ALIASES, allowed=_allowed())
    assert result.reason == "numbers", phrase


def test_rounding_tolerance() -> None:
    allowed = _allowed()
    assert allowed.matches(19.1) and allowed.matches(19.14) and allowed.matches(19)
    assert not allowed.matches(19.2)
    assert allowed.matches(5) and allowed.matches(5.9) and allowed.matches(6)  # |delta|, round
    assert allowed.matches(4.5) and allowed.matches(4) and allowed.matches(5)  # округление 4,5


@pytest.mark.parametrize(
    ("phrase", "section", "reason"),
    [
        ("Рекомендуется перераспределить заявки.", "lagging", "advice"),
        ("Руководителю следует проверить этап.", "lagging", "advice"),
        ("Стоит обратить внимание на этап.", "lagging", "advice"),
        # мягкие советы, которые пропускал NO_ADVICE_RE отчёта «Просрочки»
        ("Целесообразно пересмотреть нормы этапа.", "lagging", "advice"),
        ("Предлагаем усилить контроль за КАМ-01.", "attention", "advice"),
        ("Важно ускорить обработку заявок.", "lagging", "advice"),
        ("Стоит обсудить распределение заявок.", "lagging", "advice"),
        ("Надо ускорить этап.", "lagging", "advice"),
        ("Желательно уделить внимание этапу.", "lagging", "advice"),
        ("Имеет смысл перераспределить заявки.", "lagging", "advice"),
        ("Как ускорить этап «Этап 7»?", "questions", "advice"),
        ("Как сократить просрочки?", "questions", "advice"),
        # оценки людей и работы
        ("КАМ-01 не справляется: просрочено 9.", "attention", "evaluative"),
        ("Слабый результат: просрочено 9.", "lagging", "evaluative"),
        ("Работа с заявками неэффективна.", "lagging", "evaluative"),
        # утверждение про КАМа без единого числа
        ("КАМ-01 требует внимания.", "attention", "person_without_numbers"),
        ("КАМ-01: больше всего просрочек в команде.", "lagging", "person_without_numbers"),
        ("КАМ-07: просрочено 9.", "attention", "unknown_alias"),
        ("Подробнее: https://example.com", "lagging", "link"),
        ("Смотрите <b>9</b>", "lagging", "link"),
        ("Что задерживает этап", "questions", "not_question"),
        ("Просрочено 9. " + "а" * 300, "lagging", "too_long"),
    ],
)
def test_phrase_rules(phrase: str, section: str, reason: str) -> None:
    result = v.check_phrase(phrase, section=section, aliases=ALIASES, allowed=_allowed())
    assert result.reason == reason


@pytest.mark.parametrize(
    "phrase",
    [
        "Что задерживает заявки на этапе «Этап 7»?",
        "КАМ-01: что задерживает заявки?",  # вопрос про КАМа — без чисел можно
        "Доля просроченных снизилась: было 11, стало 9.",
        "Медиана задержки сверх нормы 4,5 дн.",
    ],
)
def test_facts_are_not_advice_or_evaluation(phrase: str) -> None:
    section = "questions" if phrase.endswith("?") else "lagging"
    result = v.check_phrase(phrase, section=section, aliases=ALIASES, allowed=_allowed())
    assert result.reason is None, (phrase, result)


def test_stage_name_in_quotes_is_not_advice() -> None:
    facts = {**FACTS, "overdue": {**FACTS["overdue"], "stages": [{"name": "Ускорить найм"}]}}
    allowed = v.allowed_values(facts)
    ok = v.check_phrase(
        "Просрочено 9 на этапе «Ускорить найм».", section="lagging", aliases={}, allowed=allowed
    )
    assert ok.reason is None


def test_alias_phrase_numbers_come_from_its_row() -> None:
    """Во фразе с псевдонимом — цифры этого КАМа и общие, но не строки других КАМов."""
    facts = {
        "overdue": {
            "kpi": {"overdue": {"value": 40}},
            "kams": [{"kam": "КАМ-01", "overdue": 6}, {"kam": "КАМ-02", "overdue": 13}],
        }
    }
    allowed = v.allowed_values(facts)

    def reason(phrase: str) -> str | None:
        return v.check_phrase(phrase, section="attention", aliases=ALIASES, allowed=allowed).reason

    assert reason("КАМ-01: просрочено 6.") is None
    assert reason("КАМ-02: просрочено 13 из 40 по команде.") is None
    assert reason("КАМ-01: просрочено 13.") == "numbers"  # цифра КАМ-02
    assert reason("КАМ-01 и КАМ-02: 6 и 13.") is None
    # без псевдонима — любые цифры отчёта
    assert (
        v.check_phrase(
            "У одного КАМа 13 просрочек.", section="lagging", aliases=ALIASES, allowed=allowed
        ).reason
        is None
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("КАМ 1: просрочено 9", "КАМ-01: просрочено 9"),
        ("КАМ‑01: просрочено 9", "КАМ-01: просрочено 9"),
        ("(кам-2) просрочено 9", "(КАМ-02) просрочено 9"),
        ("КАМ—12 и КАМ-01", "КАМ-12 и КАМ-01"),
        ("КАМов в команде 9", "КАМов в команде 9"),
        ("КАМ 3, КАМ-04", "КАМ-03, КАМ-04"),
        ("(КАМ 4)", "(КАМ-04)"),
        ("КАМ-5 — 3 заявки", "КАМ-05 — 3 заявки"),
        # обычная речь, а не псевдоним: ФИО подставлять нельзя
        ("У одного КАМ 3 заявки просрочены.", "У одного КАМ 3 заявки просрочены."),
    ],
)
def test_alias_normalization(raw: str, expected: str) -> None:
    assert v.normalize_aliases(raw)[0] == expected


def test_alias_digits_are_not_numbers() -> None:
    # номер псевдонима не делает «2» допустимым числом
    allowed = v.allowed_values({"kams": ["КАМ-02"], "x": 5})
    assert not allowed.matches(2)
    result = v.check_phrase("КАМ-02: 5", section="attention", aliases=ALIASES, allowed=allowed)
    assert result.reason is None and result.numbers == 1


def test_verify_counts_and_keeps_good_phrases() -> None:
    sections = {
        "headline": "На 16.10.2026 просрочено 9 из 1 234. Это на 13 меньше, чем было.",
        "going_well": ["Просроченных на срез меньше: 9 против 11."],
        "lagging": ["Больше 30 дней сверх нормы: 1.", "Рекомендуется ускорить этап."],
        "attention": ["КАМ-1: просрочено 3 на этапе «Этап 7»."],
        "questions": ["Что задерживает заявки на этапе «Этап 7»?"],
    }
    result = v.verify(sections, FACTS, ALIASES)
    assert result.sections["headline"] == "На 16.10.2026 просрочено 9 из 1 234."
    assert result.sections["lagging"] == ["Больше 30 дней сверх нормы: 1."]
    assert result.sections["attention"] == ["КАМ-01: просрочено 3 на этапе «Этап 7»."]
    assert result.checks == {
        # дата, 9, 1 234 | 13 (удалено) | 9, 11 | 30, 1 | 3 — номер псевдонима и «Этап 7» не числа
        "numbers_total": 9,
        "numbers_matched": 8,
        "sentences_removed": 2,
        "advice_removed": 1,
        "removed_by_reason": {"numbers": 1, "advice": 1, "evaluative": 0, "other": 0},
    }


def test_verify_fails_when_headline_removed() -> None:
    sections = {"headline": "Просрочено 99.", "going_well": ["Было 11, стало 9."]}
    with pytest.raises(v.VerificationFailed) as exc:
        v.verify(sections, FACTS, ALIASES)
    assert exc.value.reason == "headline_removed"


def test_verify_fails_with_too_few_phrases_after_removal() -> None:
    # одна фраза осталась, одна удалена (не больше половины) — слишком мало
    with pytest.raises(v.VerificationFailed) as exc:
        v.verify({"headline": "Просрочено 9.", "lagging": ["Рост 77."]}, FACTS, ALIASES)
    assert exc.value.reason == "too_few_phrases"
    # две остались, одна удалена — проходит
    result = v.verify(
        {"headline": "Просрочено 9.", "going_well": ["Было 11."], "lagging": ["Рост 77."]},
        FACTS,
        ALIASES,
    )
    assert result.checks["sentences_removed"] == 1


def test_short_answer_without_removals_passes() -> None:
    """Пустой период: модель законно отвечает одной фразой — это не ошибка проверки."""
    result = v.verify({"headline": "Просрочено 9."}, FACTS, ALIASES)
    assert result.sections["headline"] == "Просрочено 9."
    assert result.checks["sentences_removed"] == 0


def test_verify_fails_when_more_than_half_removed() -> None:
    sections = {
        "headline": "Просрочено 9.",
        "going_well": ["Было 11."],
        "lagging": ["Рост 77.", "Рост 78.", "Рост 79."],
    }
    with pytest.raises(v.VerificationFailed) as exc:
        v.verify(sections, FACTS, ALIASES)
    assert exc.value.reason == "too_many_removed"
    assert exc.value.checks["sentences_removed"] == 3


def test_verify_without_enforce_never_fails() -> None:
    result = v.verify({"headline": "Просрочено 99."}, FACTS, ALIASES, enforce=False)
    assert result.sections["headline"] == "" and result.checks["sentences_removed"] == 1


def test_sentence_split_keeps_abbreviations() -> None:
    assert v.split_sentences("Рост +7,5 п.п. к прошлому периоду. Охват 62,5%.") == [
        "Рост +7,5 п.п. к прошлому периоду.",
        "Охват 62,5%.",
    ]


def test_mentioned_aliases_in_order() -> None:
    sections = {
        "headline": "Итог.",
        "lagging": ["КАМ-03: 1.", "КАМ-01: 2 (КАМ-03)."],
        "attention": ["КАМ-02: 3."],
    }
    assert v.mentioned_aliases(sections) == ["КАМ-03", "КАМ-01", "КАМ-02"]


def test_real_facts_accept_report_numbers() -> None:
    result = build_team_facts(samples.team_summary(), samples.team_sla())
    allowed = v.allowed_values(result.facts)
    for phrase in (
        "КАМ-02: просрочено 6 из 20 (30%), собственных 5.",
        "Уложились в норму: 71,4% решённых этапов, к прошлому периоду +10,3 п.п.",
        "Напоминаний 12, доставлено 11, отреагировали 63,6% за 72 ч.",
        "На 12.10 просрочено 9, на 21.09 — 10.",
    ):
        check = v.check_phrase(phrase, section="lagging", aliases=result.aliases, allowed=allowed)
        assert check.reason is None, phrase
