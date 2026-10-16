"""Выгрузка вкладки «Просрочки»: XLSX, PDF «Сроки этапов: просрочки», CSV/JSON.

Вызывается механизмом выгрузок `POST /export/jobs {entity_type: "report:sla"}` (MinIO,
аудит `export.created`, журнал выгрузок, та же область видимости, что у API). Файл —
`overdue_{ГГГГММДД-ГГГГММДД}.{ext}`, поле `rows` — число просроченных заявок.
- XLSX (`_Sheet`: жирная шапка, закреплённая строка, автофильтр, защита CWE-1236):
  «Сводка» (в A1 — строка-итог), «КАМы», «Этапы», «Просрочено сейчас», «Опоздание
  закрытых этапов», «Тренд», «Напоминания» (только если журнал не пуст), «Как считается»;
- PDF (fpdf2): `options.by_kam=false` — «Сводка без имён» (о правиле отбора — только
  число КАМов), `true` — с фактами по КАМам, таблицей КАМов и просроченными заявками
  (до 20 на КАМа, остальное — в XLSX). При области «только свои» — один вариант: разбор
  себя. Модель страниц `pdf_sections` строится без fpdf и проверяется тестами;
- CSV/JSON — плоский список просроченных заявок общими писателями выгрузки.

Тексты — те же, что в UI («Как считается», правило отбора, оговорки): только факты.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AppUser
from app.modules.reporting.kam_activity import (
    REPORT_TZ,
    WORKFLOW_LABELS,
    KamActivityParams,
    _resolve_scope,
    viewer_scope,
)
from app.modules.reporting.kam_activity_export import (
    _DATE_FORMAT,
    _PCT_FORMAT,
    ExportMeta,
    _json_ready,
    _msk_date,
    _num,
    _Sheet,
    _signed,
    period_text,
)
from app.modules.reporting.pdf import (
    DANGER,
    MUTED,
    TEXT,
    WARNING,
    Column,
    KpiItem,
    make_report_pdf,
)
from app.modules.reporting.sla import (
    ATTENTION_RULE,
    BUCKET_KEYS,
    BUCKET_LABELS,
    build_sla,
    build_sla_kam,
)

REPORT_TITLE = "Сроки этапов: просрочки"
FILE_PREFIX = "overdue"
SHEET_NAMES: tuple[str, ...] = (
    "Сводка",
    "КАМы",
    "Этапы",
    "Просрочено сейчас",
    "Опоздание закрытых этапов",
    "Тренд",
    "Напоминания",
    "Как считается",
)
REMINDERS_SHEET = "Напоминания"
PDF_ITEMS_PER_KAM = 20
EMPTY = "—"
_DAYS_FORMAT = "0.0"

NORM_SOURCES = {"stage": "задана для этапа", "workflow": "общая для процесса", "global": "общая"}
ACTION_KINDS = {
    "transition": "переход",
    "created": "создание",
    "comment": "комментарий",
    "file": "файл",
}
EPISODE_STATUSES = {
    "reacted_72h": "действие в течение 72 ч",
    "no_action": "без действий в системе",
    "pending": "ожидает (72 ч не прошли)",
    "undelivered": "не доставлено",
}
COVERAGE_LABELS = {"none": "журнал пуст", "partial": "частично", "full": "полностью"}
# оттенки корзин старения: от «внимание» к «опасно» (как последовательная шкала в UI)
BUCKET_COLORS = {
    "d1_3": "#E9B949",
    "d4_7": WARNING,
    "d8_14": "#D9822B",
    "d15_30": "#C2410C",
    "d31_plus": DANGER,
}

KPI_LABELS: tuple[tuple[str, str, str | None], ...] = (
    ("overdue", "Просрочено", None),
    ("active", "В работе", None),
    ("own_overdue", "Собственные просрочки", None),
    ("inherited", "Получены уже просроченными", None),
    ("overdue_pct", "Доля просроченных, %", _PCT_FORMAT),
    ("delay_median_days", "Сейчас сверх нормы: медиана, дн.", _DAYS_FORMAT),
    ("delay_avg_days", "Сейчас сверх нормы: среднее, дн.", _DAYS_FORMAT),
    ("delay_max_days", "Сейчас сверх нормы: максимум, дн.", _DAYS_FORMAT),
    ("delay_total_days", "Заявко-дней сверх нормы", _DAYS_FORMAT),
    ("overdue_30_plus", "Больше 30 дн. сверх нормы", None),
    ("overdue_no_action", "После срока без действий в системе", None),
    ("decided", "Решено этапов за период", None),
    ("on_time", "Уложились в норму", None),
    ("on_time_pct", "Уложились в норму, %", _PCT_FORMAT),
    ("completed", "Завершено этапов", None),
    ("late_closed", "Закрыто с опозданием", None),
    ("late_delay_avg_days", "Опоздание закрытых этапов: среднее, дн.", _DAYS_FORMAT),
    ("late_delay_median_days", "Опоздание закрытых этапов: медиана, дн.", _DAYS_FORMAT),
    ("inflow", "Поступило за период", None),
)

REMINDER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("visits_reminded", "Визитов с напоминанием"),
    ("visits_escalated", "Эскалировано"),
    ("sent", "Напоминаний"),
    ("delivered", "Доставлено"),
    ("episodes", "Эпизодов"),
    ("matured", "Прошло 72 ч"),
    ("reacted_72h", "Действие в течение 72 ч"),
    ("reacted_72h_pct", "Действие в течение 72 ч, %"),
    ("no_action", "Без действий в системе"),
    ("pending", "Ожидают"),
    ("undelivered", "Не доставлено"),
    ("reaction_median_hours", "Первое действие: медиана, ч"),
    ("reaction_avg_hours", "Первое действие: среднее, ч"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Форматирование
# --------------------------------------------------------------------------------------


def _days_text(value: Any) -> str:
    return EMPTY if value is None else f"{_num(float(value))} дн."


def _pct_text(value: Any) -> str:
    return EMPTY if value is None else f"{_num(float(value))}%"


def _date_text(value: str | None) -> str:
    day = _msk_date(value)
    return day.strftime("%d.%m.%Y") if day else EMPTY


def _place(item: Mapping[str, Any]) -> str:
    university = item.get("university")
    if university:
        return university.get("short_name") or university.get("name") or EMPTY
    client = item.get("client")
    if isinstance(client, Mapping):
        return client.get("display_name") or client.get("name") or EMPTY
    return client or EMPTY


def _link(request_id: str) -> str:
    return f"/reports/kam-activity?tab=sla&request={request_id}"


def describe_filters(report: Mapping[str, Any], *, names: bool = True) -> str:
    """Фильтры текстом; без имён (`names=False`) — только число КАМов фильтра."""
    params = report.get("params") or {}
    scope = report.get("scope") or {}
    parts = ["Процесс: " + WORKFLOW_LABELS.get(params.get("workflow_type") or "", "B2B и B2C")]
    kam_ids = params.get("kam_id") or []
    if scope.get("own_only"):
        parts.append("Только свои заявки")
    elif kam_ids and names:
        known = {row["kam_id"]: row["kam"] for row in report.get("kams", [])}
        known.update({kam["id"]: kam["name"] for kam in [report.get("kam") or {}] if kam})
        parts.append("КАМ: " + ", ".join(known.get(kam_id, kam_id) for kam_id in kam_ids))
    elif kam_ids:
        parts.append(f"КАМ: выбрано {len(kam_ids)}")
    else:
        parts.append("КАМ: все")
    if params.get("university_id"):
        parts.append(f"Вузы: выбрано {len(params['university_id'])}")
    if params.get("direction_id"):
        parts.append(f"ИТ-направления: выбрано {len(params['direction_id'])}")
    return " · ".join(parts)


def _comparison_caption(report: Mapping[str, Any], compare: str) -> str:
    comparison = report.get("comparison") or {}
    key = "snapshot_label" if compare == "snapshot" else "period_label"
    return comparison.get(key, "")


# --------------------------------------------------------------------------------------
# Плоский список (CSV / JSON / лист «Просрочено сейчас»)
# --------------------------------------------------------------------------------------

FLAT_COLUMNS: list[tuple[str, str]] = [
    ("kam", "КАМ"),
    ("title", "Заявка"),
    ("workflow_type", "Процесс"),
    ("place", "Вуз / клиент"),
    ("stage", "Этап"),
    ("stage_since", "На этапе с"),
    ("on_stage_days", "Дней на этапе"),
    ("norm_days", "Норма, дн."),
    ("norm_source", "Источник нормы"),
    ("delay_days", "Сверх нормы, дн."),
    ("bucket", "Корзина"),
    ("overdue_since", "Просрочена с"),
    ("no_action_after_due", "Без действий после срока"),
    ("inherited", "Получена просроченной"),
    ("with_kam_since", "У КАМа с"),
    ("reminders", "Напоминаний"),
    ("delivered", "Доставлено"),
    ("escalated", "Эскалирована"),
    ("last_action_at", "Последнее действие: дата"),
    ("last_action_kind", "Последнее действие: что"),
    ("last_action_actor", "Последнее действие: кто"),
    ("link", "Ссылка"),
]


def _yes(value: bool) -> str:
    return "Да" if value else "Нет"


def sla_flat_rows(report: Mapping[str, Any]) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Просроченные заявки плоской таблицей: колонки и строки (даты — `date`)."""
    rows: list[dict[str, Any]] = []
    for item in (report.get("overdue") or {}).get("items", []):
        last = item.get("last_action") or {}
        rows.append(
            {
                "kam": item["kam"],
                "title": item["title"],
                "workflow_type": WORKFLOW_LABELS.get(item["workflow_type"], item["workflow_type"]),
                "place": _place(item),
                "stage": item["stage"]["name"],
                "stage_since": _msk_date(item["stage_since"]),
                "on_stage_days": item["on_stage_days"],
                "norm_days": item["norm_days"],
                "norm_source": NORM_SOURCES.get(item["norm_source"], item["norm_source"]),
                "delay_days": item["delay_days"],
                "bucket": BUCKET_LABELS.get(item["bucket"], item["bucket"]),
                "overdue_since": _msk_date(item["overdue_since"]),
                "no_action_after_due": _yes(item["no_action_after_due"]),
                "inherited": _yes(item["inherited"]),
                "with_kam_since": _msk_date(item["with_kam_since"]),
                "reminders": item["reminders"],
                "delivered": item["delivered"],
                "escalated": _yes(item["escalated"]),
                "last_action_at": _msk_date(last.get("at")),
                "last_action_kind": ACTION_KINDS.get(last.get("kind") or "", last.get("kind")),
                "last_action_actor": last.get("actor"),
                "link": _link(item["request_id"]),
            }
        )
    return list(FLAT_COLUMNS), rows


# --------------------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------------------


def _write_summary(sheet: _Sheet, report: Mapping[str, Any], meta: ExportMeta) -> None:
    sheet.title(report["headline"])  # A1 — строка-итог, как в UI и PDF
    sheet.subtitle(report["definition"])
    sheet.subtitle(f"{REPORT_TITLE}. {period_text(report)}")
    sheet.subtitle(f"Фильтры: {meta.filters_text}")
    sheet.subtitle(
        f"Сформировал: {meta.generated_by}, "
        f"{meta.generated_at.astimezone(REPORT_TZ):%d.%m.%Y %H:%M} МСК"
    )
    sheet.skip()

    sheet.subtitle("Показатели", bold=True)
    sheet.header(["Показатель", "Сейчас", "Было", "Изменение", "Сравнение"])
    kpi = report.get("kpi") or {}
    for key, label, fmt in KPI_LABELS:
        item = kpi.get(key)
        if item is None:
            continue
        sheet.line(
            [
                label,
                item["value"],
                item["prev"],
                item["delta"],
                _comparison_caption(report, item["compare"]),
            ],
            [None, fmt, fmt, fmt, None],
        )
    sheet.skip()

    sheet.subtitle("Старение просрочек", bold=True)
    sheet.header(["Сверх нормы", "Сейчас", _comparison_caption(report, "snapshot") or "Прошлый"])
    for row in report.get("aging", []):
        sheet.line([row["label"], row["count"], row["prev"]])
    sheet.skip()

    by_workflow = report.get("by_workflow") or {}
    if by_workflow:
        sheet.subtitle("Вузы / Клиенты", bold=True)
        sheet.header(
            ["Процесс", "В работе", "Просрочено", "Доля, %", "Медиана, дн.", "Уложились, %"]
        )
        for row in by_workflow.values():
            sheet.line(
                [
                    row["label"],
                    row["active"],
                    row["overdue"],
                    row["overdue_pct"],
                    row["delay_median_days"],
                    row["on_time_pct"],
                ],
                [None, None, None, _PCT_FORMAT, _DAYS_FORMAT, _PCT_FORMAT],
            )
        sheet.skip()

    inactive = report.get("inactive_owners")
    if inactive:
        sheet.line(
            [
                f"У неактивных сотрудников ({inactive['users']}): в работе {inactive['active']}, "
                f"из них просрочено {inactive['overdue']}"
            ]
        )
        sheet.skip()

    attention = report.get("attention") or {}
    matched = sum(1 for row in report.get("kams", []) if row.get("attention"))
    if attention:
        sheet.subtitle(
            "Просрочек больше, чем по команде, или старше 30 дней: "
            f"{matched} из {attention.get('total_kams', 0)} КАМов",
            bold=True,
        )
        sheet.line([f"Правило: {attention.get('rule') or ATTENTION_RULE}"])
        for item in attention.get("items", []):
            sheet.line([item["kam"], "; ".join(item["facts"])])
        sheet.skip()

    notes = report.get("notes") or []
    if notes:
        sheet.subtitle("Оговорки о данных", bold=True)
        for note in notes:
            sheet.line([note])
    sheet.widths([58, 14, 14, 14, 18, 14])


def _aging_values(aging: Mapping[str, int]) -> list[int]:
    return [aging.get(key, 0) for key in BUCKET_KEYS]


def _write_kams(sheet: _Sheet, report: Mapping[str, Any], *, with_reminders: bool) -> None:
    snapshot = _comparison_caption(report, "snapshot")
    labels = [
        "КАМ",
        "Активен",
        "В работе",
        "Просрочено",
        "Собственные",
        "Получены просроченными",
        "Доля просроченных, %",
        "Мало заявок",
        "Сверх нормы: среднее, дн.",
        "Сверх нормы: медиана, дн.",
        "Сверх нормы: максимум, дн.",
        "Заявко-дней сверх нормы",
        *(f"Сверх нормы {BUCKET_LABELS[key]}" for key in BUCKET_KEYS),
        "Больше 30 дн.",
        "Без действий после срока",
        "Решено этапов",
        "Уложились",
        "Уложились, %",
        "Завершено этапов",
        "Закрыто с опозданием",
        "Из них получены просроченными",
        "Опоздание: среднее, дн.",
        "Опоздание: медиана, дн.",
        "Поступило за период",
        f"Просрочено {snapshot}".strip(),
        "Изменение просроченных",
        "Под правилом отбора",
    ]
    if with_reminders:
        labels += [label for _key, label in REMINDER_COLUMNS]
    header = sheet.header(labels)
    days = _DAYS_FORMAT
    formats = [None] * 6 + [_PCT_FORMAT, None, days, days, days, days]
    formats += [None] * 5 + [None, None, None, None, _PCT_FORMAT, None, None, None, days, days]
    for row in report.get("kams", []):
        prev = row.get("prev") or {}
        delta = row.get("delta") or {}
        values = [
            row["kam"],
            _yes(row["is_active"]),
            row["active"],
            row["overdue"],
            row["own_overdue"],
            row["inherited"],
            row["overdue_pct"],
            _yes(row["small_sample"]),
            row["delay_avg_days"],
            row["delay_median_days"],
            row["delay_max_days"],
            row["delay_total_days"],
            *_aging_values(row["aging"]),
            row["overdue_30_plus"],
            row["overdue_no_action"],
            row["decided"],
            row["on_time"],
            row["on_time_pct"],
            row["completed"],
            row["late_closed"],
            row["late_inherited"],
            row["late_delay_avg_days"],
            row["late_delay_median_days"],
            row["inflow"],
            prev.get("overdue"),
            delta.get("overdue"),
            _yes(row["attention"]),
        ]
        if with_reminders:
            stats = row.get("reminders") or {}
            values += [stats.get(key) for key, _label in REMINDER_COLUMNS]
        sheet.line(values, formats)
    sheet.finish_table(header, len(labels))
    sheet.widths([34] + [12] * (len(labels) - 1))


def _write_stages(sheet: _Sheet, report: Mapping[str, Any]) -> None:
    labels = [
        "Процесс",
        "Этап",
        "Норма, дн.",
        "Источник нормы",
        "В работе",
        "Просрочено",
        "Доля, %",
        *(BUCKET_LABELS[key] for key in BUCKET_KEYS),
        "Медиана, дн.",
        "Среднее, дн.",
        "Заявко-дней сверх нормы",
        "Решено этапов",
        "Уложились",
        "Уложились, %",
        "Завершено",
        "Опоздание: среднее, дн.",
    ]
    header = sheet.header(labels)
    formats = [None] * 6 + [_PCT_FORMAT] + [None] * 5 + [_DAYS_FORMAT] * 3
    formats += [None, None, _PCT_FORMAT, None, _DAYS_FORMAT]
    for workflow_type, rows in (report.get("stages") or {}).items():
        for row in rows:
            sheet.line(
                [
                    WORKFLOW_LABELS.get(workflow_type, workflow_type),
                    row["name"],
                    row["norm_days"],
                    NORM_SOURCES.get(row["norm_source"], row["norm_source"]),
                    row["active"],
                    row["overdue"],
                    row["overdue_pct"],
                    *_aging_values(row["aging"]),
                    row["delay_median_days"],
                    row["delay_avg_days"],
                    row["delay_total_days"],
                    row["decided"],
                    row.get("on_time"),
                    row["on_time_pct"],
                    row["completed"],
                    row["late_delay_avg_days"],
                ],
                formats,
            )
    sheet.finish_table(header, len(labels))
    sheet.widths([10, 30, 10, 20] + [11] * (len(labels) - 4))


def _write_overdue(sheet: _Sheet, report: Mapping[str, Any]) -> int:
    columns, rows = sla_flat_rows(report)
    header = sheet.header([label for _key, label in columns])
    formats: list[str | None] = []
    for key, _label in columns:
        if key in ("stage_since", "overdue_since", "with_kam_since", "last_action_at"):
            formats.append(_DATE_FORMAT)
        elif key in ("on_stage_days", "delay_days"):
            formats.append(_DAYS_FORMAT)
        else:
            formats.append(None)
    for row in rows:
        sheet.line([row[key] for key, _label in columns], formats)
    sheet.finish_table(header, len(columns))
    sheet.widths(
        [28, 40, 7, 20, 24, 12, 10, 9, 18, 11, 13, 12, 12, 12, 12, 9, 9, 9, 12, 14, 22, 52]
    )
    return len(rows)


def _write_late(sheet: _Sheet, report: Mapping[str, Any]) -> None:
    labels = [
        "КАМ",
        "Заявка",
        "Процесс",
        "Вуз / клиент",
        "Этап",
        "Вход",
        "Выход",
        "Длительность, дн.",
        "Норма, дн.",
        "Опоздание, дн.",
        "Куда вышла",
        "Получена просроченной",
        "Ссылка",
    ]
    header = sheet.header(labels)
    formats = [None] * 5 + [_DATE_FORMAT, _DATE_FORMAT, _DAYS_FORMAT, None, _DAYS_FORMAT]
    for item in (report.get("late_closed") or {}).get("items", []):
        exited = item["exited_to"]
        where = exited["name"] + (" (возврат)" if exited.get("is_return") else "")
        sheet.line(
            [
                item.get("kam"),
                item["title"],
                WORKFLOW_LABELS.get(item.get("workflow_type") or "", item.get("workflow_type")),
                _place(item),
                item["stage"]["name"],
                _msk_date(item["entered"]),
                _msk_date(item["left_at"]),
                item["duration_days"],
                item["norm_days"],
                item["delay_days"],
                where,
                _yes(item["inherited"]),
                _link(item["request_id"]),
            ],
            formats,
        )
    sheet.finish_table(header, len(labels))
    sheet.widths([28, 40, 7, 20, 24, 12, 12, 12, 10, 12, 24, 12, 52])


def _write_trend(sheet: _Sheet, report: Mapping[str, Any]) -> None:
    trend = report.get("trend") or {}
    unit = "Неделя" if trend.get("granularity") == "week" else "Месяц"
    labels = [
        f"{unit} с",
        "по",
        "Дата среза",
        "Корзина закончилась",
        "В периоде отчёта",
        "В работе",
        "Просрочено",
        "Доля, %",
        "Больше 30 дн.",
        "Средняя задержка, дн.",
        "Решено этапов",
        "Уложились",
        "Уложились, %",
    ]
    header = sheet.header(labels)
    formats = [_DATE_FORMAT, _DATE_FORMAT, None, None, None, None, None, _PCT_FORMAT, None]
    formats += [_DAYS_FORMAT, None, None, _PCT_FORMAT]
    for point in trend.get("points", []):
        sheet.line(
            [
                date.fromisoformat(point["start"]),
                date.fromisoformat(point["end"]),
                point["label"],
                _yes(point["complete"]),
                _yes(point["in_period"]),
                point["active"],
                point["overdue"],
                point["overdue_pct"],
                point["overdue_30_plus"],
                point["delay_avg_days"],
                point["decided"],
                point["on_time"],
                point["on_time_pct"],
            ],
            formats,
        )
    sheet.finish_table(header, len(labels))
    sheet.widths([12, 12, 11, 12, 12, 10, 11, 9, 11, 13, 11, 11, 11])


def _write_reminders(sheet: _Sheet, report: Mapping[str, Any]) -> None:
    reminders = report.get("reminders") or {}
    info = reminders.get("info") or {}
    sheet.subtitle("Напоминания о зависших заявках", bold=True)
    sheet.line([info.get("note", "")])
    journal_from = info.get("journal_from")
    sheet.line(
        [
            "Журнал ведётся с",
            date.fromisoformat(journal_from).strftime("%d.%m.%Y") if journal_from else EMPTY,
        ]
    )
    sheet.line(["Покрытие периода", COVERAGE_LABELS.get(info.get("coverage") or "", EMPTY)])
    sheet.line(["Проверка зависших", "включена" if info.get("check_enabled") else "выключена"])
    sheet.line(["Правило для ответственного", "есть" if info.get("rules_active") else "нет"])
    sheet.skip()
    header = sheet.header(["КАМ", *(label for _key, label in REMINDER_COLUMNS)])
    first = header
    team = reminders.get("team")
    if team:
        sheet.line(["Команда", *(team.get(key) for key, _label in REMINDER_COLUMNS)])
    for row in report.get("kams", []):
        stats = row.get("reminders") or {}
        sheet.line([row["kam"], *(stats.get(key) for key, _label in REMINDER_COLUMNS)])
    sheet.skip()
    items = reminders.get("items") or []
    if items:
        sheet.subtitle("Эпизоды: первое напоминание в периоде", bold=True)
        sheet.header(
            [
                "КАМ",
                "Заявка",
                "Этап",
                "Первое напоминание",
                "Доставлено",
                "Статус",
                "Первое действие",
                "Что",
                "Через, ч",
            ]
        )
        for item in items:
            sheet.line(
                [
                    item.get("kam"),
                    item["title"],
                    item["stage"]["name"],
                    _msk_date(item["first_reminder_at"]),
                    _msk_date(item["delivered_at"]),
                    EPISODE_STATUSES.get(item["status"], item["status"]),
                    _msk_date(item["reaction_at"]),
                    ACTION_KINDS.get(item["reaction_kind"] or "", item["reaction_kind"]),
                    item["reaction_hours"],
                ],
                [None, None, None, _DATE_FORMAT, _DATE_FORMAT, None, _DATE_FORMAT, None, None],
            )
    sheet.sheet.freeze_panes = f"A{first + 1}"
    sheet.widths([30, 40, 24, 16, 14, 24, 16, 14, 12, 12, 12, 14, 14, 14])


def _write_definitions(sheet: _Sheet, report: Mapping[str, Any]) -> None:
    sheet.title("Как считается")
    sheet.line([report.get("definition", "")])
    sheet.skip()
    sheet.header(["Термин", "Определение"])
    for item in report.get("definitions", []):
        sheet.line([item["term"], item["text"]])
    sheet.skip()
    sheet.line(["Правило отбора", ATTENTION_RULE])
    notes = report.get("notes") or []
    if notes:
        sheet.skip()
        sheet.subtitle("Оговорки о данных", bold=True)
        for note in notes:
            sheet.line([note])
    sheet.widths([28, 110])


def sheet_names(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Листы книги: «Напоминания» — только если журнал напоминаний не пуст."""
    coverage = ((report.get("reminders") or {}).get("info") or {}).get("coverage")
    if coverage == "none":
        return tuple(name for name in SHEET_NAMES if name != REMINDERS_SHEET)
    return SHEET_NAMES


def render_sla_xlsx(report: Mapping[str, Any], meta: ExportMeta) -> bytes:
    import openpyxl

    names = sheet_names(report)
    with_reminders = REMINDERS_SHEET in names
    workbook = openpyxl.Workbook()
    sheets = {name: workbook.create_sheet(name) for name in names}
    workbook.remove(workbook.active)
    _write_summary(_Sheet(sheets["Сводка"]), report, meta)
    _write_kams(_Sheet(sheets["КАМы"]), report, with_reminders=with_reminders)
    _write_stages(_Sheet(sheets["Этапы"]), report)
    _write_overdue(_Sheet(sheets["Просрочено сейчас"]), report)
    _write_late(_Sheet(sheets["Опоздание закрытых этапов"]), report)
    _write_trend(_Sheet(sheets["Тренд"]), report)
    if with_reminders:
        _write_reminders(_Sheet(sheets[REMINDERS_SHEET]), report)
    _write_definitions(_Sheet(sheets["Как считается"]), report)
    workbook.active = 0
    workbook.properties.title = REPORT_TITLE
    workbook.properties.creator = "Альма"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------------------
# PDF: модель страниц (без fpdf) и отрисовка
# --------------------------------------------------------------------------------------

Section = dict[str, Any]


def _kpi(
    report: Mapping[str, Any],
    key: str,
    label: str,
    *,
    pct: bool = False,
    inverted: bool = False,
    hint: str | None = None,
) -> KpiItem:
    item = (report.get("kpi") or {}).get(key) or {}
    value = item.get("value")
    shown = _pct_text(value) if pct else _num(value)
    delta = item.get("delta")
    caption = _comparison_caption(report, item.get("compare") or "snapshot")
    if delta is None or delta == 0:
        note = hint or ("нет базы для сравнения" if delta is None else f"без изменений {caption}")
        return KpiItem(label, shown, None, None, inverted, note)
    unit = " п.п." if pct else ""
    return KpiItem(label, shown, f"{_signed(delta, unit)} {caption}", delta > 0, inverted, hint)


def _kpi_cards(report: Mapping[str, Any]) -> list[KpiItem]:
    kpi = report.get("kpi") or {}
    active = (kpi.get("active") or {}).get("value") or 0
    overdue = (kpi.get("overdue") or {}).get("value") or 0
    decided = (kpi.get("decided") or {}).get("value") or 0
    on_time = (kpi.get("on_time") or {}).get("value") or 0
    return [
        _kpi(report, "overdue", "Просрочено", inverted=True, hint=f"из {active} в работе"),
        _kpi(
            report,
            "overdue_no_action",
            "После срока без действий",
            inverted=True,
            hint=f"из {overdue} просроченных",
        ),
        _kpi(report, "overdue_30_plus", "Больше 30 дн. сверх нормы", inverted=True),
        _kpi(
            report,
            "on_time_pct",
            "Уложились в норму",
            pct=True,
            hint=f"{on_time} из {decided} этапов",
        ),
    ]


def _stage_bars(stages: Iterable[tuple[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    rows = [(workflow_type, row) for workflow_type, row in stages if row["overdue"]]
    top = max((row["overdue"] for _wf, row in rows), default=0)
    bars = []
    for workflow_type, row in sorted(rows, key=lambda pair: -pair[1]["overdue"]):
        norm = NORM_SOURCES.get(row["norm_source"], row["norm_source"])
        bars.append(
            {
                "label": f"{row['name']} ({WORKFLOW_LABELS.get(workflow_type, workflow_type)})",
                "value": row["overdue"],
                "max": top,
                "color": row.get("color") or DANGER,
                "note": (
                    f"{row['overdue']} из {row['active']} · медиана "
                    f"{_days_text(row['delay_median_days'])} · норма {row['norm_days']} дн. "
                    f"({norm})"
                ),
            }
        )
    return bars


def _stage_pairs(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    stages = report.get("stages") or {}
    if isinstance(stages, Mapping):
        return [(wf, row) for wf, rows in stages.items() for row in rows]
    return [(row.get("workflow_type", ""), row) for row in stages]


def _aging_table(pairs: Sequence[tuple[str, Mapping[str, Any]]]) -> Section:
    columns = [("stage", "Этап", 2.6)] + [(key, BUCKET_LABELS[key], 1.0) for key in BUCKET_KEYS]
    rows = []
    for workflow_type, row in pairs:
        if not row["overdue"]:
            continue
        rows.append(
            {
                "stage": f"{row['name']} ({WORKFLOW_LABELS.get(workflow_type, workflow_type)})",
                **{key: row["aging"].get(key, 0) or EMPTY for key in BUCKET_KEYS},
            }
        )
    return {
        "kind": "table",
        "columns": columns,
        "rows": rows,
        "empty": "Просроченных заявок нет.",
    }


def _trend_table(report: Mapping[str, Any]) -> Section:
    trend = report.get("trend") or {}
    rows = [
        {
            "label": point["label"] + ("" if point["complete"] else " (не закончилась)"),
            "active": point["active"],
            "overdue": point["overdue"],
            "pct": _pct_text(point["overdue_pct"]),
            "over30": point["overdue_30_plus"],
        }
        for point in trend.get("points", [])
    ]
    return {
        "kind": "table",
        "columns": [
            ("label", "На дату", 1.6),
            ("active", "В работе", 1.0),
            ("overdue", "Просрочено", 1.0),
            ("pct", "%", 0.8),
            ("over30", "Больше 30 дн.", 1.1),
        ],
        "rows": rows,
        "empty": "Точек тренда нет.",
    }


def _aging_bars(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    aging = report.get("aging") or []
    top = max((row["count"] for row in aging), default=0)
    caption = _comparison_caption(report, "snapshot")
    return [
        {
            "label": row["label"],
            "value": row["count"],
            "max": top,
            "color": BUCKET_COLORS.get(row["key"], DANGER),
            "note": f"{row['count']} ({caption}: {EMPTY if row['prev'] is None else row['prev']})",
        }
        for row in aging
    ]


def _overdue_rows(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": item["title"],
            "place": _place(item),
            "stage": item["stage"]["name"],
            "days": f"{_num(item['on_stage_days'])} / {item['norm_days']}",
            "delay": f"+{_num(item['delay_days'])}",
            "flags": ", ".join(
                flag
                for flag, on in (
                    ("без действий после срока", item["no_action_after_due"]),
                    ("получена просроченной", item["inherited"]),
                )
                if on
            )
            or EMPTY,
        }
        for item in items
    ]


_OVERDUE_COLUMNS = [
    ("title", "Заявка", 3.2),
    ("place", "Вуз / клиент", 1.5),
    ("stage", "Этап", 1.8),
    ("days", "На этапе / норма", 1.3),
    ("delay", "Сверх нормы", 1.0),
    ("flags", "Отметки", 2.2),
]


def _closing_sections(report: Mapping[str, Any]) -> list[Section]:
    sections: list[Section] = [{"kind": "section", "title": "Как считается"}]
    sections.append({"kind": "paragraph", "text": report.get("definition", "")})
    for item in report.get("definitions", []):
        sections.append({"kind": "paragraph", "text": f"{item['term']}. {item['text']}"})
    notes = report.get("notes") or []
    if notes:
        sections.append({"kind": "section", "title": "Оговорки о данных"})
        sections.append({"kind": "bullets", "lines": list(notes)})
    return sections


def pdf_sections(
    report: Mapping[str, Any], by_kam: bool, meta: ExportMeta | None = None
) -> list[Section]:
    """Модель PDF по командному отчёту: «Сводка без имён» (`by_kam=False`) или по КАМам.

    В сводке без имён нет ни одного ФИО — ни КАМов, ни автора выгрузки (кто выгрузил,
    видно в журнале выгрузок и аудите `export.created`): её можно переслать как
    обезличенную."""
    filters = describe_filters(report, names=by_kam)
    lines = [period_text(report), f"Фильтры: {filters}"]
    if meta is not None and by_kam:
        lines.append(f"Сформировал: {meta.generated_by}")
    pairs = _stage_pairs(report)
    sections: list[Section] = [
        {"kind": "title", "title": REPORT_TITLE, "lines": lines},
        {"kind": "lead", "text": report["headline"]},
        {"kind": "paragraph", "text": report["definition"]},
        {"kind": "kpis", "items": _kpi_cards(report)},
        {
            "kind": "section",
            "title": "Где застревают",
            "note": "Число просроченных по этапам на дату среза; ось — от нуля.",
        },
        {"kind": "bars", "rows": _stage_bars(pairs), "empty": "Просроченных заявок нет."},
        _aging_table(pairs),
        {
            "kind": "section",
            "title": "Тренд",
            "note": "Срезы на конец недели (воскресенье, МСК) или месяца; доля — при 5 и более "
            "заявках в работе.",
        },
        _trend_table(report),
        {"kind": "page"},
        {"kind": "section", "title": "Старение", "note": "Задержка сверх нормы на дату среза."},
        {"kind": "bars", "rows": _aging_bars(report), "empty": "Просроченных заявок нет."},
    ]
    by_workflow = report.get("by_workflow") or {}
    if by_workflow:
        sections.append({"kind": "section", "title": "Вузы / Клиенты"})
        sections.append(
            {
                "kind": "table",
                "columns": [
                    ("label", "Процесс", 1.8),
                    ("active", "В работе", 1.0),
                    ("overdue", "Просрочено", 1.0),
                    ("pct", "Доля", 0.9),
                    ("median", "Медиана сверх нормы", 1.4),
                    ("on_time", "Уложились в норму", 1.3),
                ],
                "rows": [
                    {
                        "label": row["label"],
                        "active": row["active"],
                        "overdue": row["overdue"],
                        "pct": _pct_text(row["overdue_pct"]),
                        "median": _days_text(row["delay_median_days"]),
                        "on_time": _pct_text(row["on_time_pct"]),
                    }
                    for row in by_workflow.values()
                ],
                "empty": "Нет данных.",
            }
        )
    inactive = report.get("inactive_owners")
    if inactive:
        sections.append(
            {
                "kind": "paragraph",
                "text": f"У неактивных сотрудников: в работе {inactive['active']}, из них "
                f"просрочено {inactive['overdue']}.",
            }
        )
    attention = report.get("attention") or {}
    kams = report.get("kams") or []
    matched = sum(1 for row in kams if row.get("attention"))
    total = attention.get("total_kams", 0)
    if not by_kam:
        sections.append(
            {
                "kind": "section",
                "title": f"Просрочек больше, чем по команде: {matched} из {total} КАМов",
                "note": attention.get("rule") or ATTENTION_RULE,
            }
        )
    else:
        sections.append(
            {
                "kind": "section",
                "title": "Просрочек больше, чем по команде, или старше 30 дней: "
                f"{matched} из {total} КАМов",
                "note": attention.get("rule") or ATTENTION_RULE,
            }
        )
        items = attention.get("items") or []
        sections.append(
            {
                "kind": "bullets",
                "lines": [f"{item['kam']}: {'; '.join(item['facts'])}" for item in items]
                or ["Нет КАМов, подходящих под правило."],
            }
        )
        sections.append({"kind": "section", "title": "КАМы"})
        sections.append(
            {
                "kind": "table",
                "columns": [
                    ("kam", "КАМ", 2.8),
                    ("active", "В работе", 0.9),
                    ("overdue", "Просрочено", 1.3),
                    ("inherited", "Получено при передаче", 1.2),
                    ("no_action", "Без действий после срока", 1.3),
                    ("delay", "Сверх нормы: ср. / мед. / макс.", 2.0),
                    ("on_time", "Уложились", 1.2),
                ],
                "rows": [
                    {
                        "kam": row["kam"] + ("" if row["is_active"] else " (неактивен)"),
                        "active": row["active"],
                        "overdue": f"{row['overdue']} · {_pct_text(row['overdue_pct'])}",
                        "inherited": row["inherited"] or EMPTY,
                        "no_action": row["overdue_no_action"] or EMPTY,
                        "delay": " / ".join(
                            _num(row[key])
                            for key in ("delay_avg_days", "delay_median_days", "delay_max_days")
                        ),
                        "on_time": f"{row['on_time']} из {row['decided']}"
                        if row["decided"]
                        else EMPTY,
                    }
                    for row in kams
                    if row["active"] or row["decided"]
                ],
                "empty": "КАМов с заявками в работе нет.",
            }
        )
        items_by_kam: dict[str, list[Mapping[str, Any]]] = {}
        for item in (report.get("overdue") or {}).get("items", []):
            items_by_kam.setdefault(item["kam_id"], []).append(item)
        if items_by_kam:
            sections.append(
                {
                    "kind": "section",
                    "title": "Просроченные заявки по КАМам",
                    "note": f"До {PDF_ITEMS_PER_KAM} заявок на КАМа, по убыванию задержки; "
                    "полный список — в XLSX.",
                    "keep": 60,
                }
            )
        names = {row["kam_id"]: row["kam"] for row in kams}
        for kam_id, items in items_by_kam.items():
            shown = items[:PDF_ITEMS_PER_KAM]
            sections.append(
                {
                    "kind": "section",
                    "title": names.get(kam_id) or shown[0]["kam"],
                    "note": f"Просрочено {len(items)}",
                    "keep": 40,
                }
            )
            sections.append(
                {"kind": "table", "columns": _OVERDUE_COLUMNS, "rows": _overdue_rows(shown)}
            )
            if len(items) > len(shown):
                sections.append(
                    {"kind": "paragraph", "text": f"и ещё {len(items) - len(shown)} — в XLSX"}
                )
    sections.extend(_closing_sections(report))
    return sections


def pdf_kam_sections(report: Mapping[str, Any], meta: ExportMeta | None = None) -> list[Section]:
    """Модель PDF разбора КАМа (вариант для области «только свои»)."""
    lines = [period_text(report), f"Фильтры: {describe_filters(report)}"]
    if meta is not None:
        lines.append(f"Сформировал: {meta.generated_by}")
    kam = report.get("kam") or {}
    overdue = (report.get("overdue") or {}).get("items", [])
    sections: list[Section] = [
        {"kind": "title", "title": f"{REPORT_TITLE} — {kam.get('name', '')}", "lines": lines},
        {"kind": "lead", "text": report["headline"]},
        {"kind": "paragraph", "text": report["definition"]},
        {"kind": "kpis", "items": _kpi_cards(report)},
        {"kind": "section", "title": "Старение", "note": "Задержка сверх нормы на дату среза."},
        {"kind": "bars", "rows": _aging_bars(report), "empty": "Просроченных заявок нет."},
        {
            "kind": "section",
            "title": "Просроченные заявки",
            "note": "По убыванию задержки; «на этапе / норма» — в днях.",
        },
        {
            "kind": "table",
            "columns": _OVERDUE_COLUMNS,
            "rows": _overdue_rows(overdue),
            "empty": "Просроченных заявок нет.",
        },
        {"kind": "section", "title": "Уложились в норму по этапам"},
        {
            "kind": "table",
            "columns": [
                ("stage", "Этап", 2.6),
                ("decided", "Решено", 1.0),
                ("on_time", "Уложились", 1.0),
                ("pct", "%", 0.8),
            ],
            "rows": [
                {
                    "stage": f"{row['name']} ({WORKFLOW_LABELS.get(workflow_type, workflow_type)})",
                    "decided": row["decided"],
                    "on_time": row.get("on_time"),
                    "pct": _pct_text(row["on_time_pct"]),
                }
                for workflow_type, row in _stage_pairs(report)
                if row["decided"]
            ],
            "empty": "Решённых этапов за период нет.",
        },
        {"kind": "section", "title": "Тренд"},
        _trend_table(report),
    ]
    late = (report.get("late_closed") or {}).get("items", [])
    sections.append({"kind": "section", "title": "Опоздание закрытых этапов"})
    sections.append(
        {
            "kind": "table",
            "columns": [
                ("title", "Заявка", 3.0),
                ("stage", "Этап", 1.8),
                ("left", "Выход", 1.1),
                ("delay", "Опоздание", 1.0),
                ("to", "Куда вышла", 1.8),
            ],
            "rows": [
                {
                    "title": item["title"],
                    "stage": item["stage"]["name"],
                    "left": _date_text(item["left_at"]),
                    "delay": f"+{_num(item['delay_days'])}",
                    "to": item["exited_to"]["name"],
                }
                for item in late
            ],
            "empty": "Закрытых с опозданием этапов нет.",
        }
    )
    reminders = report.get("reminders") or {}
    info = reminders.get("info") or {}
    sections.append({"kind": "section", "title": "Напоминания"})
    if info.get("coverage") == "none":
        sections.append({"kind": "paragraph", "text": "Журнал напоминаний пуст."})
    else:
        sections.append(
            {
                "kind": "bullets",
                "lines": [
                    f"Доставлено {reminders.get('delivered', 0)} из {reminders.get('sent', 0)} · "
                    f"визитов с напоминанием {reminders.get('visits_reminded', 0)} · "
                    f"эскалировано {reminders.get('visits_escalated', 0)}",
                    "Первое действие в системе после напоминания: в течение 72 ч — "
                    f"{reminders.get('reacted_72h', 0)} из {reminders.get('matured', 0)}; "
                    f"медиана {_num(reminders.get('reaction_median_hours'))} ч; ожидают — "
                    f"{reminders.get('pending', 0)}",
                    info.get("note", ""),
                ],
            }
        )
    sections.extend(_closing_sections(report))
    return sections


def section_texts(sections: Iterable[Section]) -> list[str]:
    """Все строки модели PDF (для проверок «без имён» и «без советов»)."""
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, KpiItem):
            texts.extend(v for v in (value.label, value.value, value.delta, value.hint) if v)
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(list(sections))
    return texts


def render_sections_pdf(sections: Sequence[Section], meta: ExportMeta) -> bytes:
    generated = f"{meta.generated_at.astimezone(REPORT_TZ):%d.%m.%Y %H:%M} МСК"
    pdf = make_report_pdf(title=REPORT_TITLE, generated=generated)
    for section in sections:
        kind = section["kind"]
        if kind == "title":
            pdf.title_block(section["title"], section.get("lines", []))
        elif kind == "lead":
            pdf.paragraph(section["text"], size=10.5, color=TEXT)
            pdf.ln(1.5)
        elif kind == "paragraph":
            pdf.paragraph(section["text"], size=8)
            pdf.ln(1)
        elif kind == "kpis":
            pdf.kpi_grid(section["items"])
        elif kind == "section":
            pdf.section(section["title"], section.get("note"), keep=section.get("keep", 18))
        elif kind == "bars":
            rows = section["rows"]
            if not rows:
                pdf.paragraph(section.get("empty", ""), color=MUTED)
            for row in rows:
                pdf.hbar(row["label"], row["value"], row["max"], row["color"], row["note"])
            pdf.ln(2)
        elif kind == "table":
            pdf.data_table(
                [Column(key, label, weight) for key, label, weight in section["columns"]],
                section["rows"],
                empty_text=section.get("empty", "Нет данных"),
            )
        elif kind == "bullets":
            pdf.bullets([line for line in section["lines"] if line])
        elif kind == "page":
            pdf.add_page()
    return pdf.to_bytes()


def render_sla_pdf(report: Mapping[str, Any], meta: ExportMeta, *, by_kam: bool = False) -> bytes:
    return render_sections_pdf(pdf_sections(report, by_kam, meta), meta)


def render_sla_kam_pdf(report: Mapping[str, Any], meta: ExportMeta) -> bytes:
    return render_sections_pdf(pdf_kam_sections(report, meta), meta)


# --------------------------------------------------------------------------------------
# Точка входа выгрузки
# --------------------------------------------------------------------------------------

_EXTENSIONS = {"xlsx": "xlsx", "pdf": "pdf", "csv": "csv", "json": "json"}


def file_name(report: Mapping[str, Any], export_format: str) -> str:
    period = report["period"]
    stamp = f"{date.fromisoformat(period['from']):%Y%m%d}-{date.fromisoformat(period['to']):%Y%m%d}"
    return f"{FILE_PREFIX}_{stamp}.{_EXTENSIONS[export_format]}"


async def build_sla_file(
    session: AsyncSession,
    *,
    user: AppUser,
    filters: Mapping[str, Any],
    export_format: str,
    options: Mapping[str, Any] | None = None,
) -> tuple[bytes, int, str]:
    """Файл «Просрочек»: `(байты, просроченных заявок, имя файла)`; область — как у API.

    `options.by_kam` (PDF) — «По КАМам» вместо «Сводки без имён». При области «только
    свои» PDF один — разбор себя, и считается только он (без командного отчёта)."""
    if export_format not in _EXTENSIONS:
        from app.core.errors import unprocessable

        raise unprocessable(f"Формат «{export_format}» не поддерживается для «Просрочек»")
    opts = options or {}
    params = KamActivityParams.from_filters(filters)
    by_kam = bool(opts.get("by_kam"))

    scope = viewer_scope(user)
    if export_format == "pdf" and scope.own_user_id is not None:
        # «только свои» — один вариант PDF: разбор себя; 403 на чужой kam_id — как у API
        _resolve_scope(user, params, scope)
        kam_report = await build_sla_kam(
            session,
            user,
            user.id,
            dataclasses.replace(params, kam_ids=()),
            scope=scope,
            use_cache=False,
            limit_items=False,
        )
        meta = ExportMeta(
            generated_by=user.full_name or user.username,
            generated_at=_now(),
            filters_text=describe_filters(kam_report),
        )
        data = await asyncio.to_thread(render_sla_kam_pdf, kam_report, meta)
        return data, int(kam_report["overdue"]["total"]), file_name(kam_report, export_format)

    report = await build_sla(
        session,
        user,
        params,
        scope=scope,
        use_cache=False,
        include_late=True,
        include_reminders=True,
        limit_items=False,
    )
    name = file_name(report, export_format)
    rows = int(report["overdue"]["total"])

    if export_format in ("csv", "json"):
        from app.modules.import_export.export import render_export

        columns, flat = sla_flat_rows(report)
        data = await asyncio.to_thread(
            render_export,
            columns,
            _json_ready(flat) if export_format == "json" else flat,
            export_format=export_format,
            encoding=(opts.get("encoding") or "utf-8").lower(),
            delimiter=opts.get("csv_delimiter") or ";",
        )
        return data, rows, name

    meta = ExportMeta(
        generated_by=user.full_name or user.username,
        generated_at=_now(),
        filters_text=describe_filters(report, names=by_kam or export_format == "xlsx"),
    )
    if export_format == "xlsx":
        data = await asyncio.to_thread(render_sla_xlsx, report, meta)
        return data, rows, name
    data = await asyncio.to_thread(render_sla_pdf, report, meta, by_kam=by_kam)
    return data, rows, name
