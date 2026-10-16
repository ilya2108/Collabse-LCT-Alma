"""Факты для сводки: отчёты → allowlist, псевдонимы КАМов, каноническая форма и sha256.

Принцип — allowlist: в факты копируются только перечисленные здесь поля; строки берутся
только из разрешённых источников (названия этапов и ИТ-направлений — очищенные, метки
процессов `B2B`/`B2C`, подписи корзин и правило отбора — константы кода, даты периода).
Никогда не попадают: ФИО (`kam`, `actor`), клиенты B2C и названия вузов (`client`,
`university`, `untouched_assigned`), заголовки и id заявок, суммы, тексты выводов
(`highlights`, `headline`, `notes`, `definitions`), списки заявок (`overdue.items`,
`top_stuck`), комментарии, студенты (кроме агрегатов), `as_of` и `generated_at` (хеш
не зависит от времени запроса).

Псевдонимы: id КАМов отчёта, отсортированные как строки, → `КАМ-01`, `КАМ-02`, …
(детерминированно). Больше 30 КАМов — в факты попадают 30 строк (сначала отобранные
правилом «Требуют внимания», затем по просрочкам и зависшим), остальные — суммой в
`others`. Числа — как в отчётах, без пересчёта.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import orjson

from app.modules.reporting.sla import ATTENTION_RULE, KPI_COMPARE, REACTION_HORIZON

MAX_KAMS = 30
MAX_STAGES = 30
TREND_POINTS = 8
TEXT_LIMIT = 60
ALIAS_PREFIX = "КАМ-"
WORKFLOW_LABELS = {"b2b": "B2B", "b2c": "B2C"}
ALL_WORKFLOWS = "все"
REACTION_HORIZON_HOURS = int(REACTION_HORIZON.total_seconds() // 3600)

_UNSAFE_RE = re.compile(r"[^\w\s«»\"'().,:;+№%/–—-]")
_TAG_RE = re.compile(r"<[^<>]*>")

ACTIVITY_KPI_KEYS: tuple[str, ...] = (
    "coverage_pct",
    "assigned_touched",
    "universities_assigned",
    "requests_count",
    "new_requests",
    "won",
    "lost",
    "conversion",
    "interactions",
    "stuck",
    "talent_pool_added",
    "universities_with_contract",
    "students_studying",
)
ACTIVITY_KAM_KEYS: tuple[str, ...] = (
    "is_active",
    "universities_assigned",
    "assigned_touched",
    "coverage_pct",
    "requests_count",
    "new_requests",
    "won",
    "lost",
    "conversion",
    "interactions",
    "interactions_prev",
    "stuck",
)
ACTIVITY_STAGE_KEYS: tuple[str, ...] = (
    "reached",
    "on_stage_at_end",
    "stuck",
    "avg_days",
    "threshold_days",
    "is_terminal",
)
OVERDUE_KPI_KEYS: tuple[str, ...] = tuple(key for key, _compare in KPI_COMPARE)
OVERDUE_KAM_KEYS: tuple[str, ...] = (
    "active",
    "overdue",
    "own_overdue",
    "inherited",
    "overdue_pct",
    "small_sample",
    "delay_median_days",
    "overdue_30_plus",
    "overdue_no_action",
    "on_time_pct",
    "inflow",
)
OVERDUE_STAGE_KEYS: tuple[str, ...] = (
    "norm_days",
    "active",
    "overdue",
    "overdue_pct",
    "delay_median_days",
    "on_time_pct",
)
BY_WORKFLOW_KEYS: tuple[str, ...] = (
    "active",
    "overdue",
    "overdue_pct",
    "delay_median_days",
    "on_time_pct",
)
TREND_KEYS: tuple[str, ...] = ("overdue", "overdue_pct", "on_time_pct")
REMINDER_KEYS: tuple[str, ...] = (
    "sent",
    "delivered",
    "reacted_72h_pct",
    "reaction_median_hours",
)
TEAM_REF_KEYS: tuple[str, ...] = (
    "overdue_pct",
    "delay_median_days",
    "on_time_pct",
    "active_median",
    "reacted_72h_pct",
)
# суммы по КАМам вне 30 строк (только счётчики — доли и медианы не суммируются)
ACTIVITY_SUM_KEYS: tuple[str, ...] = (
    "universities_assigned",
    "assigned_touched",
    "requests_count",
    "new_requests",
    "won",
    "lost",
    "interactions",
    "interactions_prev",
    "stuck",
)
OVERDUE_SUM_KEYS: tuple[str, ...] = (
    "active",
    "overdue",
    "own_overdue",
    "inherited",
    "overdue_30_plus",
    "overdue_no_action",
    "inflow",
)


@dataclass(frozen=True)
class Facts:
    """Факты (то, что уходит наружу) и псевдонимы `{"КАМ-01": "<uuid>"}`."""

    facts: dict[str, Any]
    aliases: dict[str, str]

    @property
    def sha256(self) -> str:
        return facts_sha256(self.facts)


# --------------------------------------------------------------------------------------
# Примитивы
# --------------------------------------------------------------------------------------


def clean_text(value: Any) -> str:
    """Название этапа или направления: без разметки и управляющих символов, одной строкой,
    не длиннее 60 символов."""
    text = _UNSAFE_RE.sub("", _TAG_RE.sub(" ", str(value or "")))
    return " ".join(text.split())[:TEXT_LIMIT].strip()


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return value
    return None


def _flag(value: Any) -> bool:
    return bool(value)


def _pick(row: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        result[key] = value if isinstance(value, bool) else _number(value)
    return result


def _kpi_block(block: Any, *, compare: bool = False) -> dict[str, Any]:
    block = block if isinstance(block, Mapping) else {}
    result = {key: _number(block.get(key)) for key in ("value", "prev", "delta")}
    if compare and block.get("compare") in ("snapshot", "period"):
        result["compare"] = block["compare"]
    return result


def _kpi(kpi: Any, keys: Sequence[str], *, compare: bool = False) -> dict[str, Any]:
    kpi = kpi if isinstance(kpi, Mapping) else {}
    return {key: _kpi_block(kpi.get(key), compare=compare) for key in keys}


def _ru_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return None


def _workflow_label(code: Any) -> str:
    return WORKFLOW_LABELS.get(str(code), "")


def canonical_json(facts: Mapping[str, Any]) -> bytes:
    """Каноническая форма фактов: ключи отсортированы, UTF-8 без экранирования."""
    return orjson.dumps(facts, option=orjson.OPT_SORT_KEYS)


def facts_sha256(facts: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(facts)).hexdigest()


# --------------------------------------------------------------------------------------
# Период и фильтры
# --------------------------------------------------------------------------------------


def period_facts(report: Mapping[str, Any]) -> dict[str, Any]:
    """Период из отчёта «Просрочки»: даты ДД.ММ.ГГГГ, без `as_of` (стабильность хеша)."""
    period = report.get("period") or {}
    previous = report.get("previous_period") or {}
    comparison = report.get("comparison") or {}
    return {
        "from": _ru_date(period.get("from")),
        "to": _ru_date(period.get("to")),
        "days": _number(period.get("days")),
        "is_current": _flag(period.get("is_current")),
        "previous": {"from": _ru_date(previous.get("from")), "to": _ru_date(previous.get("to"))},
        "snapshot_label": clean_text(comparison.get("snapshot_label")) or None,
    }


def filter_facts(
    params: Mapping[str, Any], directions: Sequence[Mapping[str, Any]] | None, *, kind: str
) -> dict[str, Any]:
    workflow = params.get("workflow_type")
    result: dict[str, Any] = {"workflow": _workflow_label(workflow) or ALL_WORKFLOWS}
    if kind == "team":
        result["kams_selected"] = len(params.get("kam_id") or ())
    result["universities_selected"] = len(params.get("university_id") or ())
    result["directions"] = [
        name for name in (clean_text(item.get("name")) for item in directions or ()) if name
    ]
    return result


# --------------------------------------------------------------------------------------
# Блоки отчётов
# --------------------------------------------------------------------------------------


def activity_stages(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Конверсия по этапам «Сводки для заказчика»: этапы с данными, не больше 30."""
    rows: list[dict[str, Any]] = []
    for workflow_type, stages in (summary.get("stages") or {}).items():
        label = _workflow_label(workflow_type)
        if not label:
            continue
        for stage in stages or ():
            picked = _pick(stage, ACTIVITY_STAGE_KEYS)
            if not (picked["reached"] or picked["on_stage_at_end"] or picked["stuck"]):
                continue
            rows.append({"workflow": label, "name": clean_text(stage.get("name")), **picked})
    return _limit_stages(rows)


def _limit_stages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Не больше 30 этапов: при избытке — с наибольшими просрочками/зависшими, в порядке
    схемы."""
    if len(rows) <= MAX_STAGES:
        return rows
    ranked = sorted(
        range(len(rows)),
        key=lambda i: (
            -(rows[i].get("overdue") or rows[i].get("stuck") or 0),
            -(rows[i].get("active") or rows[i].get("on_stage_at_end") or 0),
            i,
        ),
    )
    keep = set(ranked[:MAX_STAGES])
    return [row for index, row in enumerate(rows) if index in keep]


def overdue_stages(rows: Iterable[tuple[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for workflow_type, stage in rows:
        label = _workflow_label(workflow_type)
        if not label:
            continue
        picked = _pick(stage, OVERDUE_STAGE_KEYS)
        decided = _number(stage.get("decided")) or 0
        if not (picked["active"] or picked["overdue"] or decided):
            continue
        result.append({"workflow": label, "name": clean_text(stage.get("name")), **picked})
    return _limit_stages(result)


def aging_facts(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "bucket": clean_text(row.get("label")),
            "count": _number(row.get("count")),
            "prev": _number(row.get("prev")),
        }
        for row in report.get("aging") or ()
    ]


def trend_facts(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Последние ≤ 8 завершённых точек тренда внутри периода."""
    points = [
        point
        for point in ((report.get("trend") or {}).get("points") or ())
        if point.get("complete") and point.get("in_period")
    ]
    return [
        {"label": clean_text(point.get("label")), **_pick(point, TREND_KEYS)}
        for point in points[-TREND_POINTS:]
    ]


def _reminders_full(info: Any) -> bool:
    return isinstance(info, Mapping) and info.get("coverage") == "full"


def reminder_facts(stats: Any) -> dict[str, Any] | None:
    if not isinstance(stats, Mapping):
        return None
    return {**_pick(stats, REMINDER_KEYS), "reaction_horizon_hours": REACTION_HORIZON_HOURS}


# --------------------------------------------------------------------------------------
# Псевдонимы
# --------------------------------------------------------------------------------------


def make_aliases(ids: Iterable[str]) -> dict[str, str]:
    """`{"КАМ-01": id}` по id, отсортированным как строки."""
    return {f"{ALIAS_PREFIX}{index:02d}": kam_id for index, kam_id in enumerate(sorted(ids), 1)}


def _select_kams(
    activity_rows: Mapping[str, Mapping[str, Any]],
    overdue_rows: Mapping[str, Mapping[str, Any]],
    attention_ids: set[str],
) -> list[str]:
    ids = set(activity_rows) | set(overdue_rows)
    if len(ids) <= MAX_KAMS:
        return sorted(ids)

    def rank(kam_id: str) -> tuple[Any, ...]:
        overdue = overdue_rows.get(kam_id) or {}
        activity = activity_rows.get(kam_id) or {}
        return (
            kam_id not in attention_ids and not overdue.get("attention"),
            -(_number(overdue.get("overdue")) or 0),
            -(_number(activity.get("stuck")) or 0),
            kam_id,
        )

    return sorted(sorted(ids, key=rank)[:MAX_KAMS])


def _sums(rows: Iterable[Mapping[str, Any]], keys: Sequence[str]) -> dict[str, Any]:
    rows = list(rows)
    result: dict[str, Any] = {"kams": len(rows)}
    for key in keys:
        result[key] = sum(_number(row.get(key)) or 0 for row in rows)
    return result


# --------------------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------------------


def build_team_facts(summary: Mapping[str, Any], sla: Mapping[str, Any]) -> Facts:
    """Факты по команде: «Сводка для заказчика» (`kam-activity/summary`) + «Просрочки»."""
    activity_rows = {
        str(row["kam_id"]): row for row in summary.get("by_kam") or () if row.get("kam_id")
    }
    overdue_rows = {str(row["kam_id"]): row for row in sla.get("kams") or () if row.get("kam_id")}
    attention_block = sla.get("attention") or {}
    attention_ids = {
        str(item["kam_id"]) for item in attention_block.get("items") or () if item.get("kam_id")
    }
    selected = _select_kams(activity_rows, overdue_rows, attention_ids)
    aliases = make_aliases(selected)
    alias_of = {kam_id: alias for alias, kam_id in aliases.items()}

    kpi = summary.get("kpi") or {}
    activity: dict[str, Any] = {
        "kpi": _kpi(kpi, ACTIVITY_KPI_KEYS),
        "stages": activity_stages(summary),
        "kams": [
            {"kam": alias_of[kam_id], **_pick(activity_rows[kam_id], ACTIVITY_KAM_KEYS)}
            for kam_id in selected
            if kam_id in activity_rows
        ],
        "risks": {
            "stuck": _number((kpi.get("stuck") or {}).get("value")),
            "untouched_assigned": len(summary.get("untouched_assigned") or ()),
            "lost": _number((kpi.get("lost") or {}).get("value")),
        },
    }
    others_activity = [row for kam_id, row in activity_rows.items() if kam_id not in alias_of]
    if others_activity:
        activity["others"] = _sums(others_activity, ACTIVITY_SUM_KEYS)

    sla_stages = [
        (workflow_type, stage)
        for workflow_type, stages in (sla.get("stages") or {}).items()
        for stage in stages or ()
    ]
    overdue: dict[str, Any] = {
        "kpi": _kpi(sla.get("kpi"), OVERDUE_KPI_KEYS, compare=True),
        "aging": aging_facts(sla),
        "by_workflow": {
            _workflow_label(code): _pick(row, BY_WORKFLOW_KEYS)
            for code, row in (sla.get("by_workflow") or {}).items()
            if _workflow_label(code)
        },
        "stages": overdue_stages(sla_stages),
        "trend": trend_facts(sla),
        "kams": [
            {
                "kam": alias_of[kam_id],
                **_pick(overdue_rows[kam_id], OVERDUE_KAM_KEYS),
                "delta_overdue_pct": _number(
                    (overdue_rows[kam_id].get("delta") or {}).get("overdue_pct")
                ),
                "attention": bool(overdue_rows[kam_id].get("attention")),
            }
            for kam_id in selected
            if kam_id in overdue_rows
        ],
        "attention": {
            "rule": ATTENTION_RULE,
            "team_own_overdue_pct": _number(attention_block.get("team_own_overdue_pct")),
            "threshold_pct": _number(attention_block.get("threshold_pct")),
            "kams": [
                alias_of[str(item["kam_id"])]
                for item in attention_block.get("items") or ()
                if str(item.get("kam_id")) in alias_of
            ],
        },
    }
    others_overdue = [row for kam_id, row in overdue_rows.items() if kam_id not in alias_of]
    if others_overdue:
        overdue["others"] = _sums(others_overdue, OVERDUE_SUM_KEYS)
    reminders = sla.get("reminders") or {}
    if _reminders_full(reminders.get("info")):
        stats = reminder_facts(reminders.get("team"))
        if stats is not None:
            overdue["reminders"] = stats

    facts = {
        "report": "team",
        "period": period_facts(sla),
        "filters": filter_facts(
            summary.get("params") or {}, summary.get("directions"), kind="team"
        ),
        "activity": activity,
        "overdue": overdue,
    }
    return Facts(facts=facts, aliases=aliases)


def build_kam_facts(
    kam_id: str, activity_summary: Mapping[str, Any], sla_kam: Mapping[str, Any]
) -> Facts:
    """Факты по КАМу: «Сводка для заказчика» с фильтром по нему + разбор «Просрочек»."""
    aliases = make_aliases([str(kam_id)])
    (alias,) = aliases
    overdue: dict[str, Any] = {
        "kpi": _kpi(sla_kam.get("kpi"), OVERDUE_KPI_KEYS, compare=True),
        "aging": aging_facts(sla_kam),
        "stages": overdue_stages(
            (stage.get("workflow_type"), stage) for stage in sla_kam.get("stages") or ()
        ),
        "trend": trend_facts(sla_kam),
    }
    reminders = sla_kam.get("reminders") or {}
    if _reminders_full(reminders.get("info")):
        stats = reminder_facts(reminders)
        if stats is not None:
            overdue["reminders"] = stats
    team = sla_kam.get("team")
    if isinstance(team, Mapping):  # при области «только свои» справки команды нет
        overdue["team"] = _pick(team, TEAM_REF_KEYS)
    facts = {
        "report": "kam",
        "kam": alias,
        "period": period_facts(sla_kam),
        "filters": filter_facts(
            sla_kam.get("params") or {}, activity_summary.get("directions"), kind="kam"
        ),
        "activity": {
            "kpi": _kpi(activity_summary.get("kpi"), ACTIVITY_KPI_KEYS),
            "stages": activity_stages(activity_summary),
        },
        "overdue": overdue,
    }
    return Facts(facts=facts, aliases=aliases)
