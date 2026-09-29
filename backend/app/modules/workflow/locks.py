"""Advisory-локи PostgreSQL для гонки «переход vs применение схемы» (workflow-engine.md §6.5).

- `transition_deal` берёт SHARED-лок workflow: переходы параллельны между собой;
- `apply_workflow_change` берёт эксклюзивный: дожидается хвоста переходов и
  замораживает новые на миллисекунды применения.

Ключ — `hashtextextended('wf:' || workflow_id, 0)` (bigint), считается на стороне PG.
Локи транзакционные (`_xact_`): освобождаются на COMMIT/ROLLBACK автоматически.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def acquire_shared_workflow_lock(session: AsyncSession, workflow_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended('wf:' || :wid, 0))"),
        {"wid": str(workflow_id)},
    )


async def acquire_exclusive_workflow_lock(
    session: AsyncSession, workflow_id: uuid.UUID
) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('wf:' || :wid, 0))"),
        {"wid": str(workflow_id)},
    )
