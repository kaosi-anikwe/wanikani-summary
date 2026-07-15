# wanikani-summary

A lightweight FastAPI service that monitors your [WaniKani](https://www.wanikani.com) review queue and delivers push notifications via [ntfy.sh](https://ntfy.sh).

## Features

- **Hourly checks** — fires exactly at the top of every clock hour
- **Push notifications** — sends alerts to your phone via ntfy.sh when reviews are waiting
- **Quiet hours** — configurable window to suppress notifications at night
- **On-demand trigger** — `GET /check` to instantly check and notify
- **Timezone-aware** — all scheduling and quiet hours respect your configured timezone

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check — returns `{"status": "running"}` |
| `GET` | `/check` | Immediately checks review queue and sends a push notification |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WANIKANI_API_KEY` | ✅ | — | Your WaniKani API token (v2) |
| `NTFY_TOPIC` | ✅ | — | Your ntfy.sh topic name |
| `TIMEZONE` | ❌ | `UTC` | IANA timezone name, e.g. `Europe/London`, `America/New_York` |
| `QUIET_START` | ❌ | `24` | Hour (0–23) when quiet period begins. `24` disables quiet hours |
| `QUIET_END` | ❌ | `5` | Hour (0–23) when quiet period ends |

> **Quiet hours** use 24h format in the configured `TIMEZONE`. The window wraps midnight —  
> e.g. `QUIET_START=23`, `QUIET_END=7` means no notifications from 11 PM to 7 AM.

## Running Locally

```bash
# Install dependencies
uv sync

# Run with environment variables
WANIKANI_API_KEY=your_key NTFY_TOPIC=your_topic uv run uvicorn main:app --reload
```

## Docker

```bash
# Build
docker build -t wanikani-summary .

# Run
docker run -p 8000:8000 \
  -e WANIKANI_API_KEY=your_key \
  -e NTFY_TOPIC=your_topic \
  -e TIMEZONE=Europe/London \
  -e QUIET_START=23 \
  -e QUIET_END=7 \
  wanikani-summary
```

## Deploying to Coolify

1. Connect your GitHub repository
2. Set **Build Pack** to `Dockerfile`
3. Set **Port** to `8000`
4. Add the environment variables above under **Environment Variables**
5. Set the health check path to `/` with a **start period** of at least 20 seconds

## Tech Stack

- [FastAPI](https://fastapi.tiangolo.com) — web framework
- [httpx](https://www.python-httpx.org) — async HTTP client
- [uvicorn](https://www.uvicorn.org) — ASGI server
- [uv](https://docs.astral.sh/uv/) — package manager
- [tzdata](https://pypi.org/project/tzdata/) — timezone database
