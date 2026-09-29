"""Конфигурация cms-stub: все параметры приходят из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass

#: Расписание повторов доставки вебхука: 1 мин → 5 мин → 30 мин → 2 ч → 6 ч (api-contract §13.7).
DEFAULT_RETRY_SCHEDULE: tuple[int, ...] = (60, 300, 1800, 7200, 21600)


def _parse_retry_schedule(raw: str | None) -> tuple[int, ...]:
    if raw is None or not raw.strip():
        return DEFAULT_RETRY_SCHEDULE
    try:
        schedule = tuple(int(chunk) for chunk in raw.replace(" ", "").split(",") if chunk)
    except ValueError as exc:
        raise ValueError(
            "WEBHOOK_RETRY_SCHEDULE: ожидается список секунд через запятую, например «60,300,1800»"
        ) from exc
    if not schedule or any(delay < 0 for delay in schedule):
        raise ValueError("WEBHOOK_RETRY_SCHEDULE: значения должны быть неотрицательными секундами")
    return schedule


@dataclass(frozen=True)
class Settings:
    """Параметры сервиса; создаются один раз при старте приложения."""

    webhook_secret: str = "dev-cms-secret"
    crm_webhook_url: str = "http://backend:8000/api/v1/integrations/cms/webhook"
    db_path: str = "/data/cms-stub.sqlite3"
    retry_schedule: tuple[int, ...] = DEFAULT_RETRY_SCHEDULE
    http_timeout_seconds: float = 10.0
    relay_poll_seconds: float = 5.0
    relay_auto_start: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            webhook_secret=os.environ.get("CMS_WEBHOOK_SECRET", cls.webhook_secret),
            crm_webhook_url=os.environ.get("CRM_WEBHOOK_URL", cls.crm_webhook_url),
            db_path=os.environ.get("DB_PATH", cls.db_path),
            retry_schedule=_parse_retry_schedule(os.environ.get("WEBHOOK_RETRY_SCHEDULE")),
            http_timeout_seconds=float(
                os.environ.get("HTTP_TIMEOUT_SECONDS", cls.http_timeout_seconds)
            ),
            relay_poll_seconds=float(os.environ.get("RELAY_POLL_SECONDS", cls.relay_poll_seconds)),
        )
