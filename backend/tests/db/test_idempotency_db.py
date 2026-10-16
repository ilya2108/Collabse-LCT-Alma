"""Хранилище Idempotency-Key на PostgreSQL (api-contract.md §1.6, data-model.md §9.6).

Протокол claim → complete/release/heartbeat, шифрование сохранённого ответа, перехват
аренды, сброс просроченного ключа, очистка пачками, гонка двух заявок на настоящих
COMMIT, нормализация `integration_mode` миграцией 0003 и сквозной прогон middleware
на мини-приложении (роутеры ui и internal с тем же хранилищем).

`now()` внутри теста на `db_session` — время начала внешней транзакции (заморожено),
поэтому сдвиги времени задаются явными UPDATE относительно `now()`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import RequestIdMiddleware, register_error_handlers, unauthorized
from app.core.idempotency import (
    REPLAYED_HEADER,
    IdempotencyConfig,
    IdempotencyMiddleware,
    KeyRequest,
    StoredResponse,
)
from app.core.idempotency_store import (
    PgIdempotencyStore,
    get_idempotency_store,
    purge_expired_keys,
    reset_idempotency_store,
)
from app.core.security import TokenClaims, get_token_claims, verify_internal_token
from app.models import AppSetting, AppUser, IdempotencyKey

from .conftest import BACKEND_ROOT

BODY = '{"id": "p-1", "name": "Мой пресет"}'.encode()


def _req(
    key: str | None = None, *, principal: str = "test:alice", body_hash: str = "a"
) -> KeyRequest:
    return KeyRequest(
        principal=principal,
        key=key or str(uuid.uuid4()),
        method="POST",
        path="/api/v1/ui/presets",
        request_hash=body_hash * 64,
        request_id="rid-1",
    )


def _response(body: bytes = BODY, status: int = 201) -> StoredResponse:
    return StoredResponse(
        status_code=status,
        headers=[["content-type", "application/json"], ["location", "/api/v1/ui/presets/p-1"]],
        body=body,
    )


@pytest.fixture
def store(db_session_factory: async_sessionmaker[AsyncSession]) -> PgIdempotencyStore:
    return PgIdempotencyStore(db_session_factory, ttl_hours=24, lease_seconds=60)


async def _row(session: AsyncSession, row_id: uuid.UUID) -> sa.Row | None:
    result = await session.execute(
        sa.text(
            "SELECT status, lock_token, locked_until, expires_at, response_status, "
            "response_headers, response_body, response_body_omitted, replay_count, "
            "first_request_id, completed_at, now() AS db_now FROM idempotency_key WHERE id = :id"
        ),
        {"id": row_id},
    )
    return result.first()


# --------------------------------------------------------------------------------------
# Протокол хранилища (внутри транзакции теста)
# --------------------------------------------------------------------------------------


async def test_schema_has_constraints(db_session: AsyncSession) -> None:
    names = set(
        (
            await db_session.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'idempotency_key'::regclass"
                )
            )
        ).scalars()
    )
    assert {
        "uq_idempotency_key_principal_key",
        "ck_idempotency_key_status",
        "ck_idempotency_key_completed_response",
        "ck_idempotency_key_key_length",
    } <= names


async def test_claim_inserts_processing_row(
    store: PgIdempotencyStore, db_session: AsyncSession
) -> None:
    result = await store.claim(_req("k-insert"))
    assert result.kind == "owned"
    assert result.ticket is not None and not result.ticket.takeover
    row = await _row(db_session, result.ticket.id)
    assert row is not None
    assert row.status == "processing"
    assert row.lock_token == result.ticket.lock_token
    assert row.first_request_id == "rid-1"
    assert row.locked_until - row.db_now == timedelta(seconds=60)
    expected = datetime.now(timezone.utc) + timedelta(hours=24)
    assert abs((row.expires_at - expected).total_seconds()) < 5


async def test_in_progress_then_complete_encrypted_then_replay(
    store: PgIdempotencyStore, db_session: AsyncSession
) -> None:
    req = _req("k-flow")
    owned = await store.claim(req)
    assert owned.ticket is not None
    assert (await store.claim(req)).kind == "in_progress"

    assert await store.complete(owned.ticket, _response())
    row = await _row(db_session, owned.ticket.id)
    assert row is not None
    assert row.status == "completed"
    assert row.response_status == 201
    assert row.lock_token is None and row.locked_until is None and row.completed_at is not None
    raw = bytes(row.response_body)
    assert raw.startswith(b"gAAAAA")  # Fernet-шифртекст
    assert b"p-1" not in raw and "Мой пресет".encode() not in raw
    entity = await db_session.get(IdempotencyKey, owned.ticket.id)
    assert entity is not None and entity.response_body == BODY.decode("utf-8")

    replay = await store.claim(req)
    assert replay.kind == "replay"
    assert replay.response == StoredResponse(
        201,
        [["content-type", "application/json"], ["location", "/api/v1/ui/presets/p-1"]],
        BODY,
    )
    assert (await _row(db_session, owned.ticket.id)).replay_count == 1
    assert (await store.claim(_req("k-flow", body_hash="b"))).kind == "reused"
    # повтор завершения тем же тикетом — ключ уже не processing
    assert not await store.complete(owned.ticket, _response())


async def test_principal_scopes_key(store: PgIdempotencyStore) -> None:
    alice = await store.claim(_req("k-shared"))
    bob = await store.claim(_req("k-shared", principal="test:bob"))
    assert alice.kind == bob.kind == "owned"
    assert alice.ticket is not None and bob.ticket is not None
    assert alice.ticket.id != bob.ticket.id


async def test_release_requires_own_token(
    store: PgIdempotencyStore, db_session: AsyncSession
) -> None:
    owned = await store.claim(_req("k-release"))
    assert owned.ticket is not None
    foreign = type(owned.ticket)(owned.ticket.id, uuid.uuid4())
    assert not await store.release(foreign)
    assert await _row(db_session, owned.ticket.id) is not None
    assert await store.release(owned.ticket)
    assert await _row(db_session, owned.ticket.id) is None
    assert (await store.claim(_req("k-release"))).kind == "owned"


async def test_expired_lease_taken_over(
    store: PgIdempotencyStore, db_session: AsyncSession
) -> None:
    req = _req("k-lease")
    first = await store.claim(req)
    assert first.ticket is not None
    await db_session.execute(
        sa.text(
            "UPDATE idempotency_key SET locked_until = now() - interval '1 second' WHERE id = :id"
        ),
        {"id": first.ticket.id},
    )
    second = await store.claim(req)
    assert second.kind == "owned"
    assert second.ticket is not None and second.ticket.takeover
    assert second.ticket.id == first.ticket.id
    assert second.ticket.lock_token != first.ticket.lock_token
    assert not await store.complete(first.ticket, _response())  # старый владелец опоздал
    assert not await store.heartbeat(first.ticket)
    assert await store.complete(second.ticket, _response())


async def test_expired_key_reset(store: PgIdempotencyStore, db_session: AsyncSession) -> None:
    req = _req("k-expired")
    owned = await store.claim(req)
    assert owned.ticket is not None
    await store.complete(owned.ticket, _response())
    await db_session.execute(
        sa.text(
            "UPDATE idempotency_key SET expires_at = now() - interval '1 second', "
            "replay_count = 5 WHERE id = :id"
        ),
        {"id": owned.ticket.id},
    )
    # просроченный ключ свободен даже для другого запроса
    fresh = await store.claim(_req("k-expired", body_hash="c"))
    assert fresh.kind == "owned"
    assert fresh.ticket is not None and fresh.ticket.id == owned.ticket.id
    row = await _row(db_session, owned.ticket.id)
    assert row is not None
    assert row.status == "processing"
    assert row.response_status is None
    assert row.response_headers is None  # SQL NULL, а не JSON null
    assert row.response_body is None
    assert row.replay_count == 0
    assert row.expires_at > row.db_now + timedelta(hours=23)


async def test_heartbeat_extends_lease(store: PgIdempotencyStore, db_session: AsyncSession) -> None:
    owned = await store.claim(_req("k-heartbeat"))
    assert owned.ticket is not None
    await db_session.execute(
        sa.text(
            "UPDATE idempotency_key SET locked_until = now() + interval '1 second' WHERE id = :id"
        ),
        {"id": owned.ticket.id},
    )
    assert await store.heartbeat(owned.ticket)
    row = await _row(db_session, owned.ticket.id)
    assert row is not None and row.locked_until - row.db_now == timedelta(seconds=60)
    await store.complete(owned.ticket, _response())
    assert not await store.heartbeat(owned.ticket)


async def test_omitted_and_undecryptable_body_unavailable(
    store: PgIdempotencyStore, db_session: AsyncSession
) -> None:
    omitted_req = _req("k-omitted")
    owned = await store.claim(omitted_req)
    assert owned.ticket is not None
    await store.complete(owned.ticket, StoredResponse(201, [], None, body_omitted=True))
    assert (await store.claim(omitted_req)).kind == "replay_unavailable"

    broken_req = _req("k-broken")
    owned = await store.claim(broken_req)
    assert owned.ticket is not None
    await store.complete(owned.ticket, _response())
    # шифртекст ключом, выведенным из ротации, — не 500, а replay_unavailable
    await db_session.execute(
        sa.text("UPDATE idempotency_key SET response_body = :b WHERE id = :id"),
        {"b": b"gAAAAA-not-a-valid-token", "id": owned.ticket.id},
    )
    assert (await store.claim(broken_req)).kind == "replay_unavailable"


async def test_empty_body_replayed(store: PgIdempotencyStore) -> None:
    req = _req("k-204")
    owned = await store.claim(req)
    assert owned.ticket is not None
    await store.complete(owned.ticket, StoredResponse(204, [], b""))
    replay = await store.claim(req)
    assert replay.kind == "replay"
    assert replay.response == StoredResponse(204, [], b"")


async def test_purge_expired_in_batches(db_session: AsyncSession) -> None:
    for n in range(7):
        await db_session.execute(
            sa.text(
                "INSERT INTO idempotency_key (principal, idem_key, method, path, request_hash, "
                "status, response_status, expires_at) VALUES ('test:purge', :k, 'POST', '/x', "
                ":h, 'completed', 201, now() + make_interval(hours => :hours))"
            ),
            {"k": f"k{n}", "h": "a" * 64, "hours": -1 if n < 5 else 1},
        )
    assert await purge_expired_keys(db_session, batch_size=2) == 5
    left = (
        await db_session.execute(
            sa.text("SELECT idem_key FROM idempotency_key WHERE principal = 'test:purge'")
        )
    ).scalars()
    assert sorted(left) == ["k5", "k6"]
    assert await purge_expired_keys(db_session, batch_size=2) == 0


async def test_purge_respects_max_batches(db_session: AsyncSession) -> None:
    for n in range(5):
        await db_session.execute(
            sa.text(
                "INSERT INTO idempotency_key (principal, idem_key, method, path, request_hash, "
                "expires_at) VALUES ('test:max', :k, 'POST', '/x', :h, now() - interval '1 hour')"
            ),
            {"k": f"m{n}", "h": "a" * 64},
        )
    assert await purge_expired_keys(db_session, batch_size=2, max_batches=1) == 2


# --------------------------------------------------------------------------------------
# Настоящие COMMIT: гонка и хранилище по умолчанию (глобальный движок приложения)
# --------------------------------------------------------------------------------------


async def test_concurrent_claims_single_owner(
    db_committed_sessionmaker: async_sessionmaker[AsyncSession], db_engine: AsyncEngine
) -> None:
    store = PgIdempotencyStore(db_committed_sessionmaker, ttl_hours=24, lease_seconds=60)
    req = _req("k-race")
    results = await asyncio.gather(store.claim(req), store.claim(req))
    assert sorted(r.kind for r in results) == ["in_progress", "owned"]
    async with db_engine.connect() as conn:
        count = await conn.scalar(
            sa.text("SELECT count(*) FROM idempotency_key WHERE idem_key = 'k-race'")
        )
    assert count == 1


async def test_default_store_commits_via_app_engine(
    db_committed_sessionmaker: async_sessionmaker[AsyncSession], db_engine: AsyncEngine
) -> None:
    reset_idempotency_store()
    store = get_idempotency_store()
    assert get_idempotency_store() is store
    req = _req("k-default")
    owned = await store.claim(req)
    assert owned.ticket is not None
    assert await store.complete(owned.ticket, _response())
    async with db_engine.connect() as conn:
        status = await conn.scalar(
            sa.text("SELECT status FROM idempotency_key WHERE idem_key = 'k-default'")
        )
    assert status == "completed"
    reset_idempotency_store()
    assert get_idempotency_store() is not store


async def test_rekey_reencrypts_stored_responses(
    db_committed_sessionmaker: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python -m app.tools.rekey` перешифровывает и сохранённые ответы (152-ФЗ, §8.4)."""
    from cryptography.fernet import Fernet

    from app.core import pii
    from app.tools.rekey import _rekey_table

    store = PgIdempotencyStore(db_committed_sessionmaker, ttl_hours=24, lease_seconds=60)
    owned = await store.claim(_req("k-rekey"))
    assert owned.ticket is not None
    await store.complete(owned.ticket, _response())

    settings = get_settings()
    new_key = Fernet.generate_key().decode()
    rotated = pii.PiiCrypto([new_key, *settings.pii_fernet_key_list], settings.pii_hmac_key)
    monkeypatch.setattr(pii, "_crypto", rotated)
    assert await _rekey_table(IdempotencyKey, ("response_body",)) == 1

    async with db_engine.connect() as conn:
        raw = await conn.scalar(
            sa.text("SELECT response_body FROM idempotency_key WHERE idem_key = 'k-rekey'")
        )
    assert Fernet(new_key.encode()).decrypt(bytes(raw)) == BODY


# --------------------------------------------------------------------------------------
# Миграция 0003: нормализация integration_mode
# --------------------------------------------------------------------------------------


def _migration_0003() -> Any:
    path = BACKEND_ROOT / "alembic" / "versions" / "20261017_0003_idempotency_keys.py"
    spec = importlib.util.spec_from_file_location("alembic_0003_idempotency_keys", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_integration_mode_fix_normalizes_stream(db_session: AsyncSession) -> None:
    await db_session.execute(sa.delete(AppSetting).where(AppSetting.key == "integration_mode"))
    db_session.add(
        AppSetting(
            key="integration_mode",
            value={"value": "stream"},
            description="Транспорт интеграций: http | stream",
        )
    )
    await db_session.flush()
    await db_session.execute(sa.text(_migration_0003()._INTEGRATION_MODE_FIX))
    row = (
        await db_session.execute(
            sa.text("SELECT value, description FROM app_setting WHERE key = 'integration_mode'")
        )
    ).one()
    assert row.value == {"value": "http"}
    assert "outbox PostgreSQL" in row.description and "stream" not in row.description


# --------------------------------------------------------------------------------------
# Сквозной прогон: middleware + PG-хранилище + настоящие роутеры (без app.main)
# --------------------------------------------------------------------------------------


@pytest.fixture
async def stack(
    db_session: AsyncSession,
    db_session_factory: async_sessionmaker[AsyncSession],
    user_factory: Any,
) -> AsyncIterator[tuple[httpx.AsyncClient, AppUser, dict[str, str]]]:
    from app.modules.internal.router import router as internal_router
    from app.modules.ui.router import router as ui_router

    user = await user_factory(roles=("kam",))
    await db_session.commit()
    token = f"test-{uuid.uuid4()}"
    claims = {
        token: TokenClaims(
            sub=str(user.keycloak_sub),
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=tuple(user.roles),
            azp="crm-frontend",
        )
    }

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def claims_override(request: Request) -> TokenClaims:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if bearer not in claims:
            raise unauthorized()
        return claims[bearer]

    async def principal(headers: Any) -> str | None:
        bearer = (headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if bearer in claims:
            return f"sub:{claims[bearer].sub}"
        if verify_internal_token(
            headers.get("x-internal-token"), get_settings().internal_api_token
        ):
            return "internal"
        return None

    app = FastAPI()
    register_error_handlers(app)
    api = APIRouter(prefix="/api/v1")
    api.include_router(ui_router)
    api.include_router(internal_router)
    app.include_router(api)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_token_claims] = claims_override
    app.add_middleware(
        IdempotencyMiddleware,
        store=PgIdempotencyStore(db_session_factory, ttl_hours=24, lease_seconds=60),
        principal_resolver=principal,
        config=IdempotencyConfig(heartbeat_seconds=None),
    )
    app.add_middleware(RequestIdMiddleware)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, user, {"Authorization": f"Bearer {token}"}


async def test_preset_created_once_and_replayed(
    stack: tuple[httpx.AsyncClient, AppUser, dict[str, str]], db_session: AsyncSession
) -> None:
    client, user, auth = stack
    headers = {**auth, "Idempotency-Key": str(uuid.uuid4())}
    payload = {"screen": "registry", "name": "Мои вузы", "state": {"sort": "name"}}
    first = await client.post("/api/v1/ui/presets", json=payload, headers=headers)
    second = await client.post("/api/v1/ui/presets", json=payload, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers[REPLAYED_HEADER] == "true"
    assert REPLAYED_HEADER not in first.headers

    setting = await db_session.get(AppSetting, f"ui:presets:{user.id}")
    assert setting is not None
    assert [item["name"] for item in setting.value["items"]] == ["Мои вузы"]

    other = await client.post(
        "/api/v1/ui/presets", json={**payload, "name": "Другое"}, headers=headers
    )
    assert other.status_code == 422
    assert other.json()["error"]["code"] == "idempotency_key_reused"

    # без ключа — прежнее поведение: обработчик выполняется снова
    plain = await client.post("/api/v1/ui/presets", json=payload, headers=auth)
    assert plain.status_code == 201
    assert plain.json()["id"] != first.json()["id"]
    assert REPLAYED_HEADER not in plain.headers


async def test_delete_replayed_as_204(
    stack: tuple[httpx.AsyncClient, AppUser, dict[str, str]],
) -> None:
    client, _, auth = stack
    created = await client.post(
        "/api/v1/ui/presets", json={"screen": "board", "name": "Срочные"}, headers=auth
    )
    preset_id = created.json()["id"]
    headers = {**auth, "Idempotency-Key": "delete-once"}
    first = await client.delete(f"/api/v1/ui/presets/{preset_id}", headers=headers)
    second = await client.delete(f"/api/v1/ui/presets/{preset_id}", headers=headers)
    assert first.status_code == second.status_code == 204
    assert second.headers[REPLAYED_HEADER] == "true"


async def test_purge_endpoint_via_internal_token(
    stack: tuple[httpx.AsyncClient, AppUser, dict[str, str]], db_session: AsyncSession
) -> None:
    client, _, _ = stack
    for n in range(3):
        await db_session.execute(
            sa.text(
                "INSERT INTO idempotency_key (principal, idem_key, method, path, request_hash, "
                "expires_at) VALUES ('test:cron', :k, 'POST', '/x', :h, now() - interval '1 hour')"
            ),
            {"k": f"old{n}", "h": "a" * 64},
        )
    internal = {"X-Internal-Token": get_settings().internal_api_token}
    denied = await client.post("/api/v1/internal/jobs/purge-idempotency-keys")
    assert denied.status_code == 401
    response = await client.post("/api/v1/internal/jobs/purge-idempotency-keys", headers=internal)
    assert response.status_code == 200
    body = response.json()
    assert body["purged"] == 3
    assert isinstance(body["duration_ms"], int)
    # CronJob с ключом: повтор отдаёт сохранённый ответ, а не новый прогон
    keyed = {**internal, "Idempotency-Key": "cron-run-1"}
    first = await client.post("/api/v1/internal/jobs/purge-idempotency-keys", headers=keyed)
    again = await client.post("/api/v1/internal/jobs/purge-idempotency-keys", headers=keyed)
    assert again.headers[REPLAYED_HEADER] == "true"
    assert again.json() == first.json()
