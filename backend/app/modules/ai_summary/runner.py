"""Фоновые задачи сводок: `spawn(job_id)` запускает `service.run_job` в цикле событий
процесса и держит ссылку до завершения (иначе сборщик мусора может снять задачу).

`shutdown()` в lifespan отменяет незавершённые задачи и сразу закрывает их строки
`failed: interrupted` (`service.mark_interrupted`) — иначе при rollout или scale-down HPA
они висели бы «Формируется…» до `AI_SUMMARY_JOB_STALE_SECONDS` и присоединяли к себе новые
запросы с тем же ключом. Не успели (БД недоступна, kill -9) — строки закроет stale-логика.
Задача переживает только свой процесс — очередь ждущих живёт в шлюзе, а не в backend.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

logger = logging.getLogger("app.ai_summary.runner")

# задача → id задачи сводки (для закрытия строк при остановке процесса)
_tasks: dict[asyncio.Task[None], uuid.UUID] = {}
SHUTDOWN_MARK_TIMEOUT_SECONDS = 5.0


async def _run(job_id: uuid.UUID) -> None:
    from app.modules.ai_summary.service import run_job  # цикл импорта service ↔ runner

    try:
        await run_job(job_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — задача не роняет цикл событий, причина — в логе
        logger.exception("ai_summary.runner_failed job_id=%s", job_id)


def spawn(job_id: uuid.UUID) -> None:
    task = asyncio.create_task(_run(job_id), name=f"ai-summary-{job_id}")
    _tasks[task] = job_id
    task.add_done_callback(lambda done: _tasks.pop(done, None))


def pending() -> int:
    return len(_tasks)


async def shutdown() -> None:
    tasks = dict(_tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    _tasks.clear()
    if not tasks:
        return
    from app.modules.ai_summary.service import mark_interrupted

    try:
        async with asyncio.timeout(SHUTDOWN_MARK_TIMEOUT_SECONDS):
            marked = await mark_interrupted(list(tasks.values()))
        logger.info("ai_summary.shutdown_interrupted jobs=%s marked=%s", len(tasks), marked)
    except Exception:  # noqa: BLE001 — остановка пода не должна падать: строки закроет stale
        logger.warning("ai_summary.shutdown_mark_failed jobs=%s", len(tasks), exc_info=True)
