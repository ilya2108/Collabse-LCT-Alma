"""Аутентификация внутреннего API: только backend с общим секретом `X-Internal-Token`.

Сетевой периметр (NetworkPolicy 41-llm-gateway-dmz) и так пускает сюда только backend;
токен — второй рубеж на случай ошибки в политике или port-forward. Общеизвестный dev-токен
при настоящем ключе GigaChat не принимается вовсе (`Settings.dev_token_with_real_key`).
"""
from __future__ import annotations

import hmac

from fastapi import Request

from app.errors import ApiError

INTERNAL_TOKEN_HEADER = "X-Internal-Token"


DEV_TOKEN_REFUSED = (
    "Шлюз с настоящим ключом GigaChat не принимает dev-токен: задайте случайный "
    "LLM_GATEWAY_TOKEN (make dev-env или make llm-secret)"
)


async def require_internal_token(request: Request) -> None:
    settings = request.app.state.settings
    if settings.dev_token_with_real_key:
        # общеизвестный токен из репозитория при настоящем ключе — отказ при любом APP_ENV
        raise ApiError(401, "unauthorized", DEV_TOKEN_REFUSED)
    provided = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    expected = settings.llm_gateway_token.get_secret_value()
    if not (
        provided and expected and hmac.compare_digest(provided.encode(), expected.encode())
    ):
        raise ApiError(
            401, "unauthorized", "Неверный или отсутствующий заголовок X-Internal-Token"
        )
