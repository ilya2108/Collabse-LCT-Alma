"""JSON-логирование в stdout: структурированные записи для журнала отправок и отладки.

Гигиена логов: тексты уведомлений в логи не пишутся (только id/канал/длина),
адреса получателей маскируются (`mask_recipient`), а поля, похожие на секреты
(token/secret/authorization/password), вычищаются форматтером как последняя линия
обороны. ПДн студентов в этот сервис не попадают by design (Р-7), маскирование
покрывает рабочие контакты сотрудников.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Штатные атрибуты LogRecord — всё остальное считаем структурированными полями (extra=...).
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
}

_SECRET_MARKERS = ("token", "secret", "authorization", "password", "credential")


def mask_recipient(recipient: str) -> str:
    """Замаскировать адрес получателя для лога: и***@corp.local, @iv***, 199***."""
    if not recipient:
        return ""
    if "@" in recipient[1:]:  # email
        local, _, domain = recipient.rpartition("@")
        return f"{local[:1]}***@{domain}"
    if recipient.startswith("@"):  # telegram @username
        return f"{recipient[:3]}***"
    return f"{recipient[:3]}***"  # chat_id и прочие идентификаторы


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = "[redacted]" if _is_secret_key(key) else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Служебный шум aiogram/httpx не нужен на INFO.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
