"""Аутентификация и авторизация.

- Люди: JWT Keycloak (OIDC + PKCE на фронте), валидация подписи по JWKS realm'а
  (кэш 10 минут, форс-обновление при неизвестном kid), roles — из `realm_access.roles`.
- Сервисы: те же JWT client credentials с ролями `svc_notification` / `svc_integration`.
- CronJob и межсервисные вызовы: статический `X-Internal-Token` (constant-time сравнение).
- Роли людей — источник истины БД Альмы (`app_user.roles`, Р-F2-1): из токена берётся
  только стартовое назначение при создании записи; права эндпоинтов —
  `require_permission(<код права>)` по каталогу `core/rbac_catalog.py`.

api-contract.md §1.2, §11.1; deployment.md §3.4 (двойное имя Keycloak: JWKS качаем по
внутреннему URL, `iss` проверяем по внешнему).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from functools import cache
from hmac import compare_digest
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ensure_role_catalog
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import forbidden, unauthorized
from app.core.rbac_catalog import PERMISSION_CODES, SYSTEM_ROLE_CODES, permission_name
from app.models.user import AppUser

# системные роли CRM (OVERVIEW.md §2.2) — только они берутся из токена как стартовое
# назначение при первом входе; дальше роли назначаются в Альме (свои роли — тоже там)
PEOPLE_ROLES: tuple[str, ...] = SYSTEM_ROLE_CODES
SERVICE_ROLES = ("svc_notification", "svc_integration")
KNOWN_ROLES = frozenset(PEOPLE_ROLES + SERVICE_ROLES)

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TokenClaims:
    """Проверенные claims access-токена Keycloak."""

    sub: str
    username: str
    email: str | None
    full_name: str | None
    roles: tuple[str, ...]
    azp: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    def has_any_role(self, roles: tuple[str, ...] | frozenset[str]) -> bool:
        return bool(set(self.roles) & set(roles))


class JwksCache:
    """JWKS realm'а с TTL-кэшем в памяти процесса и форс-обновлением на неизвестный kid."""

    def __init__(self, url: str, ttl_seconds: int) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float = 0.0

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            payload = response.json()
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in payload.get("keys", []):
            if jwk.get("use") not in (None, "sig"):
                continue
            try:
                key = jwt.PyJWK(jwk)
            except jwt.PyJWKError:
                continue
            if key.key_id:
                keys[key.key_id] = key
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> jwt.PyJWK:
        expired = time.monotonic() - self._fetched_at > self._ttl
        if expired or kid not in self._keys:
            try:
                await self._refresh()
            except httpx.HTTPError as exc:  # Keycloak недоступен
                if kid not in self._keys:
                    raise unauthorized("Сервис авторизации недоступен, повторите позже") from exc
        key = self._keys.get(kid)
        if key is None:
            raise unauthorized("Токен подписан неизвестным ключом")
        return key


_jwks_cache: JwksCache | None = None


def get_jwks_cache() -> JwksCache:
    global _jwks_cache
    if _jwks_cache is None:
        settings = get_settings()
        _jwks_cache = JwksCache(settings.jwks_url, settings.jwks_cache_ttl_seconds)
    return _jwks_cache


def reset_jwks_cache() -> None:
    global _jwks_cache
    _jwks_cache = None


def parse_token_payload(payload: dict) -> TokenClaims:
    """Достаёт из payload токена канонические claims (роли фильтруются по KNOWN_ROLES)."""
    realm_roles = payload.get("realm_access", {}).get("roles", []) or []
    roles = tuple(sorted(set(realm_roles) & KNOWN_ROLES))
    return TokenClaims(
        sub=payload["sub"],
        username=payload.get("preferred_username") or payload["sub"],
        email=payload.get("email"),
        full_name=payload.get("name"),
        roles=roles,
        azp=payload.get("azp"),
        raw=payload,
    )


def validate_audience(payload: dict, allowed: list[str]) -> bool:
    """`aud` (или `azp`) должен пересекаться со списком допустимых client id."""
    if not allowed:
        return True
    aud = payload.get("aud")
    audiences = {aud} if isinstance(aud, str) else set(aud or [])
    if payload.get("azp"):
        audiences.add(payload["azp"])
    return bool(audiences & set(allowed))


async def decode_token(token: str) -> TokenClaims:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise unauthorized("Некорректный токен авторизации") from exc
    kid = header.get("kid")
    if not kid:
        raise unauthorized("Некорректный токен авторизации")
    key = await get_jwks_cache().get_key(kid)
    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer,
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("Срок действия токена истёк") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("Некорректный токен авторизации") from exc
    if not validate_audience(payload, settings.keycloak_audience_list):
        raise unauthorized("Токен выпущен для другого приложения")
    return parse_token_payload(payload)


async def get_token_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenClaims:
    if credentials is None or not credentials.credentials:
        raise unauthorized()
    return await decode_token(credentials.credentials)


async def sync_app_user(session: AsyncSession, claims: TokenClaims) -> AppUser:
    """Upsert зеркала `app_user` по `keycloak_sub` (data-model.md §3).

    Если строка была создана сидом до первого логина (sub ещё неизвестен),
    она «усыновляется» по username — дубликаты исключены.

    Роли — источник истины БД Альмы (Р-F2-1): при создании записи роли токена из
    PEOPLE_ROLES становятся стартовым назначением, у существующих пользователей роли
    из токена больше не перезаписываются (их назначает администратор в Альме).
    """
    sub = uuid.UUID(claims.sub)
    user = (
        await session.execute(select(AppUser).where(AppUser.keycloak_sub == sub))
    ).scalar_one_or_none()
    if user is None:
        user = (
            await session.execute(
                select(AppUser).where(func.lower(AppUser.username) == claims.username.lower())
            )
        ).scalar_one_or_none()
        if user is not None:
            user.keycloak_sub = sub
    if user is None:
        user = AppUser(
            keycloak_sub=sub,
            username=claims.username,
            email=claims.email,
            full_name=claims.full_name,
            roles=[role for role in claims.roles if role in PEOPLE_ROLES],
        )
        session.add(user)
    else:
        if user.username != claims.username:
            user.username = claims.username
        if claims.email and user.email != claims.email:
            user.email = claims.email
        if claims.full_name and user.full_name != claims.full_name:
            user.full_name = claims.full_name
    await session.flush()
    await session.commit()
    return user


async def get_current_user(
    claims: Annotated[TokenClaims, Depends(get_token_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppUser:
    """Текущий пользователь-человек (зеркалится в app_user).

    Сервисная учётная запись (только `svc_*` в токене) интерфейсом CRM не пользуется —
    запись `app_user` для неё не создаётся. Пользователь без ролей CRM зеркалится
    (администратор видит его в списке и назначает роль), но получает 403.
    """
    if claims.has_any_role(SERVICE_ROLES) and not claims.has_any_role(PEOPLE_ROLES):
        raise forbidden("Сервисная учётная запись не работает с интерфейсом CRM")
    user = await sync_app_user(session, claims)
    if not user.is_active:
        raise forbidden("Учётная запись деактивирована")
    await ensure_role_catalog(session)
    if session.in_transaction():
        # загрузка каталога (раз в TTL) открыла читающую транзакцию: закрываем её, как и
        # sync_app_user, — долгие ответы (SSE /events/stream) не держат «idle in transaction»
        await session.commit()
    if not user.access.role_codes:
        raise forbidden("Учётной записи не назначены роли CRM — обратитесь к администратору")
    return user


@cache
def require_permission(code: str):
    """Зависимость: пользователь с правом `code` (для права с областью — хотя бы в одном
    процессе; процесс конкретного объекта проверяет обработчик), иначе 403.

    Опечатка в коде права падает при импорте роутера. У зависимости есть атрибут
    `required_permission` — по нему тест матрицы доступа обходит все маршруты.
    """
    if code not in PERMISSION_CODES:
        raise ValueError(f"Неизвестное право «{code}»: см. app/core/rbac_catalog.py")
    title = permission_name(code)

    async def dependency(
        user: Annotated[AppUser, Depends(get_current_user)],
    ) -> AppUser:
        if not user.access.has(code):
            raise forbidden(f"Недостаточно прав: «{title}»")
        return user

    dependency.required_permission = code  # type: ignore[attr-defined]
    dependency.__name__ = f"require_permission_{code.replace(':', '_')}"
    return dependency


def check_roles(user_roles: list[str] | tuple[str, ...], allowed: tuple[str, ...]) -> bool:
    """Совместимость (удаляется в волне 3): чистая проверка пересечения ролей."""
    return bool(set(user_roles) & set(allowed))


def require_roles(*allowed: str):
    """Совместимость (удаляется в волне 3): доступ только ролям из списка; иначе 403.

    Новый код — `require_permission`. Атрибут `required_roles` нужен тесту матрицы
    доступа для маршрутов, ещё не переведённых на права.
    """

    async def dependency(
        user: Annotated[AppUser, Depends(get_current_user)],
    ) -> AppUser:
        if not check_roles(user.roles, allowed):
            raise forbidden()
        return user

    dependency.required_roles = tuple(allowed)  # type: ignore[attr-defined]
    return dependency


def require_service_role(role: str):
    """Зависимость для сервисных клиентов (бот/интеграции): токен без зеркала app_user."""

    async def dependency(
        claims: Annotated[TokenClaims, Depends(get_token_claims)],
    ) -> TokenClaims:
        if role not in claims.roles:
            raise forbidden()
        return claims

    return dependency


def verify_internal_token(provided: str | None, expected: str) -> bool:
    """Constant-time сравнение X-Internal-Token (api-contract.md §11.1)."""
    if not provided or not expected:
        return False
    return compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    settings = get_settings()
    if not verify_internal_token(x_internal_token, settings.internal_api_token):
        raise unauthorized("Неверный внутренний токен")
