"""Аварийное управление ролями (Р-F2-9): вход, если в Альме не осталось администратора.

    python -m app.tools.rbac grant-admin <username>   # назначить роль admin
    python -m app.tools.rbac list-admins              # кто может управлять ролями

В контуре — через `kubectl -n crm exec deploy/backend -- python -m app.tools.rbac …`.
Перед назначением каталог прав и системные роли сверяются с кодом (на случай ручной
порчи таблиц). Назначение пишется в аудит `user.roles_changed` с `after.source = "cli"`
и публикует SSE `access.changed` — реплики сбрасывают кэш каталога ролей.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.core.access import ensure_role_catalog
from app.core.cache import close_cache
from app.core.db import dispose_engine, get_session_factory
from app.models.user import AppUser
from app.modules.access.bootstrap import seed_rbac_catalog
from app.modules.access.service import ADMIN_ROLES_PERMISSION, set_user_roles


async def grant_admin(username: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        await seed_rbac_catalog(session)
        await session.commit()
        user = (
            await session.execute(
                select(AppUser).where(func.lower(AppUser.username) == username.lower())
            )
        ).scalar_one_or_none()
        if user is None:
            print(  # noqa: T201
                f"rbac: пользователь «{username}» не найден — он появится после первого "
                "входа в Альму или синхронизации с Keycloak",
                file=sys.stderr,
            )
            return 1
        if "admin" in (user.roles or []):
            print(f"rbac: у «{user.username}» уже есть роль admin")  # noqa: T201
            return 0
        await set_user_roles(
            session,
            actor=None,
            user_id=user.id,
            roles=sorted({*(user.roles or []), "admin"}),
            version=None,
            ip=None,
            source="cli",
        )
        state = "" if user.is_active else " (учётная запись деактивирована — включите её)"
        print(f"rbac: «{user.username}» назначена роль admin{state}")  # noqa: T201
    return 0


async def list_admins() -> int:
    factory = get_session_factory()
    async with factory() as session:
        catalog = await ensure_role_catalog(session, force=True)
        codes = catalog.role_codes_with(ADMIN_ROLES_PERMISSION)
        users = (
            (
                await session.execute(
                    select(AppUser)
                    .where(AppUser.roles.overlap(list(codes)))
                    .order_by(AppUser.username)
                )
            )
            .scalars()
            .all()
        )
        if not users:
            print("rbac: нет пользователей с правом управления ролями")  # noqa: T201
            return 1
        for user in users:
            state = "активен" if user.is_active else "деактивирован"
            roles = ", ".join(sorted(set(user.roles or []) & set(codes)))
            print(f"{user.username}\t{user.full_name or ''}\t{roles}\t{state}")  # noqa: T201
    return 0


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.rbac", description="Аварийное управление ролями Альмы"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant-admin", help="назначить пользователю роль admin")
    grant.add_argument("username", help="логин (username) пользователя Альмы")
    commands.add_parser("list-admins", help="пользователи с правом управления ролями")
    args = parser.parse_args(argv)
    try:
        if args.command == "grant-admin":
            return await grant_admin(args.username)
        return await list_admins()
    finally:
        await close_cache()
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
