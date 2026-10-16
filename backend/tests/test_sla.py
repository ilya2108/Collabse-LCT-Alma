"""Юнит-тесты вкладки «Просрочки» (`app.modules.reporting.sla`): чистые функции без БД.

Визиты и подотрезки, просрочка строго больше нормы, корзины по целым суткам, решения без
ошибки выжившего, атрибуция на дату, разностные массивы тренда против точного среза,
правило отбора и тексты без советов, напоминания и реакция, оговорки о смене норм.
"""

from __future__ import annotations

import random
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from app.core.errors import ApiError
from app.modules.reporting import sla
from app.modules.reporting.kam_activity import REPORT_TZ, KamActivityParams, _bounds
from app.modules.reporting.sla import (
    ATTENTION_RULE,
    DEFINITION,
    DEFINITIONS,
    NO_ADVICE_RE,
    Action,
    DealInfo,
    Episode,
    HistoryRow,
    OwnerTimeline,
    ReminderRow,
    SnapshotGrid,
    StageInfo,
    attention,
    build_visits,
    coverage,
    decided_by_bucket,
    decided_in,
    decision,
    delay_at,
    delay_bucket,
    genitive_person,
    headline,
    inherited_at,
    is_overdue_at,
    kam_facts,
    match_reminders,
    no_action_ids,
    norm_cascade,
    norm_change_notes,
    parse_dedup_key,
    reaction,
    reminder_episode,
    segment_at,
    short_person,
    snapshot,
    summarize_reminders,
    summarize_snapshot,
    trend_grid,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=REPORT_TZ)
DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)

WF = "b2b"
A = uuid.UUID(int=1)  # «Новая», норма 7 (задана для этапа)
B = uuid.UUID(int=2)  # «Переговоры», норма 14 (процесс)
C = uuid.UUID(int=3)  # «Встреча», норма 3
WON = uuid.UUID(int=4)  # «Завершена» (терминальный)

STAGES = {
    A: StageInfo(A, WF, "new", "Новая", "#1", 0, False, None, False, 7, "stage"),
    B: StageInfo(B, WF, "negotiation", "Переговоры", "#2", 2, False, None, False, 14, "workflow"),
    C: StageInfo(C, WF, "meeting", "Встреча", "#3", 1, False, None, True, 3, "stage"),
    WON: StageInfo(WON, WF, "completed", "Завершена", "#4", 6, True, "won", False, 14, "workflow"),
}

KAM_A = uuid.UUID(int=101)
KAM_B = uuid.UUID(int=102)


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def deal(
    *, entered: float = 0, stage: uuid.UUID = A, owner: uuid.UUID = KAM_A, created: float = 0
) -> DealInfo:
    return DealInfo(
        deal_id=uuid.UUID(int=500),
        title="МФТИ — Python",
        pipeline=WF,
        university_id=uuid.UUID(int=900),
        university_name="Московский физико-технический институт",
        university_short="МФТИ",
        client=None,
        responsible_id=owner,
        stage_id=stage,
        stage_entered_at=at(entered),
        created_at=at(created),
    )


def row(
    days: float,
    to: uuid.UUID,
    frm: uuid.UUID | None = None,
    kind: str = "manual",
    *,
    is_return: bool = False,
) -> HistoryRow:
    is_entry = kind != "system_migration" and frm != to
    return HistoryRow(
        deal_id=uuid.UUID(int=500),
        at=at(days),
        from_stage_id=frm,
        to_stage_id=to,
        kind=kind,
        is_entry=is_entry,
        is_return=is_return,
    )


def visits_of(rows: list[HistoryRow], **kwargs: Any) -> list[sla.Visit]:
    info = kwargs.pop("info", None) or deal()
    return build_visits(rows, info, STAGES, is_current=kwargs.pop("is_current", False))


# --- 1–3: корзины, строгость, округление ------------------------------------------------


def test_bucket_boundaries() -> None:
    assert delay_bucket(3 * DAY) == "d1_3"
    assert delay_bucket(3 * DAY + SECOND) == "d4_7"
    assert delay_bucket(7 * DAY) == "d4_7"
    assert delay_bucket(14 * DAY) == "d8_14"
    assert delay_bucket(30 * DAY) == "d15_30"
    assert delay_bucket(30 * DAY + SECOND) == "d31_plus"
    assert delay_bucket(SECOND) == "d1_3"
    assert delay_bucket(86400 * 3 + 1) == "d4_7"  # секунды числом


def test_overdue_strictly_greater_than_norm() -> None:
    (visit,) = visits_of([row(0, A)])
    assert not is_overdue_at(visit, at(7))  # ровно норма — ещё не просрочена
    assert is_overdue_at(visit, at(7) + timedelta(microseconds=1))
    assert visit.overdue_from == at(7)


def test_delay_rounding_display() -> None:
    """3 сут + 1 с: показывается «+3,0 дн.», но корзина — «4–7» (оговорено)."""
    (visit,) = visits_of([row(0, A)])
    moment = at(7) + 3 * DAY + SECOND
    delay = delay_at(visit, moment)
    assert delay == 3 * DAY + SECOND
    assert sla._days(delay) == 3.0
    assert delay_bucket(delay) == "d4_7"
    assert any("+3,0 дн." in text for _term, text in DEFINITIONS)


# --- 4–7: визиты, подотрезки, срок, решения ---------------------------------------------


def test_build_visits() -> None:
    rows = [
        row(0, A, None, "import"),  # импорт (from = NULL) открывает визит
        row(5, B, A),  # ручной вход в «Переговоры»
        row(6, B, B, "integration"),  # событие LMS этап не меняет, визит не делит
        row(8, C, B, "system_migration"),  # перевод схемы — новый подотрезок
        row(9, WON, C, "system_migration"),  # перевод на терминальный — конец визита
    ]
    first, second = visits_of(rows)
    assert (first.entered, first.left, first.exit_kind) == (at(0), at(5), "manual")
    assert first.exit_stage_id == B
    assert second.entered == at(5) and second.left is None
    assert [s.stage_id for s in second.segments] == [B, C, WON]
    assert second.active_end == at(9)
    assert second.exit_kind == "system_terminal"
    assert segment_at(second, at(6)).stage_id == B


def test_stage_and_norm_at_t() -> None:
    rows = [row(0, A), row(1, B, A), row(10, C, B, "system_migration")]
    _first, visit = visits_of(rows)
    before = segment_at(visit, at(9))
    after = segment_at(visit, at(11))
    assert (before.stage_id, before.norm) == (B, 14 * DAY)
    assert (after.stage_id, after.norm) == (C, 3 * DAY)
    # время на этапе не обнулено переводом: задержка считается от входа в «Переговоры»
    assert delay_at(visit, at(11)) == (at(11) - at(1)) - 3 * DAY


def test_overdue_from_across_segments() -> None:
    """Перевод на этап с меньшей нормой: просрочка с момента перевода."""
    rows = [row(0, B), row(5, C, B, "system_migration")]
    (visit,) = visits_of(rows)
    assert visit.overdue_from == at(5)  # 5 сут > нормы «Встречи» (3) — сразу при переводе
    assert not is_overdue_at(visit, at(4.9))
    assert is_overdue_at(visit, at(5))


def test_decision() -> None:
    late = visits_of([row(0, A), row(10, B, A)])[0]
    assert decision(late) == (at(7), False)  # не уложились — в момент истечения нормы
    on_time = visits_of([row(0, A), row(3, B, A)])[0]
    assert decision(on_time) == (at(3), True)  # уложились — в момент ручного выхода
    to_terminal = visits_of([row(0, A), row(3, WON, A, "system_migration")])[0]
    assert to_terminal.exit_kind == "system_terminal"
    assert decision(to_terminal) is None  # перевод схемы до срока — решения нет
    still_open = visits_of([row(0, A)])[0]
    assert decision(still_open) == (at(7), False)  # «решится» в будущем — отсекает период


def test_on_time_no_survivorship() -> None:
    """КАМ без завершений, у которого в периоде истекли 3 срока, — 0 %, а не «—»."""
    infos = [deal(entered=i) for i in range(3)]
    visits = []
    for index, info in enumerate(infos):
        info = DealInfo(**{**info.__dict__, "deal_id": uuid.UUID(int=600 + index)})
        rows = [HistoryRow(info.deal_id, at(index), None, A, "manual", True)]
        visits += build_visits(rows, info, STAGES, is_current=False)
    timelines = {v.deal_id: OwnerTimeline((), KAM_A) for v in visits}
    items = decided_in(visits, timelines, at(0), at(30))
    summary = sla.summarize_decided(items)
    assert summary == {"decided": 3, "on_time": 0, "on_time_pct": 0.0}


# --- 9–11: владелец на дату, «получена просроченной», срез -----------------------------


def test_owner_at_without_changes() -> None:
    assert OwnerTimeline((), KAM_A).owner_at(at(5)) == KAM_A


def test_owner_at_before_between_after() -> None:
    other = uuid.UUID(int=103)
    timeline = OwnerTimeline(
        [(at(10), KAM_A, KAM_B), (at(20), KAM_B, other)], current=uuid.UUID(int=104)
    )
    assert timeline.owner_at(at(5)) == KAM_A  # до первого — before первого
    assert timeline.owner_at(at(10)) == KAM_B  # момент изменения — уже новый
    assert timeline.owner_at(at(15)) == KAM_B
    # после последнего — текущий ответственный (защита от рассинхрона)
    assert timeline.owner_at(at(25)) == uuid.UUID(int=104)
    assert timeline.acquired_at(at(15)) == at(10)
    assert timeline.acquired_at(at(5)) is None


def test_owner_at_same_timestamps() -> None:
    timeline = OwnerTimeline([(at(10), KAM_A, KAM_B), (at(10), KAM_B, KAM_A)], current=KAM_A)
    assert timeline.owner_at(at(10)) == KAM_A
    assert timeline.owner_at(at(9)) == KAM_A
    spans = timeline.spans(at(0), at(20))
    assert spans == [(at(0), at(10), KAM_A), (at(10), at(20), KAM_A)]


def test_inherited_relative_to_owner_at_t() -> None:
    """На T и T_prev разные владельцы — разные флаги «получена просроченной»."""
    (visit,) = visits_of([row(0, A)])  # срок истёк на 7-й день
    timeline = OwnerTimeline([(at(10), KAM_A, KAM_B)], current=KAM_B)
    assert inherited_at(visit, timeline, at(12)) == (True, at(10))  # B получил просроченной
    assert inherited_at(visit, timeline, at(9)) == (False, None)  # у A — своя просрочка
    early = OwnerTimeline([(at(3), KAM_A, KAM_B)], current=KAM_B)
    assert inherited_at(visit, early, at(12)) == (False, at(3))  # получил до срока


def test_snapshot_ignores_closed_and_terminal() -> None:
    infos = []
    rows_by_deal = {
        1: [row(0, A), row(3, B, A)],  # открыт визит на B
        2: [row(0, A), row(2, WON, A)],  # закрыта (терминальный этап)
        3: [row(0, A), row(2, WON, A, "system_migration")],  # переведена на терминальный
    }
    visits = []
    for number, rows in rows_by_deal.items():
        info = DealInfo(**{**deal().__dict__, "deal_id": uuid.UUID(int=number)})
        infos.append(info)
        rows = [
            HistoryRow(
                info.deal_id, r.at, r.from_stage_id, r.to_stage_id, r.kind, r.is_entry, r.is_return
            )
            for r in rows
        ]
        visits += build_visits(rows, info, STAGES, is_current=False)
    timelines = {info.deal_id: OwnerTimeline((), KAM_A) for info in infos}
    items = snapshot(visits, at(5), timelines)
    assert [item.visit.deal_id for item in items] == [uuid.UUID(int=1)]
    assert items[0].stage_id == B and not items[0].overdue


# --- 12–13: разностные массивы и сетка тренда --------------------------------------------


def _random_dataset(seed: int) -> tuple[list[sla.Visit], dict[uuid.UUID, OwnerTimeline]]:
    rng = random.Random(seed)
    owners = [uuid.UUID(int=700 + i) for i in range(4)]
    visits: list[sla.Visit] = []
    timelines: dict[uuid.UUID, OwnerTimeline] = {}
    for number in range(60):
        deal_id = uuid.UUID(int=10_000 + number)
        info = DealInfo(**{**deal().__dict__, "deal_id": deal_id})
        moment = rng.uniform(-40, 30)
        stage = rng.choice([A, B, C])
        rows = [HistoryRow(deal_id, at(moment), None, stage, "manual", True)]
        for _ in range(rng.randint(0, 4)):
            moment += rng.uniform(0.5, 20)
            target = rng.choice([A, B, C, WON])
            kind = rng.choice(["manual", "manual", "system_migration"])
            if target == stage:
                continue
            rows.append(HistoryRow(deal_id, at(moment), stage, target, kind, kind == "manual"))
            stage = target
        visits += build_visits(rows, info, STAGES, is_current=False)
        changes = []
        owner = rng.choice(owners)
        point = -45.0
        for _ in range(rng.randint(0, 3)):
            point += rng.uniform(1, 30)
            new_owner = rng.choice(owners)
            changes.append((at(point), owner, new_owner))
            owner = new_owner
        timelines[deal_id] = OwnerTimeline(changes, current=owner)
    return visits, timelines


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_grid_matches_naive_snapshot(seed: int) -> None:
    visits, timelines = _random_dataset(seed)
    times = [at(-30 + 3.5 * i) for i in range(20)] + [at(40)]
    grid = SnapshotGrid(times)
    for visit in visits:
        grid.add_visit(visit, timelines[visit.deal_id])
    owners = {timeline.owner_at(t) for timeline in timelines.values() for t in times}
    for owner in owners:
        series = grid.series(lambda o, owner=owner: o == owner)
        for moment, point in zip(times, series, strict=True):
            exact = summarize_snapshot(
                item for item in snapshot(visits, moment, timelines) if item.owner == owner
            )
            assert point["active"] == exact["active"]
            assert point["overdue"] == exact["overdue"]
            assert point["overdue_30_plus"] == exact["overdue_30_plus"]
            assert point["delay_avg_days"] == exact["delay_avg_days"]


def test_trend_grid() -> None:
    now = datetime(2026, 10, 16, 12, 0, tzinfo=REPORT_TZ)
    bounds = _bounds(date(2026, 10, 1), date(2026, 10, 16), now)
    granularity, window_start, points = trend_grid(bounds)
    assert granularity == "week"
    assert window_start == datetime(2026, 8, 17, tzinfo=REPORT_TZ)  # понедельник 00:00 МСК
    assert window_start.weekday() == 0
    assert bounds.as_of - window_start >= timedelta(weeks=8)
    assert points[0].label == "23.08"  # дата среза: воскресенье
    assert points[0].complete and not points[0].in_period
    assert not points[-1].complete and points[-1].snapshot_at == bounds.as_of
    assert points[-1].label == "16.10"
    assert any(point.in_period for point in points)
    long = _bounds(date(2026, 1, 1), date(2026, 9, 30), now)
    granularity, start, months = trend_grid(long)
    assert granularity == "month" and start == long.start
    assert months[0].label == "на 31.01" and all(point.complete for point in months)
    values = [
        {"active": 4, "overdue": 2, "overdue_30_plus": 0, "delay_avg_days": 1.0} for _ in months
    ]
    rows = sla.trend_points(months, values, [(4, 3)] * len(months))
    assert rows[0]["overdue_pct"] is None and rows[0]["on_time_pct"] is None  # n < 5
    rows = sla.trend_points(months, values, [(5, 3)] * len(months))
    assert rows[0]["on_time_pct"] == 60.0


def test_decided_by_bucket() -> None:
    now = datetime(2026, 10, 16, 12, 0, tzinfo=REPORT_TZ)
    _g, _start, points = trend_grid(_bounds(date(2026, 10, 1), date(2026, 10, 16), now))
    visit = visits_of([row(0, A)])[0]
    items = [
        sla.DecisionItem(visit, points[1].start + DAY, True, KAM_A, A),
        sla.DecisionItem(visit, points[1].start + 2 * DAY, False, KAM_A, A),
    ]
    counts = decided_by_bucket(items, points)
    assert counts[1] == (2, 1) and sum(c[0] for c in counts) == 2


# --- 14–17: команда, правило отбора, тексты ----------------------------------------------


def _row(kam: str, active: int, own: int, inherited: int = 0, own30: int = 0) -> dict[str, Any]:
    return {
        "kam_id": kam,
        "kam": kam,
        "is_active": True,
        "active": active,
        "overdue": own + inherited,
        "own_overdue": own,
        "inherited": inherited,
        "overdue_pct": round((own + inherited) * 100 / active, 1) if active else None,
        "overdue_30_plus": own30,
        "overdue_no_action": 0,
        "delay_avg_days": 5.0 if own + inherited else None,
        "delay_median_days": 4.0 if own + inherited else None,
        "delay_max_days": 9.0 if own + inherited else None,
        "_own_30_plus": own30,
        "_own_delay_total": float(own),
    }


def test_team_weighted_and_medians() -> None:
    rows = [
        _row("a", 10, 1),
        _row("b", 2, 2),
        _row("c", 4, 0),
        {**_row("d", 5, 5), "is_active": False},
        _row("e", 0, 0),
    ]
    team_rows, team = sla.team_reference(rows)
    assert [row["kam"] for row in team_rows] == ["a", "b", "c"]
    assert team == {"kams_total": 3, "active_median": 4, "overdue_median": 1}
    assert sla._inactive_owners(rows) == {"users": 1, "active": 5, "overdue": 5}
    # доля взвешена по заявкам, а не «среднее средних»
    block, _selected = attention(team_rows)
    assert block["team_own_overdue_pct"] == round(3 * 100 / 16, 1)


def test_attention_rule() -> None:
    team = [
        _row("base1", 20, 2),
        _row("base2", 20, 2),
        _row("high", 6, 3),  # 50 % ≥ 1,5 × команды
        _row("inherited_only", 6, 0, inherited=4),  # только унаследованные — не попадает
        _row("small", 2, 2),  # меньше 3 в работе
        _row("old", 10, 1, own30=1),  # собственная просрочка > 30 дней
        _row("one", 5, 1),  # меньше 2 собственных
    ]
    block, selected = attention(team)
    assert selected == {"high", "old"}
    assert [item["kam"] for item in block["items"]] == ["high", "old"]
    assert block["total_kams"] == len(team)
    assert block["rule"] == ATTENTION_RULE

    # 1,5 × команды по несокращённой доле: ровно на пороге — попадает (3/4 = 1,5 × 5/10)
    _block, chosen = attention([_row("x", 4, 3), _row("y", 6, 2)])
    assert chosen == {"x"}
    _block, chosen = attention([_row("x", 4, 3), _row("y", 6, 3)])  # 3/4 < 1,5 × 6/10
    assert chosen == set()

    # порядок: доля ↓, число ↓, сумма задержки ↓; лимит 8
    many = [_row(f"k{i:02d}", 3, 3) for i in range(12)] + [_row("big", 100, 0)]
    block, chosen = attention(many)
    assert len(chosen) == 12 and len(block["items"]) == sla.ATTENTION_LIMIT

    # фиксированный набор из 20 КАМов — отобрано меньше половины
    rng = random.Random(2026)
    fixed = [_row(f"kam{i}", rng.randint(3, 15), 0) for i in range(20)]
    for index in (1, 4, 9, 13):
        fixed[index] = _row(f"kam{index}", 8, 5)
    block, chosen = attention(fixed)
    assert 0 < len(chosen) < 10


def test_kam_facts_without_advice() -> None:
    row = {
        **_row("Павлов Роман Евгеньевич", 12, 6, inherited=1, own30=2),
        "overdue_30_plus": 2,
        "overdue_no_action": 3,
        "delay_avg_days": 11.4,
        "delay_median_days": 9.0,
        "delay_max_days": 48.0,
        "prev": {"overdue": 4},
    }
    facts = kam_facts(
        row,
        team_pct=21.0,
        active_median=7,
        prev_label="01.10",
        transfer_dates=[date(2026, 10, 11)],
    )
    assert facts[0] == "1 из 7 получена уже просроченной (передача 11.10)"
    assert facts[1] == "Просрочено 7 из 12 в работе — 58 % (по команде 21 %)"
    assert facts[2] == "Сверх нормы: в среднем 11,4 дн., обычно 9 дн., максимум 48 дн."
    assert "Больше 30 дней сверх нормы: 2" in facts
    assert "После срока без действий в системе: 3" in facts
    assert len(facts) == sla.FACTS_LIMIT
    assert not any(NO_ADVICE_RE.search(fact) for fact in facts)
    # строки с нулём пропущены
    quiet = kam_facts(
        {**_row("x", 5, 2), "prev": {"overdue": 2}},
        team_pct=None,
        active_median=None,
        prev_label="01.10",
    )
    assert not any("30 дней" in fact or "без действий" in fact for fact in quiet)
    assert not any(fact.startswith("На 01.10") for fact in quiet)


def test_no_advice_re() -> None:
    assert NO_ADVICE_RE.search("Заявка стоит на этапе «Переговоры»") is None
    for text in ("Стоит обратить внимание", "рекомендуем проверить", "Нужно связаться"):
        assert NO_ADVICE_RE.search(text), text
    texts = [DEFINITION, ATTENTION_RULE, sla.REMINDERS_NOTE, sla.TRANSFER_NOTE]
    texts += [text for _term, text in DEFINITIONS]
    assert not any(NO_ADVICE_RE.search(text) for text in texts)
    assert not any(re.search(r"\bSLA\b", text) for text in texts)


def test_headline() -> None:
    cur = {"active": 84, "overdue": 17, "overdue_30_plus": 3, "overdue_no_action": 6}
    prev = {"active": 79, "overdue": 13}
    text = headline(
        at="16.10.2026",
        prev_label="01.10",
        cur=cur,
        prev=prev,
        top_stage=("«Переговоры» (вузы)", 6),
    )
    assert text == (
        "На 16.10.2026 дольше нормы — 17 из 84 заявок в работе (20 %; на 01.10 — 13 из 79, "
        "16 %). Больше всего на этапе «Переговоры» (вузы): 6. Больше 30 дней сверх нормы: 3; "
        "по 6 после срока не было действий в системе."
    )
    assert text == headline(
        at="16.10.2026",
        prev_label="01.10",
        cur=cur,
        prev=prev,
        top_stage=("«Переговоры» (вузы)", 6),
    )
    quiet = headline(
        at="16.10.2026",
        prev_label="01.10",
        cur={**cur, "overdue_30_plus": 0, "overdue_no_action": 0},
        prev={"active": 0, "overdue": 0},
    )
    assert quiet == "На 16.10.2026 дольше нормы — 17 из 84 заявок в работе (20 %)."
    assert headline(at="x", prev_label=None, cur={**cur, "overdue": 0}, prev=None) == (
        "Просроченных заявок нет; в работе 84."
    )
    assert headline(at="x", prev_label=None, cur={**cur, "active": 0}, prev=None) == (
        "Заявок в работе нет."
    )
    owner = headline(
        at="16.10.2026",
        prev_label=None,
        cur={"active": 7, "overdue": 4, "overdue_30_plus": 0, "overdue_no_action": 0},
        prev=None,
        owner=genitive_person("Павлов Роман Евгеньевич"),
    )
    assert owner.startswith("У Павлова Р. Е.: на 16.10.2026 дольше нормы — 4 из 7")
    assert not NO_ADVICE_RE.search(text)


def test_person_names() -> None:
    assert short_person("Павлов Роман Евгеньевич") == "Павлов Р. Е."
    assert genitive_person("Михайлова Ксения Андреевна") == "Михайловой К. А."
    assert genitive_person("Беляев Олег Иванович") == "Беляева О. И."
    assert genitive_person("Ремизова Анна") == "Ремизовой А."
    assert genitive_person("admin") == "admin"


# --- 18–22: напоминания, реакция, покрытие журнала ---------------------------------------


def test_parse_dedup_key() -> None:
    deal_id, stage_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    base = f"stuck:{deal_id}:{stage_id}:responsible:3"
    assert parse_dedup_key(base) == ("responsible", base)
    assert parse_dedup_key(f"{base}:{user_id}") == ("responsible", base)
    manager = f"stuck:{deal_id}:{stage_id}:manager:5"
    assert parse_dedup_key(f"{manager}:{user_id}") == ("manager", manager)
    old = f"stuck:{deal_id}:{stage_id}:4"  # старый ключ из 4 частей
    assert parse_dedup_key(old) == ("responsible", old)
    assert parse_dedup_key(old, True) == ("manager", old)


def _reminder(days: float, level: str = "responsible", *, status: str = "sent", **kw: Any):
    base = kw.pop("base", f"stuck:500:{A}:{level}:{int(days)}")
    return ReminderRow(
        row_id=uuid.uuid4(),
        deal_id=uuid.UUID(int=500),
        created_at=at(days),
        sent_at=at(days) + timedelta(minutes=1) if status == "sent" else None,
        status=status,
        recipient_id=kw.pop("recipient", KAM_A),
        level=level,
        base_key=base,
    )


def test_reminders_matching() -> None:
    first, second = visits_of([row(0, A), row(20, B, A)])
    rows = [
        _reminder(8),
        _reminder(8, base=f"stuck:500:{A}:responsible:8"),  # второй получатель того же ключа
        _reminder(11, status="skipped"),
        _reminder(12, "manager"),
        _reminder(35),
        _reminder(-1),  # раньше первого визита — не сопоставляется
    ]
    matched = match_reminders([first, second], rows)
    summary = matched[first.key]
    assert (summary.reminders, summary.delivered, summary.escalated) == (2, 1, True)
    assert matched[second.key].reminders == 1 and not matched[second.key].escalated
    episode = reminder_episode(first, summary)
    assert episode.first_at == at(8) and episode.delivered_at == at(8) + timedelta(minutes=1)
    # первое напоминание раньше окна — эпизод в окно не попадает
    timelines = {first.deal_id: OwnerTimeline((), KAM_A)}
    stats = summarize_reminders(
        matched, [episode], timelines, at(9), at(40), lambda owner: owner == KAM_A
    )
    assert stats["episodes"] == 0
    assert stats["visits_reminded"] == 2 and stats["visits_escalated"] == 1
    assert (stats["sent"], stats["delivered"]) == (2, 1)


def test_reaction() -> None:
    (visit,) = visits_of([row(0, A), row(20, B, A)])[:1]
    start = at(8)
    base = Episode(visit=visit, recipient=KAM_A, first_at=start, delivered_at=start)
    foreign = Action(at(8.1), "comment", KAM_B)
    own = Action(at(8.2), "comment", KAM_A)
    result = reaction(base, [foreign, own], at(30))
    assert result.status == "reacted_72h" and result.reaction_hours == 4.8
    assert reaction(base, [foreign], at(30)).status == "no_action"  # чужое — не реакция
    assert reaction(base, [], at(9)).status == "pending"  # 72 ч ещё не прошли
    late = Action(at(12), "comment", KAM_A)
    slow = reaction(base, [late], at(30))
    assert slow.status == "no_action" and slow.reaction_hours == 96.0
    # окно до min(left, T): выход с этапа — действие, комментарий после выхода — нет
    (closed,) = visits_of([row(0, A), row(9, B, A)])[:1]
    episode = Episode(visit=closed, recipient=KAM_A, first_at=start, delivered_at=start)
    assert reaction(episode, [Action(at(9), "transition", KAM_A)], at(30)).status == ("reacted_72h")
    after_exit = reaction(episode, [Action(at(9.5), "comment", KAM_A)], at(30))
    assert after_exit.status == "no_action" and after_exit.reaction_at is None
    undelivered = Episode(visit=visit, recipient=KAM_A, first_at=start, delivered_at=None)
    assert reaction(undelivered, [own], at(30)).status == "undelivered"
    # уровень manager эпизода не образует; получатель — из recipient_user_id
    manager_only = sla.summarize_visit_reminders([_reminder(8, "manager")])
    assert reminder_episode(visit, manager_only) is None
    other = sla.summarize_visit_reminders([_reminder(8, recipient=KAM_B)])
    assert reminder_episode(visit, other).recipient == KAM_B


def test_coverage_and_prev_delta_hidden() -> None:
    start = at(10)
    assert coverage(None, start) == "none"
    assert coverage(at(12), start) == "partial"
    assert coverage(at(10), start) == "full"
    assert coverage(at(1), start) == "full"


def test_overdue_no_action() -> None:
    (visit,) = visits_of([row(0, A)])
    timelines = {visit.deal_id: OwnerTimeline((), KAM_A)}
    items = snapshot([visit], at(12), timelines)
    before_due = {visit.deal_id: sla.LastAction(at(5), "comment", KAM_B)}
    after_due = {visit.deal_id: sla.LastAction(at(8), "comment", KAM_B)}  # любой автор
    assert no_action_ids(items, before_due) == {visit.deal_id}
    assert no_action_ids(items, after_due) == set()
    assert no_action_ids(items, {}) == {visit.deal_id}


# --- 23–24: оговорки о нормах, параметры выгрузки ----------------------------------------


def test_norm_cascade() -> None:
    assert norm_cascade(7, 14, 10) == (7, "stage")
    assert norm_cascade(None, 14, 10) == (14, "workflow")
    assert norm_cascade(None, None, 10) == (10, "global")


def test_norm_change_notes() -> None:
    applied = datetime(2026, 10, 3, 12, 0, tzinfo=REPORT_TZ)
    operations = [
        {"op": "update_status", "status_id": str(B), "patch": {"stuck_threshold_days": 10}},
        {"op": "update_status", "status_id": str(A), "patch": {"stuck_threshold_days": None}},
        {"op": "update_status", "status_id": str(A), "patch": {"reset_stuck_threshold": False}},
        {"op": "update_status", "status_id": str(A), "patch": {"color": "#fff"}},
        {
            "op": "add_status",
            "status": {"code": "x", "name": "Икс", "stuck_threshold_days": 5},
        },
        {"op": "delete_status", "status_id": str(C), "migrate_to_status_id": str(B)},
    ]
    notes = norm_change_notes([("b2b", applied, operations)], STAGES)
    assert notes == [
        "Норма этапа «Переговоры» (вузы) изменена 03.10.2026; все визиты оценены по "
        "действующей норме.",
        "Заявки с этапа «Встреча» перенесены на «Переговоры» 03.10.2026; время на этапе не "
        "обнулено, с этой даты действует норма этапа «Переговоры».",
    ]
    reset = [{"op": "update_status", "status_id": str(A), "patch": {"reset_stuck_threshold": True}}]
    assert len(norm_change_notes([("b2b", applied, reset)], STAGES)) == 1
    assert norm_change_notes([("b2b", applied, [{"op": "broken"}])], STAGES) == []
    assert not any(NO_ADVICE_RE.search(note) for note in notes)


def test_params_from_filters() -> None:
    kam = uuid.uuid4()
    params = KamActivityParams.from_filters(
        {"from": "2026-10-01", "to": "2026-10-16", "kam_id": [str(kam)], "wf": "b2b"}
    )
    assert params.kam_ids == (kam,) and params.workflow_type == "b2b"
    assert params.date_from == date(2026, 10, 1)
    with pytest.raises(ApiError) as excinfo:
        KamActivityParams.from_filters({"kam_id": "not-a-uuid"})
    assert excinfo.value.status_code == 400


def test_is_current_open_visit_starts_at_stage_entered_at() -> None:
    info = deal(entered=2)
    (visit,) = build_visits([row(0, A)], info, STAGES, is_current=True)
    assert visit.entered == at(2)
    (past,) = build_visits([row(0, A)], info, STAGES, is_current=False)
    assert past.entered == at(0)
    (synthetic,) = build_visits([], info, STAGES, is_current=False)
    assert (synthetic.entered, synthetic.segments[0].stage_id) == (at(2), A)
