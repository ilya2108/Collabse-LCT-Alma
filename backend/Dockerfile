# backend — модульный монолит FastAPI (см. docs/design/deployment.md §2.1)
# Один артефакт для API, Jobs (alembic/seed) и utility-команд — версии кода и миграций не расходятся.

# --- builder ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime ---
FROM python:3.12-slim-bookworm
# curl нужен намеренно: этот же образ используется Jobs и удобен для отладки probes
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app \
    && apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# 2 воркера на под: масштабирование — подами (HPA), метрика CPU на под предсказуема
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
