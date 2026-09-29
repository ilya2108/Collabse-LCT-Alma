"""HTTP-клиент к backend /api/v1/internal/bot/* (api-contract.md §9.2–9.3).

Бот не имеет собственного доступа к данным: каждая команда — запрос в backend,
который резолвит chat_id -> app_user и применяет RBAC этого пользователя.
Аутентификация — Bearer сервисного аккаунта crm-notification (client credentials).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.keycloak import KeycloakError, KeycloakTokenManager

logger = logging.getLogger(__name__)


class BackendUnavailableError(Exception):
    """Backend или Keycloak недоступны — бот отвечает «попробуйте позже»."""


class BackendApiError(Exception):
    """Ошибка уровня API backend'а (конверт §1.4)."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class NotLinkedError(BackendApiError):
    """chat_id не привязан ни к одному пользователю CRM."""


def _parse_error(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json().get("error", {})
        return error.get("code", "unknown"), error.get("message", "Неизвестная ошибка")
    except ValueError:
        return "unknown", f"HTTP {response.status_code}"


class BackendClient:
    def __init__(
        self,
        base_url: str,
        tokens: KeycloakTokenManager,
        http: httpx.AsyncClient,
    ) -> None:
        self._api = f"{base_url.rstrip('/')}/api/v1"
        self._tokens = tokens
        self._http = http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotent_write: bool = False,
    ) -> httpx.Response:
        try:
            token = await self._tokens.get_token()
        except KeycloakError as exc:
            raise BackendUnavailableError(str(exc)) from exc
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Request-Id": str(uuid.uuid4()),
        }
        if idempotent_write:
            # Обязателен для мутирующих POST от бота (api-contract.md §1.6).
            headers["Idempotency-Key"] = str(uuid.uuid4())
        try:
            response = await self._http.request(
                method, f"{self._api}{path}", json=json, params=params, headers=headers
            )
            if response.status_code == 401:
                # Токен мог протухнуть/быть отозван — один принудительный refresh.
                self._tokens.invalidate()
                headers["Authorization"] = f"Bearer {await self._tokens.get_token()}"
                response = await self._http.request(
                    method, f"{self._api}{path}", json=json, params=params, headers=headers
                )
        except KeycloakError as exc:
            raise BackendUnavailableError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailableError(f"Backend недоступен: {exc}") from exc
        return response

    @staticmethod
    def _raise_for_error(response: httpx.Response, *, not_found_is_unlinked: bool) -> None:
        if response.status_code < 400:
            return
        code, message = _parse_error(response)
        if response.status_code == 404 and not_found_is_unlinked:
            raise NotLinkedError(404, code, message)
        raise BackendApiError(response.status_code, code, message)

    # --- Привязка -------------------------------------------------------------------

    async def bind(self, code: str, chat_id: int, tg_username: str | None) -> dict[str, Any]:
        """POST /internal/bot/bind: обмен one-time кода на привязку.

        404 — код неверен/просрочен; 409 — chat_id уже привязан к другому пользователю.
        """
        response = await self._request(
            "POST",
            "/internal/bot/bind",
            json={"code": code, "chat_id": chat_id, "tg_username": tg_username},
            idempotent_write=True,
        )
        self._raise_for_error(response, not_found_is_unlinked=False)
        return response.json()

    async def get_binding(self, chat_id: int) -> dict[str, Any]:
        response = await self._request("GET", f"/internal/bot/bindings/{chat_id}")
        self._raise_for_error(response, not_found_is_unlinked=True)
        return response.json()

    async def unbind(self, chat_id: int) -> None:
        response = await self._request("DELETE", f"/internal/bot/bindings/{chat_id}")
        self._raise_for_error(response, not_found_is_unlinked=True)

    # --- Данные команд (RBAC привязанного пользователя) ------------------------------

    async def summary(self, chat_id: int) -> dict[str, Any]:
        response = await self._request("GET", f"/internal/bot/users/{chat_id}/summary")
        self._raise_for_error(response, not_found_is_unlinked=True)
        return response.json()

    async def requests(
        self, chat_id: int, *, stuck: bool = False, limit: int = 10
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if stuck:
            params["stuck"] = "true"
        response = await self._request(
            "GET", f"/internal/bot/users/{chat_id}/requests", params=params
        )
        self._raise_for_error(response, not_found_is_unlinked=True)
        payload = response.json()
        return payload.get("items", payload) if isinstance(payload, dict) else payload

    async def notifications(
        self, chat_id: int, *, unread: bool = True, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Лента уведомлений §9.1 для команды /inbox (api-contract.md §9.3)."""
        params: dict[str, Any] = {"limit": limit}
        if unread:
            params["unread"] = "true"
        response = await self._request(
            "GET", f"/internal/bot/users/{chat_id}/notifications", params=params
        )
        self._raise_for_error(response, not_found_is_unlinked=True)
        payload = response.json()
        return payload.get("items", payload) if isinstance(payload, dict) else payload
