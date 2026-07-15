# Stage 1: Build stage with uv
FROM ghcr.io/astral-sh/uv:python3.11-alpine AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies using cache mount for super fast rebuilds
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy source code and perform final sync
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Clean, small runtime stage
FROM python:3.11-alpine

WORKDIR /app

# 1. Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv

# 2. COPY THE SOURCE FILES (The missing link!)
COPY main.py .

# Prepend virtualenv path to avoid needing to activate it
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Explicit health check so Coolify/Docker know when the container is ready.
# --start-period gives uvicorn time to bind before checks begin.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost:8000/ || exit 1

# Execute uvicorn server directly from the virtual env
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
