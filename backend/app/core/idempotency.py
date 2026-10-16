"""Сквозной Idempotency-Key для мутаций API (api-contract.md §1.6, data-model.md §9.6).

Семантика — черновик IETF `draft-ietf-httpapi-idempotency-key-header`:
- POST/PUT/PATCH/DELETE под `/api/v1/` с заголовком `Idempotency-Key` (1..255 печатных
  ASCII, рекомендуется UUID v4; кавычки sf-string допустимы). Без заголовка поведение
  прежнее; вебхуки `/integrations/{lms,cms}/webhook` исключены — у них своя
  идемпотентность по `event_id` и HMAC (§13.5);
- область действия ключа — принципал: `sub:<keycloak sub>` (проверенный JWT) или `internal`
  (X-Internal-Token). Ключ одного пользователя не даёт доступа к ответу другого;
- повтор с тем же отпечатком (метод, путь, query, media-type, дайджест тела) в пределах
  24 ч после успеха → сохранённые статус, тело и заголовки + `Idempotent-Replayed: true`;
  тот же ключ с другим запросом → 422 `idempotency_key_reused`; повтор, пока первый
  запрос в работе → 409 `idempotency_in_progress` + `Retry-After: 1`;
- сохраняются только 2xx: при 4xx/5xx и исключении ключ освобождается, повтор безопасен;
- хранилище — таблица PostgreSQL `idempotency_key` (app.core.idempotency_store); его
  ошибка → fail-closed 503 `service_unavailable` + `Retry-After: 2`.

Протокол из трёх фаз, не атомарный (обработчики коммитят сами): заявка на ключ —
короткий commit до бизнес-транзакции; выполнение под арендой (`locked_until`, продление
каждые lease/4 с, защита токеном владельца); завершение — commit до отправки клиенту
последнего чанка тела. Падение процесса между COMMIT бизнес-операции и сохранением
ответа (миллисекунды) закрывают доменные защиты: `version`, машина состояний импорта,
UNIQUE, `dedup_key`, `event_id`.

Отступления от черновика IETF: тело ошибки — единый конверт проекта §1.4 вместо RFC 9457;
заголовок повтора `Idempotent-Replayed: true` (конвенция Stripe).

Middleware — чистый ASGI (не BaseHTTPMiddleware): только так можно не пустить запрос
в обработчик, перехватить тело ответа и проиграть буферизованное тело запроса.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, Any, Literal, Protocol

import orjson
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import ApiError, error_payload, request_id_var
from app.core.security import decode_token, verify_internal_token

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.core.config import Settings

logger = logging.getLogger("app.idempotency")

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_EXCLUDED_PATHS = frozenset(
    {"/api/v1/integrations/lms/webhook", "/api/v1/integrations/cms/webhook"}
)
# не сохраняются и не проигрываются: служебные/транспортные заголовки и X-Request-Id
# (повтор получает свой trace_id от RequestIdMiddleware)
_DROP_RESPONSE_HEADERS = frozenset(
    {
        "date",
        "server",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "x-request-id",
        "set-cookie",
        "idempotent-replayed",
    }
)

_KEY_RE = re.compile(r"[\x21-\x7e]{1,255}")
_BOUNDARY_RE = re.compile(r"""boundary=(?:"([^"]+)"|([^;\s]+))""", re.IGNORECASE)
# граница multipart у браузера случайна на каждый ретрай FormData — в дайджесте
# она заменяется постоянным маркером, иначе легитимный повтор загрузки получил бы 422
_BOUNDARY_TOKEN = b"\x00B\x00"
_JSON_CANONICAL_LIMIT = 1024 * 1024  # JSON до 1 МиБ канонизируется (порядок ключей)
_SPOOL_BYTES = 1024 * 1024  # тело запроса в памяти до 1 МиБ, дальше — на диске
_REPLAY_CHUNK = 64 * 1024

MESSAGE_KEY_INVALID = (
    "Некорректный заголовок Idempotency-Key: ожидается строка до 255 печатных символов "
    "(рекомендуется UUID)"
)
MESSAGE_IN_PROGRESS = (
    "Запрос с этим ключом идемпотентности ещё выполняется — повторите через несколько секунд"
)
MESSAGE_REPLAY_UNAVAILABLE = (
    "Операция уже выполнена, но сохранённый ответ недоступен — обновите данные"
)
MESSAGE_KEY_REUSED = (
    "Ключ идемпотентности уже использован для другого запроса — сгенерируйте новый ключ"
)
MESSAGE_STORE_UNAVAILABLE = (
    "Хранилище ключей идемпотентности временно недоступно — повторите запрос с тем же ключом"
)


# --------------------------------------------------------------------------------------
# Чистые функции: ключ, отпечаток запроса, политика сохранения
# --------------------------------------------------------------------------------------


class IdempotencyKeyInvalid(ValueError):
    """Заголовок Idempotency-Key не соответствует контракту §1.6."""


def parse_idempotency_key(values: list[str]) -> str:
    """Ровно одно значение; одна пара внешних кавычек (sf-string) снимается;
    1..255 печатных ASCII без пробелов."""
    if len(values) != 1:
        raise IdempotencyKeyInvalid("ожидается ровно один заголовок Idempotency-Key")
    raw = values[0].strip(" \t")
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    if not _KEY_RE.fullmatch(raw):
        raise IdempotencyKeyInvalid("ключ — 1..255 печатных ASCII-символов без пробелов")
    return raw


def media_type(content_type: str | None) -> str:
    """`application/json; charset=utf-8` → `application/json` (без параметров)."""
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _is_json(media: str) -> bool:
    return media == "application/json" or media.endswith("+json")


def _multipart_boundary(content_type: str | None) -> bytes | None:
    if not content_type or not media_type(content_type).startswith("multipart/"):
        return None
    match = _BOUNDARY_RE.search(content_type)
    if match is None:
        return None
    boundary = match.group(1) or match.group(2)
    return boundary.encode("latin-1") if boundary else None


class BodyDigest:
    """Потоковый sha256 тела запроса. Для multipart каждая `--<boundary>` заменяется
    постоянным маркером; хвост `len(marker) - 1` байт придерживается между чанками,
    чтобы граница, разрезанная чанками, тоже нашлась."""

    def __init__(self, content_type: str | None) -> None:
        self._hash = hashlib.sha256()
        boundary = _multipart_boundary(content_type)
        self._marker = b"--" + boundary if boundary else None
        self._tail = b""

    def update(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._marker is None:
            self._hash.update(chunk)
            return
        data = (self._tail + chunk).replace(self._marker, _BOUNDARY_TOKEN)
        keep = len(self._marker) - 1
        if len(data) > keep:
            self._hash.update(data[: len(data) - keep])
            self._tail = data[len(data) - keep :]
        else:
            self._tail = data

    def hexdigest(self) -> str:
        final = self._hash.copy()
        final.update(self._tail)
        return final.hexdigest()


def canonical_json_digest(raw: bytes) -> str | None:
    """sha256 канонического JSON (ключи отсортированы); None — тело не JSON."""
    try:
        canonical = orjson.dumps(orjson.loads(raw), option=orjson.OPT_SORT_KEYS)
    except (orjson.JSONDecodeError, orjson.JSONEncodeError, TypeError):
        return None
    return hashlib.sha256(canonical).hexdigest()


def request_fingerprint(
    method: str, path: str, query: str, content_type: str | None, body_digest: str
) -> str:
    """Отпечаток запроса: sha256 от метода, пути, query, media-type и дайджеста тела."""
    parts = (method.upper(), path, query, media_type(content_type), body_digest)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def should_persist(status_code: int) -> bool:
    """Сохраняются только успешные ответы: 4xx/5xx состояние не меняют и повторяемы."""
    return 200 <= status_code < 300


def filter_response_headers(raw: list[tuple[bytes, bytes]]) -> list[list[str]]:
    """ASGI-заголовки ответа → `[[name, value], ...]` без служебных."""
    kept: list[list[str]] = []
    for name, value in raw:
        decoded = name.decode("latin-1").lower()
        if decoded in _DROP_RESPONSE_HEADERS:
            continue
        kept.append([decoded, value.decode("latin-1")])
    return kept


# --------------------------------------------------------------------------------------
# Протокол хранилища
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyRequest:
    principal: str
    key: str
    method: str
    path: str  # путь + '?query'
    request_hash: str
    request_id: str | None = None


@dataclass(frozen=True)
class Ticket:
    """Право владельца на ключ: завершить/освободить можно только с этим lock_token."""

    id: uuid.UUID
    lock_token: uuid.UUID
    takeover: bool = False


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    headers: list[list[str]]
    body: bytes | None
    body_omitted: bool = False


@dataclass(frozen=True)
class KeySnapshot:
    """Состояние записи ключа; `expired` и `lease_expired` считаются в SQL по now()."""

    method: str
    path: str
    request_hash: str
    status: str
    expired: bool
    lease_expired: bool


class Decision(str, Enum):
    RESET = "reset"  # запись просрочена (TTL) — перезаписать как новую
    REUSED = "reused"  # ключ от другого запроса → 422
    REPLAY = "replay"  # успех сохранён → повтор ответа
    IN_PROGRESS = "in_progress"  # первый запрос ещё в работе → 409
    TAKEOVER = "takeover"  # аренда истекла (владелец упал) → перехватить


def decide(snapshot: KeySnapshot, req: KeyRequest) -> Decision:
    """Что делать с существующей записью ключа (порядок проверок важен)."""
    if snapshot.expired:
        return Decision.RESET
    if (snapshot.method, snapshot.path, snapshot.request_hash) != (
        req.method,
        req.path,
        req.request_hash,
    ):
        return Decision.REUSED
    if snapshot.status == "completed":
        return Decision.REPLAY
    if not snapshot.lease_expired:
        return Decision.IN_PROGRESS
    return Decision.TAKEOVER


ClaimKind = Literal["owned", "replay", "in_progress", "reused", "replay_unavailable"]


@dataclass(frozen=True)
class ClaimResult:
    kind: ClaimKind
    ticket: Ticket | None = None
    response: StoredResponse | None = None


class IdempotencyStore(Protocol):
    async def claim(self, req: KeyRequest) -> ClaimResult: ...

    async def complete(self, ticket: Ticket, response: StoredResponse) -> bool: ...

    async def release(self, ticket: Ticket) -> bool: ...

    async def heartbeat(self, ticket: Ticket) -> bool: ...


# --------------------------------------------------------------------------------------
# Принципал и конфигурация
# --------------------------------------------------------------------------------------

PrincipalResolver = Callable[[Headers], Awaitable[str | None]]


async def resolve_principal(headers: Headers) -> str | None:
    """Проверенный JWT → `sub:<sub>`; валидный X-Internal-Token → `internal`; иначе None
    (middleware пропускает запрос, приложение само ответит 401)."""
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token:
        try:
            claims = await decode_token(token)
        except ApiError:
            pass  # невалидный токен: приложение само ответит 401
        else:
            return f"sub:{claims.sub}"
    if verify_internal_token(headers.get("x-internal-token"), get_settings().internal_api_token):
        return "internal"
    return None


@dataclass(frozen=True)
class IdempotencyConfig:
    enabled: bool = True
    ttl_hours: int = 24
    lease_seconds: int = 60
    heartbeat_seconds: float | None = 15.0  # None — продление выключено (тесты)
    max_request_bytes: int = 60 * 1024 * 1024
    max_response_bytes: int = 1024 * 1024
    path_prefix: str = "/api/v1/"
    excluded_paths: frozenset[str] = DEFAULT_EXCLUDED_PATHS

    @classmethod
    def from_settings(cls, settings: Settings) -> IdempotencyConfig:
        return cls(
            enabled=settings.idempotency_enabled,
            ttl_hours=settings.idempotency_ttl_hours,
            lease_seconds=settings.idempotency_lease_seconds,
            heartbeat_seconds=settings.idempotency_lease_seconds / 4,
            max_request_bytes=settings.idempotency_max_request_bytes,
            max_response_bytes=settings.idempotency_max_response_bytes,
        )


# --------------------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------------------


def _error_response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=error_payload(code, message), headers=headers
    )


class _RequestTooLarge(Exception):
    pass


class _ClientGone(Exception):
    pass


class IdempotencyMiddleware:
    """ASGI-middleware Idempotency-Key. Подключается внутри RequestIdMiddleware
    (видит trace_id; ApiError обработчиков к нему приходят уже JSON-ответом).

    Всё ленивое: без аргументов конфиг, хранилище и резолвер берутся из Settings при
    запросе, к БД на старте не ходит. Резолвер по умолчанию ищется в момент запроса как
    атрибут модуля `resolve_principal` — тесты подменяют его через monkeypatch.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: IdempotencyStore | None = None,
        principal_resolver: PrincipalResolver | None = None,
        config: IdempotencyConfig | None = None,
    ) -> None:
        self.app = app
        self._store = store
        self._resolver = principal_resolver
        self._config = config

    def _get_config(self) -> IdempotencyConfig:
        if self._config is None:
            return IdempotencyConfig.from_settings(get_settings())
        return self._config

    def _get_store(self) -> IdempotencyStore:
        if self._store is not None:
            return self._store
        from app.core.idempotency_store import get_idempotency_store

        return get_idempotency_store()

    @staticmethod
    def _applies(scope: Scope, config: IdempotencyConfig) -> bool:
        path: str = scope["path"]
        return (
            config.enabled
            and path.startswith(config.path_prefix)
            and path not in config.excluded_paths
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key_values = headers.getlist(IDEMPOTENCY_HEADER)
        config = self._get_config() if key_values else None
        if config is None or not self._applies(scope, config):
            await self.app(scope, receive, send)
            return

        resolver = self._resolver or resolve_principal  # имя модуля — в момент запроса
        try:
            principal = await resolver(headers)
        except Exception:
            logger.warning("Idempotency-Key: не удалось определить принципала", exc_info=True)
            await self._unavailable(scope, receive, send)
            return
        if principal is None:
            await self.app(scope, receive, send)  # аноним — приложение само ответит 401
            return

        try:
            key = parse_idempotency_key(key_values)
        except IdempotencyKeyInvalid:
            response = _error_response(400, "idempotency_key_invalid", MESSAGE_KEY_INVALID)
            await response(scope, receive, send)
            return

        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > config.max_request_bytes:
            await self._too_large(scope, receive, send, config)
            return

        buffer: Any = SpooledTemporaryFile(max_size=_SPOOL_BYTES)
        try:
            try:
                size, body_digest = await self._read_body(receive, buffer, headers, config)
            except _RequestTooLarge:
                await self._too_large(scope, receive, send, config)
                return
            except _ClientGone:
                return
            query = scope.get("query_string", b"").decode("latin-1")
            method = scope["method"].upper()
            path = scope["path"] + (f"?{query}" if query else "")
            req = KeyRequest(
                principal=principal,
                key=key,
                method=method,
                path=path,
                request_hash=request_fingerprint(
                    method, scope["path"], query, headers.get("content-type"), body_digest
                ),
                request_id=request_id_var.get() or None,
            )
            await self._handle(scope, receive, send, req, buffer, size, config)
        finally:
            buffer.close()

    async def _read_body(
        self, receive: Receive, buffer: Any, headers: Headers, config: IdempotencyConfig
    ) -> tuple[int, str]:
        content_type = headers.get("content-type")
        digest = BodyDigest(content_type)
        size = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _ClientGone
            if message["type"] != "http.request":
                continue
            chunk: bytes = message.get("body", b"")
            more_body = bool(message.get("more_body", False))
            size += len(chunk)
            if size > config.max_request_bytes:
                raise _RequestTooLarge
            if chunk:
                buffer.write(chunk)
                digest.update(chunk)
        body_digest = digest.hexdigest()
        if _is_json(media_type(content_type)) and size <= _JSON_CANONICAL_LIMIT:
            buffer.seek(0)
            body_digest = canonical_json_digest(buffer.read()) or body_digest
        buffer.seek(0)
        return size, body_digest

    async def _handle(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        req: KeyRequest,
        buffer: Any,
        size: int,
        config: IdempotencyConfig,
    ) -> None:
        store = self._get_store()
        try:
            result = await store.claim(req)
        except Exception:
            logger.warning(
                "Idempotency-Key: хранилище недоступно (principal=%s, key=%s)",
                req.principal,
                req.key,
                exc_info=True,
            )
            await self._unavailable(scope, receive, send)
            return

        if result.kind == "replay" and result.response is not None:
            logger.info(
                "Idempotency-Key: повтор ответа (principal=%s, key=%s, path=%s)",
                req.principal,
                req.key,
                req.path,
            )
            await self._send_replay(send, result.response)
            return
        if result.kind == "in_progress":
            response = _error_response(
                409, "idempotency_in_progress", MESSAGE_IN_PROGRESS, {"Retry-After": "1"}
            )
            await response(scope, receive, send)
            return
        if result.kind == "reused":
            response = _error_response(422, "idempotency_key_reused", MESSAGE_KEY_REUSED)
            await response(scope, receive, send)
            return
        if result.kind != "owned" or result.ticket is None:  # replay_unavailable
            response = _error_response(
                409, "idempotency_replay_unavailable", MESSAGE_REPLAY_UNAVAILABLE
            )
            await response(scope, receive, send)
            return

        ticket = result.ticket
        if ticket.takeover:
            logger.warning(
                "Idempotency-Key: аренда предыдущего владельца истекла, запрос выполняется "
                "повторно (principal=%s, key=%s)",
                req.principal,
                req.key,
            )
        await self._run_owned(scope, receive, send, store, ticket, req, buffer, size, config)

    async def _run_owned(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        store: IdempotencyStore,
        ticket: Ticket,
        req: KeyRequest,
        buffer: Any,
        size: int,
        config: IdempotencyConfig,
    ) -> None:
        sent = 0
        body_replayed = False

        async def replay_receive() -> Message:
            nonlocal sent, body_replayed
            if not body_replayed:
                chunk = buffer.read(_REPLAY_CHUNK)
                sent += len(chunk)
                more = sent < size
                if not more:
                    body_replayed = True
                return {"type": "http.request", "body": chunk, "more_body": more}
            return await receive()  # дальше — исходный канал (ожидание disconnect)

        status_code = 0
        raw_headers: list[tuple[bytes, bytes]] = []
        captured = bytearray()
        omitted = False
        finalized = False

        async def finalize() -> None:
            nonlocal finalized
            finalized = True
            try:
                if should_persist(status_code):
                    body: bytes | None = None if omitted else bytes(captured)
                    if body is not None:
                        try:
                            body.decode("utf-8")
                        except UnicodeDecodeError:
                            body = None
                    stored = StoredResponse(
                        status_code=status_code,
                        headers=filter_response_headers(raw_headers),
                        body=body,
                        body_omitted=body is None,
                    )
                    if not await store.complete(ticket, stored):
                        logger.warning(
                            "Idempotency-Key: ответ не сохранён — аренда перехвачена "
                            "(principal=%s, key=%s)",
                            req.principal,
                            req.key,
                        )
                else:
                    await store.release(ticket)
            except Exception:
                logger.warning(
                    "Idempotency-Key: не удалось завершить ключ (principal=%s, key=%s)",
                    req.principal,
                    req.key,
                    exc_info=True,
                )

        async def capture_send(message: Message) -> None:
            nonlocal status_code, raw_headers, omitted
            message_type = message["type"]
            if message_type == "http.response.start":
                status_code = int(message["status"])
                raw_headers = list(message.get("headers", []))
            elif message_type == "http.response.body" and not finalized:
                chunk: bytes = message.get("body", b"")
                if not omitted:
                    if len(captured) + len(chunk) > config.max_response_bytes:
                        omitted = True
                        captured.clear()
                    else:
                        captured.extend(chunk)
                if not message.get("more_body", False):
                    # сначала фиксируем результат, потом отдаём клиенту последний чанк
                    await finalize()
            elif message_type == "http.response.pathsend" and not finalized:
                omitted = True  # файл с диска — тело не сохраняем
                await finalize()
            await send(message)

        heartbeat = None
        if config.heartbeat_seconds:
            heartbeat = asyncio.create_task(
                self._heartbeat(store, ticket, req, config.heartbeat_seconds)
            )
        try:
            await self.app(scope, replay_receive, capture_send)
        except Exception:
            # CancelledError сюда не попадает: при разрыве ключ не освобождаем — аренда
            # истечёт сама (безопаснее для окна «после COMMIT»)
            if not finalized:
                finalized = True
                try:
                    await store.release(ticket)
                except Exception:
                    logger.warning(
                        "Idempotency-Key: не удалось освободить ключ (principal=%s, key=%s)",
                        req.principal,
                        req.key,
                        exc_info=True,
                    )
            raise
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
        if not finalized:
            logger.warning(
                "Idempotency-Key: обработчик не отправил ответ целиком, ключ освободится "
                "по истечении аренды (principal=%s, key=%s)",
                req.principal,
                req.key,
            )

    async def _heartbeat(
        self, store: IdempotencyStore, ticket: Ticket, req: KeyRequest, interval: float
    ) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                alive = await store.heartbeat(ticket)
            except Exception:
                logger.warning(
                    "Idempotency-Key: не удалось продлить аренду (principal=%s, key=%s)",
                    req.principal,
                    req.key,
                    exc_info=True,
                )
                continue
            if not alive:
                logger.warning(
                    "Idempotency-Key: аренда перехвачена другим запросом (principal=%s, key=%s)",
                    req.principal,
                    req.key,
                )
                return

    @staticmethod
    async def _send_replay(send: Send, stored: StoredResponse) -> None:
        body = stored.body or b""
        headers = [
            (name.encode("latin-1"), value.encode("latin-1")) for name, value in stored.headers
        ]
        if stored.status_code >= 200 and stored.status_code not in (204, 304):
            headers.append((b"content-length", str(len(body)).encode("latin-1")))
        headers.append((REPLAYED_HEADER.lower().encode("latin-1"), b"true"))
        await send(
            {"type": "http.response.start", "status": stored.status_code, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    @staticmethod
    async def _unavailable(scope: Scope, receive: Receive, send: Send) -> None:
        response = _error_response(
            503, "service_unavailable", MESSAGE_STORE_UNAVAILABLE, {"Retry-After": "2"}
        )
        await response(scope, receive, send)

    @staticmethod
    async def _too_large(
        scope: Scope, receive: Receive, send: Send, config: IdempotencyConfig
    ) -> None:
        limit_mb = config.max_request_bytes // (1024 * 1024)
        response = _error_response(413, "file_too_large", f"Тело запроса превышает {limit_mb} МБ")
        await response(scope, receive, send)


# --------------------------------------------------------------------------------------
# OpenAPI: заголовок и ответы 409/422 у каждой мутации (Swagger /api/v1/docs)
# --------------------------------------------------------------------------------------

_OPENAPI_PARAMETER: dict[str, Any] = {
    "name": IDEMPOTENCY_HEADER,
    "in": "header",
    "required": False,
    "schema": {"type": "string", "maxLength": 255},
    "description": (
        "Ключ идемпотентности (рекомендуется UUID v4), api-contract.md §1.6. Хранится 24 ч; "
        "повтор с тем же запросом → сохранённый ответ + `Idempotent-Replayed: true`; "
        "тот же ключ с другим телом → 422 `idempotency_key_reused`; запрос ещё "
        "выполняется → 409 `idempotency_in_progress` + `Retry-After`."
    ),
}
_OPENAPI_409 = (
    "Конфликт. Для Idempotency-Key: `idempotency_in_progress` (запрос с этим ключом ещё "
    "выполняется, `Retry-After`) или `idempotency_replay_unavailable`."
)
_OPENAPI_422 = (
    "`idempotency_key_reused` — ключ идемпотентности уже использован для другого запроса."
)


def _merge_response(responses: dict[str, Any], status: str, text: str) -> None:
    current = responses.get(status)
    if current is None:
        responses[status] = {"description": text}
        return
    description = str(current.get("description") or "")
    if text not in description:
        current["description"] = f"{description}. {text}" if description else text


def add_idempotency_to_openapi(
    schema: dict[str, Any],
    *,
    path_prefix: str = "/api/v1/",
    excluded_paths: frozenset[str] = DEFAULT_EXCLUDED_PATHS,
) -> dict[str, Any]:
    """Добавляет header-параметр Idempotency-Key и описания 409/422 ко всем мутациям
    под `/api/v1/` (кроме вебхуков). Идемпотентна: повторный вызов ничего не меняет."""
    for path, item in (schema.get("paths") or {}).items():
        if not path.startswith(path_prefix) or path in excluded_paths:
            continue
        for method, operation in item.items():
            if method.upper() not in MUTATING_METHODS or not isinstance(operation, dict):
                continue
            parameters = operation.setdefault("parameters", [])
            if not any(
                p.get("in") == "header" and str(p.get("name", "")).lower() == "idempotency-key"
                for p in parameters
            ):
                parameters.append(dict(_OPENAPI_PARAMETER))
            responses = operation.setdefault("responses", {})
            _merge_response(responses, "409", _OPENAPI_409)
            _merge_response(responses, "422", _OPENAPI_422)
    return schema


def install_idempotency_openapi(app: FastAPI) -> None:
    """Оборачивает `app.openapi`: схема строится как раньше и дополняется заголовком."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        return add_idempotency_to_openapi(original())

    app.openapi = openapi  # type: ignore[method-assign]
