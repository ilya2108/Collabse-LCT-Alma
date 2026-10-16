"""Сводка по правилам: детерминированный текст из тех же фактов, без ИИ (работает офлайн).

Те же разделы, что у GigaChat, те же правила текста (только факты, без советов, КАМ — в
начале пункта псевдонимом), формат чисел — как в выводах отчёта (`kam_activity._num`,
`_signed`: запятая, «п.п.»). Пункт добавляется только при выполненном условии. Тексты
проходят ту же проверку `verify`, что и ответ модели, — без удалений (тест).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.reporting.kam_activity import _num, _plural, _signed

GOING_WELL_LIMIT = 4
LAGGING_LIMIT = 4
ATTENTION_LIMIT = 5
QUESTIONS_LIMIT = 3


def _value(block: Any, key: str = "value") -> Any:
    return block.get(key) if isinstance(block, Mapping) else None


def _kpi(facts: Mapping[str, Any], group: str, name: str) -> Mapping[str, Any]:
    return ((facts.get(group) or {}).get("kpi") or {}).get(name) or {}


def _positive(value: Any) -> bool:
    return isinstance(value, int | float) and value > 0


def _negative(value: Any) -> bool:
    return isinstance(value, int | float) and value < 0


def _end(text: str) -> str:
    """Точка в конце предложения (кроме оканчивающихся на «п.п.» и «дн.»)."""
    return text if text.endswith(".") else f"{text}."


def _requests(n: int) -> str:
    return _plural(n, ("заявка", "заявки", "заявок"))


def _headline(facts: Mapping[str, Any]) -> str:
    period = facts.get("period") or {}
    active = _value(_kpi(facts, "overdue", "active")) or 0
    overdue = _value(_kpi(facts, "overdue", "overdue")) or 0
    overdue_pct = _value(_kpi(facts, "overdue", "overdue_pct"))
    created = _value(_kpi(facts, "activity", "new_requests")) or 0
    won = _value(_kpi(facts, "activity", "won")) or 0
    lost = _value(_kpi(facts, "activity", "lost")) or 0
    requests = _value(_kpi(facts, "activity", "requests_count")) or 0
    if not active and not requests and not created:
        return f"За период {period.get('from')}–{period.get('to')} заявок в работе нет."
    flow = f"за период создано {_num(created)}, выиграно {_num(won)}, отказов {_num(lost)}"
    if not active:
        return f"На {period.get('to')} заявок в работе нет; {flow}."
    share = f" ({_num(overdue_pct)}%)" if overdue_pct is not None else ""
    return (
        f"На {period.get('to')} в работе {_num(active)} {_requests(active)}, "
        f"просрочено {_num(overdue)}{share}; {flow}."
    )


def _going_well(facts: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    on_time = _kpi(facts, "overdue", "on_time_pct")
    if _positive(on_time.get("delta")) and on_time.get("value") is not None:
        lines.append(
            _end(
                f"Уложились в норму: {_num(on_time['value'])}% решённых этапов, "
                f"к прошлому периоду {_signed(on_time['delta'], ' п.п.')}"
            )
        )
    overdue = _kpi(facts, "overdue", "overdue")
    if _negative(overdue.get("delta")) and overdue.get("prev") is not None:
        lines.append(
            "Просроченных на срез меньше, чем на начало периода: "
            f"{_num(overdue['value'])} против {_num(overdue['prev'])}."
        )
    coverage = _kpi(facts, "activity", "coverage_pct")
    if _positive(coverage.get("delta")) and coverage.get("value") is not None:
        lines.append(
            _end(
                f"Охват закреплённых вузов: {_num(coverage['value'])}%, "
                f"к прошлому периоду {_signed(coverage['delta'], ' п.п.')}"
            )
        )
    interactions = _kpi(facts, "activity", "interactions")
    if _positive(interactions.get("delta")):
        lines.append(
            f"Взаимодействий за период: {_num(interactions['value'])}, "
            f"к прошлому периоду {_signed(interactions['delta'])}."
        )
    won = _kpi(facts, "activity", "won")
    if _positive(won.get("delta")):
        lines.append(
            f"Выиграно заявок: {_num(won['value'])}, к прошлому периоду {_signed(won['delta'])}."
        )
    return lines[:GOING_WELL_LIMIT]


def top_overdue_stage(facts: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Этап с наибольшим числом просрочек (при равенстве — первый в порядке схемы)."""
    best: Mapping[str, Any] | None = None
    for stage in (facts.get("overdue") or {}).get("stages") or ():
        if not _positive(stage.get("overdue")):
            continue
        if best is None or stage["overdue"] > best["overdue"]:
            best = stage
    return best


def _stage_line(stage: Mapping[str, Any]) -> str:
    median = stage.get("delay_median_days")
    tail = f", медиана сверх нормы {_num(median)} дн." if median is not None else "."
    return (
        f"Больше всего просрочек на этапе «{stage['name']}» ({stage['workflow']}): "
        f"{_num(stage['overdue'])} из {_num(stage.get('active') or 0)}{tail}"
    )


def _lagging(facts: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    stage = top_overdue_stage(facts)
    if stage is not None:
        lines.append(_stage_line(stage))
    over30 = _value(_kpi(facts, "overdue", "overdue_30_plus"))
    if _positive(over30):
        lines.append(f"Заявок больше 30 дней сверх нормы: {_num(over30)}.")
    no_action = _value(_kpi(facts, "overdue", "overdue_no_action"))
    if _positive(no_action):
        lines.append(f"Просроченных заявок без действий после срока: {_num(no_action)}.")
    untouched = ((facts.get("activity") or {}).get("risks") or {}).get("untouched_assigned")
    if _positive(untouched):
        lines.append(f"Закреплённых вузов без взаимодействий за период: {_num(untouched)}.")
    on_time = _kpi(facts, "overdue", "on_time_pct")
    if _negative(on_time.get("delta")) and on_time.get("value") is not None:
        lines.append(
            _end(
                f"Уложились в норму: {_num(on_time['value'])}% решённых этапов, "
                f"к прошлому периоду {_signed(on_time['delta'], ' п.п.')}"
            )
        )
    overdue = _kpi(facts, "overdue", "overdue")
    if _positive(overdue.get("delta")) and overdue.get("prev") is not None:
        lines.append(
            "Просроченных на срез больше, чем на начало периода: "
            f"{_num(overdue['value'])} против {_num(overdue['prev'])}."
        )
    return lines[:LAGGING_LIMIT]


def _attention_team(facts: Mapping[str, Any]) -> list[str]:
    overdue = facts.get("overdue") or {}
    rows = {row["kam"]: row for row in overdue.get("kams") or ()}
    lines: list[str] = []
    for alias in (overdue.get("attention") or {}).get("kams") or ():
        row = rows.get(alias)
        if row is None:
            continue
        text = f"{alias}: просрочено {_num(row['overdue'])} из {_num(row['active'])}"
        if row.get("overdue_pct") is not None:
            text += f" ({_num(row['overdue_pct'])}%)"
        text += f", собственных {_num(row['own_overdue'])}"
        if _positive(row.get("overdue_30_plus")):
            text += f", больше 30 дней сверх нормы — {_num(row['overdue_30_plus'])}"
        lines.append(text + ".")
    return lines[:ATTENTION_LIMIT]


def _attention_kam(facts: Mapping[str, Any]) -> list[str]:
    overdue = facts.get("overdue") or {}
    team = overdue.get("team") or {}
    lines: list[str] = []
    pct = _value(_kpi(facts, "overdue", "overdue_pct"))
    if pct is not None and team.get("overdue_pct") is not None:
        lines.append(f"Доля просроченных {_num(pct)}% при {_num(team['overdue_pct'])}% по команде.")
    median = _value(_kpi(facts, "overdue", "delay_median_days"))
    if median is not None and team.get("delay_median_days") is not None:
        lines.append(
            f"Медиана задержки сверх нормы {_num(median)} дн. "
            f"при {_num(team['delay_median_days'])} дн. по команде."
        )
    buckets = [row for row in overdue.get("aging") or () if _positive(row.get("count"))]
    if buckets:
        top = max(buckets, key=lambda row: row["count"])
        lines.append(f"Больше всего просрочек в корзине «{top['bucket']}»: {_num(top['count'])}.")
    no_action = _value(_kpi(facts, "overdue", "overdue_no_action"))
    if _positive(no_action):
        lines.append(f"После срока без действий в системе: {_num(no_action)}.")
    return lines[:ATTENTION_LIMIT]


def _questions(facts: Mapping[str, Any], *, team: bool) -> list[str]:
    lines: list[str] = []
    stage = top_overdue_stage(facts)
    if stage is not None:
        lines.append(f"Что задерживает заявки на этапе «{stage['name']}»?")
    overdue = facts.get("overdue") or {}
    if team:
        rows = {row["kam"]: row for row in overdue.get("kams") or ()}
        for alias in (overdue.get("attention") or {}).get("kams") or ():
            if _positive((rows.get(alias) or {}).get("overdue_30_plus")):
                lines.append(f"{alias}: какие заявки просрочены больше чем на 30 дней?")
                break
    elif _positive(_value(_kpi(facts, "overdue", "overdue_30_plus"))):
        lines.append("Какие заявки просрочены больше чем на 30 дней?")
    no_action = _value(_kpi(facts, "overdue", "overdue_no_action"))
    if _positive(no_action):
        noun = _plural(
            no_action,
            ("просроченной заявке", "просроченным заявкам", "просроченным заявкам"),
        )
        lines.append(f"Почему по {_num(no_action)} {noun} нет действий после срока?")
    return lines[:QUESTIONS_LIMIT]


def rules_sections(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Разделы сводки по правилам (с псевдонимами, как ответ модели)."""
    team = facts.get("report") == "team"
    return {
        "headline": _headline(facts),
        "going_well": _going_well(facts),
        "lagging": _lagging(facts),
        "attention": _attention_team(facts) if team else _attention_kam(facts),
        "questions": _questions(facts, team=team),
    }
