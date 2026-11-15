FROM oven/bun:1.3.14 AS web
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web ./
RUN bun run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_SYNC=1
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY alembic ./alembic
COPY api ./api
COPY --from=web /web/dist ./web/dist
# One worker on purpose: the Level 5 rate limiter lives in process memory.
CMD uv run alembic upgrade head && exec uv run uvicorn api.main:app --host 0.0.0.0 --port $PORT --workers 1
