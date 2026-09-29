"""Keycloak: client credentials для бота и валидация admin-Bearer для журнала отправок.

Бот ходит в backend под сервисным аккаунтом клиента `crm-notification`
(роль `svc_notification`, OVERVIEW.md §2.2); токен кэшируется до истечения
и принудительно обновляется при 401 от backend.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

_EXPIRY_SKEW_SECONDS = 30


class KeycloakError(Exception):
    """Keycloak недоступен или отверг client credentials."""


class KeycloakTokenManager:
    def __init__(
        self,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def has_token(self) -> bool:
        return self._token is not None and time.monotonic() < self._expires_at

    async def get_token(self) -> str:
        if self.has_token:
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self.has_token:
                return self._token  # type: ignore[return-value]
            try:
                response = await self._http.post(
                    self._token_endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
            except httpx.HTTPError as exc:
                raise KeycloakError(f"Keycloak недоступен: {exc}") from exc
            if response.status_code != 200:
                raise KeycloakError(
                    f"Keycloak отверг client credentials (HTTP {response.status_code})"
                )
            payload = response.json()
            self._token = payload["access_token"]
            self._expires_at = (
                time.monotonic() + int(payload.get("expires_in", 60)) - _EXPIRY_SKEW_SECONDS
            )
            logger.info("Получен сервисный токен Keycloak", extra={"client": self._client_id})
            return self._token  # type: ignore[return-value]

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0


class JWTVerificationError(Exception):
    pass


class KeycloakJWTVerifier:
    """Проверка пользовательских Bearer-токенов (RS256 по JWKS realm'а).

    Используется единственным «человеческим» эндпоинтом сервиса — GET /api/v1/outbox
    (роль admin, api-contract.md §9.4).
    """

    def __init__(
        self,
        jwks_endpoint: str,
        allowed_issuers: set[str],
        http: httpx.AsyncClient,
        cache_ttl_seconds: int = 600,
    ) -> None:
        self._jwks_endpoint = jwks_endpoint
        self._allowed_issuers = allowed_issuers
        self._http = http
        self._cache_ttl = cache_ttl_seconds
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def _load_keys(self, force: bool = False) -> dict[str, Any]:
        if not force and self._keys and time.monotonic() - self._fetched_at < self._cache_ttl:
            return self._keys
        async with self._lock:
            if not force and self._keys and (
                time.monotonic() - self._fetched_at < self._cache_ttl
            ):
                return self._keys
            try:
                response = await self._http.get(self._jwks_endpoint)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise JWTVerificationError(f"JWKS недоступен: {exc}") from exc
            self._keys = {
                key["kid"]: jwt.PyJWK.from_dict(key).key
                for key in response.json().get("keys", [])
                if key.get("use", "sig") == "sig" and key.get("kid")
            }
            self._fetched_at = time.monotonic()
            return self._keys

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise JWTVerificationError("Некорректный формат токена") from exc
        keys = await self._load_keys()
        if kid not in keys:
            keys = await self._load_keys(force=True)  # ротация ключей realm'а
        key = keys.get(kid)
        if key is None:
            raise JWTVerificationError("Неизвестный ключ подписи токена")
        try:
            claims = jwt.decode(
                token, key=key, algorithms=["RS256"], options={"verify_aud": False}
            )
        except jwt.PyJWTError as exc:
            raise JWTVerificationError(f"Невалидный токен: {exc}") from exc
        if claims.get("iss") not in self._allowed_issuers:
            raise JWTVerificationError("Токен выпущен неизвестным издателем")
        return claims

    @staticmethod
    def realm_roles(claims: dict[str, Any]) -> list[str]:
        return list(claims.get("realm_access", {}).get("roles", []))
