"""Вкладка «Просрочки» отчёта «Активность КАМов» (api-contract.md §7.2.2, ux.md §6.4).

«Тикет» — визит заявки на этапе: от входа в этап до выхода из него. Срок визиту задаёт
норма этапа — существующий порог зависания (этап → процесс → глобальная настройка), по
которому работают доска, бот и отчёт КАМов. В коде отчёт называется `sla`, в интерфейсе,
PDF и именах файлов — «Просрочки» (`overdue_*`).

Определения (один текст для UI, XLSX и PDF — `DEFINITIONS`):
- период P — `[from 00:00 МСК; to+1 00:00 МСК)`, срез T = `as_of = min(конец, сейчас)`;
  прошлый период — той же длины перед текущим, его срез T_prev — начало P («на 01.10»);
- визит — `[entered, left)`: вход — строка истории со сменой этапа, кроме перевода схемы
  (`stage_entry_condition`, как у отчёта КАМов и доски). Строки `system_migration` делят
  визит на подотрезки: этап и норма берутся на момент среза; перевод на терминальный этап
  завершает визит (`active_end`), но в «Уложились в норму» не попадает. Для текущего
  периода открытый визит начинается с `deal.stage_entered_at` (как доска и бот);
- просрочен на t — визит активен и `t − entered > N(этап на t)`, строго, по точному
  времени: совпадает с «зависшими» отчёта КАМов, фильтром доски `?stuck=true` и ботом;
- задержка — `(t − entered) − N`; корзины — по целым суткам вверх: 1–3, 4–7, 8–14,
  15–30, больше 30;
- решение по визиту — момент истечения нормы («не уложились») либо ручной выход до срока
  («уложились»); «Уложились в норму» считается по решениям в периоде, поэтому этап, который
  ещё висит после срока, тоже входит в знаменатель (без ошибки выжившего);
- атрибуция — на дату: владелец по `audit_log request.assigned` (`OwnerTimeline`), а не
  текущий ответственный. «Получена просроченной» (`inherited`) — владелец на дату получил
  заявку, когда срок уже прошёл; в правило отбора такие заявки не входят;
- напоминания — журнал `notification` (`request.stuck`); реакция — первое собственное
  действие получателя после первого доставленного напоминания, горизонт 72 ч.

Рекомендаций нет: только факты, правила отбора и оговорки о данных (`NO_ADVICE_RE`
проверяет тексты в тестах). Тренд строится разностными массивами по отрезкам
«визит × подотрезок × владелец» (`SnapshotGrid`), медианы и корзины — точным срезом только
для T и T_prev.

Область — `report_scope` и `kam_activity._resolve_scope` (403 на процесс вне области и на
чужой `kam_id` при «только свои»). Кэш — cache-aside на 60 с, ключи `dash:sla:{sha1}` и
`dash:sla-kam:{sha1}`; начало журнала напоминаний кэшируется отдельно на час
(`dash:sla:journal_from`). Выгрузка идёт мимо кэша.
"""

from __future__ import annotations

import math
import re
import statistics
import uuid
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import DealScope
from app.core.cache import get_cache
from app.core.errors import not_found
from app.models.crm import Counterparty, University
from app.models.deal import Deal, DealComment, DealStageHistory
from app.models.service import AppSetting, AuditLog, FileObject, Notification, NotificationRule
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowChangeRequest, WorkflowStage
from app.modules.reporting.kam_activity import (
    DELETED_SUFFIX,
    REPORT_TZ,
    KamActivityParams,
    PeriodBounds,
    PersonRef,
    _apply_deal_filters,
    _cache_key,
    _deal_ids_param,
    _delta,
    _plural,
    _resolve_scope,
    _scope_payload,
    _visible_workflows,
    fetch_people,
    previous_bounds,
    resolve_bounds,
    resolve_params,
    stage_entry_condition,
    viewer_scope,
)
from app.modules.reporting.service import REPORT_CACHE_TTL_SECONDS, _envelope
from app.modules.workflow.service import _global_stuck_default, parse_operations

CODE = "sla"
CODE_KAM = "sla-kam"

DAY = timedelta(days=1)
ZERO = timedelta(0)
THIRTY_DAYS = timedelta(days=30)

# (ключ, подпись) корзин задержки сверх нормы; граница — целые сутки вверх
BUCKETS: tuple[tuple[str, str], ...] = (
    ("d1_3", "1–3 дн."),
    ("d4_7", "4–7 дн."),
    ("d8_14", "8–14 дн."),
    ("d15_30", "15–30 дн."),
    ("d31_plus", "больше 30 дн."),
)
BUCKET_KEYS: tuple[str, ...] = tuple(key for key, _label in BUCKETS)
BUCKET_LABELS: dict[str, str] = dict(BUCKETS)

ATTENTION_MIN_ACTIVE = 3
ATTENTION_MIN_OWN_OVERDUE = 2
ATTENTION_FACTOR = Fraction(3, 2)  # «не меньше чем в 1,5 раза выше, чем по команде»
ATTENTION_LIMIT = 8
FACTS_LIMIT = 6
SMALL_SAMPLE = 3
TREND_MIN_WEEKS = 8
TREND_WEEKLY_MAX_DAYS = 184
TREND_MIN_N = 5
REACTION_HORIZON = timedelta(hours=72)
MAX_OVERDUE_ITEMS = 2000
MAX_KAM_ITEMS = 5000
JOURNAL_FROM_TTL = 3600
JOURNAL_FROM_KEY = "dash:sla:journal_from"

MANUAL_EXITS: frozenset[str] = frozenset({"manual", "return", "outcome"})
WORKFLOW_SHORT = {"b2b": "вузы", "b2c": "клиенты"}
WORKFLOW_TITLES = {"b2b": "Вузы (B2B)", "b2c": "Клиенты (B2C)"}

ATTENTION_RULE = (
    "В списке — КАМы, у которых не меньше 3 заявок в работе, не меньше 2 собственных "
    "просрочек и доля собственных просрочек не меньше чем в 1,5 раза выше, чем по команде; "
    "а также все, у кого есть собственная просрочка больше 30 дней. Заявки, полученные уже "
    "просроченными, не учитываются. Порядок — по доле собственных просрочек, затем по их "
    "числу, затем по сумме задержки. До 8 строк."
)
DEFINITION = (
    "Просрочено — заявка на этапе дольше нормы этапа. Норма задаётся в настройках процесса, "
    "в календарных днях, включая выходные и праздники."
)
# «Как считается» — один текст для UI, XLSX и PDF
DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "Просрочено",
        "Заявка на этапе дольше нормы этапа (строго больше, с точностью до секунды). То же, "
        "что «зависшие» на других вкладках, фильтр доски «Зависшие» и команда бота.",
    ),
    (
        "Норма этапа",
        "Порог зависания из настроек процесса: задан для этапа, иначе общий для процесса. "
        "Календарные дни, включая выходные и праздники.",
    ),
    (
        "Бейдж и напоминание",
        "Бейдж на карточке заявки и первое напоминание считают целые сутки, поэтому "
        "появляются до суток позже, чем заявка попадает в отчёт.",
    ),
    (
        "Действующая норма",
        "Все этапы, в том числе прошлых периодов, оцениваются по норме, которая действует "
        "сейчас. Если норму меняли, это указано в оговорках.",
    ),
    (
        "Этап",
        "Этап на дату среза. Перевод схемы процесса и события LMS время на этапе не обнуляют.",
    ),
    (
        "Чья заявка",
        "Заявка учитывается за тем, кто вёл её на дату среза (по истории передач), а в "
        "«Детализации» — за текущим ответственным. «Получена просроченной» — КАМ получил "
        "заявку, когда срок уже прошёл; такие заявки в правило отбора не входят.",
    ),
    (
        "Только свои заявки",
        "КАМ видит заявки, где он ответственный сейчас; этапы, пройденные до передачи ему "
        "заявки, в его отчёт не попадают.",
    ),
    (
        "Уложились в норму",
        "Из этапов, по которым в периоде наступило решение: заявка ушла с этапа до срока "
        "(уложились) или срок истёк (не уложились). Этапы, которые ещё не дошли до срока, "
        "не учитываются.",
    ),
    (
        "Сейчас сверх нормы",
        "Задержка просроченных заявок на дату среза. Медиана («обычно») — у половины "
        "просрочек задержка меньше.",
    ),
    (
        "Опоздание закрытых этапов",
        "Насколько дольше нормы длился этап, с которого заявку перевели в периоде.",
    ),
    (
        "Без действий после срока",
        "После истечения нормы в заявке не было ручного перехода, комментария или файла — "
        "любого автора.",
    ),
    (
        "Собственное действие",
        "В блоке напоминаний — действие получателя напоминания; в «Активности КАМов» — "
        "текущего ответственного, поэтому после передач числа расходятся.",
    ),
    (
        "Архив",
        "Архивированная заявка исключается из всех периодов, в том числе прошлых.",
    ),
    (
        "Округление",
        "Задержка показывается до 0,1 дня, корзина считается по целым суткам вверх: задержка "
        "чуть больше 3 суток видна как «+3,0 дн.», но попадает в корзину «4–7 дн.».",
    ),
)
REMINDERS_NOTE = (
    "Напоминание — запись журнала уведомлений, включая не доставленные (нет адреса или канал "
    "отключён). Если получатель выключил событие в настройках, запись не создаётся."
)
# советы и оценки в текстах отчёта запрещены; «Заявка стоит на этапе» — не совет
NO_ADVICE_RE = re.compile(
    r"\b(рекоменд\w*|следует|необходимо|нужно|"
    r"стоит\s+(?:обратить|рассмотреть|уделить|проверить|усилить))\b",
    re.IGNORECASE | re.UNICODE,
)

# KPI: ключ → сравнение (остатки — со срезом на начало периода, потоки — с прошлым периодом)
KPI_COMPARE: tuple[tuple[str, str], ...] = (
    ("overdue", "snapshot"),
    ("active", "snapshot"),
    ("own_overdue", "snapshot"),
    ("inherited", "snapshot"),
    ("overdue_pct", "snapshot"),
    ("delay_median_days", "snapshot"),
    ("delay_avg_days", "snapshot"),
    ("delay_max_days", "snapshot"),
    ("delay_total_days", "snapshot"),
    ("overdue_30_plus", "snapshot"),
    ("overdue_no_action", "snapshot"),
    ("decided", "period"),
    ("on_time", "period"),
    ("on_time_pct", "period"),
    ("completed", "period"),
    ("late_closed", "period"),
    ("late_delay_avg_days", "period"),
    ("late_delay_median_days", "period"),
    ("inflow", "period"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Модель: этапы, заявки, визиты, владельцы
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StageInfo:
    stage_id: uuid.UUID
    workflow_type: str
    code: str
    name: str  # без пометки «(удалён)»
    color: str | None
    sort_order: int
    is_terminal: bool
    outcome: str | None
    deleted: bool
    norm_days: int
    norm_source: str  # stage | workflow | global

    @property
    def norm(self) -> timedelta:
        return timedelta(days=self.norm_days)

    @property
    def label(self) -> str:
        return f"{self.name}{DELETED_SUFFIX}" if self.deleted else self.name

    def ref(self) -> dict[str, Any]:
        return {
            "id": str(self.stage_id),
            "code": self.code,
            "name": self.label,
            "color": self.color,
            "deleted": self.deleted,
        }


def norm_cascade(
    stage_days: int | None, workflow_days: int | None, global_days: int
) -> tuple[int, str]:
    """Норма этапа и её источник: этап → процесс → глобальная настройка (как `deal_rows_stmt`)."""
    if stage_days is not None:
        return int(stage_days), "stage"
    if workflow_days is not None:
        return int(workflow_days), "workflow"
    return int(global_days), "global"


@dataclass(frozen=True)
class DealInfo:
    deal_id: uuid.UUID
    title: str
    pipeline: str
    university_id: uuid.UUID | None
    university_name: str | None
    university_short: str | None
    client: str | None
    responsible_id: uuid.UUID
    stage_id: uuid.UUID
    stage_entered_at: datetime
    created_at: datetime

    def university_ref(self) -> dict[str, Any] | None:
        if self.pipeline != "b2b" or self.university_id is None:
            return None
        return {
            "id": str(self.university_id),
            "name": self.university_name or "Вуз без названия",
            "short_name": self.university_short,
        }

    def client_ref(self) -> str | None:
        return self.client if self.pipeline == "b2c" else None

    def place(self) -> str:
        """Подпись «вуз / клиент» для таблиц выгрузки."""
        if self.pipeline == "b2b":
            return self.university_short or self.university_name or "—"
        return self.client or "—"


@dataclass(frozen=True)
class HistoryRow:
    deal_id: uuid.UUID
    at: datetime
    from_stage_id: uuid.UUID | None
    to_stage_id: uuid.UUID
    kind: str
    is_entry: bool
    is_return: bool = False


@dataclass(frozen=True)
class Segment:
    """Подотрезок визита `[start, end)` на одном этапе (end=None — до сих пор)."""

    start: datetime
    end: datetime | None
    stage_id: uuid.UUID
    norm: timedelta
    terminal: bool


@dataclass(frozen=True)
class Visit:
    deal_id: uuid.UUID
    seq: int  # номер визита в заявке
    entered: datetime
    left: datetime | None
    segments: tuple[Segment, ...]
    exit_kind: str  # manual | return | outcome | system_terminal | open
    exit_stage_id: uuid.UUID | None
    active_end: datetime | None  # начало терминального подотрезка или left
    overdue_from: datetime | None  # первый момент просрочки (может быть в будущем)

    @property
    def key(self) -> tuple[uuid.UUID, int]:
        return self.deal_id, self.seq

    @property
    def last_segment(self) -> Segment:
        """Последний нетерминальный подотрезок (до `active_end`)."""
        active = [s for s in self.segments if not s.terminal and _before(s.start, self.active_end)]
        return active[-1] if active else self.segments[0]


def _before(moment: datetime, limit: datetime | None) -> bool:
    return limit is None or moment < limit


def delay_bucket(delay: timedelta | float | int) -> str:
    """Корзина задержки: `k = ceil(задержка / сутки)`; ровно 3 сут — «1–3», 3 сут + 1 с — «4–7»."""
    if not isinstance(delay, timedelta):
        delay = timedelta(seconds=delay)
    days = -((-delay) // DAY)  # целочисленный ceil
    if days <= 3:
        return "d1_3"
    if days <= 7:
        return "d4_7"
    if days <= 14:
        return "d8_14"
    if days <= 30:
        return "d15_30"
    return "d31_plus"


def _exit_kind(row: HistoryRow, stages: Mapping[uuid.UUID, StageInfo]) -> str:
    stage = stages.get(row.to_stage_id)
    if stage is not None and stage.is_terminal:
        return "outcome"
    if row.is_return:
        return "return"
    return "manual"


def _clamp_parts(
    parts: Sequence[tuple[datetime, uuid.UUID]], entered: datetime
) -> list[tuple[datetime, uuid.UUID]]:
    result: list[tuple[datetime, uuid.UUID]] = []
    for start, stage_id in parts:
        start = max(start, entered)
        if result and result[-1][0] == start:
            result[-1] = (start, stage_id)
        else:
            result.append((start, stage_id))
    return result


def make_visit(
    deal_id: uuid.UUID,
    seq: int,
    entered: datetime,
    left: datetime | None,
    segments: Sequence[Segment],
    exit_kind: str,
    exit_stage_id: uuid.UUID | None = None,
) -> Visit:
    """Визит с вычисленными `active_end` и `overdue_from` (минимум по нетерминальным
    подотрезкам значения `max(a_i, entered + N_i)`, если оно раньше конца подотрезка)."""
    terminal_at = next((s.start for s in segments if s.terminal), None)
    active_end = terminal_at if terminal_at is not None else left
    if terminal_at is not None:
        exit_kind = "system_terminal"
    overdue: datetime | None = None
    for segment in segments:
        if segment.terminal or not _before(segment.start, active_end):
            break
        end = segment.end
        if active_end is not None:
            end = active_end if end is None else min(end, active_end)
        candidate = max(segment.start, entered + segment.norm)
        if end is None or candidate < end:
            overdue = candidate  # первый подходящий подотрезок даёт минимум
            break
    return Visit(
        deal_id=deal_id,
        seq=seq,
        entered=entered,
        left=left,
        segments=tuple(segments),
        exit_kind=exit_kind,
        exit_stage_id=exit_stage_id,
        active_end=active_end,
        overdue_from=overdue,
    )


def build_visits(
    rows: Sequence[HistoryRow],
    deal: DealInfo,
    stages: Mapping[uuid.UUID, StageInfo],
    *,
    is_current: bool,
    default_days: int = 14,
) -> list[Visit]:
    """Визиты заявки по строкам истории (строки-входы и переводы схемы, по времени).

    Вход открывает визит и закрывает предыдущий; перевод схемы начинает новый подотрезок;
    строка импорта (from = NULL) — вход. Заявка без строк-входов получает синтетический
    визит `(stage_id, stage_entered_at)`. Для текущего периода открытый визит начинается с
    `deal.stage_entered_at` — как доска и бот.
    """
    drafts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        if row.is_entry:
            if current is not None:
                current["left"] = row.at
                current["exit"] = _exit_kind(row, stages)
                current["exit_stage"] = row.to_stage_id
            current = {
                "entered": row.at,
                "parts": [(row.at, row.to_stage_id)],
                "left": None,
                "exit": "open",
                "exit_stage": None,
            }
            drafts.append(current)
        elif row.kind == "system_migration" and current is not None:
            if row.to_stage_id != current["parts"][-1][1]:
                current["parts"].append((row.at, row.to_stage_id))
    if not drafts:
        drafts.append(
            {
                "entered": deal.stage_entered_at,
                "parts": [(deal.stage_entered_at, deal.stage_id)],
                "left": None,
                "exit": "open",
                "exit_stage": None,
            }
        )
    elif is_current and drafts[-1]["left"] is None:
        last = drafts[-1]
        entered = deal.stage_entered_at
        if len(drafts) > 1 and drafts[-2]["left"] is not None:
            entered = max(entered, drafts[-2]["left"])
        last["entered"] = entered
        last["parts"] = _clamp_parts(last["parts"], entered)

    visits: list[Visit] = []
    for seq, draft in enumerate(drafts):
        parts = draft["parts"]
        segments: list[Segment] = []
        for index, (start, stage_id) in enumerate(parts):
            end = parts[index + 1][0] if index + 1 < len(parts) else draft["left"]
            stage = stages.get(stage_id)
            segments.append(
                Segment(
                    start=start,
                    end=end,
                    stage_id=stage_id,
                    norm=stage.norm if stage is not None else timedelta(days=default_days),
                    terminal=stage.is_terminal if stage is not None else False,
                )
            )
        visits.append(
            make_visit(
                deal.deal_id,
                seq,
                draft["entered"],
                draft["left"],
                segments,
                draft["exit"],
                draft["exit_stage"],
            )
        )
    return visits


def segment_at(visit: Visit, moment: datetime) -> Segment | None:
    for segment in visit.segments:
        if segment.start <= moment and _before(moment, segment.end):
            return segment
    return None


def is_active_at(visit: Visit, moment: datetime) -> bool:
    return visit.entered <= moment and _before(moment, visit.active_end)


def delay_at(visit: Visit, moment: datetime) -> timedelta | None:
    """Задержка на момент: `(t − entered) − N(этап на t)`; None — визит не активен."""
    if not is_active_at(visit, moment):
        return None
    segment = segment_at(visit, moment)
    if segment is None:
        return None
    return (moment - visit.entered) - segment.norm


def is_overdue_at(visit: Visit, moment: datetime) -> bool:
    delay = delay_at(visit, moment)
    return delay is not None and delay > ZERO


def overdue_from(visit: Visit) -> datetime | None:
    return visit.overdue_from


def decision(visit: Visit) -> tuple[datetime, bool] | None:
    """(момент решения, уложились): срок истёк — «не уложились» в момент истечения;
    ручной выход до срока — «уложились» в момент выхода; иначе решения нет."""
    if visit.overdue_from is not None:
        return visit.overdue_from, False
    if visit.exit_kind in MANUAL_EXITS and visit.left is not None:
        return visit.left, True
    return None


class OwnerTimeline:
    """Владелец заявки на дату по `audit_log request.assigned` (пары before → after)."""

    def __init__(
        self,
        changes: Sequence[tuple[datetime, uuid.UUID | None, uuid.UUID | None]],
        current: uuid.UUID,
    ) -> None:
        self.changes = list(changes)
        self.current = current
        self._times = [at for at, _before, _after in self.changes]

    def owner_at(self, moment: datetime) -> uuid.UUID:
        if not self.changes:
            return self.current
        index = bisect_right(self._times, moment)
        if index == 0:
            return self.changes[0][1] or self.current
        if index == len(self.changes):
            return self.current  # последнее изменение — защита от рассинхрона
        return self.changes[index - 1][2] or self.current

    def acquired_at(self, moment: datetime) -> datetime | None:
        """Момент последнего изменения не позже `moment` (с него владеет `owner_at`)."""
        index = bisect_right(self._times, moment)
        return self._times[index - 1] if index else None

    def spans(
        self, start: datetime, end: datetime | None
    ) -> list[tuple[datetime, datetime | None, uuid.UUID]]:
        """Отрезки владения внутри `[start, end)`."""
        cuts = [at for at in self._times if start < at and _before(at, end)]
        bounds: list[datetime | None] = [start, *cuts, end]
        result: list[tuple[datetime, datetime | None, uuid.UUID]] = []
        for left, right in zip(bounds, bounds[1:], strict=False):
            assert left is not None
            if right is not None and right <= left:
                continue
            result.append((left, right, self.owner_at(left)))
        return result


def inherited_at(
    visit: Visit, timeline: OwnerTimeline, moment: datetime
) -> tuple[bool, datetime | None]:
    """(получена уже просроченной, у владельца с) относительно владельца на `moment`."""
    acquired = timeline.acquired_at(moment)
    if acquired is None or acquired <= visit.entered:
        return False, None
    inherited = visit.overdue_from is not None and acquired > visit.overdue_from
    return inherited, acquired


# --------------------------------------------------------------------------------------
# Срезы, решения, закрытия, поступления
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapItem:
    visit: Visit
    owner: uuid.UUID
    stage_id: uuid.UUID
    norm: timedelta
    delay: timedelta  # ≤ 0 — в пределах нормы
    inherited: bool
    with_kam_since: datetime | None

    @property
    def overdue(self) -> bool:
        return self.delay > ZERO


def snapshot(
    visits: Iterable[Visit], moment: datetime, timelines: Mapping[uuid.UUID, OwnerTimeline]
) -> list[SnapItem]:
    """Точный срез на момент: заявки в работе с этапом, задержкой и владельцем на дату."""
    items: list[SnapItem] = []
    for visit in visits:
        if not is_active_at(visit, moment):
            continue
        segment = segment_at(visit, moment)
        if segment is None:
            continue
        timeline = timelines[visit.deal_id]
        delay = (moment - visit.entered) - segment.norm
        inherited, since = inherited_at(visit, timeline, moment)
        items.append(
            SnapItem(
                visit=visit,
                owner=timeline.owner_at(moment),
                stage_id=segment.stage_id,
                norm=segment.norm,
                delay=delay,
                inherited=inherited and delay > ZERO,
                with_kam_since=since,
            )
        )
    return items


@dataclass(frozen=True)
class DecisionItem:
    visit: Visit
    at: datetime
    on_time: bool
    owner: uuid.UUID
    stage_id: uuid.UUID


def decided_in(
    visits: Iterable[Visit],
    timelines: Mapping[uuid.UUID, OwnerTimeline],
    start: datetime,
    end: datetime,
) -> list[DecisionItem]:
    """Решения в `[start, end)`; этап — на момент решения, владелец — на момент решения."""
    items: list[DecisionItem] = []
    for visit in visits:
        result = decision(visit)
        if result is None:
            continue
        at, on_time = result
        if not start <= at < end:
            continue
        segment = visit.last_segment if on_time else segment_at(visit, at)
        if segment is None:
            continue
        items.append(
            DecisionItem(
                visit=visit,
                at=at,
                on_time=on_time,
                owner=timelines[visit.deal_id].owner_at(at),
                stage_id=segment.stage_id,
            )
        )
    return items


@dataclass(frozen=True)
class ClosedItem:
    visit: Visit
    left: datetime
    owner: uuid.UUID
    stage_id: uuid.UUID
    norm: timedelta
    late: bool
    delay: timedelta  # опоздание закрытого этапа (0 — в пределах нормы)
    inherited: bool


def closed_late_in(
    visits: Iterable[Visit],
    timelines: Mapping[uuid.UUID, OwnerTimeline],
    start: datetime,
    end: datetime,
) -> list[ClosedItem]:
    """Завершённые в `[start, end)` ручным выходом; опоздание — по последнему подотрезку,
    владелец — на момент выхода, `inherited` — если он получил заявку уже после срока."""
    items: list[ClosedItem] = []
    for visit in visits:
        if visit.exit_kind not in MANUAL_EXITS or visit.left is None:
            continue
        if not start <= visit.left < end:
            continue
        segment = visit.last_segment
        timeline = timelines[visit.deal_id]
        late = visit.overdue_from is not None and visit.overdue_from < visit.left
        delay = max((visit.left - visit.entered) - segment.norm, ZERO) if late else ZERO
        inherited = False
        if late:
            acquired = timeline.acquired_at(visit.left)
            inherited = (
                acquired is not None
                and acquired > visit.entered
                and visit.overdue_from is not None
                and acquired > visit.overdue_from
            )
        items.append(
            ClosedItem(
                visit=visit,
                left=visit.left,
                owner=timeline.owner_at(visit.left),
                stage_id=segment.stage_id,
                norm=segment.norm,
                late=late,
                delay=delay,
                inherited=inherited,
            )
        )
    return items


def inflow(
    deals: Iterable[DealInfo],
    timelines: Mapping[uuid.UUID, OwnerTimeline],
    start: datetime,
    end: datetime,
) -> Counter[uuid.UUID]:
    """Поступило за период: созданные в нём (владелец на момент создания) плюс назначения."""
    result: Counter[uuid.UUID] = Counter()
    for deal in deals:
        timeline = timelines[deal.deal_id]
        if start <= deal.created_at < end:
            result[timeline.owner_at(deal.created_at)] += 1
        for at, _before, after in timeline.changes:
            if start <= at < end and after is not None:
                result[after] += 1
    return result


# --------------------------------------------------------------------------------------
# Сводки: числа для KPI, строк КАМов и этапов
# --------------------------------------------------------------------------------------


def _days(value: timedelta | float | None) -> float | None:
    if value is None:
        return None
    seconds = value.total_seconds() if isinstance(value, timedelta) else float(value)
    return round(seconds / 86400, 1)


def _ratio_pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def _median_days(seconds: Sequence[float]) -> float | None:
    return _days(statistics.median(seconds)) if seconds else None


def _avg_days(seconds: Sequence[float]) -> float | None:
    return _days(sum(seconds) / len(seconds)) if seconds else None


def summarize_snapshot(
    items: Iterable[SnapItem], no_action: Collection[uuid.UUID] = ()
) -> dict[str, Any]:
    """Остатки на срез: в работе, просрочено (собственные / полученные), задержка, корзины."""
    active = overdue = own = inherited = over30 = own30 = no_action_count = 0
    delays: list[float] = []
    own_total = 0.0
    aging = dict.fromkeys(BUCKET_KEYS, 0)
    for item in items:
        active += 1
        if not item.overdue:
            continue
        overdue += 1
        seconds = item.delay.total_seconds()
        delays.append(seconds)
        aging[delay_bucket(item.delay)] += 1
        beyond = item.delay > THIRTY_DAYS
        over30 += beyond
        if item.inherited:
            inherited += 1
        else:
            own += 1
            own_total += seconds
            own30 += beyond
        if item.visit.deal_id in no_action:
            no_action_count += 1
    return {
        "active": active,
        "overdue": overdue,
        "own_overdue": own,
        "inherited": inherited,
        "overdue_pct": _ratio_pct(overdue, active),
        "delay_avg_days": _avg_days(delays),
        "delay_median_days": _median_days(delays),
        "delay_max_days": _days(max(delays)) if delays else None,
        "delay_total_days": _days(sum(delays)) if delays else None,
        "aging": aging,
        "overdue_30_plus": over30,
        "overdue_no_action": no_action_count,
        # служебное для правила отбора
        "_own_30_plus": own30,
        "_own_delay_total": own_total,
    }


def summarize_decided(items: Iterable[DecisionItem]) -> dict[str, Any]:
    decided = on_time = 0
    for item in items:
        decided += 1
        on_time += item.on_time
    return {"decided": decided, "on_time": on_time, "on_time_pct": _ratio_pct(on_time, decided)}


def summarize_closed(items: Iterable[ClosedItem]) -> dict[str, Any]:
    completed = late = late_inherited = 0
    delays: list[float] = []
    for item in items:
        completed += 1
        if item.late:
            late += 1
            late_inherited += item.inherited
            delays.append(item.delay.total_seconds())
    return {
        "completed": completed,
        "late_closed": late,
        "late_inherited": late_inherited,
        "late_delay_avg_days": _avg_days(delays),
        "late_delay_median_days": _median_days(delays),
    }


def kpi_block(value: Any, prev: Any, compare: str) -> dict[str, Any]:
    """`{value, prev, delta, compare}`: проценты — дельта в п.п., дни — с 1 знаком."""
    return {"value": value, "prev": prev, "delta": _delta(value, prev), "compare": compare}


def kpi_dict(cur: Mapping[str, Any], prev: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: kpi_block(cur.get(key), prev.get(key), compare) for key, compare in KPI_COMPARE}


def no_action_ids(
    items: Iterable[SnapItem], last_actions: Mapping[uuid.UUID, LastAction]
) -> set[uuid.UUID]:
    """Заявки, просроченные на срез, в которых после истечения нормы не было действий."""
    result: set[uuid.UUID] = set()
    for item in items:
        if not item.overdue or item.visit.overdue_from is None:
            continue
        last = last_actions.get(item.visit.deal_id)
        if last is None or last.at < item.visit.overdue_from:
            result.add(item.visit.deal_id)
    return result


# --------------------------------------------------------------------------------------
# Тренд: сетка срезов и разностные массивы
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendPoint:
    start: datetime
    end: datetime
    snapshot_at: datetime
    label: str
    complete: bool
    in_period: bool


def _month_after(moment: datetime) -> datetime:
    day = moment.astimezone(REPORT_TZ).date().replace(day=1)
    following = (day + timedelta(days=32)).replace(day=1)
    return datetime.combine(following, time.min, REPORT_TZ)


def trend_grid(bounds: PeriodBounds) -> tuple[str, datetime, list[TrendPoint]]:
    """Корзины тренда: недели (понедельник 00:00 МСК, не короче 8 недель) при периоде
    до 184 дней, иначе месяцы внутри периода. Срез корзины — `min(конец, as_of)`."""
    as_of = bounds.as_of
    weekly = bounds.days <= TREND_WEEKLY_MAX_DAYS
    if weekly:
        anchor = min(bounds.start, as_of - timedelta(weeks=TREND_MIN_WEEKS)).astimezone(REPORT_TZ)
        monday = anchor.date() - timedelta(days=anchor.weekday())
        cursor = datetime.combine(monday, time.min, REPORT_TZ)
    else:
        cursor = bounds.start
    window_start = cursor
    points: list[TrendPoint] = []
    while cursor < as_of:
        end = cursor + timedelta(weeks=1) if weekly else min(_month_after(cursor), bounds.end)
        snapshot_at = min(end, as_of)
        label = (snapshot_at - timedelta(seconds=1)).astimezone(REPORT_TZ).strftime("%d.%m")
        points.append(
            TrendPoint(
                start=cursor,
                end=end,
                snapshot_at=snapshot_at,
                label=label if weekly else f"на {label}",
                complete=snapshot_at == end,
                in_period=snapshot_at > bounds.start and cursor < bounds.as_of,
            )
        )
        cursor = end
    return ("week" if weekly else "month"), window_start, points


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICRO = timedelta(microseconds=1)
_THIRTY_US = THIRTY_DAYS // _MICRO
_DAY_US = DAY // _MICRO


def _us(moment: datetime) -> int:
    return (moment - _EPOCH) // _MICRO


class SnapshotGrid:
    """Разностные массивы срезов на точках тренда по владельцам.

    Отрезок `[x, y)` с нормой (срок `due = entered + N`) добавляет +1 «в работе» на
    точках `x ≤ t < y`, +1 «просрочено» на точках `t > due` (строго — `bisect_right`),
    +1 «больше 30 дн.» на точках `t > due + 30 сут` и `due` в сумму сроков: средняя
    задержка в точке — `(count·t − Σdue) / count`. Целые микросекунды — без ошибок
    округления на границах.
    """

    _ACTIVE, _OVERDUE, _OVER30, _DUE = range(4)

    def __init__(self, times: Sequence[datetime]) -> None:
        self.times = list(times)
        self._t = [_us(moment) for moment in self.times]
        self._rows: dict[uuid.UUID, list[list[int]]] = {}

    def _row(self, owner: uuid.UUID) -> list[list[int]]:
        row = self._rows.get(owner)
        if row is None:
            size = len(self._t) + 1
            row = self._rows[owner] = [[0] * size for _ in range(4)]
        return row

    def add_span(
        self, owner: uuid.UUID, start: datetime, end: datetime | None, due: datetime
    ) -> None:
        times = self._t
        first = bisect_left(times, _us(start))
        last = len(times) if end is None else bisect_left(times, _us(end))
        if first >= last:
            return
        active, overdue, over30, due_sum = self._row(owner)
        active[first] += 1
        active[last] -= 1
        due_us = _us(due)
        from_overdue = max(first, bisect_right(times, due_us))
        if from_overdue < last:
            overdue[from_overdue] += 1
            overdue[last] -= 1
            due_sum[from_overdue] += due_us
            due_sum[last] -= due_us
        from_30 = max(first, bisect_right(times, due_us + _THIRTY_US))
        if from_30 < last:
            over30[from_30] += 1
            over30[last] -= 1

    def add_visit(self, visit: Visit, timeline: OwnerTimeline) -> None:
        for segment in visit.segments:
            if segment.terminal or not _before(segment.start, visit.active_end):
                break
            end = segment.end
            if visit.active_end is not None:
                end = visit.active_end if end is None else min(end, visit.active_end)
            due = visit.entered + segment.norm
            for start, stop, owner in timeline.spans(segment.start, end):
                self.add_span(owner, start, stop, due)

    def owners(self) -> set[uuid.UUID]:
        return set(self._rows)

    def series(self, include: Callable[[uuid.UUID], bool] | None = None) -> list[dict[str, Any]]:
        """Значения в точках по владельцам из `include` (None — по всем)."""
        size = len(self._t)
        totals = [[0] * size for _ in range(4)]
        for owner, row in self._rows.items():
            if include is not None and not include(owner):
                continue
            for kind in range(4):
                running = 0
                source, target = row[kind], totals[kind]
                for index in range(size):
                    running += source[index]
                    target[index] += running
        result: list[dict[str, Any]] = []
        for index, moment in enumerate(self._t):
            count = totals[self._OVERDUE][index]
            avg = (
                round((count * moment - totals[self._DUE][index]) / count / _DAY_US, 1)
                if count
                else None
            )
            result.append(
                {
                    "active": totals[self._ACTIVE][index],
                    "overdue": count,
                    "overdue_30_plus": totals[self._OVER30][index],
                    "delay_avg_days": avg,
                }
            )
        return result


def decided_by_bucket(
    items: Iterable[DecisionItem], points: Sequence[TrendPoint]
) -> list[tuple[int, int]]:
    """(решено, уложились) по корзинам тренда."""
    starts = [point.start for point in points]
    counts = [[0, 0] for _ in points]
    for item in items:
        index = bisect_right(starts, item.at) - 1
        if index < 0 or not item.at < points[index].snapshot_at:
            continue
        counts[index][0] += 1
        counts[index][1] += item.on_time
    return [(decided, on_time) for decided, on_time in counts]


def trend_points(
    points: Sequence[TrendPoint],
    values: Sequence[Mapping[str, Any]],
    decided: Sequence[tuple[int, int]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for point, value, (decided_count, on_time) in zip(points, values, decided, strict=True):
        active, overdue = value["active"], value["overdue"]
        result.append(
            {
                "start": point.start.astimezone(REPORT_TZ).date().isoformat(),
                "end": point.end.astimezone(REPORT_TZ).date().isoformat(),
                "snapshot_at": point.snapshot_at.isoformat(),
                "label": point.label,
                "complete": point.complete,
                "in_period": point.in_period,
                "active": active,
                "overdue": overdue,
                "overdue_pct": _ratio_pct(overdue, active) if active >= TREND_MIN_N else None,
                "overdue_30_plus": value["overdue_30_plus"],
                "delay_avg_days": value["delay_avg_days"],
                "decided": decided_count,
                "on_time": on_time,
                "on_time_pct": (
                    _ratio_pct(on_time, decided_count) if decided_count >= TREND_MIN_N else None
                ),
            }
        )
    return result


# --------------------------------------------------------------------------------------
# Напоминания и реакция
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReminderRow:
    row_id: uuid.UUID
    deal_id: uuid.UUID
    created_at: datetime
    sent_at: datetime | None
    status: str
    recipient_id: uuid.UUID | None
    level: str  # responsible | manager
    base_key: str


def parse_dedup_key(key: str | None, escalation: Any = None) -> tuple[str, str]:
    """(уровень, базовый ключ) из `stuck:{deal}:{stage}:{level}:{bucket}[:{получатель}]`.

    Суффикс получателя появляется при нескольких адресатах; ключ из 4 частей (старый
    формат) — уровень по `payload.escalation`."""
    fallback = "manager" if escalation is True else "responsible"
    if not key:
        return fallback, ""
    parts = key.split(":")
    if len(parts) >= 5 and parts[0] == "stuck":
        level = parts[3] if parts[3] in ("responsible", "manager") else fallback
        return level, ":".join(parts[:5])
    return fallback, key


@dataclass(frozen=True)
class VisitReminders:
    rows: tuple[ReminderRow, ...]
    reminders: int  # различные базовые ключи уровня responsible
    delivered: int  # из них с хотя бы одной строкой status='sent'
    escalated: bool


def summarize_visit_reminders(rows: Sequence[ReminderRow]) -> VisitReminders:
    bases: dict[str, bool] = {}
    escalated = False
    for row in rows:
        if row.level == "manager":
            escalated = True
            continue
        bases[row.base_key] = bases.get(row.base_key, False) or row.status == "sent"
    return VisitReminders(
        rows=tuple(rows),
        reminders=len(bases),
        delivered=sum(bases.values()),
        escalated=escalated,
    )


def match_reminders(
    visits: Iterable[Visit], rows: Iterable[ReminderRow]
) -> dict[tuple[uuid.UUID, int], VisitReminders]:
    """Напоминание → визит заявки: `entered ≤ created_at < left` (номер этапа из ключа не
    используется — перевод схемы меняет этап, не визит)."""
    by_deal: dict[uuid.UUID, list[Visit]] = defaultdict(list)
    for visit in visits:
        by_deal[visit.deal_id].append(visit)
    starts: dict[uuid.UUID, list[datetime]] = {}
    for deal_id, items in by_deal.items():
        items.sort(key=lambda v: (v.entered, v.seq))
        starts[deal_id] = [v.entered for v in items]
    grouped: dict[tuple[uuid.UUID, int], list[ReminderRow]] = defaultdict(list)
    for row in rows:
        items = by_deal.get(row.deal_id)
        if not items:
            continue
        index = bisect_right(starts[row.deal_id], row.created_at) - 1
        if index < 0:
            continue
        visit = items[index]
        if visit.left is not None and row.created_at >= visit.left:
            continue
        grouped[visit.key].append(row)
    return {
        key: summarize_visit_reminders(sorted(rows, key=lambda r: (r.created_at, str(r.row_id))))
        for key, rows in grouped.items()
    }


@dataclass(frozen=True)
class Action:
    at: datetime
    kind: str  # transition | created | comment | file
    actor_id: uuid.UUID | None


@dataclass(frozen=True)
class LastAction:
    at: datetime
    kind: str
    actor_id: uuid.UUID | None


@dataclass(frozen=True)
class Episode:
    visit: Visit
    recipient: uuid.UUID
    first_at: datetime
    delivered_at: datetime | None
    status: str = "undelivered"  # reacted_72h | no_action | pending | undelivered
    reaction_at: datetime | None = None
    reaction_kind: str | None = None
    reaction_hours: float | None = None


def reminder_episode(visit: Visit, reminders: VisitReminders) -> Episode | None:
    """Эпизод визита: первое напоминание ответственному и его получатель; старт реакции —
    первое доставленное напоминание этого получателя (`coalesce(sent_at, created_at)`)."""
    rows = [r for r in reminders.rows if r.level == "responsible" and r.recipient_id]
    if not rows:
        return None
    first = rows[0]
    recipient = first.recipient_id
    assert recipient is not None
    delivered = next((r for r in rows if r.recipient_id == recipient and r.status == "sent"), None)
    return Episode(
        visit=visit,
        recipient=recipient,
        first_at=first.created_at,
        delivered_at=(delivered.sent_at or delivered.created_at) if delivered else None,
    )


def reaction(episode: Episode, actions: Sequence[Action], moment: datetime) -> Episode:
    """Статус эпизода на момент: первое собственное действие получателя в
    `[r, min(left, T)]` (ручной выход с этапа — тоже действие), горизонт `min(r + 72 ч, left)`."""
    if episode.delivered_at is None:
        return replace(episode, status="undelivered")
    start = episode.delivered_at
    left = episode.visit.left
    upper = moment if left is None else min(left, moment)
    first = next(
        (
            action
            for action in actions
            if action.actor_id == episode.recipient and start <= action.at <= upper
        ),
        None,
    )
    horizon = start + REACTION_HORIZON
    if left is not None and left < horizon:
        horizon = left
    if first is not None and first.at <= horizon:
        status = "reacted_72h"
    elif horizon <= moment:
        status = "no_action"
    else:
        status = "pending"
    return replace(
        episode,
        status=status,
        reaction_at=first.at if first else None,
        reaction_kind=first.kind if first else None,
        reaction_hours=round((first.at - start).total_seconds() / 3600, 1) if first else None,
    )


def summarize_reminders(
    visit_reminders: Mapping[tuple[uuid.UUID, int], VisitReminders],
    episodes: Iterable[Episode],
    timelines: Mapping[uuid.UUID, OwnerTimeline],
    start: datetime,
    end: datetime,
    include: Callable[[uuid.UUID], bool],
) -> dict[str, Any]:
    """Агрегаты напоминаний за `[start, end)` по получателям из `include`."""
    reminded: set[tuple[uuid.UUID, int]] = set()
    escalated: set[tuple[uuid.UUID, int]] = set()
    sent: dict[tuple[tuple[uuid.UUID, int], str, uuid.UUID], bool] = {}
    for key, summary in visit_reminders.items():
        for row in summary.rows:
            if not start <= row.created_at < end:
                continue
            if row.level == "manager":
                owner = timelines[row.deal_id].owner_at(row.created_at)
                if include(owner):
                    escalated.add(key)
                continue
            if row.recipient_id is None or not include(row.recipient_id):
                continue
            reminded.add(key)
            slot = (key, row.base_key, row.recipient_id)
            sent[slot] = sent.get(slot, False) or row.status == "sent"
    counts = Counter[str]()
    hours: list[float] = []
    total = 0
    for episode in episodes:
        if not start <= episode.first_at < end or not include(episode.recipient):
            continue
        total += 1
        counts[episode.status] += 1
        if episode.reaction_hours is not None:
            hours.append(episode.reaction_hours)
    matured = counts["reacted_72h"] + counts["no_action"]
    return {
        "visits_reminded": len(reminded),
        "visits_escalated": len(escalated),
        "sent": len(sent),
        "delivered": sum(sent.values()),
        "episodes": total,
        "matured": matured,
        "reacted_72h": counts["reacted_72h"],
        "reacted_72h_pct": _ratio_pct(counts["reacted_72h"], matured),
        "no_action": counts["no_action"],
        "pending": counts["pending"],
        "undelivered": counts["undelivered"],
        "reaction_median_hours": round(statistics.median(hours), 1) if hours else None,
        "reaction_avg_hours": round(sum(hours) / len(hours), 1) if hours else None,
    }


def coverage(journal_from: datetime | None, start: datetime) -> str:
    """Покрытие периода журналом напоминаний: none | partial | full."""
    if journal_from is None:
        return "none"
    return "partial" if journal_from > start else "full"


# --------------------------------------------------------------------------------------
# Тексты: итог, факты, правило отбора, оговорки
# --------------------------------------------------------------------------------------


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def _pct_text(part: int, whole: int) -> str:
    return f"{_round_half_up(part * 100 / whole)} %" if whole else "—"


def _pct_value_text(value: float | None) -> str:
    return "—" if value is None else f"{_round_half_up(value)} %"


def _num_text(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}".replace(".", ",")
    return str(int(value))


def _date_text(moment: datetime) -> str:
    return moment.astimezone(REPORT_TZ).strftime("%d.%m.%Y")


def _short_date(moment: datetime) -> str:
    return moment.astimezone(REPORT_TZ).strftime("%d.%m")


def at_label(bounds: PeriodBounds) -> str:
    """Дата среза T: сегодня для текущего периода, последний день — для прошедшего."""
    return _date_text(bounds.as_of - _MICRO)


def snapshot_label(bounds: PeriodBounds) -> str:
    """Срез на начало периода — «01.10»."""
    return _short_date(bounds.start)


def comparison(bounds: PeriodBounds, prev: PeriodBounds) -> dict[str, str]:
    return {
        "snapshot_label": f"к {snapshot_label(bounds)}",
        "period_label": f"к {_short_date(prev.start)}–{_short_date(prev.end - _MICRO)}",
    }


def short_person(full_name: str | None) -> str:
    """«Павлов Роман Евгеньевич» → «Павлов Р. Е.»."""
    parts = (full_name or "").split()
    if len(parts) < 2:
        return full_name or "—"
    initials = " ".join(f"{part[0]}." for part in parts[1:3])
    return f"{parts[0]} {initials}"


_FEMALE_SUFFIXES = ("ова", "ева", "ёва", "ина", "ына")
_CONSONANTS = set("бвгджзклмнпрстфхцчшщ")


def _genitive_surname(surname: str, female: bool) -> str:
    lower = surname.lower()
    if female:
        if lower.endswith(_FEMALE_SUFFIXES):
            return surname[:-1] + "ой"
        if lower.endswith(("ская", "цкая")):
            return surname[:-2] + "ой"
        return surname
    if lower.endswith(("ский", "цкий")):
        return surname[:-2] + "ого"
    if lower and lower[-1] in _CONSONANTS:
        return surname + "а"
    return surname


def genitive_person(full_name: str | None) -> str:
    """«Павлов Роман Евгеньевич» → «Павлова Р. Е.» (для «У …:»); неясный случай — как есть."""
    parts = (full_name or "").split()
    if len(parts) < 2:
        return full_name or "—"
    patronymic = parts[2].lower() if len(parts) >= 3 else ""
    if patronymic.endswith(("вна", "чна", "шна")):
        female = True
    elif patronymic.endswith(("вич", "ич")):
        female = False
    else:
        female = parts[0].lower().endswith(("а", "я"))
    return short_person(" ".join([_genitive_surname(parts[0], female), *parts[1:]]))


def stage_text(stage: StageInfo) -> str:
    return f"«{stage.label}» ({WORKFLOW_SHORT.get(stage.workflow_type, stage.workflow_type)})"


def headline(
    *,
    at: str,
    prev_label: str | None,
    cur: Mapping[str, Any],
    prev: Mapping[str, Any] | None,
    top_stage: tuple[str, int] | None = None,
    owner: str | None = None,
) -> str:
    """Детерминированная строка-итог; части с нулём не выводятся."""
    active, overdue = cur["active"], cur["overdue"]
    if active == 0:
        text = "Заявок в работе нет."
    elif overdue == 0:
        text = f"Просроченных заявок нет; в работе {active}."
    else:
        noun = _plural(active, ("заявки", "заявок", "заявок"))
        share = _pct_text(overdue, active)
        if prev is not None and prev_label and prev["active"]:
            share += (
                f"; на {prev_label} — {prev['overdue']} из {prev['active']}, "
                f"{_pct_text(prev['overdue'], prev['active'])}"
            )
        text = f"На {at} дольше нормы — {overdue} из {active} {noun} в работе ({share})."
        if top_stage is not None and top_stage[1]:
            text += f" Больше всего на этапе {top_stage[0]}: {top_stage[1]}."
        tail: list[str] = []
        if cur["overdue_30_plus"]:
            tail.append(f"больше 30 дней сверх нормы: {cur['overdue_30_plus']}")
        if cur["overdue_no_action"]:
            tail.append(f"по {cur['overdue_no_action']} после срока не было действий в системе")
        if tail:
            joined = "; ".join(tail)
            text += f" {joined[0].upper()}{joined[1:]}."
    if owner:
        text = f"У {owner}: {text[0].lower()}{text[1:]}"
    return text


def kam_facts(
    row: Mapping[str, Any],
    *,
    team_pct: float | None,
    active_median: float | None,
    prev_label: str | None,
    transfer_dates: Sequence[date] = (),
) -> list[str]:
    """До 6 строк-фактов по КАМу, без оценок; строки с нулём пропускаются."""
    lines: list[str] = []
    if row["inherited"]:
        shown = sorted(set(transfer_dates))[:2]
        verb = _plural(
            row["inherited"],
            (
                "получена уже просроченной",
                "получены уже просроченными",
                "получены уже просроченными",
            ),
        )
        line = f"{row['inherited']} из {row['overdue']} {verb}"
        if shown:
            noun = "передача" if len(shown) == 1 else "передачи"
            line += f" ({noun} {', '.join(day.strftime('%d.%m') for day in shown)})"
        lines.append(line)
    if row["overdue"]:
        team = f" (по команде {_pct_value_text(team_pct)})" if team_pct is not None else ""
        lines.append(
            f"Просрочено {row['overdue']} из {row['active']} в работе — "
            f"{_pct_text(row['overdue'], row['active'])}{team}"
        )
        lines.append(
            f"Сверх нормы: в среднем {_num_text(row['delay_avg_days'])} дн., "
            f"обычно {_num_text(row['delay_median_days'])} дн., "
            f"максимум {_num_text(row['delay_max_days'])} дн."
        )
    if row["overdue_30_plus"]:
        lines.append(f"Больше 30 дней сверх нормы: {row['overdue_30_plus']}")
    if row["overdue_no_action"]:
        lines.append(f"После срока без действий в системе: {row['overdue_no_action']}")
    if row["active"]:
        median = f" (медиана по команде {_num_text(active_median)})" if active_median else ""
        lines.append(f"В работе {row['active']}{median}")
    prev = row.get("prev") or {}
    if prev_label and prev.get("overdue") is not None and prev["overdue"] != row["overdue"]:
        if prev["overdue"]:
            lines.append(f"На {prev_label} было {prev['overdue']}, сейчас {row['overdue']}")
        else:
            lines.append(f"На {prev_label} просрочек не было, сейчас {row['overdue']}")
    return lines[:FACTS_LIMIT]


def attention(
    rows: Sequence[Mapping[str, Any]],
    *,
    own_only: bool = False,
) -> tuple[dict[str, Any], set[str]]:
    """Правило «Просрочек больше, чем по команде, или старше 30 дней» (`ATTENTION_RULE`).

    `rows` — строки КАМов команды (активные, с заявками в работе на срез) со служебными
    `_own_30_plus` и `_own_delay_total`. Доля команды — Σсобственных / Σв работе; сравнение
    по несокращённым дробям. Возвращает (блок ответа без фактов, id отобранных)."""
    sum_own = sum(row["own_overdue"] for row in rows)
    sum_active = sum(row["active"] for row in rows)
    team_share = Fraction(sum_own, sum_active) if sum_active else Fraction(0)
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        if not row["active"]:
            continue
        share = Fraction(row["own_overdue"], row["active"])
        by_share = (
            row["active"] >= ATTENTION_MIN_ACTIVE
            and row["own_overdue"] >= ATTENTION_MIN_OWN_OVERDUE
            and share >= ATTENTION_FACTOR * team_share
        )
        if by_share or row["_own_30_plus"] > 0:
            selected.append(row)
    selected.sort(
        key=lambda row: (
            -Fraction(row["own_overdue"], row["active"]),
            -row["own_overdue"],
            -row["_own_delay_total"],
            row["kam"],
        )
    )
    team_pct = round(float(team_share) * 100, 1) if sum_active else None
    block = {
        "rule": ATTENTION_RULE,
        "team_own_overdue_pct": team_pct,
        "threshold_pct": round(float(team_share * ATTENTION_FACTOR) * 100, 1)
        if sum_active
        else None,
        "total_kams": len(rows),
        "items": [] if own_only else list(selected[:ATTENTION_LIMIT]),
    }
    return block, {row["kam_id"] for row in selected}


def norm_change_notes(
    rows: Iterable[tuple[str, datetime, Any]], stages: Mapping[uuid.UUID, StageInfo]
) -> list[str]:
    """Факты о смене норм и переводах этапов из применённых пакетов схемы:
    `(процесс, applied_at, операции)`. `null`/`false` в patch — не смена нормы."""
    from app.modules.workflow.schemas import DeleteStatusOp, UpdateStatusOp

    notes: list[str] = []
    for workflow_type, applied_at, raw in rows:
        try:
            operations = parse_operations(list(raw or []))
        except Exception:  # noqa: BLE001 — повреждённый пакет не роняет отчёт
            continue
        day = _date_text(applied_at)
        suffix = WORKFLOW_SHORT.get(workflow_type, workflow_type)
        for operation in operations:
            if isinstance(operation, UpdateStatusOp):
                patch = operation.patch
                if patch.stuck_threshold_days is None and patch.reset_stuck_threshold is not True:
                    continue
                stage = stages.get(operation.status_id)
                name = stage.name if stage else "этап удалён"
                notes.append(
                    f"Норма этапа «{name}» ({suffix}) изменена {day}; все визиты оценены по "
                    "действующей норме."
                )
            elif isinstance(operation, DeleteStatusOp) and operation.migrate_to_status_id:
                source = stages.get(operation.status_id)
                target = stages.get(operation.migrate_to_status_id)
                source_name = source.name if source else "удалённого этапа"
                target_name = target.name if target else "другой этап"
                notes.append(
                    f"Заявки с этапа «{source_name}» перенесены на «{target_name}» {day}; "
                    "время на этапе не обнулено, с этой даты действует норма этапа "
                    f"«{target_name}»."
                )
    unique: list[str] = []
    for note in notes:
        if note not in unique:
            unique.append(note)
    return unique


TRANSFER_NOTE = (
    "Заявки, переданные другому КАМу, учтены у того, кто вёл их на дату; в «Детализации» — "
    "у текущего ответственного."
)


# --------------------------------------------------------------------------------------
# Запросы
# --------------------------------------------------------------------------------------


def _assigned_deals(kam_ids: Sequence[uuid.UUID]) -> sa.Select:
    """Заявки, которые когда-либо назначались на КАМов фильтра или от них уходили."""
    id_strs = [str(item) for item in kam_ids]
    return select(AuditLog.entity_id).where(
        AuditLog.entity_type == "deal",
        AuditLog.action == "request.assigned",
        or_(
            AuditLog.before["assignee_id"].astext.in_(id_strs),
            AuditLog.after["assignee_id"].astext.in_(id_strs),
        ),
    )


def deals_stmt(
    params: KamActivityParams,
    scope: DealScope | None,
    since: datetime,
    until: datetime,
) -> sa.Select:
    """Заявки окна: открытые и закрытые после `since`; фильтр КАМа — по атрибуции на дату
    (сейчас ответственный или фигурирует в передачах; окончательно — в Python)."""
    stmt = (
        select(
            Deal.id,
            Deal.title,
            Deal.pipeline,
            Deal.university_id,
            University.name.label("university_name"),
            University.short_name.label("university_short"),
            Counterparty.display_name.label("client"),
            Deal.responsible_user_id,
            Deal.stage_id,
            Deal.stage_entered_at,
            Deal.created_at,
        )
        .select_from(Deal)
        .outerjoin(University, University.id == Deal.university_id)
        .outerjoin(Counterparty, Counterparty.id == Deal.counterparty_id)
        .where(
            Deal.status != "archived",
            Deal.created_at < until,
            or_(Deal.status == "open", Deal.stage_entered_at >= since),
        )
    )
    stmt = _apply_deal_filters(stmt, replace(params, kam_ids=()), scope)
    if params.kam_ids:
        stmt = stmt.where(
            or_(
                Deal.responsible_user_id.in_(params.kam_ids),
                Deal.id.in_(_assigned_deals(params.kam_ids)),
            )
        )
    return stmt


def history_stmt(deal_ids: Sequence[uuid.UUID], until: datetime) -> sa.Select:
    history = DealStageHistory
    is_entry = stage_entry_condition(history)
    return (
        select(
            history.deal_id,
            history.created_at,
            history.from_stage_id,
            history.to_stage_id,
            history.kind,
            is_entry.label("is_entry"),
            history.is_return,
        )
        .where(
            history.deal_id == sa.any_(_deal_ids_param(deal_ids)),
            history.created_at < until,
            or_(is_entry, history.kind == "system_migration"),
        )
        .order_by(history.deal_id, history.created_at, history.id)
    )


def assignments_stmt(deal_ids: Sequence[uuid.UUID]) -> sa.Select:
    return (
        select(
            AuditLog.entity_id,
            AuditLog.created_at,
            AuditLog.before["assignee_id"].astext.label("before_id"),
            AuditLog.after["assignee_id"].astext.label("after_id"),
        )
        .where(
            AuditLog.entity_type == "deal",
            AuditLog.action == "request.assigned",
            AuditLog.entity_id == sa.any_(_deal_ids_param(deal_ids)),
        )
        .order_by(AuditLog.entity_id, AuditLog.created_at, AuditLog.id)
    )


def reminders_stmt(deal_ids: Sequence[uuid.UUID], lower: datetime, until: datetime) -> sa.Select:
    return select(
        Notification.id,
        Notification.entity_id,
        Notification.created_at,
        Notification.sent_at,
        Notification.status,
        Notification.recipient_user_id,
        Notification.dedup_key,
        Notification.payload["escalation"].label("escalation"),
    ).where(
        Notification.event_type == "request.stuck",
        Notification.entity_type == "deal",
        Notification.entity_id == sa.any_(_deal_ids_param(deal_ids)),
        Notification.created_at >= lower,
        Notification.created_at < until,
    )


def journal_from_stmt() -> sa.Select:
    return select(func.min(Notification.created_at)).where(
        Notification.event_type == "request.stuck"
    )


def last_actions_stmt(deal_ids: Sequence[uuid.UUID], before: datetime) -> sa.Select:
    """Последнее действие в системе до момента — без нижней границы окна: ручная строка
    истории (включая создание), неудалённый комментарий, неудалённый файл; любой автор."""
    ids = func.unnest(_deal_ids_param(deal_ids)).table_valued("deal_id").render_derived(name="d")
    history = DealStageHistory
    h = (
        select(
            history.created_at.label("at"),
            history.actor_user_id.label("actor_id"),
            sa.case((history.from_stage_id.is_(None), "created"), else_="transition").label("kind"),
        )
        .where(
            history.deal_id == ids.c.deal_id,
            history.kind == "manual",
            history.created_at < before,
        )
        .order_by(history.created_at.desc(), history.id.desc())
        .limit(1)
        .lateral("h")
    )
    c = (
        select(DealComment.created_at.label("at"), DealComment.author_user_id.label("actor_id"))
        .where(
            DealComment.deal_id == ids.c.deal_id,
            DealComment.deleted_at.is_(None),
            DealComment.created_at < before,
        )
        .order_by(DealComment.created_at.desc())
        .limit(1)
        .lateral("c")
    )
    f = (
        select(FileObject.created_at.label("at"), FileObject.uploaded_by.label("actor_id"))
        .where(
            FileObject.entity_type == "deal",
            FileObject.entity_id == ids.c.deal_id,
            FileObject.deleted_at.is_(None),
            FileObject.created_at < before,
        )
        .order_by(FileObject.created_at.desc())
        .limit(1)
        .lateral("f")
    )
    return (
        select(
            ids.c.deal_id,
            h.c.at.label("h_at"),
            h.c.actor_id.label("h_actor"),
            h.c.kind.label("h_kind"),
            c.c.at.label("c_at"),
            c.c.actor_id.label("c_actor"),
            f.c.at.label("f_at"),
            f.c.actor_id.label("f_actor"),
        )
        .select_from(ids)
        .outerjoin(h, sa.true())
        .outerjoin(c, sa.true())
        .outerjoin(f, sa.true())
    )


def actions_stmt(deal_ids: Sequence[uuid.UUID], lower: datetime, until: datetime) -> sa.Select:
    """Действия в системе `[lower, until]` (UNION ALL трёх источников) — для реакции."""
    ids = _deal_ids_param(deal_ids)
    history = DealStageHistory
    transitions = select(
        history.deal_id.label("deal_id"),
        history.created_at.label("at"),
        sa.case((history.from_stage_id.is_(None), "created"), else_="transition").label("kind"),
        history.actor_user_id.label("actor_id"),
    ).where(
        history.deal_id == sa.any_(ids),
        history.kind == "manual",
        history.created_at >= lower,
        history.created_at <= until,
    )
    comments = select(
        DealComment.deal_id.label("deal_id"),
        DealComment.created_at.label("at"),
        sa.literal("comment").label("kind"),
        DealComment.author_user_id.label("actor_id"),
    ).where(
        DealComment.deal_id == sa.any_(ids),
        DealComment.deleted_at.is_(None),
        DealComment.created_at >= lower,
        DealComment.created_at <= until,
    )
    files = select(
        FileObject.entity_id.label("deal_id"),
        FileObject.created_at.label("at"),
        sa.literal("file").label("kind"),
        FileObject.uploaded_by.label("actor_id"),
    ).where(
        FileObject.entity_type == "deal",
        FileObject.entity_id == sa.any_(ids),
        FileObject.deleted_at.is_(None),
        FileObject.created_at >= lower,
        FileObject.created_at <= until,
    )
    return sa.union_all(transitions, comments, files)


def norm_changes_stmt(workflow_types: Sequence[str], since: datetime, until: datetime) -> sa.Select:
    return (
        select(Workflow.code, WorkflowChangeRequest.applied_at, WorkflowChangeRequest.operations)
        .join(Workflow, Workflow.id == WorkflowChangeRequest.workflow_id)
        .where(
            WorkflowChangeRequest.status == "applied",
            WorkflowChangeRequest.applied_at >= since,
            WorkflowChangeRequest.applied_at < until,
            Workflow.code.in_(tuple(workflow_types)),
        )
        .order_by(WorkflowChangeRequest.applied_at)
    )


# --------------------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------------------


async def fetch_stage_infos(
    session: AsyncSession, workflow_types: Sequence[str], *, default_days: int
) -> dict[uuid.UUID, StageInfo]:
    """Все этапы процессов (включая мягко удалённые) с действующей нормой и её источником."""
    if not workflow_types:
        return {}
    stmt = (
        select(WorkflowStage, Workflow.code, Workflow.stuck_threshold_days)
        .join(Workflow, Workflow.id == WorkflowStage.workflow_id)
        .where(Workflow.code.in_(tuple(workflow_types)))
        .order_by(Workflow.code, WorkflowStage.sort_order)
    )
    result: dict[uuid.UUID, StageInfo] = {}
    for stage, workflow_code, workflow_days in (await session.execute(stmt)).all():
        norm_days, source = norm_cascade(stage.stuck_threshold_days, workflow_days, default_days)
        result[stage.id] = StageInfo(
            stage_id=stage.id,
            workflow_type=workflow_code,
            code=stage.code,
            name=stage.name,
            color=stage.color,
            sort_order=stage.sort_order,
            is_terminal=stage.is_terminal,
            outcome=stage.terminal_outcome,
            deleted=stage.deleted_at is not None,
            norm_days=norm_days,
            norm_source=source,
        )
    return result


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


async def fetch_deals(
    session: AsyncSession,
    params: KamActivityParams,
    scope: DealScope,
    since: datetime,
    until: datetime,
) -> dict[uuid.UUID, DealInfo]:
    result = await session.execute(deals_stmt(params, scope, since, until))
    return {
        row.id: DealInfo(
            deal_id=row.id,
            title=row.title,
            pipeline=row.pipeline,
            university_id=row.university_id,
            university_name=row.university_name,
            university_short=row.university_short,
            client=row.client,
            responsible_id=row.responsible_user_id,
            stage_id=row.stage_id,
            stage_entered_at=row.stage_entered_at,
            created_at=row.created_at,
        )
        for row in result.all()
    }


async def fetch_history(
    session: AsyncSession, deal_ids: Sequence[uuid.UUID], until: datetime
) -> dict[uuid.UUID, list[HistoryRow]]:
    result: dict[uuid.UUID, list[HistoryRow]] = defaultdict(list)
    if not deal_ids:
        return result
    for row in (await session.execute(history_stmt(deal_ids, until))).all():
        result[row.deal_id].append(
            HistoryRow(
                deal_id=row.deal_id,
                at=row.created_at,
                from_stage_id=row.from_stage_id,
                to_stage_id=row.to_stage_id,
                kind=row.kind,
                is_entry=bool(row.is_entry),
                is_return=bool(row.is_return),
            )
        )
    return result


async def fetch_assignments(
    session: AsyncSession, deal_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[datetime, uuid.UUID | None, uuid.UUID | None]]]:
    result: dict[uuid.UUID, list[tuple[datetime, uuid.UUID | None, uuid.UUID | None]]] = (
        defaultdict(list)
    )
    if not deal_ids:
        return result
    for row in (await session.execute(assignments_stmt(deal_ids))).all():
        result[row.entity_id].append(
            (row.created_at, _uuid_or_none(row.before_id), _uuid_or_none(row.after_id))
        )
    return result


async def fetch_reminders(
    session: AsyncSession, deal_ids: Sequence[uuid.UUID], lower: datetime, until: datetime
) -> list[ReminderRow]:
    if not deal_ids:
        return []
    rows: list[ReminderRow] = []
    for row in (await session.execute(reminders_stmt(deal_ids, lower, until))).all():
        level, base = parse_dedup_key(row.dedup_key, row.escalation)
        rows.append(
            ReminderRow(
                row_id=row.id,
                deal_id=row.entity_id,
                created_at=row.created_at,
                sent_at=row.sent_at,
                status=row.status,
                recipient_id=row.recipient_user_id,
                level=level,
                base_key=base or str(row.id),
            )
        )
    return rows


async def fetch_journal_from(session: AsyncSession, *, use_cache: bool = True) -> datetime | None:
    """Глобальное начало журнала напоминаний (кэш на час: индекса под запрос нет)."""
    cache = get_cache()
    if use_cache:
        cached = await cache.get_json(JOURNAL_FROM_KEY)
        if isinstance(cached, dict) and "value" in cached:
            value = cached["value"]
            return datetime.fromisoformat(value) if value else None
    value = (await session.execute(journal_from_stmt())).scalar_one_or_none()
    if use_cache:
        await cache.set_json(
            JOURNAL_FROM_KEY, {"value": value.isoformat() if value else None}, JOURNAL_FROM_TTL
        )
    return value


async def fetch_last_actions(
    session: AsyncSession, deal_ids: Sequence[uuid.UUID], before: datetime
) -> dict[uuid.UUID, LastAction]:
    if not deal_ids:
        return {}
    result: dict[uuid.UUID, LastAction] = {}
    for row in (await session.execute(last_actions_stmt(deal_ids, before))).all():
        candidates = [
            LastAction(at=at, kind=kind, actor_id=actor)
            for at, kind, actor in (
                (row.h_at, row.h_kind, row.h_actor),
                (row.c_at, "comment", row.c_actor),
                (row.f_at, "file", row.f_actor),
            )
            if at is not None
        ]
        if candidates:
            result[row.deal_id] = max(candidates, key=lambda action: action.at)
    return result


async def fetch_actions(
    session: AsyncSession, deal_ids: Sequence[uuid.UUID], lower: datetime, until: datetime
) -> dict[uuid.UUID, list[Action]]:
    result: dict[uuid.UUID, list[Action]] = defaultdict(list)
    if not deal_ids:
        return result
    for row in (await session.execute(actions_stmt(deal_ids, lower, until))).all():
        result[row.deal_id].append(Action(at=row.at, kind=row.kind, actor_id=row.actor_id))
    for items in result.values():
        items.sort(key=lambda action: action.at)
    return result


async def fetch_reminder_settings(session: AsyncSession) -> tuple[bool, datetime | None, bool]:
    """(проверка зависших включена, выключена с, есть активное правило для ответственного)."""
    setting = await session.get(AppSetting, "stuck_check_enabled")
    enabled = True
    disabled_since = None
    if setting is not None and isinstance(setting.value, dict):
        enabled = setting.value.get("value") is not False
        disabled_since = None if enabled else setting.updated_at
    rules = (
        await session.execute(
            select(func.count())
            .select_from(NotificationRule)
            .where(
                NotificationRule.event_type == "request.stuck",
                NotificationRule.recipient_type == "responsible",
                NotificationRule.is_active.is_(True),
            )
        )
    ).scalar_one()
    return enabled, disabled_since, bool(rules)


async def fetch_names(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    rows = await session.execute(
        select(AppUser.id, AppUser.full_name, AppUser.username).where(AppUser.id.in_(wanted))
    )
    return {user_id: full_name or username for user_id, full_name, username in rows.all()}


# --------------------------------------------------------------------------------------
# Набор данных отчёта
# --------------------------------------------------------------------------------------


@dataclass
class SlaDataset:
    bounds: PeriodBounds
    prev: PeriodBounds
    granularity: str
    window_start: datetime
    points: list[TrendPoint]
    workflows: tuple[str, ...]
    stages: dict[uuid.UUID, StageInfo]
    deals: dict[uuid.UUID, DealInfo]
    visits: list[Visit]
    timelines: dict[uuid.UUID, OwnerTimeline]
    snap: list[SnapItem]
    snap_prev: list[SnapItem]
    last_actions: dict[uuid.UUID, LastAction]
    no_action: set[uuid.UUID]
    no_action_prev: set[uuid.UUID]
    decisions: list[DecisionItem]
    closures: list[ClosedItem]
    inflow_cur: Counter[uuid.UUID]
    inflow_prev: Counter[uuid.UUID]
    grid: SnapshotGrid
    visit_reminders: dict[tuple[uuid.UUID, int], VisitReminders]
    episodes: list[Episode]
    journal_from: datetime | None
    check_enabled: bool
    disabled_since: datetime | None
    rules_active: bool
    notes: list[str]
    people: dict[uuid.UUID, PersonRef]
    names: dict[uuid.UUID, str] = field(default_factory=dict)
    _indexes: dict[str, dict[uuid.UUID, list[Any]]] = field(default_factory=dict, repr=False)

    def select(self, name: str, include: Callable[[uuid.UUID], bool]) -> list[Any]:
        """Элементы `snap | snap_prev | decisions | closures` владельцев фильтра (индекс по
        владельцу строится один раз: строки КАМов не перебирают весь набор заново)."""
        items: list[Any] = getattr(self, name)
        if not isinstance(include, Include):
            return [item for item in items if include(item.owner)]
        owners = include.owners
        if owners is None:
            return items
        index = self._indexes.get(name)
        if index is None:
            index = defaultdict(list)
            for item in items:
                index[item.owner].append(item)
            self._indexes[name] = index
        return [item for owner in owners for item in index.get(owner, ())]

    def person(self, user_id: uuid.UUID) -> PersonRef:
        return self.people.get(user_id) or PersonRef(
            user_id=user_id,
            full_name=self.names.get(user_id, "Пользователь не найден"),
            is_active=False,
        )

    def owners(self) -> set[uuid.UUID]:
        result = {item.owner for item in self.snap} | {item.owner for item in self.snap_prev}
        result |= {item.owner for item in self.decisions}
        result |= {item.owner for item in self.closures}
        result |= set(self.inflow_cur) | set(self.inflow_prev)
        result |= {episode.recipient for episode in self.episodes}
        return result

    def reminders_info(self) -> dict[str, Any]:
        return {
            "coverage": coverage(self.journal_from, self.bounds.start),
            "prev_coverage": coverage(self.journal_from, self.prev.start),
            "journal_from": (
                self.journal_from.astimezone(REPORT_TZ).date().isoformat()
                if self.journal_from
                else None
            ),
            "check_enabled": self.check_enabled,
            "disabled_since": (
                self.disabled_since.astimezone(REPORT_TZ).date().isoformat()
                if self.disabled_since
                else None
            ),
            "rules_active": self.rules_active,
            "note": REMINDERS_NOTE,
        }


def _keep_visit(visit: Visit, since: datetime) -> bool:
    if visit.active_end is None:
        return True
    return visit.active_end > visit.entered and visit.active_end > since


async def load_dataset(
    session: AsyncSession,
    params: KamActivityParams,
    scope: DealScope,
    bounds: PeriodBounds,
    *,
    use_cache: bool = True,
) -> SlaDataset:
    """Все данные отчёта одним проходом: заявки окна, визиты, владельцы, срезы T и T_prev,
    решения, закрытия, тренд, напоминания и реакция, оговорки. `params.kam_ids` — фильтр
    по атрибуции на дату (предфильтр в SQL, окончательный отбор — у потребителя)."""
    default_days = await _global_stuck_default(session)
    prev = previous_bounds(bounds)
    granularity, window_start, points = trend_grid(bounds)
    since = min(prev.start, window_start)
    until = bounds.as_of
    workflows = _visible_workflows(params, scope)
    stages = await fetch_stage_infos(session, workflows, default_days=default_days)
    deals = await fetch_deals(session, params, scope, since, until) if workflows else {}
    deal_ids = list(deals)
    history = await fetch_history(session, deal_ids, until)
    changes = await fetch_assignments(session, deal_ids)

    visits: list[Visit] = []
    for deal in deals.values():
        for visit in build_visits(
            history.get(deal.deal_id, []),
            deal,
            stages,
            is_current=bounds.is_current,
            default_days=default_days,
        ):
            if _keep_visit(visit, since):
                visits.append(visit)
    timelines = {
        deal.deal_id: OwnerTimeline(changes.get(deal.deal_id, ()), deal.responsible_id)
        for deal in deals.values()
    }

    snap = snapshot(visits, bounds.as_of, timelines)
    snap_prev = snapshot(visits, prev.as_of, timelines)
    overdue_now = sorted({item.visit.deal_id for item in snap if item.overdue}, key=str)
    overdue_then = sorted({item.visit.deal_id for item in snap_prev if item.overdue}, key=str)
    last_actions = await fetch_last_actions(session, overdue_now, bounds.as_of)
    last_prev = await fetch_last_actions(session, overdue_then, prev.as_of)

    decisions = decided_in(visits, timelines, since, bounds.as_of)
    closures = closed_late_in(visits, timelines, prev.start, bounds.as_of)
    grid = SnapshotGrid([point.snapshot_at for point in points])
    for visit in visits:
        grid.add_visit(visit, timelines[visit.deal_id])

    # напоминания: нижняя граница — самый ранний вход загруженных визитов
    lower = min((visit.entered for visit in visits), default=None)
    reminder_rows = (
        await fetch_reminders(session, deal_ids, lower, until) if lower is not None else []
    )
    visit_reminders = match_reminders(visits, reminder_rows)
    starts: list[Episode] = []
    by_key = {visit.key: visit for visit in visits}
    for key, summary in visit_reminders.items():
        episode = reminder_episode(by_key[key], summary)
        if episode is not None and prev.start <= episode.first_at < bounds.as_of:
            starts.append(episode)
    delivered = [episode for episode in starts if episode.delivered_at is not None]
    actions = (
        await fetch_actions(
            session,
            sorted({episode.visit.deal_id for episode in delivered}, key=str),
            min(episode.delivered_at for episode in delivered if episode.delivered_at),
            bounds.as_of,
        )
        if delivered
        else {}
    )
    episodes = [
        reaction(
            episode,
            actions.get(episode.visit.deal_id, ()),
            bounds.as_of if episode.first_at >= bounds.start else prev.as_of,
        )
        for episode in starts
    ]

    journal_from = await fetch_journal_from(session, use_cache=use_cache)
    check_enabled, disabled_since, rules_active = await fetch_reminder_settings(session)
    change_rows = (
        (await session.execute(norm_changes_stmt(workflows, since, _now()))).all()
        if workflows
        else []
    )
    notes = norm_change_notes(change_rows, stages)
    if any(since <= at < until for items in changes.values() for at, *_rest in items):
        notes.append(TRANSFER_NOTE)
    if not check_enabled:
        since_text = f" с {_date_text(disabled_since)}" if disabled_since else ""
        notes.append(f"Проверка зависших выключена{since_text}: напоминания не отправляются.")
    if coverage(journal_from, bounds.start) == "partial" and journal_from is not None:
        notes.append(f"Журнал напоминаний ведётся с {_date_text(journal_from)}.")

    dataset = SlaDataset(
        bounds=bounds,
        prev=prev,
        granularity=granularity,
        window_start=window_start,
        points=points,
        workflows=workflows,
        stages=stages,
        deals=deals,
        visits=visits,
        timelines=timelines,
        snap=snap,
        snap_prev=snap_prev,
        last_actions=last_actions,
        no_action=no_action_ids(snap, last_actions),
        no_action_prev=no_action_ids(snap_prev, last_prev),
        decisions=decisions,
        closures=closures,
        inflow_cur=inflow(deals.values(), timelines, bounds.start, bounds.as_of),
        inflow_prev=inflow(deals.values(), timelines, prev.start, prev.as_of),
        grid=grid,
        visit_reminders=visit_reminders,
        episodes=episodes,
        journal_from=journal_from,
        check_enabled=check_enabled,
        disabled_since=disabled_since,
        rules_active=rules_active,
        notes=notes,
        people={},
    )
    dataset.people = await fetch_people(session, dataset.owners(), params, scope)
    actor_ids = {action.actor_id for action in last_actions.values() if action.actor_id}
    missing = (actor_ids | dataset.owners()) - set(dataset.people)
    dataset.names = await fetch_names(session, missing)
    return dataset


# --------------------------------------------------------------------------------------
# Сборка ответа: команда и КАМ
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Include:
    """Фильтр владельцев: `owners=None` — все (команда), иначе — КАМы фильтра."""

    owners: frozenset[uuid.UUID] | None = None

    def __call__(self, owner: uuid.UUID) -> bool:
        return self.owners is None or owner in self.owners


def _include(kam_ids: Iterable[uuid.UUID] | None) -> Include:
    return Include(frozenset(kam_ids or ()) or None)


def _period_sums(
    ds: SlaDataset, include: Include, start: datetime, end: datetime, inflow_counts: Counter
) -> dict[str, Any]:
    decided = summarize_decided(
        item for item in ds.select("decisions", include) if start <= item.at < end
    )
    closed = summarize_closed(
        item for item in ds.select("closures", include) if start <= item.left < end
    )
    return {
        **decided,
        **closed,
        "inflow": sum(count for owner, count in inflow_counts.items() if include(owner)),
    }


def _sums(ds: SlaDataset, include: Include) -> tuple[dict[str, Any], dict[str, Any]]:
    """(текущие, прошлые) значения KPI по владельцам из `include`."""
    cur = {
        **summarize_snapshot(ds.select("snap", include), ds.no_action),
        **_period_sums(ds, include, ds.bounds.start, ds.bounds.as_of, ds.inflow_cur),
    }
    prev = {
        **summarize_snapshot(ds.select("snap_prev", include), ds.no_action_prev),
        **_period_sums(ds, include, ds.prev.start, ds.prev.as_of, ds.inflow_prev),
    }
    return cur, prev


def _aging_rows(cur: Mapping[str, Any], prev: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "count": cur["aging"][key],
            "prev": prev["aging"][key] if prev is not None else None,
        }
        for key, label in BUCKETS
    ]


def _stage_aggregates(ds: SlaDataset, include: Include) -> dict[uuid.UUID, dict[str, Any]]:
    """Метрики по этапам: остатки — по этапу на T, решения — по этапу на момент решения,
    завершения — по последнему подотрезку."""
    snaps: dict[uuid.UUID, list[SnapItem]] = defaultdict(list)
    for item in ds.select("snap", include):
        snaps[item.stage_id].append(item)
    decisions: dict[uuid.UUID, list[DecisionItem]] = defaultdict(list)
    for item in ds.select("decisions", include):
        if ds.bounds.start <= item.at < ds.bounds.as_of:
            decisions[item.stage_id].append(item)
    closures: dict[uuid.UUID, list[ClosedItem]] = defaultdict(list)
    for item in ds.select("closures", include):
        if ds.bounds.start <= item.left < ds.bounds.as_of:
            closures[item.stage_id].append(item)
    result: dict[uuid.UUID, dict[str, Any]] = {}
    for stage_id in set(snaps) | set(decisions) | set(closures):
        snap = summarize_snapshot(snaps.get(stage_id, ()))
        result[stage_id] = {
            **snap,
            **summarize_decided(decisions.get(stage_id, ())),
            **summarize_closed(closures.get(stage_id, ())),
        }
    return result


def _stage_row(stage: StageInfo, values: Mapping[str, Any] | None) -> dict[str, Any]:
    values = values or {
        **summarize_snapshot(()),
        **summarize_decided(()),
        **summarize_closed(()),
    }
    return {
        **stage.ref(),
        "sort_order": stage.sort_order,
        "norm_days": stage.norm_days,
        "norm_source": stage.norm_source,
        "active": values["active"],
        "overdue": values["overdue"],
        "overdue_pct": values["overdue_pct"],
        "aging": dict(values["aging"]),
        "delay_median_days": values["delay_median_days"],
        "delay_avg_days": values["delay_avg_days"],
        "delay_total_days": values["delay_total_days"],
        "decided": values["decided"],
        "on_time": values["on_time"],
        "on_time_pct": values["on_time_pct"],
        "completed": values["completed"],
        "late_delay_avg_days": values["late_delay_avg_days"],
    }


def _ordered_stages(ds: SlaDataset, workflow_type: str) -> list[StageInfo]:
    return sorted(
        (
            stage
            for stage in ds.stages.values()
            if stage.workflow_type == workflow_type and not stage.is_terminal
        ),
        key=lambda stage: (stage.sort_order, stage.deleted, stage.name),
    )


def stage_rows(
    ds: SlaDataset, include: Include, *, only_with_data: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """Этапы в порядке схемы без терминальных; удалённый — только если по нему есть данные."""
    values = _stage_aggregates(ds, include)
    result: dict[str, list[dict[str, Any]]] = {}
    for workflow_type in ds.workflows:
        rows: list[dict[str, Any]] = []
        for stage in _ordered_stages(ds, workflow_type):
            data = values.get(stage.stage_id)
            has_data = data is not None and (data["active"] or data["decided"] or data["completed"])
            if (stage.deleted or only_with_data) and not has_data:
                continue
            rows.append(_stage_row(stage, data))
        result[workflow_type] = rows
    return result


def top_stage(ds: SlaDataset, rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[str, int]:
    """Этап с наибольшим числом просроченных: при равенстве — по сумме задержки, затем по
    порядку в схеме."""
    best: tuple[Any, ...] | None = None
    chosen: tuple[str, int] = ("", 0)
    for wf_index, workflow_type in enumerate(ds.workflows):
        for index, row in enumerate(rows.get(workflow_type, ())):
            if not row["overdue"]:
                continue
            key = (-row["overdue"], -(row["delay_total_days"] or 0), wf_index, index)
            if best is None or key < best:
                best = key
                stage = ds.stages[uuid.UUID(row["id"])]
                chosen = (stage_text(stage), row["overdue"])
    return chosen


def _by_workflow(ds: SlaDataset, include: Include) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for workflow_type in ds.workflows:
        snap = summarize_snapshot(
            item
            for item in ds.select("snap", include)
            if ds.deals[item.visit.deal_id].pipeline == workflow_type
        )
        decided = summarize_decided(
            item
            for item in ds.select("decisions", include)
            if ds.bounds.start <= item.at < ds.bounds.as_of
            and ds.deals[item.visit.deal_id].pipeline == workflow_type
        )
        result[workflow_type] = {
            "label": WORKFLOW_TITLES.get(workflow_type, workflow_type),
            "active": snap["active"],
            "overdue": snap["overdue"],
            "overdue_pct": snap["overdue_pct"],
            "delay_median_days": snap["delay_median_days"],
            "on_time_pct": decided["on_time_pct"],
        }
    return result


def _trend(ds: SlaDataset, include: Include) -> dict[str, Any]:
    values = ds.grid.series(include)
    decided = decided_by_bucket(ds.select("decisions", include), ds.points)
    window_to = (ds.bounds.as_of - _MICRO).astimezone(REPORT_TZ).date()
    return {
        "granularity": ds.granularity,
        "window": {
            "from": ds.window_start.astimezone(REPORT_TZ).date().isoformat(),
            "to": window_to.isoformat(),
        },
        "min_n": TREND_MIN_N,
        "points": trend_points(ds.points, values, decided),
    }


def _last_action_payload(ds: SlaDataset, deal_id: uuid.UUID) -> dict[str, Any] | None:
    action = ds.last_actions.get(deal_id)
    if action is None:
        return None
    name = None
    if action.actor_id is not None:
        person = ds.people.get(action.actor_id)
        name = short_person(person.full_name if person else ds.names.get(action.actor_id))
    return {
        "at": action.at.isoformat(),
        "kind": action.kind,
        "actor_id": str(action.actor_id) if action.actor_id else None,
        "actor": name,
        "days_ago": _days(ds.bounds.as_of - action.at),
    }


def overdue_item(ds: SlaDataset, item: SnapItem) -> dict[str, Any]:
    deal = ds.deals[item.visit.deal_id]
    stage = ds.stages.get(item.stage_id)
    reminders = ds.visit_reminders.get(item.visit.key)
    person = ds.person(item.owner)
    return {
        "request_id": str(deal.deal_id),
        "title": deal.title,
        "workflow_type": deal.pipeline,
        "university": deal.university_ref(),
        "client": deal.client_ref(),
        "kam_id": str(item.owner),
        "kam": person.full_name,
        "stage": stage.ref()
        if stage
        else {"id": str(item.stage_id), "code": "", "name": "—", "color": None, "deleted": True},
        "stage_since": item.visit.entered.isoformat(),
        "on_stage_days": _days(ds.bounds.as_of - item.visit.entered),
        "norm_days": stage.norm_days if stage else _days(item.norm),
        "norm_source": stage.norm_source if stage else "global",
        "delay_days": _days(item.delay),
        "bucket": delay_bucket(item.delay),
        "overdue_since": item.visit.overdue_from.isoformat() if item.visit.overdue_from else None,
        "no_action_after_due": deal.deal_id in ds.no_action,
        "inherited": item.inherited,
        "with_kam_since": item.with_kam_since.isoformat() if item.with_kam_since else None,
        "reminders": reminders.reminders if reminders else 0,
        "delivered": reminders.delivered if reminders else 0,
        "escalated": reminders.escalated if reminders else False,
        "last_action": _last_action_payload(ds, deal.deal_id),
    }


def overdue_list(ds: SlaDataset, include: Include, limit: int | None) -> dict[str, Any]:
    items = sorted(
        (item for item in ds.select("snap", include) if item.overdue),
        key=lambda item: (-item.delay, ds.deals[item.visit.deal_id].title),
    )
    shown = items if limit is None else items[:limit]
    return {
        "total": len(items),
        "truncated": len(shown) < len(items),
        "items": [overdue_item(ds, item) for item in shown],
    }


def _exited_to(ds: SlaDataset, visit: Visit) -> dict[str, Any]:
    stage = ds.stages.get(visit.exit_stage_id) if visit.exit_stage_id else None
    return {
        "name": stage.label if stage else "—",
        "is_return": visit.exit_kind == "return",
        "outcome": stage.outcome if stage and stage.is_terminal else None,
    }


def late_closed_list(ds: SlaDataset, include: Include, limit: int | None) -> dict[str, Any]:
    items = sorted(
        (
            item
            for item in ds.select("closures", include)
            if item.late and ds.bounds.start <= item.left < ds.bounds.as_of
        ),
        key=lambda item: (-item.delay, ds.deals[item.visit.deal_id].title),
    )
    shown = items if limit is None else items[:limit]
    rows = []
    for item in shown:
        deal = ds.deals[item.visit.deal_id]
        stage = ds.stages.get(item.stage_id)
        rows.append(
            {
                "request_id": str(deal.deal_id),
                "title": deal.title,
                "workflow_type": deal.pipeline,
                "kam_id": str(item.owner),
                "kam": ds.person(item.owner).full_name,
                "university": deal.university_ref(),
                "client": deal.client_ref(),
                "stage": stage.ref()
                if stage
                else {"id": str(item.stage_id), "code": "", "name": "—", "color": None},
                "entered": item.visit.entered.isoformat(),
                "left_at": item.left.isoformat(),
                "duration_days": _days(item.left - item.visit.entered),
                "norm_days": stage.norm_days if stage else _days(item.norm),
                "delay_days": _days(item.delay),
                "inherited": item.inherited,
                "exited_to": _exited_to(ds, item.visit),
            }
        )
    return {"total": len(items), "items": rows}


def episodes_list(ds: SlaDataset, include: Include, limit: int | None) -> list[dict[str, Any]]:
    items = sorted(
        (
            episode
            for episode in ds.episodes
            if ds.bounds.start <= episode.first_at < ds.bounds.as_of and include(episode.recipient)
        ),
        key=lambda episode: episode.first_at,
        reverse=True,
    )
    if limit is not None:
        items = items[:limit]
    rows = []
    for episode in items:
        deal = ds.deals[episode.visit.deal_id]
        segment = segment_at(episode.visit, episode.first_at) or episode.visit.last_segment
        stage = ds.stages.get(segment.stage_id)
        rows.append(
            {
                "request_id": str(deal.deal_id),
                "title": deal.title,
                "kam_id": str(episode.recipient),
                "kam": ds.person(episode.recipient).full_name,
                "stage": stage.ref()
                if stage
                else {"id": str(segment.stage_id), "code": "", "name": "—", "color": None},
                "first_reminder_at": episode.first_at.isoformat(),
                "delivered_at": episode.delivered_at.isoformat() if episode.delivered_at else None,
                "status": episode.status,
                "reaction_at": episode.reaction_at.isoformat() if episode.reaction_at else None,
                "reaction_kind": episode.reaction_kind,
                "reaction_hours": episode.reaction_hours,
            }
        )
    return rows


def _public(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if not key.startswith("_")}


def _reminder_stats(ds: SlaDataset, include: Include, *, prev: bool = False) -> dict[str, Any]:
    start, end = (ds.prev.start, ds.prev.as_of) if prev else (ds.bounds.start, ds.bounds.as_of)
    return summarize_reminders(ds.visit_reminders, ds.episodes, ds.timelines, start, end, include)


def _transfer_dates(ds: SlaDataset, owner: uuid.UUID) -> list[date]:
    """Даты передач заявок, полученных владельцем уже просроченными (на срез T)."""
    dates = {
        item.with_kam_since.astimezone(REPORT_TZ).date()
        for item in ds.snap
        if item.owner == owner and item.overdue and item.inherited and item.with_kam_since
    }
    return sorted(dates)


def kam_row(ds: SlaDataset, person: PersonRef, *, with_reminders: bool) -> dict[str, Any]:
    owner = person.user_id
    include = _include((owner,))
    cur, prev = _sums(ds, include)
    row = {
        "kam_id": str(owner),
        "kam": person.full_name,
        "is_active": person.is_active,
        "roles": list(person.roles),
        "active": cur["active"],
        "overdue": cur["overdue"],
        "own_overdue": cur["own_overdue"],
        "inherited": cur["inherited"],
        "overdue_pct": cur["overdue_pct"],
        "small_sample": cur["active"] < SMALL_SAMPLE,
        "delay_avg_days": cur["delay_avg_days"],
        "delay_median_days": cur["delay_median_days"],
        "delay_max_days": cur["delay_max_days"],
        "delay_total_days": cur["delay_total_days"],
        "aging": dict(cur["aging"]),
        "overdue_30_plus": cur["overdue_30_plus"],
        "overdue_no_action": cur["overdue_no_action"],
        "decided": cur["decided"],
        "on_time": cur["on_time"],
        "on_time_pct": cur["on_time_pct"],
        "completed": cur["completed"],
        "late_closed": cur["late_closed"],
        "late_inherited": cur["late_inherited"],
        "late_delay_avg_days": cur["late_delay_avg_days"],
        "late_delay_median_days": cur["late_delay_median_days"],
        "inflow": cur["inflow"],
        "reminders": _reminder_stats(ds, include) if with_reminders else None,
        "prev": {
            "active": prev["active"],
            "overdue": prev["overdue"],
            "own_overdue": prev["own_overdue"],
            "overdue_pct": prev["overdue_pct"],
            "on_time_pct": prev["on_time_pct"],
            "delay_avg_days": prev["delay_avg_days"],
            "delay_median_days": prev["delay_median_days"],
        },
        "delta": {
            key: _delta(cur[key], prev[key])
            for key in (
                "overdue",
                "overdue_pct",
                "on_time_pct",
                "delay_avg_days",
                "delay_median_days",
            )
        },
        "attention": False,
        "_own_30_plus": cur["_own_30_plus"],
        "_own_delay_total": cur["_own_delay_total"],
    }
    return row


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    value = statistics.median(values)
    return float(value) if not float(value).is_integer() else int(value)


def team_reference(rows: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], dict]:
    """(КАМы команды — активные с заявками в работе на срез, медианы нагрузки)."""
    team = [row for row in rows if row["is_active"] and row["active"] > 0]
    return team, {
        "kams_total": len(team),
        "active_median": _median([row["active"] for row in team]),
        "overdue_median": _median([row["overdue"] for row in team]),
    }


def _inactive_owners(rows: Sequence[Mapping[str, Any]]) -> dict[str, int] | None:
    inactive = [row for row in rows if not row["is_active"] and row["active"] > 0]
    if not inactive:
        return None
    return {
        "users": len(inactive),
        "active": sum(row["active"] for row in inactive),
        "overdue": sum(row["overdue"] for row in inactive),
    }


def _kam_people(ds: SlaDataset, include: Include) -> list[PersonRef]:
    ids = {owner for owner in ds.owners() if include(owner)}
    ids |= {person_id for person_id in ds.people if include(person_id)}
    return [ds.person(person_id) for person_id in ids]


def _row_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    pct = row["overdue_pct"]
    return (-row["own_overdue"], -(pct if pct is not None else -1), row["kam"].casefold())


def team_body(
    ds: SlaDataset,
    *,
    kam_ids: Sequence[uuid.UUID] = (),
    own_only: bool = False,
    include_late: bool = False,
    include_reminders: bool = False,
    limit_items: bool = True,
) -> dict[str, Any]:
    """Тело ответа `GET /reports/sla` (без конверта)."""
    include = _include(kam_ids)
    info = ds.reminders_info()
    with_reminders = info["coverage"] != "none"
    cur, prev = _sums(ds, include)
    rows = [
        kam_row(ds, person, with_reminders=with_reminders) for person in _kam_people(ds, include)
    ]
    rows.sort(key=_row_sort_key)
    team_rows, team = team_reference(rows)
    block, selected = attention(team_rows, own_only=own_only)
    for row in rows:
        row["attention"] = not own_only and row["kam_id"] in selected
    prev_label = snapshot_label(ds.bounds)
    items = []
    for row in block["items"]:
        items.append(
            {
                "kam_id": row["kam_id"],
                "kam": row["kam"],
                "active": row["active"],
                "overdue": row["overdue"],
                "own_overdue": row["own_overdue"],
                "inherited": row["inherited"],
                "overdue_pct": row["overdue_pct"],
                "overdue_30_plus": row["overdue_30_plus"],
                "facts": kam_facts(
                    row,
                    team_pct=cur["overdue_pct"],
                    active_median=team["active_median"],
                    prev_label=prev_label,
                    transfer_dates=_transfer_dates(ds, uuid.UUID(row["kam_id"])),
                ),
            }
        )
    block["items"] = items
    if own_only:
        team = {"kams_total": 1}
    stages = stage_rows(ds, include)
    body: dict[str, Any] = {
        "period": {**ds.bounds.as_json(), "is_current": ds.bounds.is_current},
        "previous_period": ds.prev.as_json(),
        "comparison": comparison(ds.bounds, ds.prev),
        "headline": headline(
            at=at_label(ds.bounds),
            prev_label=prev_label,
            cur=cur,
            prev=prev,
            top_stage=top_stage(ds, stages),
        ),
        "definition": DEFINITION,
        "definitions": [{"term": term, "text": text} for term, text in DEFINITIONS],
        "kpi": kpi_dict(cur, prev),
        "team": team,
        "inactive_owners": _inactive_owners(rows),
        "aging": _aging_rows(cur, prev),
        "by_workflow": _by_workflow(ds, include),
        "stages": stages,
        "trend": _trend(ds, include),
        "kams": [_public(row) for row in rows],
        "attention": block,
        "overdue": overdue_list(ds, include, MAX_OVERDUE_ITEMS if limit_items else None),
        "reminders": {
            "info": info,
            "team": _reminder_stats(ds, include) if with_reminders else None,
        },
        "notes": list(ds.notes),
    }
    if include_late:
        body["late_closed"] = late_closed_list(ds, include, None)
    if include_reminders and with_reminders:
        body["reminders"]["items"] = episodes_list(ds, include, None)
    return body


def kam_body(
    ds: SlaDataset, kam: PersonRef, *, with_team: bool, limit_items: bool = True
) -> dict[str, Any]:
    """Тело ответа `GET /reports/sla/kams/{kam_id}`; `team` — справочно, только при широкой
    области (набор данных загружен без фильтра КАМа)."""
    include = _include((kam.user_id,))
    info = ds.reminders_info()
    with_reminders = info["coverage"] != "none"
    cur, prev = _sums(ds, include)
    limit = MAX_KAM_ITEMS if limit_items else None
    team_ref: dict[str, Any] | None = None
    if with_team:
        everyone = _include(None)
        team_cur, _team_prev = _sums(ds, everyone)
        rows = [kam_row(ds, person, with_reminders=False) for person in _kam_people(ds, everyone)]
        _team_rows, team = team_reference(rows)
        reminders_all = _reminder_stats(ds, everyone) if with_reminders else {}
        team_ref = {
            "overdue_pct": team_cur["overdue_pct"],
            "delay_median_days": team_cur["delay_median_days"],
            "delay_avg_days": team_cur["delay_avg_days"],
            "on_time_pct": team_cur["on_time_pct"],
            "active_median": team["active_median"],
            "reacted_72h_pct": reminders_all.get("reacted_72h_pct"),
            "reaction_median_hours": reminders_all.get("reaction_median_hours"),
        }
    stages = stage_rows(ds, include, only_with_data=True)
    flat_stages = [
        {"workflow_type": workflow_type, **row}
        for workflow_type in ds.workflows
        for row in stages.get(workflow_type, [])
    ]
    reminders: dict[str, Any] = {"info": info}
    if with_reminders:
        reminders.update(_reminder_stats(ds, include))
        reminders["items"] = episodes_list(ds, include, limit)
        reminders["prev"] = _reminder_stats(ds, include, prev=True)
    return {
        "period": {**ds.bounds.as_json(), "is_current": ds.bounds.is_current},
        "previous_period": ds.prev.as_json(),
        "comparison": comparison(ds.bounds, ds.prev),
        "kam": {
            "id": str(kam.user_id),
            "name": kam.full_name,
            "is_active": kam.is_active,
            "roles": list(kam.roles),
        },
        "headline": headline(
            at=at_label(ds.bounds),
            prev_label=snapshot_label(ds.bounds),
            cur=cur,
            prev=prev,
            top_stage=top_stage(ds, stages),
            owner=genitive_person(kam.full_name),
        ),
        "definition": DEFINITION,
        "definitions": [{"term": term, "text": text} for term, text in DEFINITIONS],
        "kpi": kpi_dict(cur, prev),
        "team": team_ref,
        "aging": _aging_rows(cur, prev),
        "overdue": overdue_list(ds, include, limit),
        "stages": flat_stages,
        "trend": _trend(ds, include),
        "late_closed": late_closed_list(ds, include, limit),
        "reminders": reminders,
        "notes": list(ds.notes),
    }


# --------------------------------------------------------------------------------------
# Точки входа (кэш и конверт)
# --------------------------------------------------------------------------------------


def _prepare(
    viewer: AppUser, params: KamActivityParams, scope: DealScope | None
) -> tuple[KamActivityParams, DealScope, PeriodBounds]:
    now = _now()
    params = resolve_params(params, today=now.astimezone(REPORT_TZ).date())
    scope = scope if scope is not None else viewer_scope(viewer)
    params = _resolve_scope(viewer, params, scope)
    return params, scope, resolve_bounds(params, now=now)


async def build_sla(
    session: AsyncSession,
    viewer: AppUser,
    params: KamActivityParams,
    *,
    scope: DealScope | None = None,
    use_cache: bool = True,
    include_late: bool = False,
    include_reminders: bool = False,
    limit_items: bool = True,
) -> dict[str, Any]:
    """«Просрочки» по команде (или по КАМам фильтра) с кэшем cache-aside на 60 с.
    `include_late`/`include_reminders`/`limit_items=False` — для выгрузки (мимо кэша)."""
    params, scope, bounds = _prepare(viewer, params, scope)
    cacheable = use_cache and not include_late and not include_reminders and limit_items
    cache = get_cache()
    key = _cache_key(CODE, params, scope)
    if cacheable:
        cached = await cache.get_json(key)
        if cached is not None:
            return cached
    dataset = await load_dataset(session, params, scope, bounds, use_cache=use_cache)
    body = {
        "scope": _scope_payload(scope),
        **team_body(
            dataset,
            kam_ids=params.kam_ids,
            own_only=scope.own_user_id is not None,
            include_late=include_late,
            include_reminders=include_reminders,
            limit_items=limit_items,
        ),
    }
    result = _envelope(CODE, params, body)  # type: ignore[arg-type]
    if cacheable:
        await cache.set_json(key, result, REPORT_CACHE_TTL_SECONDS)
    return result


async def build_sla_kam(
    session: AsyncSession,
    viewer: AppUser,
    kam_id: uuid.UUID,
    params: KamActivityParams,
    *,
    scope: DealScope | None = None,
    use_cache: bool = True,
    limit_items: bool = True,
) -> dict[str, Any]:
    """Разбор КАМа: 403 — «только свои» и чужой id (или процесс вне области), 404 — нет
    пользователя; пользователь без данных — 200 с нулями. `kam_id` из query не участвует."""
    params, scope, bounds = _prepare(viewer, replace(params, kam_ids=(kam_id,)), scope)
    user = await session.get(AppUser, kam_id)
    if user is None:
        raise not_found("Пользователь не найден")
    cacheable = use_cache and limit_items
    cache = get_cache()
    key = _cache_key(CODE_KAM, params, scope)
    if cacheable:
        cached = await cache.get_json(key)
        if cached is not None:
            return cached
    with_team = scope.own_user_id is None
    # справочные значения команды нужны только при широкой области — тогда без фильтра КАМа
    load_params = replace(params, kam_ids=()) if with_team else params
    dataset = await load_dataset(session, load_params, scope, bounds, use_cache=use_cache)
    person = dataset.people.get(kam_id) or PersonRef(
        user_id=kam_id,
        full_name=user.full_name or user.username,
        roles=tuple(user.roles or ()),
        is_active=bool(user.is_active),
    )
    body = {
        "scope": _scope_payload(scope),
        **kam_body(dataset, person, with_team=with_team, limit_items=limit_items),
    }
    result = _envelope(CODE_KAM, params, body)  # type: ignore[arg-type]
    if cacheable:
        await cache.set_json(key, result, REPORT_CACHE_TTL_SECONDS)
    return result


__all__ = ["CODE", "CODE_KAM", "build_sla", "build_sla_kam", "load_dataset"]
