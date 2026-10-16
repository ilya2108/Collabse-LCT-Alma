"""Счётчики вызовов с момента старта процесса (GET /v1/usage): исходы и задержки."""
from __future__ import annotations

from collections import deque

# Коды, которые шлюз может вернуть на /v1/chat после проверки токена и тела.
ERROR_CODES = (
    "validation_error",
    "queue_full",
    "queue_timeout",
    "llm_not_configured",
    "llm_budget_exceeded",
    "llm_rate_limited",
    "llm_auth_failed",
    "llm_upstream_error",
    "llm_timeout",
    "internal_error",
)


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


class CallStats:
    def __init__(self, window: int = 200) -> None:
        self.ok = 0
        self.errors: dict[str, int] = dict.fromkeys(ERROR_CODES, 0)
        # задержки успешных вызовов (без очереди) — скользящее окно для p50/p95
        self._latencies: deque[int] = deque(maxlen=window)
        self.last_latency_ms: int | None = None

    def record_ok(self, latency_ms: int) -> None:
        self.ok += 1
        self._latencies.append(latency_ms)
        self.last_latency_ms = latency_ms

    def record_error(self, code: str) -> None:
        self.errors[code] = self.errors.get(code, 0) + 1

    def latency(self) -> dict[str, int | None]:
        values = list(self._latencies)
        if not values:
            return {"p50": None, "p95": None, "last": self.last_latency_ms}
        return {
            "p50": _percentile(values, 0.5),
            "p95": _percentile(values, 0.95),
            "last": self.last_latency_ms,
        }
