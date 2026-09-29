"""Запуск long-polling aiogram как фоновой задачи внутри процесса FastAPI (lifespan).

Один процесс — одна точка probes (deployment.md §2.3): readiness смотрит на
`BotRunner.running`. Без TELEGRAM_BOT_TOKEN runner не создаётся вовсе.
Остановка graceful: сначала `dispatcher.stop_polling()` (дообработка текущего
батча апдейтов), cancel — только по таймауту; вызывается из lifespan ДО закрытия
HTTP-клиентов, чтобы хэндлеры в полёте не остались без backend-клиента.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.backend_client import BackendClient
from app.bot.handlers import router
from app.bot.middleware import RateLimitMiddleware
from app.metrics import Metrics
from app.ratelimit import TelegramRateLimiter

logger = logging.getLogger(__name__)

_GRACEFUL_STOP_TIMEOUT = 5.0

BOT_COMMANDS = [
    BotCommand(command="my", description="Мои открытые заявки"),
    BotCommand(command="stuck", description="Зависшие заявки"),
    BotCommand(command="summary", description="Сводка по воронке"),
    BotCommand(command="inbox", description="Непрочитанные уведомления"),
    BotCommand(command="link", description="Привязать аккаунт «Альмы» (код из настроек)"),
    BotCommand(command="unlink", description="Отвязать Telegram"),
    BotCommand(command="help", description="Справка"),
]


def create_dispatcher(
    backend: BackendClient,
    limiter: TelegramRateLimiter,
    metrics: Metrics | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher()
    router.message.middleware(RateLimitMiddleware(limiter, metrics))
    dispatcher.include_router(router)
    dispatcher["backend"] = backend  # DI в хэндлеры по имени аргумента
    dispatcher["metrics"] = metrics
    return dispatcher


class BotRunner:
    def __init__(self, bot: Bot, dispatcher: Dispatcher) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="tg-bot-polling")
        logger.info("Telegram-бот: long-polling запущен")

    async def _run(self) -> None:
        with contextlib.suppress(Exception):
            await self._bot.set_my_commands(BOT_COMMANDS)
        # Через внешний прокси (закрытый контур/DMZ) сетевые обрывы long-poll —
        # штатный режим, а не авария: перезапускаем polling с нарастающей паузой.
        # Фатальны только CancelledError (graceful shutdown через stop()).
        backoff = 1.0
        while True:
            try:
                await self._dispatcher.start_polling(
                    self._bot,
                    handle_signals=False,
                    allowed_updates=["message"],
                    polling_timeout=25,
                )
                return  # штатный stop_polling
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Long-polling Telegram оборвался — перезапуск через %.0f с",
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _request_stop(self) -> None:
        with contextlib.suppress(RuntimeError):  # «Polling is not started» — уже остановлен
            await self._dispatcher.stop_polling()

    async def stop(self) -> None:
        """Graceful shutdown: штатный stop_polling, cancel — только по таймауту."""
        if self._task is None:
            return
        task, self._task = self._task, None
        if not task.done():
            stopper = asyncio.create_task(self._request_stop(), name="tg-bot-stop")
            done, _ = await asyncio.wait({task}, timeout=_GRACEFUL_STOP_TIMEOUT)
            stopper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stopper
            if task not in done:
                logger.warning("Telegram-бот не остановился штатно, отменяем задачу polling")
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await self._bot.session.close()
        logger.info("Telegram-бот остановлен")
