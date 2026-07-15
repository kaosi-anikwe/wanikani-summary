import os
import logging
import asyncio
import httpx
from typing import Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# 1. Define the Lifespan Async Context Manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP LOGIC ---
    # Start your background scheduler loop on container startup
    task = asyncio.create_task(schedule_checker())

    yield  # The FastAPI application lives and processes requests here

    # --- SHUTDOWN LOGIC ---
    # Cleanly cancel the task when the container stops
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Background scheduler task cleanly cancelled.")


# 2. Pass the lifespan to the FastAPI instance
app = FastAPI(lifespan=lifespan)

# Configuration from Environment Variables
WANIKANI_API_KEY = os.getenv("WANIKANI_API_KEY")
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
# Quiet hours: no notifications between QUIET_START and QUIET_END (24h, in TIMEZONE)
QUIET_START = int(os.getenv("QUIET_START", "24"))  # default: 12 AM
QUIET_END = int(os.getenv("QUIET_END", "5"))  # default: 5 AM

_tz_name = os.getenv("TIMEZONE", "UTC")
try:
    TZ = ZoneInfo(_tz_name)
except ZoneInfoNotFoundError:
    logger.warning("Unknown timezone '%s', falling back to UTC.", _tz_name)
    TZ = ZoneInfo("UTC")


def is_quiet_hour() -> bool:
    """Returns True if the current hour (in TZ) falls within the configured quiet window."""
    hour = datetime.now(TZ).hour
    if QUIET_START <= QUIET_END:
        # Simple range, e.g. QUIET_START=2, QUIET_END=6
        return QUIET_START <= hour < QUIET_END
    else:
        # Wraps midnight, e.g. QUIET_START=23, QUIET_END=7 → 11 PM to 7 AM
        return hour >= QUIET_START or hour < QUIET_END


async def fetch_wanikani_data():
    """Helper to request the summary payload from WaniKani API."""
    if not WANIKANI_API_KEY:
        raise ValueError("Missing WANIKANI_API_KEY")

    headers = {"Authorization": f"Bearer {WANIKANI_API_KEY}"}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.wanikani.com/v2/summary", headers=headers
        )
        response.raise_for_status()
        return response.json()


async def send_ntfy_push(message: str, priority: str = "5", tags: str = "books,brain"):
    """Helper to broadcast messages to your configured ntfy topic."""
    if not NTFY_TOPIC:
        logger.warning("Missing NTFY_TOPIC config. Skipping push notification.")
        return

    ntfy_headers = {"Title": "WaniKani Alert", "Priority": priority, "Tags": tags}
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers=ntfy_headers,
        )


async def check_wanikani_reviews():
    try:
        data = await fetch_wanikani_data()

        # Extract current reviews due
        now = datetime.now(timezone.utc)
        reviews_data = data["data"]["reviews"]

        current_reviews_count = 0
        for period in reviews_data:
            avail_time = datetime.fromisoformat(
                period["available_at"].replace("Z", "+00:00")
            )
            if avail_time <= now:
                current_reviews_count += len(period["subject_ids"])

        # If reviews are waiting, ping ntfy
        if current_reviews_count > 0:
            msg = f"You have {current_reviews_count} WaniKani reviews waiting! Clean your queue."
            await send_ntfy_push(msg, priority="5", tags="books,brain")
            logger.info(
                "Sent scheduled notification for %d reviews.", current_reviews_count
            )
        else:
            logger.info("No reviews due at this time.")

    except Exception as e:
        logger.error("Error checking reviews: %s", e)


async def schedule_checker():
    """Fires at the top of every clock hour (in TZ). Skips notification during quiet hours."""
    while True:
        # Sleep until the next top-of-hour in the configured timezone
        now = datetime.now(TZ)
        seconds_until_next_hour = (60 - now.minute) * 60 - now.second
        logger.info(
            "Next check in %dm %ds (at the top of the next hour in %s).",
            seconds_until_next_hour // 60,
            seconds_until_next_hour % 60,
            TZ.key,
        )
        await asyncio.sleep(seconds_until_next_hour)

        if is_quiet_hour():
            current_hour = datetime.now(TZ).hour
            logger.info(
                "Quiet hours active (%d:00\u2013%d:00 %s). Skipping %d:00 check.",
                QUIET_START,
                QUIET_END,
                TZ.key,
                current_hour,
            )
            continue

        await check_wanikani_reviews()


@app.get("/")
def health_check():
    # Coolify health check
    return {"status": "running"}


@app.get("/check")
async def trigger_check_now(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """
    On-demand endpoint to immediately check for reviews.
    Sends a report directly to your phone.
    """
    try:
        data = await fetch_wanikani_data()
        now = datetime.now(timezone.utc)
        reviews_data = data["data"]["reviews"]

        # Calculate current queue count
        current_reviews_count = 0
        for period in reviews_data:
            avail_time = datetime.fromisoformat(
                period["available_at"].replace("Z", "+00:00")
            )
            if avail_time <= now:
                current_reviews_count += len(period["subject_ids"])

        # Calculate next upcoming review time
        next_review_str = "None scheduled"
        upcoming_reviews = [
            p
            for p in reviews_data
            if datetime.fromisoformat(p["available_at"].replace("Z", "+00:00")) > now
        ]
        if upcoming_reviews:
            # Sort chronologically to find the closest one
            upcoming_reviews.sort(key=lambda x: x["available_at"])
            next_time = datetime.fromisoformat(
                upcoming_reviews[0]["available_at"].replace("Z", "+00:00")
            )
            # Format nicely for reading in the configured timezone
            local_next = next_time.astimezone(TZ)
            next_review_str = local_next.strftime("%I:%M %p")

        # Construct status report payload
        if current_reviews_count > 0:
            status_report = (
                f"Current status: {current_reviews_count} reviews due right now!"
            )
            priority = "5"  # High priority to wake up sound engine
        else:
            status_report = f"Current status: Queue clean! Next review is scheduled at {next_review_str}."
            priority = "3"  # Normal priority notification

        # Queue the push notification response into FastAPI background tasks
        # so the API call resolves instantly to the user
        background_tasks.add_task(send_ntfy_push, status_report, priority, "")

        return {
            "status": "success",
            "current_reviews": current_reviews_count,
            "next_review": next_review_str,
        }

    except Exception as e:
        error_msg = f"Failed manual check: {str(e)}"
        background_tasks.add_task(send_ntfy_push, error_msg, "3", "warning")
        return {"status": "error", "message": str(e)}
