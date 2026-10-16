"""Провайдер GigaChat на чистом httpx: OAuth-кэш, chat, ретраи, фолбэк structured output.

Wire-формат — как в SDK `gigachat` 0.2.3, отлаженном в «Орбите»:

- OAuth: POST {GIGACHAT_AUTH_URL}, `Authorization: Basic <ключ>`, `RqUID: <uuid4>`,
  форма `scope=<GIGACHAT_SCOPE>` → `{"access_token", "expires_at" (мс)}` или `{"tok", "exp"}`.
  Токен живёт ~30 мин; кэшируем до `expires_at − 60 с`, обновление — под asyncio.Lock
  (параллельные запросы делают один OAuth-вызов).
- Chat: POST {GIGACHAT_API_URL}/chat/completions, `Authorization: Bearer <токен>`,
  `{"model", "messages", "temperature", "max_tokens", "stream": false}` и при схеме —
  `"response_format": {"type": "json_schema", "schema": …, "strict": false}`.

Structured output в API — бета: на 400/422 с `response_format` запоминаем
`structured_supported=False` до рестарта и повторяем без него (не считается ретраем);
backend всё равно разбирает JSON сам. 401 на chat — токен сброшен, один повтор OAuth + chat
(тоже не ретрай). Ретраи — сеть (connect/protocol), HTTP 429 и 5xx, до LLM_RETRY_ATTEMPTS
попыток внутри общего бюджета LLM_TIMEOUT_SECONDS; тайм-аут чтения не ретраится:
модель уже работала над ответом, второй такой же запрос лишь удвоит ожидание.

Ни ключ, ни токен, ни тексты сообщений в логи и тексты ошибок не попадают.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.providers.base import ProviderError, ProviderReply, ProviderRequest

logger = logging.getLogger(__name__)

USER_AGENT = "alma-llm-gateway/1.0"
# API отвергает temperature = 0 (должна быть > 0)
MIN_TEMPERATURE = 0.01
# токен обновляем заранее: за минуту до истечения
TOKEN_REFRESH_MARGIN_SECONDS = 60.0
# если OAuth не сообщил срок — токены GigaChat живут 30 минут
DEFAULT_TOKEN_TTL_SECONDS = 30 * 60.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RATE_LIMIT_RETRY_AFTER_DEFAULT = 30


class _Retryable(Exception):
    def __init__(self, error: ProviderError, retry_after: float | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.retry_after = retry_after


class _Unauthorized(Exception):
    def __init__(self, token: str) -> None:
        super().__init__("401 на chat")
        self.token = token


class _StructuredRejected(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"response_format отклонён: HTTP {status}")
        self.status = status


class _CallState:
    __slots__ = ("attempts", "failures", "spent")

    def __init__(self) -> None:
        self.attempts = 0  # все POST /chat/completions этого вызова
        self.failures = 0  # ретраибельные сбои (расходуют LLM_RETRY_ATTEMPTS)
        self.spent = 0  # total_tokens всех ответов, включая отклонённые


def _retry_after_seconds(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return int(value)
    return None


def _upstream_detail(response: httpx.Response) -> str:
    """Короткое пояснение из тела ошибки GigaChat (`message`), без эха запроса."""
    try:
        data = response.json()
    except ValueError:
        return ""
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str) or not message.strip():
        return ""
    compact = " ".join(message.split())[:160]
    return f": {compact}"


def _short(exc: Exception) -> str:
    """Текст сетевой ошибки (DNS/TLS/сброс соединения) — без заголовков и тел запроса."""
    return " ".join(str(exc).split())[:160] or "без описания"


def _usage_total(response: httpx.Response) -> int:
    if response.status_code != 200:
        return 0
    try:
        data = response.json()
    except ValueError:
        return 0
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return 0
    try:
        return max(0, int(usage.get("total_tokens") or 0))
    except (TypeError, ValueError):
        return 0


def _epoch_seconds(value: Any) -> float | None:
    """`expires_at` у GigaChat — миллисекунды epoch; на всякий случай принимаем и секунды."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value <= 0:
        return None
    return value / 1000.0 if value > 1e11 else float(value)


class GigaChatProvider:
    name = "gigachat"

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._settings = settings
        self._http = http
        # подменяются в тестах: задержки ретраев, «сейчас» для срока токена, джиттер
        self.sleep = sleep
        self.clock = clock
        self.jitter = jitter
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        # None — ещё не пробовали; False — сервер отверг response_format (до рестарта)
        self._structured_supported: bool | None = None
        self.oauth_calls = 0

    # --- состояние ---------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._settings.credentials)

    @property
    def structured_supported(self) -> bool | None:
        return self._structured_supported

    @property
    def token_cached(self) -> bool:
        return self._token_usable()

    def token_expires_in(self) -> float | None:
        """Секунд до истечения кэшированного токена (для смоука); None — токена нет."""
        if not self._token:
            return None
        return max(0.0, self._token_expires_at - self.clock())

    def _token_usable(self) -> bool:
        return bool(self._token) and (
            self.clock() < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS
        )

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def aclose(self) -> None:
        """HTTP-клиент принадлежит приложению и закрывается в его lifespan."""
        self._token = None

    # --- OAuth -------------------------------------------------------------------------

    async def ensure_token(self, *, stale: str | None = None) -> str:
        """Действующий токен; `stale` — токен, на который chat ответил 401 (обновить)."""
        if self._token_usable() and self._token != stale:
            return self._token  # type: ignore[return-value]
        async with self._get_lock():
            if self._token_usable() and self._token != stale:
                return self._token  # type: ignore[return-value]
            token, expires_at = await self._oauth()
            self._token, self._token_expires_at = token, expires_at
            return token

    async def _oauth(self) -> tuple[str, float]:
        headers = {
            "Authorization": f"Basic {self._settings.credentials}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        body = urlencode({"scope": self._settings.gigachat_scope})
        response = await self._http.post(
            self._settings.gigachat_auth_url, headers=headers, content=body
        )
        self.oauth_calls += 1
        status = response.status_code
        if status in (400, 401, 403):
            raise ProviderError(
                "llm_auth_failed",
                f"GigaChat отклонил ключ или scope (OAuth HTTP {status}"
                f"{_upstream_detail(response)})",
                http_status=502,
            )
        if status in RETRYABLE_STATUSES:
            retry_after = _retry_after_seconds(response)
            raise _Retryable(self._status_error(status, response, "OAuth"), retry_after)
        if status != 200:
            raise ProviderError(
                "llm_upstream_error",
                f"OAuth GigaChat ответил HTTP {status}{_upstream_detail(response)}",
            )
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("llm_upstream_error", "OAuth GigaChat вернул не JSON") from None
        if not isinstance(data, dict):
            raise ProviderError("llm_upstream_error", "OAuth GigaChat вернул не объект")
        token = data.get("access_token") or data.get("tok")
        if not isinstance(token, str) or not token:
            raise ProviderError("llm_upstream_error", "в ответе OAuth GigaChat нет access_token")
        expires_at = _epoch_seconds(data.get("expires_at", data.get("exp")))
        if expires_at is None:
            expires_at = self.clock() + DEFAULT_TOKEN_TTL_SECONDS
        return token, expires_at

    def _drop_token(self, token: str) -> None:
        if self._token == token:
            self._token = None
            self._token_expires_at = 0.0

    # --- chat --------------------------------------------------------------------------

    async def chat(self, request: ProviderRequest) -> ProviderReply:
        if not self.configured:
            raise ProviderError(
                "llm_not_configured",
                "GigaChat не настроен: пустой GIGACHAT_CREDENTIALS",
                http_status=503,
            )
        budget = self._settings.llm_timeout_seconds
        state = _CallState()
        deadline = asyncio.get_running_loop().time() + budget
        try:
            async with asyncio.timeout(budget):
                return await self._run(request, state, deadline)
        except TimeoutError:
            error = ProviderError(
                "llm_timeout", f"GigaChat не ответил за {budget:g} с", http_status=504
            )
            raise self._finish(error, state) from None
        except ProviderError as error:
            raise self._finish(error, state) from None

    @staticmethod
    def _finish(error: ProviderError, state: _CallState) -> ProviderError:
        error.attempts = state.attempts
        error.spent_tokens = state.spent
        return error

    async def _run(
        self, request: ProviderRequest, state: _CallState, deadline: float
    ) -> ProviderReply:
        loop = asyncio.get_running_loop()
        structured = (
            request.response_schema is not None and self._structured_supported is not False
        )
        reauthorized = False
        while True:
            state.attempts += 1
            try:
                return await self._attempt(request, state, structured)
            except _StructuredRejected as rejected:
                logger.warning(
                    "GigaChat отклонил response_format — дальше без него до рестарта",
                    extra={
                        "request_id": request.request_id,
                        "model": request.model,
                        "status_code": rejected.status,
                    },
                )
                self._structured_supported = False
                structured = False
            except _Unauthorized as unauthorized:
                if reauthorized:
                    raise ProviderError(
                        "llm_auth_failed",
                        "GigaChat отклонил токен доступа и после повторной авторизации",
                        http_status=502,
                    ) from None
                reauthorized = True
                self._drop_token(unauthorized.token)
            except _Retryable as retry:
                error = retry.error
                state.failures += 1
                if error.code == "llm_rate_limited" and error.retry_after is None:
                    error.retry_after = RATE_LIMIT_RETRY_AFTER_DEFAULT
                if state.failures >= self._settings.llm_retry_attempts:
                    raise error from None
                delay = self._delay(state.failures, retry.retry_after)
                if loop.time() + delay >= deadline:
                    # ожидание не помещается в бюджет — отдаём ошибку сразу, с Retry-After
                    raise error from None
                logger.info(
                    "повтор запроса к GigaChat",
                    extra={
                        "request_id": request.request_id,
                        "error_code": error.code,
                        "attempt": state.attempts,
                        "delay_ms": int(delay * 1000),
                    },
                )
                await self.sleep(delay)

    def _delay(self, failures: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return float(retry_after)
        base = self._settings.llm_retry_base_delay_seconds
        return base * (2 ** (failures - 1)) + self.jitter(0.0, base / 2)

    def _status_error(self, status: int, response: httpx.Response, what: str) -> ProviderError:
        if status == 429:
            return ProviderError(
                "llm_rate_limited",
                f"GigaChat: превышен лимит запросов ({what} HTTP 429)",
                http_status=503,
                retry_after=_retry_after_seconds(response),
            )
        return ProviderError(
            "llm_upstream_error",
            f"GigaChat ответил HTTP {status} ({what}){_upstream_detail(response)}",
        )

    def _chat_url(self) -> str:
        return f"{self._settings.gigachat_api_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _chat_body(request: ProviderRequest, structured: bool) -> bytes:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": max(float(request.temperature), MIN_TEMPERATURE),
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if structured and request.response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "schema": request.response_schema,
                "strict": False,
            }
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    async def _attempt(
        self, request: ProviderRequest, state: _CallState, structured: bool
    ) -> ProviderReply:
        try:
            token = await self.ensure_token()
            response = await self._http.post(
                self._chat_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Request-ID": request.request_id,
                    "User-Agent": USER_AGENT,
                },
                content=self._chat_body(request, structured),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            raise _Retryable(
                ProviderError(
                    "llm_upstream_error",
                    f"сетевая ошибка до GigaChat ({type(exc).__name__}: {_short(exc)})",
                )
            ) from None
        except httpx.TimeoutException:
            # чтение/запись/пул: модель уже работала — повтор только удвоит ожидание
            raise ProviderError(
                "llm_timeout", "GigaChat не прислал ответ вовремя (тайм-аут чтения)",
                http_status=504,
            ) from None
        except httpx.HTTPError as exc:
            raise ProviderError(
                "llm_upstream_error",
                f"ошибка соединения с GigaChat ({type(exc).__name__}: {_short(exc)})",
            ) from None

        state.spent += _usage_total(response)
        status = response.status_code
        if status == 401:
            raise _Unauthorized(token)
        if structured and status in (400, 422):
            raise _StructuredRejected(status)
        if status in RETRYABLE_STATUSES:
            raise _Retryable(
                self._status_error(status, response, "chat"), _retry_after_seconds(response)
            )
        if status != 200:
            raise ProviderError(
                "llm_upstream_error",
                f"GigaChat ответил HTTP {status}{_upstream_detail(response)}",
            )
        return self._parse(request, response, state, structured)

    def _parse(
        self,
        request: ProviderRequest,
        response: httpx.Response,
        state: _CallState,
        structured: bool,
    ) -> ProviderReply:
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("llm_upstream_error", "GigaChat вернул не JSON") from None
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ProviderError("llm_upstream_error", "пустой список choices в ответе GigaChat")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if structured and self._structured_supported is None:
            self._structured_supported = True
        if choice.get("finish_reason") == "length":
            logger.warning(
                "ответ GigaChat обрезан по max_tokens",
                extra={"request_id": request.request_id, "max_tokens": request.max_tokens},
            )
        return ProviderReply(
            text=str(message.get("content") or ""),
            model=str(data.get("model") or request.model),
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            total_tokens=_as_int(usage.get("total_tokens")),
            structured=structured,
            attempts=state.attempts,
            spent_tokens=state.spent,
        )


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
