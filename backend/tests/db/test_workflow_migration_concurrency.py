"""Конкурентность массового переноса (workflow-engine.md §6.5): настоящие COMMIT на
отдельных соединениях (`db_committed_sessionmaker`), advisory-локи видны в `pg_locks`.

Сценарии: переход во время применения ждёт и проходит; переход переносимой заявки не
теряет обновление; смешанная нагрузка без deadlock; ограниченное ожидание
(`workflow_busy`) для перехода и для применения; двойной approve.
Каждый сценарий — под `asyncio.wait_for(…, 60)`.
"""

from __future__ import annotations

import asyncio
import contextvars
import random
import time
import uuid
from collections.abc import Awaitable
from typing import Any, TypeVar

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import ApiError
from app.models import AppUser
from app.modules.workflow import locks, service
from app.tools.bench_migration import BenchDataset, prepare_dataset

from .workflow_factories import bulk_insert_open_deals

N_CONC = 2_000
SCENARIO_TIMEOUT = 60
HOLD_SECONDS = 0.3

T = TypeVar("T")
Maker = async_sessionmaker[AsyncSession]


async def _bounded(awaitable: Awaitable[T]) -> T:
    return await asyncio.wait_for(awaitable, SCENARIO_TIMEOUT)


async def _scalar(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(sa.text(sql), params)).scalar()


async def _prepare(maker: Maker, *, n: int = N_CONC, kams: int = 5) -> BenchDataset:
    async with maker() as session:
        return await prepare_dataset(session, n=n, kams=kams)


async def _version(session: AsyncSession, deal_id: uuid.UUID) -> int:
    return int(
        await _scalar(session, "SELECT xmin::text::bigint FROM deal WHERE id = :id", id=deal_id)
    )


def _hold_lock_after_acquire(
    monkeypatch: pytest.MonkeyPatch, acquired: asyncio.Event, hold: float = HOLD_SECONDS
) -> dict[str, float]:
    """Подмена `service.acquire_exclusive_workflow_lock`: после захвата ставит Event и
    держит лок `hold` секунд (имитация долгого применения)."""
    marks: dict[str, float] = {}
    original = service.acquire_exclusive_workflow_lock

    async def holding(session: AsyncSession, workflow_id: uuid.UUID, **kwargs: Any) -> None:
        await original(session, workflow_id, **kwargs)
        marks["locked"] = time.perf_counter()
        acquired.set()
        await asyncio.sleep(hold)

    monkeypatch.setattr(service, "acquire_exclusive_workflow_lock", holding)
    return marks


async def _apply(maker: Maker, ds: BenchDataset) -> dict[str, Any]:
    async with maker() as session:
        admin = await session.get(AppUser, ds.admin_id)
        assert admin is not None
        _, report = await service.apply_change(session, change_id=ds.change_id, approver=admin)
        return report


async def _transition(
    maker: Maker,
    ds: BenchDataset,
    deal_id: uuid.UUID,
    to_code: str,
    *,
    version: int | None = None,
) -> str:
    """'ok' | код ApiError (409); любые другие исключения пробрасываются (тест падает)."""
    async with maker() as session:
        admin = await session.get(AppUser, ds.admin_id)
        assert admin is not None
        if version is None:
            version = await _version(session, deal_id)
        try:
            await service.transition_deal(
                session,
                deal_id=deal_id,
                to_status_id=ds.stages[to_code],
                actor=admin,
                comment=None,
                version=version,
            )
        except ApiError as exc:
            assert exc.status_code == 409, (exc.status_code, exc.code, exc.message)
            return exc.code
        return "ok"


async def _history(maker: Maker, deal_id: uuid.UUID | None = None) -> dict[str, int]:
    async with maker() as session:
        rows = await session.execute(
            sa.text(
                "SELECT kind, count(*) FROM deal_stage_history "
                "WHERE (CAST(:d AS uuid) IS NULL OR deal_id = CAST(:d AS uuid)) GROUP BY kind"
            ),
            {"d": str(deal_id) if deal_id else None},
        )
        return {kind: int(count) for kind, count in rows.all()}


async def _wcr_status(maker: Maker, change_id: uuid.UUID) -> str:
    async with maker() as session:
        return await _scalar(
            session, "SELECT status FROM workflow_change_request WHERE id = :id", id=change_id
        )


# --------------------------------------------------------------------------------------
# 7. Переход во время применения ждёт и проходит
# --------------------------------------------------------------------------------------


async def test_parallel_transition_waits_then_succeeds(
    db_committed_sessionmaker: Maker, db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    ds = await _prepare(maker)
    acquired = asyncio.Event()
    marks = _hold_lock_after_acquire(monkeypatch, acquired)

    async def transition() -> tuple[str, float, str]:
        await acquired.wait()
        async with maker() as session:
            version = await _version(session, ds.probe_deal_id)
        started = time.perf_counter()
        outcome = await _transition(maker, ds, ds.probe_deal_id, "contract_prep", version=version)
        waited = time.perf_counter() - started
        # переход получил лок только после COMMIT применения
        return outcome, waited, await _wcr_status(maker, ds.change_id)

    async def observe() -> int:
        await acquired.wait()
        async with db_engine.connect() as conn:
            for _ in range(200):
                waiting = (
                    await conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype = 'advisory' AND NOT granted"
                        )
                    )
                ).scalar()
                await conn.rollback()
                if waiting:
                    return int(waiting)
                await asyncio.sleep(0.01)
        return 0

    report, (outcome, waited, status_seen), waiting = await _bounded(
        asyncio.gather(_apply(maker, ds), transition(), observe())
    )
    assert waiting >= 1  # переход стоял в очереди за эксклюзивным локом применения
    assert outcome == "ok"
    assert waited >= HOLD_SECONDS - 0.05
    assert status_seen == "applied"
    assert report["migrated_requests"] == N_CONC
    assert "locked" in marks
    assert await _history(maker) == {"system_migration": N_CONC, "manual": 1}
    assert await _history(maker, ds.probe_deal_id) == {"manual": 1}
    async with maker() as session:
        stage = await _scalar(
            session, "SELECT stage_id FROM deal WHERE id = :id", id=ds.probe_deal_id
        )
    assert stage == ds.stages["contract_prep"]


# --------------------------------------------------------------------------------------
# 8. Переход переносимой заявки: без потерянного обновления
# --------------------------------------------------------------------------------------


async def test_transition_of_migrating_deal_no_lost_update(
    db_committed_sessionmaker: Maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    ds = await _prepare(maker)
    async with maker() as session:
        deal_id = await _scalar(
            session,
            "SELECT id FROM deal WHERE stage_id = :s ORDER BY title LIMIT 1",
            s=ds.stages["first_contact"],
        )
        version = await _version(session, deal_id)  # КАМ открыл карточку до approve
    acquired = asyncio.Event()
    _hold_lock_after_acquire(monkeypatch, acquired)

    async def transition() -> str:
        await acquired.wait()
        return await _transition(maker, ds, deal_id, "negotiation", version=version)

    report, outcome = await _bounded(asyncio.gather(_apply(maker, ds), transition()))
    assert report["migrated_requests"] == N_CONC
    assert outcome in ("stale_version", "workflow_invalid_transition")
    assert await _history(maker, deal_id) == {"system_migration": 1}
    async with maker() as session:
        stage = await _scalar(session, "SELECT stage_id FROM deal WHERE id = :id", id=deal_id)
    assert stage == ds.stages["new"]


# --------------------------------------------------------------------------------------
# 9. Смешанная нагрузка во время применения: только 200/409, без deadlock и потерь
# --------------------------------------------------------------------------------------


async def test_no_deadlock_under_mixed_load(
    db_committed_sessionmaker: Maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20 параллельных переходов по случайным заявкам во время применения:
    - 4 «ранних» перехода с «Первичного контакта» уже держат shared-лок (и строку заявки),
      когда применение просит эксклюзивный: применение их дожидается, они проходят (200),
      их заявки уходят вручную и не переносятся;
    - остальные встают в очередь ЗА ждущим эксклюзивным локом (PostgreSQL не пускает новый
      shared вперёд): переходы с удаляемого этапа получают 409 (заявка уже перенесена),
      переходы «Переговоры → Подготовка договора» — 200.
    Только 200/409, без deadlock и 500; каждая заявка этапа ровно один раз ушла либо
    вручную, либо переносом."""
    maker = db_committed_sessionmaker
    ds = await _prepare(maker)
    async with maker() as session:
        await bulk_insert_open_deals(
            session,
            workflow_id=ds.workflow_id,
            stage_id=ds.stages["negotiation"],
            university_id=ds.university_id,
            responsible_ids=list(ds.kam_ids),
            n=10,
            title_prefix="Переговоры",
        )
        await session.commit()
        rng = random.Random(20261016)
        fc_ids = list(
            (
                await session.execute(
                    sa.text("SELECT id FROM deal WHERE stage_id = :s ORDER BY id"),
                    {"s": ds.stages["first_contact"]},
                )
            ).scalars()
        )
        neg_ids = list(
            (
                await session.execute(
                    sa.text("SELECT id FROM deal WHERE title LIKE 'Переговоры %' ORDER BY id")
                )
            ).scalars()
        )
        picked_fc = rng.sample(fc_ids, 12)
        picked_neg = rng.sample(neg_ids, 8)
        versions = {d: await _version(session, d) for d in [*picked_fc, *picked_neg]}
    early = set(picked_fc[:4])

    # «ранние» переходы держат shared-лок 150 мс — применение обязано их дождаться
    slow = contextvars.ContextVar("slow_transition", default=False)
    original_shared = service.acquire_shared_workflow_lock

    async def shared_then_maybe_slow(
        session: AsyncSession, workflow_id: uuid.UUID, **kwargs: Any
    ) -> None:
        await original_shared(session, workflow_id, **kwargs)
        if slow.get():
            await asyncio.sleep(0.15)

    monkeypatch.setattr(service, "acquire_shared_workflow_lock", shared_then_maybe_slow)
    acquired = asyncio.Event()
    _hold_lock_after_acquire(monkeypatch, acquired, hold=0.2)

    async def worker(deal_id: uuid.UUID, to_code: str, delay: float) -> tuple[uuid.UUID, str]:
        slow.set(deal_id in early)
        await asyncio.sleep(delay)
        outcome = await _transition(maker, ds, deal_id, to_code, version=versions[deal_id])
        return deal_id, outcome

    async def delayed_apply() -> dict[str, Any]:
        await asyncio.sleep(0.05)  # ранние переходы успевают взять shared-лок
        return await _apply(maker, ds)

    jobs = [worker(d, "negotiation", 0 if d in early else rng.uniform(0.1, 0.4)) for d in picked_fc]
    jobs += [worker(d, "contract_prep", rng.uniform(0, 0.4)) for d in picked_neg]
    report, *outcomes = await _bounded(asyncio.gather(delayed_apply(), *jobs))

    results = dict(outcomes)
    assert set(results.values()) <= {"ok", "stale_version", "workflow_invalid_transition"}
    assert all(results[d] == "ok" for d in early)  # начатые до применения — прошли
    assert all(results[d] != "ok" for d in picked_fc if d not in early)  # уже перенесены
    assert all(results[d] == "ok" for d in picked_neg)  # переговоры перенос не задел
    assert report["timings"]["lock_wait_ms"] >= 80  # применение ждало хвост переходов
    history = await _history(maker)
    # каждая заявка «Первичного контакта» либо ушла вручную до лока, либо перенесена
    assert report["migrated_requests"] == history["system_migration"] == N_CONC - len(early)
    assert history["manual"] == sum(1 for value in results.values() if value == "ok")
    async with maker() as session:
        doubled = await _scalar(
            session,
            "SELECT count(*) FROM (SELECT deal_id FROM deal_stage_history "
            "GROUP BY deal_id HAVING count(*) FILTER (WHERE kind = 'system_migration') > 1 "
            "OR (count(*) FILTER (WHERE kind = 'system_migration') = 1 "
            "    AND count(*) FILTER (WHERE kind = 'manual' AND from_stage_id = :fc) = 1)) t",
            fc=ds.stages["first_contact"],
        )
        left = await _scalar(
            session,
            "SELECT count(*) FROM deal WHERE stage_id = :s AND status = 'open'",
            s=ds.stages["first_contact"],
        )
    assert doubled == 0  # ни одна заявка не ушла с этапа дважды
    assert left == 0


# --------------------------------------------------------------------------------------
# 10–11. Ограниченное ожидание: workflow_busy
# --------------------------------------------------------------------------------------


async def _hold_advisory(engine: AsyncEngine, workflow_id: uuid.UUID, *, exclusive: bool):
    conn = await engine.connect()
    await conn.begin()
    fn = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await conn.execute(
        sa.text(f"SELECT {fn}(hashtextextended('wf:' || :w, 0))"), {"w": str(workflow_id)}
    )
    return conn


async def test_transition_lock_timeout_gives_workflow_busy(
    db_committed_sessionmaker: Maker, db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    ds = await _prepare(maker, n=10)
    monkeypatch.setattr(locks, "TRANSITION_LOCK_TIMEOUT_MS", 200)
    holder = await _hold_advisory(db_engine, ds.workflow_id, exclusive=True)
    try:

        async def attempt() -> tuple[ApiError, float]:
            async with maker() as session:
                admin = await session.get(AppUser, ds.admin_id)
                assert admin is not None
                version = await _version(session, ds.probe_deal_id)
                started = time.perf_counter()
                with pytest.raises(ApiError) as caught:
                    await service.transition_deal(
                        session,
                        deal_id=ds.probe_deal_id,
                        to_status_id=ds.stages["contract_prep"],
                        actor=admin,
                        comment=None,
                        version=version,
                    )
                return caught.value, time.perf_counter() - started

        error, elapsed = await _bounded(attempt())
    finally:
        await holder.rollback()
        await holder.close()
    assert error.status_code == 409 and error.code == "workflow_busy"
    assert error.headers == {"Retry-After": "2"}
    assert 0.15 <= elapsed < 2.0  # ждал lock_timeout, а не бесконечно
    assert await _history(maker, ds.probe_deal_id) == {}
    # лок освобождён — повтор того же действия проходит
    assert await _bounded(_transition(maker, ds, ds.probe_deal_id, "contract_prep")) == "ok"


async def test_apply_lock_timeout_keeps_pending(
    db_committed_sessionmaker: Maker, db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    maker = db_committed_sessionmaker
    ds = await _prepare(maker)
    monkeypatch.setattr(locks, "APPLY_LOCK_TIMEOUT_MS", 200)
    holder = await _hold_advisory(db_engine, ds.workflow_id, exclusive=False)  # «хвост перехода»
    try:
        with pytest.raises(ApiError) as caught:
            await _bounded(_apply(maker, ds))
    finally:
        await holder.rollback()
        await holder.close()
    assert caught.value.code == "workflow_busy"
    assert caught.value.headers == {"Retry-After": "2"}
    assert await _wcr_status(maker, ds.change_id) == "pending"  # пакет не виноват
    assert await _history(maker) == {}

    report = await _bounded(_apply(maker, ds))
    assert report["migrated_requests"] == N_CONC
    assert await _wcr_status(maker, ds.change_id) == "applied"


# --------------------------------------------------------------------------------------
# 12. Двойной approve
# --------------------------------------------------------------------------------------


async def test_concurrent_double_approve(db_committed_sessionmaker: Maker) -> None:
    maker = db_committed_sessionmaker
    ds = await _prepare(maker)
    results = await _bounded(
        asyncio.gather(_apply(maker, ds), _apply(maker, ds), return_exceptions=True)
    )
    reports = [r for r in results if isinstance(r, dict)]
    errors = [r for r in results if isinstance(r, BaseException)]
    assert len(reports) == 1 and len(errors) == 1
    assert isinstance(errors[0], ApiError) and errors[0].code == "conflict"
    assert reports[0]["migrated_requests"] == N_CONC
    assert await _history(maker) == {"system_migration": N_CONC}
    async with maker() as session:
        audit = await _scalar(
            session, "SELECT count(*) FROM audit_log WHERE action = 'workflow.change_applied'"
        )
        digests = await _scalar(
            session, "SELECT count(*) FROM notification WHERE entity_id = :id", id=ds.change_id
        )
    assert audit == 1
    assert digests == len(ds.kam_ids)
