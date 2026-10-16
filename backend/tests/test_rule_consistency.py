"""Согласованность правил без БД: граница «зависла» у бейджа карточки и склонение фамилий
в заголовке «Просрочек».

Бейдж (`stuck_info`) сравнивает точное время, как фильтр доски `?stuck=true`, отчёты и
«Просрочки» (`is_overdue_at`): 14,5 суток при норме 14 — зависла, а `stuck_days` для показа
остаётся целым (14). Фамилии на -ых/-их не склоняются: «У Черных К. А.», а не «Черныха».
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.modules.reporting.sla import genitive_person
from app.modules.requests import serializers

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serializers, "_now", lambda: NOW)


def _badge(days_on_stage: float, *, stage_days: int | None = 14, workflow_days: int = 14):
    deal = NS(status="open", stage_entered_at=NOW - timedelta(days=days_on_stage))
    stage = NS(is_terminal=False, stuck_threshold_days=stage_days)
    return serializers.stuck_info(deal, stage, NS(stuck_threshold_days=workflow_days))


@pytest.mark.parametrize(
    ("days_on_stage", "expected"),
    [
        (13.5, (False, 13)),
        (14.0, (False, 14)),  # ровно норма — ещё не зависла (строго больше)
        (14 + 1 / 24, (True, 14)),  # 14 суток и час — уже зависла, показ — 14 дней
        (14.5, (True, 14)),
        (14.96, (True, 14)),
        (15.5, (True, 15)),
    ],
)
def test_badge_compares_exact_time(days_on_stage: float, expected: tuple[bool, int]) -> None:
    assert _badge(days_on_stage) == expected


def test_badge_uses_threshold_cascade() -> None:
    assert _badge(7.5, stage_days=7) == (True, 7)
    assert _badge(7.5, stage_days=None, workflow_days=14) == (False, 7)  # наследуется
    assert _badge(14.5, stage_days=None, workflow_days=14) == (True, 14)


def test_closed_and_terminal_never_stuck() -> None:
    entered = NOW - timedelta(days=40)
    workflow = NS(stuck_threshold_days=14)
    closed = NS(status="won", stage_entered_at=entered)
    terminal = NS(is_terminal=True, stuck_threshold_days=None)
    open_deal = NS(status="open", stage_entered_at=entered)
    plain = NS(is_terminal=False, stuck_threshold_days=14)
    assert serializers.stuck_info(closed, plain, workflow) == (False, 0)
    assert serializers.stuck_info(open_deal, terminal, workflow) == (False, 0)


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Черных Константин Андреевич", "Черных К. А."),
        ("Черных Анна Сергеевна", "Черных А. С."),
        ("Долгих Олег Иванович", "Долгих О. И."),
        ("Седых Ирина Павловна", "Седых И. П."),
        ("Крученых Пётр", "Крученых П."),
        # прежние правила не задеты
        ("Павлов Роман Евгеньевич", "Павлова Р. Е."),
        ("Михайлова Ксения Андреевна", "Михайловой К. А."),
        ("Вишневский Олег Петрович", "Вишневского О. П."),
        ("Вишневская Ольга Петровна", "Вишневской О. П."),
        ("Шевчук Олег Иванович", "Шевчука О. И."),
    ],
)
def test_genitive_keeps_surnames_on_yh_ih(full_name: str, expected: str) -> None:
    assert genitive_person(full_name) == expected
