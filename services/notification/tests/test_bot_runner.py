"""Graceful shutdown бота: штатный stop_polling, cancel — только по таймауту."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.runner import BotRunner


class FakeDispatcher:
    """Имитация Dispatcher: polling живёт до сигнала stop_polling."""

    def __init__(self, *, hang_on_stop: bool = False) -> None:
        self._stop = asyncio.Event()
        self._hang_on_stop = hang_on_stop
        self.stop_requested = False
        self.polling_cancelled = False

    async def start_polling(self, bot, **kwargs) -> None:  # noqa: ANN001
        try:
            await self._stop.wait()
        except asyncio.CancelledError:
            self.polling_cancelled = True
            raise

    async def stop_polling(self) -> None:
        self.stop_requested = True
        if self._hang_on_stop:
            await asyncio.Event().wait()  # зависший Bot API: сигнал так и не приходит
        self._stop.set()


def _fake_bot() -> SimpleNamespace:
    return SimpleNamespace(
        set_my_commands=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )


async def test_graceful_stop_without_cancel() -> None:
    dispatcher = FakeDispatcher()
    bot = _fake_bot()
    runner = BotRunner(bot, dispatcher)  # type: ignore[arg-type]

    await runner.start()
    await asyncio.sleep(0)  # дать задаче polling стартовать
    assert runner.running

    await runner.stop()
    assert dispatcher.stop_requested
    assert not dispatcher.polling_cancelled  # остановились штатно, без cancel
    assert not runner.running
    bot.session.close.assert_awaited_once()


async def test_hanging_polling_is_cancelled_by_timeout(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("app.bot.runner._GRACEFUL_STOP_TIMEOUT", 0.05)
    dispatcher = FakeDispatcher(hang_on_stop=True)
    bot = _fake_bot()
    runner = BotRunner(bot, dispatcher)  # type: ignore[arg-type]

    await runner.start()
    await asyncio.sleep(0)
    await runner.stop()

    assert dispatcher.stop_requested
    assert dispatcher.polling_cancelled  # по таймауту задача отменена принудительно
    assert not runner.running
    bot.session.close.assert_awaited_once()


async def test_stop_is_idempotent() -> None:
    dispatcher = FakeDispatcher()
    runner = BotRunner(_fake_bot(), dispatcher)  # type: ignore[arg-type]
    await runner.stop()  # не запускался — no-op без исключений
    assert not runner.running
