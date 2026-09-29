"""Исходящие вебхуки lms-stub → CRM: outbox в SQLite + фоновый relay.

Политика доставки — как у CRM (api-contract §13.7):
ретраи только на сетевые ошибки и 5xx, backoff 1 мин → 5 мин → 30 мин → 2 ч → 6 ч
(настраивается `WEBHOOK_RETRY_SCHEDULE`); 4xx — ошибка контракта, сразу DLQ.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from .common import now_iso, to_json
from .config import Settings
from .db import Storage
from .security import sign_body

logger = logging.getLogger("lms_stub.outbox")

#: Система-источник исходящих событий этого сервиса (поле `source` конверта).
SOURCE_SYSTEM = "lms"

DeliveredHook = Callable[[dict[str, Any], dict[str, Any] | None], None]


class WebhookRelay:
    """Фоновая доставка событий из outbox в CRM с подписью HMAC."""

    def __init__(
        self,
        *,
        storage: Storage,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        on_delivered: DeliveredHook | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._on_delivered = on_delivered
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False

    # ------------------------------------------------------------- публичное

    def enqueue(
        self,
        *,
        event_type: str,
        correlation: dict[str, Any],
        payload: dict[str, Any],
        local_ref: str | None = None,
    ) -> str:
        """Собирает конверт §13.1, кладёт в outbox и будит relay. Возвращает event_id."""
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": now_iso(),
            "source": SOURCE_SYSTEM,
            "correlation": correlation,
            "payload": payload,
        }
        self._storage.enqueue_outbound(
            event_id=envelope["event_id"],
            event_type=event_type,
            local_ref=local_ref,
            envelope_raw=to_json(envelope),
        )
        self._notify()
        return envelope["event_id"]

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.http_timeout_seconds)
            self._owns_client = True
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="webhook-relay")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._wake.set()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def flush_once(self) -> int:
        """Одна итерация доставки: обрабатывает все события, чей срок наступил."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.http_timeout_seconds)
            self._owns_client = True
        rows = self._storage.due_outbound(time.time())
        for row in rows:
            await self._attempt(row)
        return len(rows)

    # ------------------------------------------------------------ внутреннее

    def _notify(self) -> None:
        """Потокобезопасная побудка relay (enqueue зовётся из threadpool FastAPI)."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._wake.set)

    async def _run(self) -> None:
        while not self._stopping:
            try:
                await self.flush_once()
            except Exception:
                logger.exception("Сбой итерации relay исходящих вебхуков")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._settings.relay_poll_seconds
                )
            self._wake.clear()

    async def _attempt(self, row: dict[str, Any]) -> None:
        assert self._client is not None
        body = row["envelope"].encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": sign_body(self._settings.webhook_secret, body),
        }
        try:
            response = await self._client.post(
                self._settings.crm_webhook_url, content=body, headers=headers
            )
        except httpx.HTTPError as exc:
            self._register_failure(row, f"{type(exc).__name__}: {exc}")
            return
        attempts = row["attempts"] + 1
        if response.status_code < 300:
            self._storage.mark_delivered(row["event_id"], attempts, response.text)
            logger.info(
                "Событие %s (%s) доставлено в CRM с попытки %d",
                row["event_id"],
                row["event_type"],
                attempts,
            )
            self._fire_delivered(row, response.text)
        elif response.status_code < 500:
            # 4xx — ошибка контракта: не ретраим (api-contract §13.7), сразу DLQ.
            self._storage.mark_dead(
                row["event_id"], attempts, f"HTTP {response.status_code}: {response.text[:300]}"
            )
            logger.warning(
                "Событие %s отклонено CRM (HTTP %d) — перенесено в DLQ",
                row["event_id"],
                response.status_code,
            )
        else:
            self._register_failure(row, f"HTTP {response.status_code}: {response.text[:300]}")

    def _register_failure(self, row: dict[str, Any], error: str) -> None:
        attempts = row["attempts"] + 1
        schedule = self._settings.retry_schedule
        if attempts > len(schedule):
            self._storage.mark_dead(row["event_id"], attempts, error)
            logger.error(
                "Событие %s не доставлено после %d попыток — перенесено в DLQ: %s",
                row["event_id"],
                attempts,
                error,
            )
            return
        delay = schedule[attempts - 1]
        self._storage.mark_retrying(row["event_id"], attempts, time.time() + delay, error)
        logger.warning(
            "Событие %s: попытка %d не удалась (%s), повтор через %d с",
            row["event_id"],
            attempts,
            error,
            delay,
        )

    def _fire_delivered(self, row: dict[str, Any], response_text: str) -> None:
        if self._on_delivered is None:
            return
        try:
            parsed = json.loads(response_text) if response_text else None
        except ValueError:
            parsed = None
        try:
            self._on_delivered(row, parsed if isinstance(parsed, dict) else None)
        except Exception:
            logger.exception("Сбой обработчика on_delivered для события %s", row["event_id"])
