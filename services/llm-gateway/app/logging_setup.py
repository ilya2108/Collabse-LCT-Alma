"""JSON-логи в stdout. Гигиена: шлюз НИКОГДА не пишет содержимое сообщений и ответов модели.

Одна строка `event=llm.chat` на вызов: длины, хеш входа, токены, тайминги, код результата.
Поля, похожие на секреты (token/secret/authorization/password/credential), вычищает
форматтер — последняя линия обороны, как в notification. Счётчики токенов модели
(`prompt_tokens`, `completion_tokens`, …) — не секреты: они в явном allowlist.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

# Штатные атрибуты LogRecord — всё остальное считаем структурированными полями (extra=...).
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
}

_SECRET_MARKERS = ("token", "secret", "authorization", "password", "credential")

# Счётчики usage модели содержат «token» в имени, но секретом не являются.
_SAFE_KEYS = {
    "prompt_tokens", "completion_tokens", "total_tokens", "tokens_today", "daily_token_cap",
    "token_cached", "spent_tokens", "max_tokens",
}


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_KEYS:
        return False
    return any(marker in lowered for marker in _SECRET_MARKERS)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
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


class _JsonStdoutHandler(logging.StreamHandler):
    """Наш обработчик root-логгера: повторный setup_logging заменяет только его."""


def setup_logging(level: str = "INFO") -> None:
    handler = _JsonStdoutHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    # чужие обработчики (pytest caplog и т. п.) не трогаем — заменяем только свой
    root.handlers = [h for h in root.handlers if not isinstance(h, _JsonStdoutHandler)]
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx на INFO пишет URL каждого запроса — шум; заголовков он не логирует, но
    # держим его на WARNING, чтобы в логе не было ничего, кроме событий шлюза.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
