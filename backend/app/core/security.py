"""Аутентификация и авторизация.

- Люди: JWT Keycloak (OIDC + PKCE на фронте), валидация подписи по JWKS realm'а
  (кэш 10 минут, форс-обновление при неизвестном kid), roles — из `realm_access.roles`.
- Сервисы: те же JWT client credentials с ролями `svc_notification` / `svc_integration`.
- CronJob и межсервисные вызовы: статический `X-Internal-Token` (constant-time сравнение).

api-contract.md §1.2, §11.1; deployment.md §3.4 (двойное имя Keycloak: JWKS качаем по
внутреннему URL, `iss` проверяем по внешнему).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import forbidden, unauthorized
from app.models.user import AppUser

# канонический список ролей проекта (OVERVIEW.md §2.2); прочие роли realm игнорируются
PEOPLE_ROLES = ("admin", "head_kam", "kam", "observer")
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
            roles=list(claims.roles),
        )
        session.add(user)
    else:
        if user.username != claims.username:
            user.username = claims.username
        if claims.email and user.email != claims.email:
            user.email = claims.email
        if claims.full_name and user.full_name != claims.full_name:
            user.full_name = claims.full_name
        if sorted(user.roles) != sorted(claims.roles):
            user.roles = list(claims.roles)
    await session.flush()
    await session.commit()
    return user


async def get_current_user(
    claims: Annotated[TokenClaims, Depends(get_token_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppUser:
    """Текущий пользователь-человек (зеркалится в app_user)."""
    if not claims.has_any_role(PEOPLE_ROLES):
        raise forbidden("Учётная запись не имеет ролей CRM")
    user = await sync_app_user(session, claims)
    if not user.is_active:
        raise forbidden("Учётная запись деактивирована")
    return user


def check_roles(user_roles: list[str] | tuple[str, ...], allowed: tuple[str, ...]) -> bool:
    """Чистая проверка пересечения ролей (юнит-тестируется отдельно от FastAPI)."""
    return bool(set(user_roles) & set(allowed))


def require_roles(*allowed: str):
    """Зависимость: доступ только ролям из списка; иначе 403."""

    async def dependency(
        user: Annotated[AppUser, Depends(get_current_user)],
    ) -> AppUser:
        if not check_roles(user.roles, allowed):
            raise forbidden()
        return user

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
