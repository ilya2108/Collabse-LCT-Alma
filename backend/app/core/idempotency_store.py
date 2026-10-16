"""Хранилище ключей идемпотентности в PostgreSQL и их очистка (api-contract.md §1.6,
data-model.md §9.6).

Почему PG, а не KeyDB: KeyDB по архитектуре эфемерный (`emptyDir`, падение кэша не роняет
CRM), а гарантия «не выполнить дважды» не может жить в кэше без персистентности. PG даёт
UNIQUE + `INSERT … ON CONFLICT` + `SELECT … FOR UPDATE`, переживает рестарты и общий для
всех реплик HPA.

Каждая операция — отдельная короткая транзакция на своём соединении пула: во время
работы обработчика соединение не держится. Время везде — `now()` на стороне БД, чтобы
часы реплик не расходились. Тело ответа хранится Fernet-шифртекстом (ответы мутаций
содержат ПДн, data-model.md §8); тело запроса не хранится — только его хеш.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta

import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.core.idempotency import (
    ClaimResult,
    Decision,
    KeyRequest,
    KeySnapshot,
    StoredResponse,
    Ticket,
    decide,
)
from app.core.pii import get_pii_crypto
from app.models.idempotency import IdempotencyKey

# INSERT упёрся в чужую строку, а SELECT её уже не нашёл (успели освободить) — повторить
_CLAIM_ATTEMPTS = 3

_t = IdempotencyKey


class PgIdempotencyStore:
    """Реализация протокола `IdempotencyStore` на таблице `idempotency_key`."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | None = None,
        *,
        ttl_hours: int,
        lease_seconds: int,
    ) -> None:
        self._session_factory = session_factory  # None → get_session_factory() лениво
        self._ttl = timedelta(hours=ttl_hours)
        self._lease = timedelta(seconds=lease_seconds)

    def _session(self) -> AsyncSession:
        if self._session_factory is not None:
            return self._session_factory()
        return get_session_factory()()

    async def claim(self, req: KeyRequest) -> ClaimResult:
        """Заявка на ключ: новая строка → owned; иначе решение по существующей (decide)."""
        async with self._session() as session:
            for _ in range(_CLAIM_ATTEMPTS):
                token = uuid.uuid4()
                inserted = (
                    await session.execute(
                        pg_insert(_t)
                        .values(
                            principal=req.principal,
                            idem_key=req.key,
                            method=req.method,
                            path=req.path,
                            request_hash=req.request_hash,
                            status="processing",
                            lock_token=token,
                            locked_until=func.now() + self._lease,
                            first_request_id=req.request_id,
                            expires_at=func.now() + self._ttl,
                        )
                        .on_conflict_do_nothing(constraint="uq_idempotency_key_principal_key")
                        .returning(_t.id)
                    )
                ).scalar_one_or_none()
                if inserted is not None:
                    await session.commit()
                    return ClaimResult("owned", Ticket(inserted, token))

                row = (
                    await session.execute(
                        select(
                            _t.id,
                            _t.method,
                            _t.path,
                            _t.request_hash,
                            _t.status,
                            _t.response_status,
                            _t.response_headers,
                            _t.response_body_omitted,
                            # сырой bytea: расшифровка — здесь, InvalidToken → replay_unavailable
                            sa.type_coerce(_t.response_body, sa.LargeBinary).label("body"),
                            (_t.expires_at <= func.now()).label("expired"),
                            func.coalesce(_t.locked_until < func.now(), sa.true()).label(
                                "lease_expired"
                            ),
                        )
                        .where(_t.principal == req.principal, _t.idem_key == req.key)
                        .with_for_update()
                    )
                ).one_or_none()
                if row is None:
                    continue

                snapshot = KeySnapshot(
                    method=row.method,
                    path=row.path,
                    request_hash=row.request_hash.rstrip(),
                    status=row.status,
                    expired=bool(row.expired),
                    lease_expired=bool(row.lease_expired),
                )
                decision = decide(snapshot, req)
                result = await self._apply_decision(session, decision, row, req)
                await session.commit()
                return result
        raise RuntimeError("Idempotency-Key: не удалось заявить ключ за отведённые попытки")

    async def _apply_decision(
        self, session: AsyncSession, decision: Decision, row: sa.Row, req: KeyRequest
    ) -> ClaimResult:
        if decision is Decision.RESET:
            token = uuid.uuid4()
            await session.execute(
                update(_t)
                .where(_t.id == row.id)
                .values(
                    method=req.method,
                    path=req.path,
                    request_hash=req.request_hash,
                    status="processing",
                    lock_token=token,
                    locked_until=func.now() + self._lease,
                    response_status=None,
                    response_headers=sa.null(),  # SQL NULL, а не JSON null
                    response_body=None,
                    response_body_omitted=False,
                    first_request_id=req.request_id,
                    replay_count=0,
                    created_at=func.now(),
                    completed_at=None,
                    expires_at=func.now() + self._ttl,
                )
            )
            return ClaimResult("owned", Ticket(row.id, token))
        if decision is Decision.TAKEOVER:
            token = uuid.uuid4()
            await session.execute(
                update(_t)
                .where(_t.id == row.id)
                .values(lock_token=token, locked_until=func.now() + self._lease)
            )
            return ClaimResult("owned", Ticket(row.id, token, takeover=True))
        if decision is Decision.REPLAY:
            await session.execute(
                update(_t).where(_t.id == row.id).values(replay_count=_t.replay_count + 1)
            )
            if row.response_body_omitted:
                return ClaimResult("replay_unavailable")
            body = b""
            if row.body is not None:
                try:
                    body = get_pii_crypto().decrypt(row.body).encode("utf-8")
                except InvalidToken:  # ключ шифрования выведен из ротации
                    return ClaimResult("replay_unavailable")
            return ClaimResult(
                "replay",
                response=StoredResponse(
                    status_code=int(row.response_status),
                    headers=[list(pair) for pair in row.response_headers or []],
                    body=body,
                ),
            )
        if decision is Decision.IN_PROGRESS:
            return ClaimResult("in_progress")
        return ClaimResult("reused")

    async def complete(self, ticket: Ticket, response: StoredResponse) -> bool:
        """Фиксирует 2xx-ответ; False — аренду перехватили (токен уже не наш)."""
        text: str | None = None
        omitted = response.body_omitted or response.body is None
        if not omitted and response.body is not None:
            try:
                text = response.body.decode("utf-8")
            except UnicodeDecodeError:
                omitted = True
        async with self._session() as session:
            result = await session.execute(
                update(_t)
                .where(
                    _t.id == ticket.id,
                    _t.lock_token == ticket.lock_token,
                    _t.status == "processing",
                )
                .values(
                    status="completed",
                    response_status=response.status_code,
                    response_headers=response.headers,
                    response_body=text,  # EncryptedStr → Fernet-шифртекст
                    response_body_omitted=omitted,
                    completed_at=func.now(),
                    lock_token=None,
                    locked_until=None,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return result.rowcount == 1

    async def release(self, ticket: Ticket) -> bool:
        """Освобождает ключ после 4xx/5xx или исключения: повтор выполнится заново."""
        async with self._session() as session:
            result = await session.execute(
                delete(_t)
                .where(
                    _t.id == ticket.id,
                    _t.lock_token == ticket.lock_token,
                    _t.status == "processing",
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return result.rowcount == 1

    async def heartbeat(self, ticket: Ticket) -> bool:
        """Продлевает аренду; False — её перехватили или ключ уже завершён."""
        async with self._session() as session:
            result = await session.execute(
                update(_t)
                .where(
                    _t.id == ticket.id,
                    _t.lock_token == ticket.lock_token,
                    _t.status == "processing",
                )
                .values(locked_until=func.now() + self._lease)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return result.rowcount == 1


async def purge_expired_keys(
    session: AsyncSession, *, batch_size: int = 5000, max_batches: int = 200
) -> int:
    """Физически удаляет просроченные ключи пачками (commit на пачку); возвращает число.
    Вызывается CronJob idempotency-purge через internal-эндпоинт (ежечасно)."""
    total = 0
    for _ in range(max_batches):
        expired_ids = (
            select(_t.id)
            .where(_t.expires_at < func.now())
            .order_by(_t.expires_at)
            .limit(batch_size)
        )
        result = await session.execute(
            delete(_t).where(_t.id.in_(expired_ids)).execution_options(synchronize_session=False)
        )
        await session.commit()
        deleted = result.rowcount or 0
        total += deleted
        if deleted < batch_size:
            break
    return total


_store: PgIdempotencyStore | None = None


def get_idempotency_store() -> PgIdempotencyStore:
    """Единственный экземпляр на процесс (параметры — из Settings)."""
    global _store
    if _store is None:
        settings = get_settings()
        _store = PgIdempotencyStore(
            ttl_hours=settings.idempotency_ttl_hours,
            lease_seconds=settings.idempotency_lease_seconds,
        )
    return _store


def reset_idempotency_store() -> None:
    """Сброс синглтона (тесты: conftest вызывает после каждого теста)."""
    global _store
    _store = None
