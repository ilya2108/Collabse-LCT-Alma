"""Advisory-локи PostgreSQL для гонки «переход vs применение схемы» (workflow-engine.md §6.5).

Порядок локов — единый для всех путей, поэтому цикла ожидания (deadlock) нет:

1. строка пакета `workflow_change_request FOR UPDATE` — только применение пакета
   (второй approve ждёт и получает 409 «Пакет уже обработан»);
2. advisory-лок воронки, ключ `hashtextextended('wf:' || workflow_id, 0)` (bigint,
   считается на стороне PG; у каждой воронки свой — правка B2B не задевает B2C):
   - `transition_deal` / `reopen_deal` берут SHARED: переходы параллельны между собой;
   - применение схемы берёт EXCLUSIVE: дожидается хвоста начатых переходов и ставит
     новые в очередь на время применения. PostgreSQL не пускает новый shared впереди
     ждущего exclusive — применение не голодает;
3. строки заявок (`FOR UPDATE` перехода, `UPDATE` пакетного переноса).

Локи транзакционные (`_xact_`): освобождаются на COMMIT/ROLLBACK автоматически.

Ожидание ограничено: перед захватом выставляется `SET LOCAL lock_timeout` (через
`set_config(..., true)` — до конца транзакции; заодно ограничивает и `FOR UPDATE`
строки заявки). По истечении PostgreSQL отдаёт SQLSTATE 55P03, а
`lock_timeout_as_busy` превращает его в `409 workflow_busy` с `Retry-After: 2`.

Что видят пользователи:
- чтение (доски, карточки, отчёты) не блокируется никогда — MVCC: до COMMIT применения
  все видят старую схему, после — новую;
- КАМ, двигающий карточку в момент применения, просто ждёт (доли секунды); затем
  переход проходит либо возвращается 409 («заявка изменена» / «переход не разрешён»),
  доска перечитывается автоматически;
- ожидание перехода ограничено `TRANSITION_LOCK_TIMEOUT_MS` (10 с), применения —
  `APPLY_LOCK_TIMEOUT_MS` (30 с; пакет при этом остаётся на согласовании).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError

TRANSITION_LOCK_TIMEOUT_MS = 10_000  # переходы/reopen ждут применения схемы не дольше
APPLY_LOCK_TIMEOUT_MS = 30_000  # применение ждёт хвоста переходов не дольше
LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE lock_not_available (сработал lock_timeout)

WORKFLOW_BUSY_RETRY_AFTER_SECONDS = 2
WORKFLOW_BUSY_MESSAGE = (
    "Схема процесса сейчас обновляется — повторите действие через несколько секунд"
)


def workflow_busy_error(message: str | None = None) -> ApiError:
    """409 `workflow_busy`: лок воронки не получен за отведённое время, операция
    не выполнена — повтор безопасен (клиент ждёт `Retry-After` секунд)."""
    return ApiError(
        409,
        "workflow_busy",
        message or WORKFLOW_BUSY_MESSAGE,
        headers={"Retry-After": str(WORKFLOW_BUSY_RETRY_AFTER_SECONDS)},
    )


def _sqlstate(exc: BaseException) -> str | None:
    """SQLSTATE из исключения SQLAlchemy (`exc.orig` — адаптер asyncpg с `sqlstate`/
    `pgcode`, его `__cause__` — исходная ошибка asyncpg) или из самой ошибки драйвера."""
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None), exc):
        if candidate is None:
            continue
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return str(code)
    return None


def is_lock_timeout(exc: BaseException) -> bool:
    """True — запрос упал по `lock_timeout` (SQLSTATE 55P03), а не по другой причине."""
    return _sqlstate(exc) == LOCK_NOT_AVAILABLE


@asynccontextmanager
async def lock_timeout_as_busy(message: str | None = None) -> AsyncIterator[None]:
    """Превращает истёкший `lock_timeout` (55P03) в `workflow_busy_error()`; остальные
    ошибки БД пробрасывает как есть. Транзакция после 55P03 прервана — откат на
    вызывающей стороне (обработчик запроса закрывает сессию с ROLLBACK)."""
    try:
        yield
    except DBAPIError as exc:
        if is_lock_timeout(exc):
            raise workflow_busy_error(message) from exc
        raise


async def _set_lock_timeout(session: AsyncSession, timeout_ms: int) -> None:
    # is_local=true ≡ SET LOCAL: действует до конца текущей транзакции
    await session.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{max(int(timeout_ms), 1)}ms"},
    )


async def acquire_shared_workflow_lock(
    session: AsyncSession, workflow_id: uuid.UUID, *, timeout_ms: int | None = None
) -> None:
    """SHARED-лок воронки (переходы, reopen). `timeout_ms=None` —
    `TRANSITION_LOCK_TIMEOUT_MS` на момент вызова (тесты подменяют константу)."""
    await _set_lock_timeout(
        session, TRANSITION_LOCK_TIMEOUT_MS if timeout_ms is None else timeout_ms
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended('wf:' || :wid, 0))"),
        {"wid": str(workflow_id)},
    )


async def acquire_exclusive_workflow_lock(
    session: AsyncSession, workflow_id: uuid.UUID, *, timeout_ms: int | None = None
) -> None:
    """EXCLUSIVE-лок воронки (применение пакета схемы, удаление роли). `timeout_ms=None` —
    `APPLY_LOCK_TIMEOUT_MS` на момент вызова. Таймаут — DBAPIError 55P03: оборачивайте
    вызов в `lock_timeout_as_busy()`."""
    await _set_lock_timeout(session, APPLY_LOCK_TIMEOUT_MS if timeout_ms is None else timeout_ms)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('wf:' || :wid, 0))"),
        {"wid": str(workflow_id)},
    )
