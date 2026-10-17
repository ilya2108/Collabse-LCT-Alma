"""Замер массового переноса заявок при удалении этапа (workflow-engine.md §6.8,
docs/evidence/migration-benchmark.md).

Запуск (DSN — DATABASE_URL; база одноразовая: таблицы домена очищаются перед прогоном):

    python -m app.tools.bench_migration [--sizes 1000,10000,50000] [--kams 20]
        [--modes batch,legacy] [--legacy-max 10000] [--json PATH] [--force]

Сценарий прогона: K КАМов, воронка B2B из сида, N открытых заявок на этапе «Первичный
контакт» (по кругу КАМов), правило `request.transitioned → responsible → telegram`; пакет
«ребро «Новая» → «Переговоры» + удаление «Первичного контакта» с переносом в «Новая»».
Применение идёт через настоящий `service.apply_change`; через 5 мс после захвата
эксклюзивного лока параллельно запускается переход «пробной» заявки («Переговоры» →
«Подготовка договора») — он ждёт применения, это ожидание и меряется.

Режимы: `batch` — пакетный перенос (CTE, `migration.migrate_deleted_stages`);
`legacy` — дословная копия прежнего поштучного цикла (`legacy_migrate_deleted_stages`
подменяет `migration.migrate_deleted_stages`): «до» и «после» меряются в одной среде
одним и тем же кодом применения.

Защита: работает только на базах `crm_test*`, `*_test`, `*_bench` (иначе `--force`),
схема — на head Alembic (иначе exit 2 с подсказкой `alembic upgrade head`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import platform
import re
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import dispose_engine, get_session_factory
from app.core.errors import ApiError
from app.core.events import EVENT_REQUEST_TRANSITIONED
from app.core.outbox import emit_event_notifications
from app.models import AppUser, Deal, NotificationRule, University, WorkflowStage
from app.modules.workflow import locks, migration, service
from app.modules.workflow.graph import ApplyPlan
from app.modules.workflow.migration import MigrationOutcome
from app.modules.workflow.schemas import Operation
from app.tools.check_migrations import applied_revision, script_head
from app.tools.seed import seed_workflows

logger = logging.getLogger("app.bench")

DEFAULT_SIZES = (1_000, 10_000, 50_000)
DEFAULT_KAMS = 20
DEFAULT_LEGACY_MAX = 10_000
PROBE_DELAY_SECONDS = 0.005  # пробный переход стартует через 5 мс после захвата лока
# в legacy-режиме лимит ожидания перехода снят — как в прежнем коде без lock_timeout,
# иначе пробный переход при N ≳ 3 000 получил бы 409 workflow_busy, а не «ждал 30 с»
LEGACY_PROBE_LOCK_TIMEOUT_MS = 30 * 60 * 1000
MODES = ("batch", "legacy")
MODE_LABELS = {"batch": "пакетный (CTE)", "legacy": "поштучный (до)"}

# имена баз, которые инструмент вправе очищать (как у tests/db + выделенная crm_bench)
_SAFE_DB_RE = re.compile(r"crm_test[a-z0-9_]*|[a-z0-9_]*_test|[a-z0-9_]*_bench")

# очищаются перед каждым прогоном; пользователи не удаляются — bench-учётки переиспользуются
RESET_TABLES = (
    "notification",
    "audit_log",
    "integration_event",
    "deal_stage_history",
    "deal",
    "workflow_change_request",
    "notification_rule",
    "workflow_transition",
    "workflow_stage",
    "workflow",
    "university",
)

_UUID = PG_UUID(as_uuid=True)


class UnsafeDatabaseError(RuntimeError):
    """База не похожа на одноразовую — очищать её инструмент отказывается."""


def assert_safe_database(name: str | None) -> None:
    if not name or not _SAFE_DB_RE.fullmatch(name):
        raise UnsafeDatabaseError(
            f"база {name!r} не похожа на одноразовую: замер очищает таблицы домена. "
            "Нужна база crm_test*, *_test или *_bench (например, crm_bench), "
            "либо явный --force"
        )


# --------------------------------------------------------------------------------------
# Данные замера (переиспользуются DB-тестами: tests/db/workflow_factories.py)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchDataset:
    admin_id: uuid.UUID
    kam_ids: tuple[uuid.UUID, ...]
    workflow_id: uuid.UUID
    stages: dict[str, uuid.UUID]  # code → id
    change_id: uuid.UUID
    probe_deal_id: uuid.UUID
    university_id: uuid.UUID


def _stage_id(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else value.id


def delete_first_contact_ops(stages: Mapping[str, Any]) -> list[Operation]:
    """Пакет замера и тестов: ребро «Новая → Переговоры» (иначе «Переговоры» станут
    недостижимы) + удаление «Первичного контакта» с переносом заявок в «Новая».
    `stages` — `{code: WorkflowStage | UUID}`."""
    first_contact = stages["first_contact"]
    confirm = getattr(first_contact, "name", None) or "Первичный контакт"
    return service.parse_operations(
        [
            {
                "op": "add_transition",
                "transition": {
                    "from_status_id": str(_stage_id(stages["new"])),
                    "to_status_id": str(_stage_id(stages["negotiation"])),
                    "name": "К переговорам",
                },
            },
            {
                "op": "delete_status",
                "status_id": str(_stage_id(first_contact)),
                "migrate_to_status_id": str(_stage_id(stages["new"])),
                "confirm_name": confirm,
            },
        ]
    )


def raw_operations(operations: Sequence[Operation]) -> list[dict[str, Any]]:
    """JSONB-форма пакета — как её сохраняет роутер `POST /workflows/{id}/changes`."""
    return [op.model_dump(mode="json", exclude_none=True) for op in operations]


_BULK_INSERT_SQL = sa.text(
    """
INSERT INTO deal (pipeline, workflow_id, stage_id, title, university_id, responsible_user_id,
                  status, stage_entered_at, last_activity_at)
SELECT 'b2b', :wf, :stage, :prefix || ' ' || lpad(g::text, 6, '0'), :uni,
       (:resp)[1 + (g - 1) % cardinality(:resp)],
       :status, now() - make_interval(secs => :age), now() - make_interval(secs => :age)
  FROM generate_series(1, :n) AS g
"""
).bindparams(
    sa.bindparam("wf", type_=_UUID),
    sa.bindparam("stage", type_=_UUID),
    sa.bindparam("uni", type_=_UUID),
    sa.bindparam("resp", type_=ARRAY(_UUID)),
    sa.bindparam("prefix", type_=sa.Text),
    sa.bindparam("status", type_=sa.Text),
    sa.bindparam("age", type_=sa.Float),
    sa.bindparam("n", type_=sa.Integer),
)


async def bulk_insert_open_deals(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    stage_id: uuid.UUID,
    university_id: uuid.UUID,
    responsible_ids: Sequence[uuid.UUID],
    n: int,
    title_prefix: str = "Заявка",
    status: str = "open",
    entered_days_ago: float = 3.0,
) -> int:
    """N заявок B2B одним INSERT … SELECT generate_series (без истории): ответственные —
    по кругу `responsible_ids`, названия «{prefix} 000001…», `stage_entered_at` и
    `last_activity_at` — `entered_days_ago` дней назад. Возвращает число строк."""
    if n <= 0:
        return 0
    if not responsible_ids:
        raise ValueError("нужен хотя бы один ответственный")
    result = await session.execute(
        _BULK_INSERT_SQL,
        {
            "wf": workflow_id,
            "stage": stage_id,
            "uni": university_id,
            "resp": list(responsible_ids),
            "prefix": title_prefix,
            "status": status,
            "age": float(entered_days_ago) * 86400.0,
            "n": int(n),
        },
    )
    return int(result.rowcount or 0)


async def reset_tables(session: AsyncSession) -> None:
    """Очистка таблиц домена перед прогоном (только из CLI, на одноразовой базе)."""
    await session.execute(
        text(f"TRUNCATE TABLE {', '.join(RESET_TABLES)} RESTART IDENTITY CASCADE")
    )
    await session.execute(text("DELETE FROM app_setting WHERE key LIKE 'notify:%'"))


async def _bench_user(
    session: AsyncSession,
    username: str,
    *,
    roles: list[str],
    full_name: str,
    tg_chat_id: int | None = None,
) -> AppUser:
    user = (
        await session.execute(select(AppUser).where(AppUser.username == username))
    ).scalar_one_or_none()
    if user is None:
        user = AppUser(
            keycloak_sub=uuid.uuid5(uuid.NAMESPACE_URL, f"https://crm.local/bench/{username}"),
            username=username,
            email=username,
        )
        session.add(user)
    user.roles = roles
    user.full_name = full_name
    user.tg_chat_id = tg_chat_id
    user.manager_id = None
    user.is_active = True
    await session.flush()
    return user


async def prepare_dataset(session: AsyncSession, *, n: int, kams: int) -> BenchDataset:
    """Админ, K КАМов с tg_chat_id, воронки из сида, вуз, правило уведомлений, N открытых
    заявок на «Первичном контакте» + пробная на «Переговорах», ANALYZE и пакет
    `delete_first_contact_ops` (pending). Всё закоммичено — готово к конкурентному прогону.
    Таблицы домена должны быть пусты (`reset_tables`)."""
    admin = await _bench_user(
        session, "bench-admin@bench", roles=["admin"], full_name="Замеров Админ"
    )
    kam_users = [
        await _bench_user(
            session,
            f"bench-kam{i:02d}@bench",
            roles=["kam"],
            full_name=f"КАМ {i:02d}",
            tg_chat_id=700_000 + i,
        )
        for i in range(1, kams + 1)
    ]
    workflows = await seed_workflows(session, admin)
    workflow, stages = workflows["b2b"]
    university = University(name="Университет замера", region="Москва")
    session.add(university)
    session.add(
        NotificationRule(
            event_type=EVENT_REQUEST_TRANSITIONED,
            recipient_type="responsible",
            channel="telegram",
            params={},
            created_by=admin.id,
        )
    )
    await session.flush()
    await bulk_insert_open_deals(
        session,
        workflow_id=workflow.id,
        stage_id=stages["first_contact"].id,
        university_id=university.id,
        responsible_ids=[k.id for k in kam_users],
        n=n,
    )
    probe = Deal(
        pipeline="b2b",
        workflow_id=workflow.id,
        stage_id=stages["negotiation"].id,
        title="Пробная заявка",
        university_id=university.id,
        responsible_user_id=kam_users[0].id,
        created_by=admin.id,
    )
    session.add(probe)
    await session.commit()
    await session.execute(text("ANALYZE deal"))
    await session.execute(text("ANALYZE deal_stage_history"))
    await session.commit()

    operations = delete_first_contact_ops(stages)
    change = await service.create_change(
        session,
        workflow_id=workflow.id,
        author=admin,
        operations=operations,
        raw_operations=raw_operations(operations),
        comment="Замер массового переноса",
    )
    return BenchDataset(
        admin_id=admin.id,
        kam_ids=tuple(k.id for k in kam_users),
        workflow_id=workflow.id,
        stages={code: stage.id for code, stage in stages.items()},
        change_id=change.id,
        probe_deal_id=probe.id,
        university_id=university.id,
    )


async def legacy_migrate_deleted_stages(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    plan: ApplyPlan,
    stage_rows: dict[uuid.UUID, WorkflowStage],
) -> MigrationOutcome:
    """Дословная копия прежнего поштучного цикла `_execute_plan` (до пакетного переноса):
    `SELECT … FOR UPDATE` заявок этапа, ORM-UPDATE каждой (отдельный `UPDATE … WHERE
    xmin=?`), строка истории и `emit_event_notifications` на каждую заявку. Только для
    замера «до»; мягкое удаление этапов и рёбер делает сервис. Сводок нет (`groups=[]`)."""
    outcome = MigrationOutcome()
    for item in plan.deleted_stages:
        target_row = (
            stage_rows.get(item.migrate_to_status_id) if item.migrate_to_status_id else None
        )
        deals = (
            (
                await session.execute(
                    select(Deal)
                    .where(Deal.stage_id == item.stage_id, Deal.status == "open")
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for deal in deals:
            assert target_row is not None  # гарантировано V-8 (E_MAPPING_REQUIRED)
            deal.stage_id = target_row.id
            # stage_entered_at НЕ трогаем: перестройка схемы не «омолаживает» заявку
            deal.last_activity_at = service._now()
            session.add(
                service._history_row(
                    deal,
                    from_stage=stage_rows[item.stage_id],
                    to_stage=target_row,
                    kind="system_migration",
                    reason="stage_deleted",
                    actor_id=None,
                    comment=(
                        f"Статус «{item.stage_name}» удалён администратором, "
                        f"заявка перенесена в «{target_row.name}»"
                    ),
                )
            )
            await emit_event_notifications(
                session,
                event_type=EVENT_REQUEST_TRANSITIONED,
                entity_type="deal",
                entity_id=deal.id,
                subject="Заявка перенесена",
                body=(
                    f"Статус «{item.stage_name}» удалён администратором, заявка "
                    f"«{deal.title}» перенесена в «{target_row.name}»"
                ),
                payload=service._transition_payload(
                    deal, target_row, migrated=True, from_status=item.stage_name
                ),
                deal=deal,
            )
            outcome.migrated_total += 1
        if deals:
            outcome.by_status.append(
                {
                    "from": item.stage_name,
                    "to": target_row.name if target_row else "",
                    "count": len(deals),
                }
            )
    # прежний код отправлял UPDATE-ы на финальном flush `_execute_plan`; здесь — сразу,
    # чтобы migrate_ms legacy-режима включал их (суммарное время применения то же)
    await session.flush()
    return outcome


# --------------------------------------------------------------------------------------
# Прогон
# --------------------------------------------------------------------------------------


@dataclass
class BenchResult:
    n: int
    mode: str
    kams: int
    migrated: int
    approve_ms: float  # весь approve: строка пакета, лок, применение, COMMIT, эффекты
    lock_wait_ms: float  # ожидание эксклюзивного лока
    lock_held_ms: float  # захват лока → возврат apply_change
    migrate_ms: float  # перенос заявок (по отчёту применения)
    probe_wait_ms: float | None  # сколько ждал параллельный переход пробной заявки
    probe_outcome: str  # 'ok' | код ошибки перехода (например, 'workflow_busy')
    history_rows: int
    notification_rows: int  # строки outbox, порождённые переносом
    digest_notifications: int
    wal_mb: float | None  # прирост WAL всего кластера PG за прогон — мерить на тихом сервере


@dataclass(frozen=True)
class BenchEnvironment:
    postgres: str
    platform: str
    python: str
    date: str
    extra: dict[str, Any] = field(default_factory=dict)


async def _scalar(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).scalar()


async def _wal_lsn() -> str | None:
    try:
        async with get_session_factory()() as session:
            return await _scalar(session, "SELECT pg_current_wal_lsn()::text")
    except Exception:  # noqa: BLE001 — WAL-метрика необязательна
        return None


async def _wal_mb_since(lsn: str | None) -> float | None:
    if lsn is None:
        return None
    try:
        async with get_session_factory()() as session:
            # параметр — text: у asyncpg нет кодека pg_lsn
            diff = await _scalar(
                session,
                "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), CAST(CAST(:lsn AS text) AS pg_lsn))",
                lsn=lsn,
            )
        return float(diff) / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return None


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


async def run_once(n: int, mode: str, *, kams: int = DEFAULT_KAMS) -> BenchResult:
    """Один прогон: очистка, набор данных, применение пакета с параллельным переходом."""
    if mode not in MODES:
        raise ValueError(f"неизвестный режим {mode!r}: {', '.join(MODES)}")
    factory = get_session_factory()
    async with factory() as session:
        await reset_tables(session)
        await session.commit()
        dataset = await prepare_dataset(session, n=n, kams=kams)

    lock_acquired = asyncio.Event()
    marks: dict[str, float] = {}
    original_lock = service.acquire_exclusive_workflow_lock
    original_migrate = migration.migrate_deleted_stages

    async def timed_lock(session: AsyncSession, workflow_id: uuid.UUID, **kwargs: Any) -> None:
        await original_lock(session, workflow_id, **kwargs)
        marks["locked"] = time.perf_counter()
        lock_acquired.set()

    async def apply() -> tuple[dict[str, Any], float, float]:
        async with factory() as session:
            admin = await session.get(AppUser, dataset.admin_id)
            assert admin is not None
            started = time.perf_counter()
            _wcr, report = await service.apply_change(
                session, change_id=dataset.change_id, approver=admin
            )
            return report, started, time.perf_counter()

    async def probe() -> tuple[float, str]:
        await asyncio.wait_for(lock_acquired.wait(), timeout=600)
        await asyncio.sleep(PROBE_DELAY_SECONDS)
        async with factory() as session:
            admin = await session.get(AppUser, dataset.admin_id)
            assert admin is not None
            version = await _scalar(
                session,
                "SELECT xmin::text::bigint FROM deal WHERE id = :id",
                id=dataset.probe_deal_id,
            )
            started = time.perf_counter()
            try:
                await service.transition_deal(
                    session,
                    deal_id=dataset.probe_deal_id,
                    to_status_id=dataset.stages["contract_prep"],
                    actor=admin,
                    comment=None,
                    version=int(version),
                )
            except ApiError as exc:  # workflow_busy — ожидание упёрлось в lock_timeout
                return time.perf_counter() - started, exc.code
            return time.perf_counter() - started, "ok"

    wal_before = await _wal_lsn()
    original_timeout = locks.TRANSITION_LOCK_TIMEOUT_MS
    service.acquire_exclusive_workflow_lock = timed_lock  # type: ignore[assignment]
    if mode == "legacy":
        migration.migrate_deleted_stages = legacy_migrate_deleted_stages  # type: ignore[assignment]
        locks.TRANSITION_LOCK_TIMEOUT_MS = LEGACY_PROBE_LOCK_TIMEOUT_MS
    try:
        (report, started, finished), (probe_seconds, probe_outcome) = await asyncio.gather(
            apply(), probe()
        )
    finally:
        service.acquire_exclusive_workflow_lock = original_lock  # type: ignore[assignment]
        migration.migrate_deleted_stages = original_migrate  # type: ignore[assignment]
        locks.TRANSITION_LOCK_TIMEOUT_MS = original_timeout
    wal_mb = await _wal_mb_since(wal_before)

    async with factory() as session:
        history_rows = await _scalar(
            session, "SELECT count(*) FROM deal_stage_history WHERE kind = 'system_migration'"
        )
        notification_rows = await _scalar(
            session, "SELECT count(*) FROM notification WHERE payload->>'migrated' = 'true'"
        )
    timings = report.get("timings") or {}
    return BenchResult(
        n=n,
        mode=mode,
        kams=kams,
        migrated=int(report["migrated_requests"]),
        approve_ms=_ms(finished - started),
        lock_wait_ms=float(timings.get("lock_wait_ms", 0)),
        lock_held_ms=_ms(finished - marks["locked"]),
        migrate_ms=float(timings.get("migrate_ms", 0)),
        probe_wait_ms=_ms(probe_seconds),
        probe_outcome=probe_outcome,
        history_rows=int(history_rows or 0),
        notification_rows=int(notification_rows or 0),
        digest_notifications=int(report.get("digest_notifications") or 0),
        wal_mb=round(wal_mb, 1) if wal_mb is not None else None,
    )


async def collect_environment() -> BenchEnvironment:
    async with get_session_factory()() as session:
        postgres = await _scalar(session, "SHOW server_version")
    return BenchEnvironment(
        postgres=f"PostgreSQL {str(postgres).split()[0]}",
        platform=f"{platform.system()} {platform.release()} {platform.machine()}",
        python=f"Python {platform.python_version()}",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


# --------------------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------------------


def fmt_int(value: int | float | None) -> str:
    if value is None:
        return "—"
    return f"{round(value):,}".replace(",", " ")


def fmt_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    whole = f"{value:,.{digits}f}"
    return whole.replace(",", " ").replace(".", ",")


def render_markdown(results: Sequence[BenchResult], env: BenchEnvironment) -> str:
    lines = [
        "## Замер массового переноса заявок",
        "",
        f"{env.postgres}, {env.platform}, {env.python}, {env.date}",
        "",
        "| N | Режим | Применение, мс | Лок удерживался, мс | Ожидание перехода, мс "
        "| Строк истории | Уведомлений | WAL, МБ |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        probe = fmt_int(r.probe_wait_ms)
        if r.probe_outcome != "ok":
            probe += f" ({r.probe_outcome})"
        lines.append(
            f"| {fmt_int(r.n)} | {MODE_LABELS.get(r.mode, r.mode)} | {fmt_int(r.approve_ms)} "
            f"| {fmt_int(r.lock_held_ms)} | {probe} "
            f"| {fmt_int(r.history_rows)} | {fmt_int(r.notification_rows)} "
            f"| {fmt_float(r.wal_mb)} |"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _parse_sizes(raw: str) -> list[int]:
    try:
        sizes = [int(part.replace("_", "")) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--sizes: ожидаются целые через запятую: {raw}") from exc
    if not sizes or any(n < 1 for n in sizes):
        raise argparse.ArgumentTypeError("--sizes: размеры должны быть положительными")
    return sizes


def _parse_modes(raw: str) -> list[str]:
    modes = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [m for m in modes if m not in MODES]
    if not modes or unknown:
        raise argparse.ArgumentTypeError(f"--modes: допустимы {', '.join(MODES)}")
    return modes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.bench_migration",
        description="Замер массового переноса заявок при удалении этапа (одноразовая БД)",
    )
    parser.add_argument(
        "--sizes", type=_parse_sizes, default=list(DEFAULT_SIZES), help="1000,10000,50000"
    )
    parser.add_argument("--kams", type=int, default=DEFAULT_KAMS, help="число КАМов")
    parser.add_argument("--modes", type=_parse_modes, default=list(MODES), help="batch,legacy")
    parser.add_argument(
        "--legacy-max",
        type=int,
        default=DEFAULT_LEGACY_MAX,
        help="legacy-режим пропускается при N больше этого (50 000 поштучно — ≈2,5 мин)",
    )
    parser.add_argument("--json", dest="json_path", help="сохранить результаты в JSON")
    parser.add_argument(
        "--force", action="store_true", help="разрешить базу с другим именем (будет очищена!)"
    )
    return parser


def _err(message: str) -> None:
    sys.stderr.write(f"bench_migration: {message}\n")


async def _amain(args: argparse.Namespace) -> int:
    try:
        async with get_session_factory()() as session:
            database = await _scalar(session, "SELECT current_database()")
        try:
            applied = await applied_revision()
        except Exception:  # noqa: BLE001 — нет alembic_version: схема не мигрирована
            applied = None
        if not args.force:
            try:
                assert_safe_database(database)
            except UnsafeDatabaseError as exc:
                _err(str(exc))
                return 2
        head = script_head()
        if applied != head:
            _err(
                f"схема базы {database!r} не на head Alembic (applied={applied!r}, "
                f"head={head!r}) — выполните `alembic upgrade head`"
            )
            return 2
        env = await collect_environment()
        results: list[BenchResult] = []
        for n in args.sizes:
            for mode in args.modes:
                if mode == "legacy" and n > args.legacy_max:
                    _err(f"N={n}: legacy пропущен (--legacy-max {args.legacy_max})")
                    continue
                result = await run_once(n, mode, kams=args.kams)
                sys.stderr.write(
                    f"N={n} {mode}: применение {result.approve_ms} мс, "
                    f"лок {result.lock_held_ms} мс, переход ждал {result.probe_wait_ms} мс "
                    f"({result.probe_outcome})\n"
                )
                results.append(result)
        sys.stdout.write(render_markdown(results, env))
        if args.json_path:
            with open(args.json_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {"environment": asdict(env), "results": [asdict(r) for r in results]},
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )
        return 0
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    args = build_parser().parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
