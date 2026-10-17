# llm-gateway — шлюз к GigaChat (см. services/llm-gateway/README.md, deployment.md §2.5).
# Единственный держатель ключа GigaChat и второй под с egress (DMZ) после notification.
# Один процесс uvicorn без --workers: семафор на 1 поток и дневной лимит — в памяти.
# Бандл НУЦ в образ не кладётся: монтируется из ConfigMap llm-gateway-ca (ротация без пересборки).

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
# байткод приложения — заранее: в рантайме корневая ФС только для чтения
RUN python -m compileall -q app

# --- runtime ---
FROM python:3.12-slim-bookworm
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8040
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8040"]
