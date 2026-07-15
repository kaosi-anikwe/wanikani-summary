# Stage 1: Build stage with uv
FROM ghcr.io/astral-sh/uv:python3.11-alpine AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Copy lockfiles first for better layer caching (no BuildKit required)
COPY pyproject.toml uv.lock ./

# Install dependencies (no cache mount - works in all Docker environments)
RUN uv sync --frozen --no-install-project --no-dev

# Copy source code and install the project itself
COPY main.py .
RUN uv sync --frozen --no-dev

# Stage 2: Clean, minimal runtime stage
FROM python:3.11-alpine

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source code
COPY main.py .

# Prepend virtualenv bin to PATH so uvicorn is found directly
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Health check - gives the server 20s to start before counting failures
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://localhost:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
