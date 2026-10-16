"""Idempotency-Key: чистые функции, решение по ключу, ASGI-middleware, OpenAPI,
подключение в app.main (api-contract.md §1.6). Без БД: хранилище — FakeStore в памяти
с той же логикой decide()."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.datastructures import Headers

from app.core import idempotency as idem
from app.core import idempotency_store
from app.core.config import get_settings
from app.core.errors import (
    REQUEST_ID_HEADER,
    ApiError,
    RequestIdMiddleware,
    conflict,
    register_error_handlers,
    unauthorized,
)
from app.core.idempotency import (
    REPLAYED_HEADER,
    BodyDigest,
    ClaimResult,
    Decision,
    IdempotencyConfig,
    IdempotencyKeyInvalid,
    IdempotencyMiddleware,
    KeyRequest,
    KeySnapshot,
    StoredResponse,
    Ticket,
    add_idempotency_to_openapi,
    canonical_json_digest,
    decide,
    filter_response_headers,
    install_idempotency_openapi,
    media_type,
    parse_idempotency_key,
    request_fingerprint,
    should_persist,
)
from app.core.security import TokenClaims
from app.main import create_app

# --------------------------------------------------------------------------------------
# Чистые функции
# --------------------------------------------------------------------------------------


def _fp(method: str = "POST", path: str = "/api/v1/x", query: str = "", body: bytes = b"{}"):
    digest = canonical_json_digest(body) or BodyDigest(None).hexdigest()
    return request_fingerprint(method, path, query, "application/json", digest)


def _multipart(boundary: str, content: bytes, name: str = "a.txt") -> bytes:
    return (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )


def _digest(content_type: str, body: bytes, chunk: int | None = None) -> str:
    digest = BodyDigest(content_type)
    if chunk is None:
        digest.update(body)
    else:
        for start in range(0, len(body), chunk):
            digest.update(body[start : start + chunk])
    return digest.hexdigest()


class TestParseKey:
    def test_uuid_ok(self) -> None:
        key = str(uuid.uuid4())
        assert parse_idempotency_key([key]) == key

    def test_sf_string_quotes_stripped(self) -> None:
        assert parse_idempotency_key(['"abc"']) == "abc"

    def test_max_length_ok(self) -> None:
        assert parse_idempotency_key(["k" * 255]) == "k" * 255

    @pytest.mark.parametrize(
        "values",
        [[""], ['""'], ["k" * 256], ["a b"], ["a\tb"], ["a\x01b"], ["ключ"], ["a", "b"], []],
    )
    def test_invalid(self, values: list[str]) -> None:
        with pytest.raises(IdempotencyKeyInvalid):
            parse_idempotency_key(values)


class TestFingerprint:
    def test_media_type(self) -> None:
        assert media_type("Application/JSON; charset=utf-8") == "application/json"
        assert media_type(None) == ""

    def test_json_key_order_ignored(self) -> None:
        assert _fp(body=b'{"a": 1, "b": [1, 2]}') == _fp(body=b'{"b":[1,2],"a":1}')

    def test_differences_change_hash(self) -> None:
        base = _fp(body=b'{"a": 1}')
        assert base != _fp(body=b'{"a": 2}')
        assert base != _fp(path="/api/v1/y", body=b'{"a": 1}')
        assert base != _fp(method="PUT", body=b'{"a": 1}')
        assert base != _fp(query="x=1", body=b'{"a": 1}')
        other_type = request_fingerprint(
            "POST", "/api/v1/x", "", "text/plain", canonical_json_digest(b'{"a": 1}') or ""
        )
        assert base != other_type

    def test_invalid_json_falls_back_to_raw(self) -> None:
        assert canonical_json_digest(b"{not json") is None
        assert canonical_json_digest(b'{"a": 1}') == canonical_json_digest(b'{ "a" : 1 }')

    def test_multipart_boundary_ignored(self) -> None:
        first = _multipart("boundaryAAA111", b"hello")
        second = _multipart("other-boundary-ZZ", b"hello")
        assert _digest("multipart/form-data; boundary=boundaryAAA111", first) == _digest(
            'multipart/form-data; boundary="other-boundary-ZZ"', second
        )

    def test_multipart_content_matters(self) -> None:
        first = _multipart("b1", b"hello")
        second = _multipart("b1", b"world")
        content_type = "multipart/form-data; boundary=b1"
        assert _digest(content_type, first) != _digest(content_type, second)

    def test_multipart_chunked_equals_whole(self) -> None:
        body = _multipart("----WebKitFormBoundary7MA4YWxkTrZu0gW", b"x" * 50 + b"--" + b"y" * 30)
        content_type = "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW"
        whole = _digest(content_type, body)
        assert _digest(content_type, body, chunk=7) == whole
        assert _digest(content_type, body, chunk=1) == whole

    def test_raw_body_digest_is_plain_sha256(self) -> None:
        import hashlib

        assert _digest("application/octet-stream", b"abc", chunk=1) == (
            hashlib.sha256(b"abc").hexdigest()
        )


class TestPolicy:
    @pytest.mark.parametrize("status", [200, 201, 202, 204])
    def test_success_persisted(self, status: int) -> None:
        assert should_persist(status)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500, 503])
    def test_errors_not_persisted(self, status: int) -> None:
        assert not should_persist(status)

    def test_filter_response_headers(self) -> None:
        raw = [
            (b"date", b"Mon"),
            (b"content-length", b"10"),
            (b"x-request-id", b"rid"),
            (b"set-cookie", b"a=b"),
            (b"content-type", b"application/json"),
            (b"location", b"/api/v1/x/1"),
        ]
        assert filter_response_headers(raw) == [
            ["content-type", "application/json"],
            ["location", "/api/v1/x/1"],
        ]


class TestDecide:
    REQ = KeyRequest("sub:u", "k", "POST", "/api/v1/x", "h" * 64)

    def _snap(self, **overrides: Any) -> KeySnapshot:
        values: dict[str, Any] = {
            "method": "POST",
            "path": "/api/v1/x",
            "request_hash": "h" * 64,
            "status": "processing",
            "expired": False,
            "lease_expired": False,
        }
        values.update(overrides)
        return KeySnapshot(**values)

    def test_expired_resets_even_with_other_hash(self) -> None:
        assert decide(self._snap(expired=True), self.REQ) is Decision.RESET
        snap = self._snap(expired=True, request_hash="z" * 64, status="completed")
        assert decide(snap, self.REQ) is Decision.RESET

    def test_reused_in_any_status(self) -> None:
        assert decide(self._snap(request_hash="z" * 64), self.REQ) is Decision.REUSED
        snap = self._snap(path="/api/v1/y", status="completed")
        assert decide(snap, self.REQ) is Decision.REUSED

    def test_replay(self) -> None:
        assert decide(self._snap(status="completed"), self.REQ) is Decision.REPLAY

    def test_in_progress(self) -> None:
        assert decide(self._snap(), self.REQ) is Decision.IN_PROGRESS

    def test_takeover(self) -> None:
        assert decide(self._snap(lease_expired=True), self.REQ) is Decision.TAKEOVER


class TestConfig:
    def test_from_settings(self) -> None:
        config = IdempotencyConfig.from_settings(get_settings())
        assert config.enabled is True
        assert config.ttl_hours == 24
        assert config.lease_seconds == 60
        assert config.heartbeat_seconds == 15
        assert config.max_request_bytes == 60 * 1024 * 1024
        assert config.max_response_bytes == 1024 * 1024
        assert "/api/v1/integrations/lms/webhook" in config.excluded_paths


class TestResolvePrincipal:
    async def test_valid_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_decode(token: str) -> TokenClaims:
            assert token == "good"
            return TokenClaims(sub="0000-sub", username="u", email=None, full_name=None, roles=())

        monkeypatch.setattr(idem, "decode_token", fake_decode)
        headers = Headers({"authorization": "Bearer good"})
        assert await idem.resolve_principal(headers) == "sub:0000-sub"

    async def test_invalid_bearer_is_anonymous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_decode(token: str) -> TokenClaims:
            raise unauthorized("Некорректный токен авторизации")

        monkeypatch.setattr(idem, "decode_token", fake_decode)
        assert await idem.resolve_principal(Headers({"authorization": "Bearer bad"})) is None

    async def test_internal_token(self) -> None:
        token = get_settings().internal_api_token
        assert await idem.resolve_principal(Headers({"x-internal-token": token})) == "internal"
        assert await idem.resolve_principal(Headers({"x-internal-token": "wrong"})) is None
        assert await idem.resolve_principal(Headers({})) is None


# --------------------------------------------------------------------------------------
# Хранилище в памяти и мини-приложение
# --------------------------------------------------------------------------------------


@dataclass
class _Row:
    id: uuid.UUID
    method: str
    path: str
    request_hash: str
    lock_token: uuid.UUID | None
    status: str = "processing"
    response: StoredResponse | None = None
    expired: bool = False
    lease_expired: bool = False
    replay_count: int = 0


@dataclass
class FakeStore:
    rows: dict[tuple[str, str], _Row] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    fail_claim: bool = False
    heartbeat_alive: bool = True

    async def claim(self, req: KeyRequest) -> ClaimResult:
        self.calls.append("claim")
        if self.fail_claim:
            raise RuntimeError("PostgreSQL недоступен")
        row = self.rows.get((req.principal, req.key))
        if row is None:
            return self._own(req)
        snapshot = KeySnapshot(
            row.method, row.path, row.request_hash, row.status, row.expired, row.lease_expired
        )
        decision = decide(snapshot, req)
        if decision is Decision.RESET:
            return self._own(req)
        if decision is Decision.TAKEOVER:
            row.lock_token = uuid.uuid4()
            row.lease_expired = False
            return ClaimResult("owned", Ticket(row.id, row.lock_token, takeover=True))
        if decision is Decision.REPLAY:
            row.replay_count += 1
            assert row.response is not None
            if row.response.body_omitted:
                return ClaimResult("replay_unavailable")
            return ClaimResult("replay", response=row.response)
        if decision is Decision.IN_PROGRESS:
            return ClaimResult("in_progress")
        return ClaimResult("reused")

    def _own(self, req: KeyRequest) -> ClaimResult:
        row = _Row(uuid.uuid4(), req.method, req.path, req.request_hash, uuid.uuid4())
        self.rows[(req.principal, req.key)] = row
        assert row.lock_token is not None
        return ClaimResult("owned", Ticket(row.id, row.lock_token))

    def _find(self, ticket: Ticket) -> tuple[tuple[str, str], _Row] | None:
        for scope_key, row in self.rows.items():
            if (
                row.id == ticket.id
                and row.lock_token == ticket.lock_token
                and row.status == "processing"
            ):
                return scope_key, row
        return None

    async def complete(self, ticket: Ticket, response: StoredResponse) -> bool:
        self.calls.append("complete")
        found = self._find(ticket)
        if found is None:
            return False
        row = found[1]
        row.status, row.response, row.lock_token = "completed", response, None
        return True

    async def release(self, ticket: Ticket) -> bool:
        self.calls.append("release")
        found = self._find(ticket)
        if found is None:
            return False
        del self.rows[found[0]]
        return True

    async def heartbeat(self, ticket: Ticket) -> bool:
        self.calls.append("heartbeat")
        return self.heartbeat_alive and self._find(ticket) is not None


async def _header_principal(headers: Headers) -> str | None:
    return headers.get("x-test-principal")


@dataclass
class MiniApp:
    app: FastAPI
    store: FakeStore
    calls: Counter
    started: asyncio.Event
    gate: asyncio.Event


def _build(
    store: FakeStore | None = None,
    *,
    config: IdempotencyConfig | None = None,
    with_request_id: bool = True,
    resolver: Any = _header_principal,
) -> MiniApp:
    store = store or FakeStore()
    calls: Counter = Counter()
    started, gate = asyncio.Event(), asyncio.Event()
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/api/v1/items", status_code=201)
    async def create_item(body: dict[str, Any]) -> dict[str, Any]:
        calls["create"] += 1
        return {"id": calls["create"], "name": body.get("name")}

    @app.get("/api/v1/items")
    async def list_items() -> dict[str, Any]:
        calls["list"] += 1
        return {"items": []}

    @app.delete("/api/v1/items/{item_id}", status_code=204)
    async def delete_item(item_id: int) -> None:
        calls["delete"] += 1

    @app.post("/api/v1/slow", status_code=201)
    async def slow() -> dict[str, Any]:
        calls["slow"] += 1
        started.set()
        await gate.wait()
        return {"slow": calls["slow"]}

    @app.post("/api/v1/conflict")
    async def conflicting() -> None:
        calls["conflict"] += 1
        raise conflict("Импорт уже выполняется")

    @app.post("/api/v1/invalid")
    async def invalid() -> None:
        calls["invalid"] += 1
        raise ApiError(400, "validation_error", "Ошибка валидации данных")

    @app.post("/api/v1/boom")
    async def boom() -> None:
        calls["boom"] += 1
        raise RuntimeError("сбой обработчика")

    @app.post("/api/v1/upload", status_code=201)
    async def upload(request: Request) -> dict[str, Any]:
        calls["upload"] += 1
        return {"size": len(await request.body()), "n": calls["upload"]}

    @app.post("/api/v1/big", status_code=201)
    async def big() -> dict[str, Any]:
        calls["big"] += 1
        return {"payload": "x" * 500}

    @app.post("/api/v1/integrations/lms/webhook")
    async def webhook() -> dict[str, Any]:
        calls["webhook"] += 1
        return {"ok": True}

    app.add_middleware(
        IdempotencyMiddleware,
        store=store,
        principal_resolver=resolver,
        config=config or IdempotencyConfig(heartbeat_seconds=None),
    )
    if with_request_id:
        app.add_middleware(RequestIdMiddleware)
    return MiniApp(app, store, calls, started, gate)


@pytest.fixture
async def mini() -> AsyncIterator[tuple[MiniApp, httpx.AsyncClient]]:
    built = _build()
    transport = httpx.ASGITransport(app=built.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield built, client


def _h(key: str | None = "key-1", principal: str | None = "sub:alice") -> dict[str, str]:
    headers: dict[str, str] = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    if principal is not None:
        headers["X-Test-Principal"] = principal
    return headers


def _code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


# --------------------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------------------


class TestMiddleware:
    async def test_without_key_handler_runs_every_time(self, mini) -> None:
        app, client = mini
        for _ in range(2):
            response = await client.post("/api/v1/items", json={"name": "a"}, headers=_h(None))
            assert response.status_code == 201
        assert app.calls["create"] == 2
        assert app.store.calls == []

    async def test_get_with_key_ignored(self, mini) -> None:
        app, client = mini
        await client.get("/api/v1/items", headers=_h())
        await client.get("/api/v1/items", headers=_h())
        assert app.calls["list"] == 2
        assert app.store.calls == []

    async def test_repeat_returns_stored_response(self, mini) -> None:
        app, client = mini
        first = await client.post("/api/v1/items", json={"name": "a", "x": 1}, headers=_h())
        # тот же JSON с другим порядком ключей — тот же отпечаток
        second = await client.post(
            "/api/v1/items",
            content=b'{"x": 1, "name": "a"}',
            headers={**_h(), "Content-Type": "application/json"},
        )
        assert app.calls["create"] == 1
        assert first.status_code == second.status_code == 201
        assert second.json() == first.json() == {"id": 1, "name": "a"}
        assert second.headers[REPLAYED_HEADER] == "true"
        assert REPLAYED_HEADER not in first.headers
        assert second.headers["content-type"] == "application/json"
        # свой trace_id у повтора (RequestIdMiddleware снаружи)
        assert second.headers[REQUEST_ID_HEADER]
        assert second.headers[REQUEST_ID_HEADER] != first.headers[REQUEST_ID_HEADER]
        assert app.store.calls == ["claim", "complete", "claim"]

    async def test_same_key_other_body_is_422(self, mini) -> None:
        app, client = mini
        await client.post("/api/v1/items", json={"name": "a"}, headers=_h())
        response = await client.post("/api/v1/items", json={"name": "b"}, headers=_h())
        assert response.status_code == 422
        assert _code(response) == "idempotency_key_reused"
        assert response.json()["error"]["trace_id"]
        assert app.calls["create"] == 1

    async def test_same_key_other_path_is_422(self, mini) -> None:
        app, client = mini
        await client.post("/api/v1/items", json={}, headers=_h())
        response = await client.post("/api/v1/upload", content=b"{}", headers=_h())
        assert response.status_code == 422
        assert app.calls["upload"] == 0

    async def test_parallel_duplicate_gets_409_then_replay(self, mini) -> None:
        app, client = mini
        first = asyncio.create_task(client.post("/api/v1/slow", headers=_h()))
        await asyncio.wait_for(app.started.wait(), timeout=5)
        busy = await client.post("/api/v1/slow", headers=_h())
        assert busy.status_code == 409
        assert _code(busy) == "idempotency_in_progress"
        assert busy.headers["Retry-After"] == "1"
        app.gate.set()
        done = await asyncio.wait_for(first, timeout=5)
        assert done.status_code == 201
        third = await client.post("/api/v1/slow", headers=_h())
        assert third.status_code == 201
        assert third.json() == done.json()
        assert third.headers[REPLAYED_HEADER] == "true"
        assert app.calls["slow"] == 1

    @pytest.mark.parametrize("path", ["/api/v1/conflict", "/api/v1/invalid"])
    async def test_error_releases_key(self, mini, path: str) -> None:
        app, client = mini
        first = await client.post(path, headers=_h())
        second = await client.post(path, headers=_h())
        assert first.status_code == second.status_code
        assert first.status_code in (400, 409)
        assert REPLAYED_HEADER not in second.headers
        assert app.calls[path.rsplit("/", 1)[-1]] == 2
        assert app.store.calls == ["claim", "release", "claim", "release"]
        assert app.store.rows == {}

    async def test_exception_releases_key_and_returns_500(self, mini) -> None:
        app, client = mini
        response = await client.post("/api/v1/boom", headers=_h())
        assert response.status_code == 500
        assert _code(response) == "internal_error"
        assert app.store.calls == ["claim", "release"]
        assert app.store.rows == {}
        await client.post("/api/v1/boom", headers=_h())
        assert app.calls["boom"] == 2

    async def test_webhook_and_anonymous_untouched(self, mini) -> None:
        app, client = mini
        for _ in range(2):
            await client.post("/api/v1/integrations/lms/webhook", headers=_h())
            await client.post("/api/v1/items", json={}, headers=_h(principal=None))
        assert app.calls["webhook"] == 2
        assert app.calls["create"] == 2
        assert app.store.calls == []

    async def test_principals_are_independent(self, mini) -> None:
        app, client = mini
        alice = await client.post("/api/v1/items", json={"name": "a"}, headers=_h())
        bob = await client.post(
            "/api/v1/items", json={"name": "a"}, headers=_h(principal="sub:bob")
        )
        assert alice.json()["id"] == 1 and bob.json()["id"] == 2
        assert REPLAYED_HEADER not in bob.headers

    @pytest.mark.parametrize("key", ["a b", "k" * 256, "ключ"])
    async def test_invalid_key_is_400(self, mini, key: str) -> None:
        app, client = mini
        headers = {"X-Test-Principal": "sub:alice", "Idempotency-Key": key}
        response = await client.post(
            "/api/v1/items", json={}, headers={k: v.encode() for k, v in headers.items()}
        )
        assert response.status_code == 400
        assert _code(response) == "idempotency_key_invalid"
        assert app.calls["create"] == 0

    async def test_two_key_headers_is_400(self, mini) -> None:
        app, client = mini
        headers = [
            ("X-Test-Principal", "sub:alice"),
            ("Idempotency-Key", "a"),
            ("Idempotency-Key", "b"),
        ]
        response = await client.post("/api/v1/items", json={}, headers=headers)
        assert response.status_code == 400

    async def test_multipart_retry_with_new_boundary_replays(self, mini) -> None:
        app, client = mini
        files = {"file": ("report.xlsx", b"content-bytes", "application/octet-stream")}
        first = await client.post("/api/v1/upload", files=files, headers=_h())
        second = await client.post("/api/v1/upload", files=files, headers=_h())
        assert first.request.headers["content-type"] != second.request.headers["content-type"]
        assert second.headers[REPLAYED_HEADER] == "true"
        assert second.json() == first.json()
        assert app.calls["upload"] == 1
        other = {"file": ("report.xlsx", b"other-bytes", "application/octet-stream")}
        changed = await client.post("/api/v1/upload", files=other, headers=_h())
        assert changed.status_code == 422

    async def test_handler_reads_buffered_body(self, mini) -> None:
        app, client = mini
        payload = b"z" * (200 * 1024)  # больше чанка проигрывания (64 КиБ)
        response = await client.post("/api/v1/upload", content=payload, headers=_h())
        assert response.status_code == 201
        assert response.json()["size"] == len(payload)

    async def test_large_response_replay_unavailable(self) -> None:
        built = _build(config=IdempotencyConfig(heartbeat_seconds=None, max_response_bytes=64))
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/api/v1/big", headers=_h())
            assert first.status_code == 201
            assert len(first.json()["payload"]) == 500  # клиент получил ответ целиком
            second = await client.post("/api/v1/big", headers=_h())
        assert second.status_code == 409
        assert _code(second) == "idempotency_replay_unavailable"
        assert built.calls["big"] == 1

    async def test_store_failure_is_503(self) -> None:
        built = _build(FakeStore(fail_claim=True))
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/items", json={}, headers=_h())
        assert response.status_code == 503
        assert _code(response) == "service_unavailable"
        assert response.headers["Retry-After"] == "2"
        assert built.calls["create"] == 0

    async def test_delete_204_replayed_empty(self, mini) -> None:
        app, client = mini
        first = await client.delete("/api/v1/items/7", headers=_h())
        second = await client.delete("/api/v1/items/7", headers=_h())
        assert first.status_code == second.status_code == 204
        assert second.content == b""
        assert second.headers[REPLAYED_HEADER] == "true"
        assert "content-length" not in second.headers
        assert app.calls["delete"] == 1

    async def test_body_over_limit_is_413(self) -> None:
        built = _build(config=IdempotencyConfig(heartbeat_seconds=None, max_request_bytes=1024))
        transport = httpx.ASGITransport(app=built.app)

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(4):
                yield b"y" * 512

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            declared = await client.post("/api/v1/upload", content=b"x" * 2048, headers=_h())
            streamed = await client.post("/api/v1/upload", content=chunks(), headers=_h("key-2"))
            small = await client.post("/api/v1/upload", content=b"x" * 1024, headers=_h("key-3"))
        for response in (declared, streamed):
            assert response.status_code == 413
            assert _code(response) == "file_too_large"
        assert small.status_code == 201
        assert built.calls["upload"] == 1
        assert built.store.calls == ["claim", "complete"]

    async def test_disabled_config_passes_through(self) -> None:
        built = _build(config=IdempotencyConfig(enabled=False, heartbeat_seconds=None))
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/items", json={}, headers=_h())
            await client.post("/api/v1/items", json={}, headers=_h())
        assert built.calls["create"] == 2
        assert built.store.calls == []

    async def test_expired_lease_is_taken_over(self, mini, caplog) -> None:
        app, client = mini
        await client.post("/api/v1/items", json={"name": "a"}, headers=_h())
        row = next(iter(app.store.rows.values()))
        row.status, row.lock_token, row.lease_expired = "processing", uuid.uuid4(), True
        with caplog.at_level(logging.WARNING, logger="app.idempotency"):
            response = await client.post("/api/v1/items", json={"name": "a"}, headers=_h())
        assert response.status_code == 201
        assert REPLAYED_HEADER not in response.headers
        assert app.calls["create"] == 2
        assert "аренда предыдущего владельца истекла" in caplog.text

    async def test_expired_key_is_reset(self, mini) -> None:
        app, client = mini
        await client.post("/api/v1/items", json={"name": "a"}, headers=_h())
        next(iter(app.store.rows.values())).expired = True
        response = await client.post("/api/v1/items", json={"name": "b"}, headers=_h())
        assert response.status_code == 201
        assert response.json()["name"] == "b"

    async def test_heartbeat_extends_lease(self, caplog) -> None:
        store = FakeStore(heartbeat_alive=False)
        built = _build(store, config=IdempotencyConfig(heartbeat_seconds=0.01))
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            task = asyncio.create_task(client.post("/api/v1/slow", headers=_h()))
            await asyncio.wait_for(built.started.wait(), timeout=5)
            with caplog.at_level(logging.WARNING, logger="app.idempotency"):
                await asyncio.sleep(0.05)
            built.gate.set()
            response = await asyncio.wait_for(task, timeout=5)
        assert response.status_code == 201
        assert store.calls.count("heartbeat") == 1  # False — продление прекращено
        assert "аренда перехвачена" in caplog.text

    async def test_default_resolver_looked_up_at_request_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = FakeStore()
        app = FastAPI()

        @app.post("/api/v1/ping")
        async def ping() -> dict[str, bool]:
            return {"ok": True}

        app.add_middleware(
            IdempotencyMiddleware, store=store, config=IdempotencyConfig(heartbeat_seconds=None)
        )

        async def patched(headers: Headers) -> str | None:
            return "sub:patched"

        monkeypatch.setattr(idem, "resolve_principal", patched)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/v1/ping", headers={"Idempotency-Key": "k"})
        assert ("sub:patched", "k") in store.rows

    async def test_resolver_failure_is_503(self) -> None:
        async def broken(headers: Headers) -> str | None:
            raise RuntimeError("JWKS недоступен")

        built = _build(resolver=broken)
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/items", json={}, headers=_h())
        assert response.status_code == 503
        assert built.calls["create"] == 0


# --------------------------------------------------------------------------------------
# OpenAPI
# --------------------------------------------------------------------------------------


class TestOpenApi:
    @staticmethod
    def _app() -> FastAPI:
        app = FastAPI(openapi_url="/api/v1/openapi.json")

        @app.post("/api/v1/requests", status_code=201)
        async def create(body: dict[str, Any]) -> dict[str, Any]:
            return body

        @app.get("/api/v1/requests")
        async def list_requests() -> dict[str, Any]:
            return {}

        @app.post("/api/v1/integrations/cms/webhook")
        async def webhook() -> dict[str, Any]:
            return {}

        @app.post("/health/ping")
        async def outside() -> dict[str, Any]:
            return {}

        install_idempotency_openapi(app)
        return app

    @staticmethod
    def _header(operation: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            p
            for p in operation.get("parameters", [])
            if p["in"] == "header" and p["name"] == "Idempotency-Key"
        ]

    def test_header_on_mutations_only(self) -> None:
        schema = self._app().openapi()
        post = schema["paths"]["/api/v1/requests"]["post"]
        header = self._header(post)
        assert len(header) == 1
        assert header[0]["required"] is False
        assert header[0]["schema"]["maxLength"] == 255
        assert "409" in post["responses"]
        assert "idempotency_key_reused" in post["responses"]["422"]["description"]
        assert not self._header(schema["paths"]["/api/v1/requests"]["get"])
        assert not self._header(schema["paths"]["/api/v1/integrations/cms/webhook"]["post"])
        assert not self._header(schema["paths"]["/health/ping"]["post"])

    def test_idempotent(self) -> None:
        app = self._app()
        app.openapi()
        schema = app.openapi()
        assert len(self._header(schema["paths"]["/api/v1/requests"]["post"])) == 1
        again = add_idempotency_to_openapi(schema)
        assert len(self._header(again["paths"]["/api/v1/requests"]["post"])) == 1
        description = again["paths"]["/api/v1/requests"]["post"]["responses"]["422"]
        assert description["description"].count("idempotency_key_reused") == 1


# --------------------------------------------------------------------------------------
# Подключение в app.main: порядок middleware, Swagger, путь запроса без БД
# --------------------------------------------------------------------------------------


def _idempotency_header(operation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        p
        for p in operation.get("parameters", [])
        if p["in"] == "header" and p["name"] == idem.IDEMPOTENCY_HEADER
    ]


@pytest.fixture(scope="module")
def app_openapi() -> dict[str, Any]:
    """Схема настоящего приложения (строится ~0,4 с — одна на модуль)."""
    return create_app().openapi()


@pytest.fixture
def app_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Хранилище по умолчанию настоящего приложения → FakeStore (middleware ищет
    get_idempotency_store в момент запроса)."""
    store = FakeStore()
    monkeypatch.setattr(idempotency_store, "get_idempotency_store", lambda: store)
    return store


def _app_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _internal(key: str) -> dict[str, str]:
    return {"X-Internal-Token": get_settings().internal_api_token, "Idempotency-Key": key}


class TestAppWiring:
    def test_middleware_order(self) -> None:
        app = create_app()
        # последний добавленный — внешний: RequestId снаружи (trace_id есть у ответов
        # middleware), Idempotency внутри (ApiError обработчиков уже JSON-ответ)
        assert [m.cls for m in app.user_middleware] == [
            RequestIdMiddleware,
            IdempotencyMiddleware,
        ]
        # без аргументов: конфиг, хранилище и резолвер — лениво из Settings при запросе
        assert app.user_middleware[1].args == () and app.user_middleware[1].kwargs == {}

    def test_openapi_header_on_every_mutation(self, app_openapi: dict[str, Any]) -> None:
        schema = app_openapi
        covered = 0
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                if method.upper() not in idem.MUTATING_METHODS:
                    assert not _idempotency_header(operation), (method, path)
                    continue
                header = _idempotency_header(operation)
                if not path.startswith("/api/v1/") or path in idem.DEFAULT_EXCLUDED_PATHS:
                    assert not header, (method, path)
                    continue
                assert len(header) == 1, (method, path)
                assert header[0]["required"] is False
                responses = operation["responses"]
                assert "idempotency_in_progress" in responses["409"]["description"]
                assert "idempotency_key_reused" in responses["422"]["description"]
                covered += 1
        assert covered >= 70  # все мутации API (74 до доработок + F1–F3)
        for webhook in idem.DEFAULT_EXCLUDED_PATHS:
            assert webhook in schema["paths"], webhook  # исключение — не опечатка в пути

    def test_openapi_keys_existing_responses(self, app_openapi: dict[str, Any]) -> None:
        paths = app_openapi["paths"]
        create = paths["/api/v1/requests"]["post"]
        assert _idempotency_header(create)
        assert not _idempotency_header(paths["/api/v1/requests"]["get"])
        # 422 FastAPI (схема HTTPValidationError) сохраняется, описание дополняется
        assert "content" in create["responses"]["422"]
        assert "idempotency_key_reused" in create["responses"]["422"]["description"]
        purge = paths["/api/v1/internal/jobs/purge-idempotency-keys"]["post"]
        assert _idempotency_header(purge)

    async def test_openapi_json_served_with_header(self) -> None:
        async with _app_client() as client:
            response = await client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        post = response.json()["paths"]["/api/v1/requests"]["post"]
        assert len(_idempotency_header(post)) == 1

    async def test_anonymous_with_key_passes_through(self, app_store: FakeStore) -> None:
        async with _app_client() as client:
            response = await client.post(
                "/api/v1/ui/presets",
                json={"screen": "s", "name": "n"},
                headers={"Idempotency-Key": "k-anon"},
            )
        assert response.status_code == 401
        assert _code(response) == "unauthorized"
        assert app_store.calls == []  # принципала нет — хранилище не трогаем

    async def test_internal_principal_4xx_releases_key(self, app_store: FakeStore) -> None:
        # настоящий резолвер признал X-Internal-Token (`internal`), обработчику нужен JWT → 401
        async with _app_client() as client:
            response = await client.post(
                "/api/v1/ui/presets", json={"screen": "s", "name": "n"}, headers=_internal("k-4xx")
            )
        assert response.status_code == 401
        assert REPLAYED_HEADER not in response.headers
        assert app_store.calls == ["claim", "release"]
        assert app_store.rows == {}

    async def test_invalid_key_400_with_trace_id(self, app_store: FakeStore) -> None:
        async with _app_client() as client:
            response = await client.post(
                "/api/v1/internal/jobs/outbox-relay", headers=_internal("two words")
            )
        assert response.status_code == 400
        assert _code(response) == "idempotency_key_invalid"
        assert response.json()["error"]["trace_id"] == response.headers[REQUEST_ID_HEADER]
        assert app_store.calls == []

    async def test_store_failure_fail_closed_503(self, app_store: FakeStore) -> None:
        app_store.fail_claim = True
        async with _app_client() as client:
            response = await client.post(
                "/api/v1/internal/jobs/purge-idempotency-keys", headers=_internal("k-503")
            )
        assert response.status_code == 503
        assert _code(response) == "service_unavailable"
        assert response.headers["Retry-After"] == "2"
        assert response.json()["error"]["trace_id"] == response.headers[REQUEST_ID_HEADER]
