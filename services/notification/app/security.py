"""Аутентификация HTTP API сервиса.

- POST /api/v1/send — только X-Internal-Token (общий секрет контура, Secret crm-internal);
- GET /api/v1/outbox — X-Internal-Token (сервисы) либо Bearer с realm-ролью admin
  (api-contract.md §9.4: журнал отправок для админа через тот же Keycloak).
"""
from __future__ import annotations

import hmac

from fastapi import Request

from app.errors import ApiError
from app.keycloak import JWTVerificationError, KeycloakJWTVerifier

INTERNAL_TOKEN_HEADER = "X-Internal-Token"


def _internal_token_valid(request: Request) -> bool:
    provided = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    expected = request.app.state.settings.internal_api_token
    return bool(provided) and hmac.compare_digest(provided, expected)


async def require_internal_token(request: Request) -> None:
    if not _internal_token_valid(request):
        raise ApiError(
            401, "unauthorized", "Неверный или отсутствующий заголовок X-Internal-Token"
        )


async def require_outbox_access(request: Request) -> None:
    if _internal_token_valid(request):
        return
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        verifier: KeycloakJWTVerifier = request.app.state.jwt_verifier
        try:
            claims = await verifier.verify(authorization.removeprefix("Bearer ").strip())
        except JWTVerificationError as exc:
            raise ApiError(401, "unauthorized", f"Невалидный токен: {exc}") from exc
        if "admin" not in verifier.realm_roles(claims):
            raise ApiError(
                403, "forbidden", "Журнал отправок доступен только роли admin"
            )
        return
    raise ApiError(
        401,
        "unauthorized",
        "Требуется X-Internal-Token или Bearer-токен администратора",
    )
