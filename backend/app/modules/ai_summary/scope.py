"""Область сводок ИИ и зависимость «право + фичефлаг».

Область — область отчётов (`report_scope`: `reports:read`, «по всем КАМам» с
`reports:all`), сужённая до процессов, где у ролей пользователя есть `reports:ai_summary`.
Дальше все проверки делают сами отчёты: процесс вне области и чужой КАМ при «только
свои» — 403 (`kam_activity._resolve_scope`), нет КАМа — 404 (`sla.build_sla_kam`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import DealScope
from app.core.db import get_session
from app.core.errors import unprocessable
from app.core.security import require_permission
from app.models.user import AppUser
from app.modules.admin.service import get_flags
from app.modules.reporting.service import report_scope

PERMISSION = "reports:ai_summary"
FLAG = "ai_summaries"
FLAG_DISABLED = "Сводки ИИ выключены администратором (фичефлаг ai_summaries)"


def ai_scope(viewer: AppUser) -> DealScope:
    """Область отчётов, ограниченная процессами с правом `reports:ai_summary`."""
    base = report_scope(viewer)
    allowed = set(viewer.access.workflow_types_with(PERMISSION))
    return DealScope(rules=tuple((wf, own) for wf, own in base.rules if wf in allowed))


async def require_ai_enabled(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission(PERMISSION))],
) -> AppUser:
    """Право `reports:ai_summary` (иначе 403), затем фичефлаг `ai_summaries` (иначе 422
    `unprocessable`, как у PDF дашборда за флагом `report_pdf`)."""
    flags = await get_flags(session)
    if not flags.get(FLAG):
        raise unprocessable(FLAG_DISABLED)
    return user
