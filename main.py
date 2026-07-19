import os
import logging
import asyncio
import httpx
from typing import Any
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass

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
BUNPRO_API_KEY = os.getenv("BUNPRO_API_KEY")
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
# Quiet hours: no notifications between QUIET_START and QUIET_END (24h, in TIMEZONE)
QUIET_START = int(os.getenv("QUIET_START", "24"))  # default: 12 AM
QUIET_END = int(os.getenv("QUIET_END", "5"))  # default: 5 AM

_tz_name = os.getenv("TIMEZONE", "UTC")
try:
    tz = ZoneInfo(_tz_name)
except ZoneInfoNotFoundError:
    logger.warning("Unknown timezone '%s', falling back to UTC.", _tz_name)
    tz = ZoneInfo("UTC")


def is_quiet_hour() -> bool:
    """Returns True if the current hour (in TZ) falls within the configured quiet window."""
    hour = datetime.now(tz).hour
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


async def fetch_bunpro_hourly_forecast():
    """Helper to request Bunpro hourly forcast."""
    if not BUNPRO_API_KEY:
        raise ValueError("Missing BUNPRO_API_KEY")

    headers = {"Authorization": f"Token token={BUNPRO_API_KEY}"}
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.bunpro.jp/api/frontend/user_stats/forecast_hourly",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


async def send_ntfy_push(
    message: str,
    priority: str = "5",
    tags: str = "books,brain",
    title: str = "Study Alert",
):
    """Helper to broadcast messages to your configured ntfy topic."""
    if not NTFY_TOPIC:
        logger.warning("Missing NTFY_TOPIC config. Skipping push notification.")
        return

    ntfy_headers = {"Title": title, "Priority": priority, "Tags": tags}
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
            await send_ntfy_push(
                msg, priority="5", tags="books,brain", title="WaniKani Alert"
            )
            logger.info(
                "Sent scheduled notification for %d reviews.", current_reviews_count
            )
        else:
            logger.info("No reviews due at this time.")

    except Exception as e:
        logger.error("Error checking reviews: %s", e)


async def check_bunpro_reviews():
    try:
        if not BUNPRO_API_KEY:
            logger.info("BUNPRO_API_KEY not configured. Skipping Bunpro check.")
            return

        data = await fetch_bunpro_hourly_forecast()
        now = datetime.now(timezone.utc)

        grammar_count = 0
        for timestamp_str, count in data.get("grammar", {}).items():
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            if dt <= now:
                grammar_count += count

        vocab_count = 0
        for timestamp_str, count in data.get("vocab", {}).items():
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            if dt <= now:
                vocab_count += count

        current_reviews_count = grammar_count + vocab_count

        # If reviews are waiting, ping ntfy
        if current_reviews_count > 0:
            msg = f"You have {current_reviews_count} Bunpro reviews waiting! ({grammar_count} grammar, {vocab_count} vocab) Clean your queue."
            await send_ntfy_push(
                msg, priority="5", tags="books,brain", title="Bunpro Alert"
            )
            logger.info(
                "Sent scheduled notification for %d Bunpro reviews.",
                current_reviews_count,
            )
        else:
            logger.info("No Bunpro reviews due at this time.")

    except Exception as e:
        logger.error("Error checking Bunpro reviews: %s", e)


async def schedule_checker():
    """Fires at the top of every clock hour (in TZ). Skips notification during quiet hours."""
    while True:
        # Sleep until the next top-of-hour in the configured timezone
        now = datetime.now(tz)
        seconds_until_next_hour = (60 - now.minute) * 60 - now.second
        logger.info(
            "Next check in %dm %ds (at the top of the next hour in %s).",
            seconds_until_next_hour // 60,
            seconds_until_next_hour % 60,
            tz.key,
        )
        await asyncio.sleep(seconds_until_next_hour)

        if is_quiet_hour():
            current_hour = datetime.now(tz).hour
            logger.info(
                "Quiet hours active (%d:00\u2013%d:00 %s). Skipping %d:00 check.",
                QUIET_START,
                QUIET_END,
                tz.key,
                current_hour,
            )
            continue

        await check_wanikani_reviews()
        await check_bunpro_reviews()


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
    now = datetime.now(timezone.utc)
    results = {}
    report_lines: list[str] = []
    has_reviews = False
    total_current = 0
    next_times: list[datetime] = []

    # Check WaniKani
    if WANIKANI_API_KEY:
        try:
            data = await fetch_wanikani_data()
            reviews_data = data["data"]["reviews"]

            # Calculate current queue count
            wk_current = 0
            for period in reviews_data:
                avail_time = datetime.fromisoformat(
                    period["available_at"].replace("Z", "+00:00")
                )
                if avail_time <= now:
                    wk_current += len(period["subject_ids"])

            total_current += wk_current

            wk_next_str = "None scheduled"
            upcoming_reviews = [
                p
                for p in reviews_data
                if datetime.fromisoformat(p["available_at"].replace("Z", "+00:00"))
                > now
                and len(p.get("subject_ids", [])) > 0
            ]
            if upcoming_reviews:
                upcoming_reviews.sort(key=lambda x: x["available_at"])
                next_time = datetime.fromisoformat(
                    upcoming_reviews[0]["available_at"].replace("Z", "+00:00")
                )
                next_times.append(next_time)
                local_next = next_time.astimezone(tz)
                wk_next_str = local_next.strftime("%I:%M %p")

            results["wanikani"] = {
                "current_reviews": wk_current,
                "next_review": wk_next_str,
            }
            if wk_current > 0:
                report_lines.append(
                    f"WaniKani: {wk_current} reviews due (Next: {wk_next_str})"
                )
                has_reviews = True
            else:
                report_lines.append(f"WaniKani: Queue clean! (Next: {wk_next_str})")

        except Exception as e:
            logger.error("Failed manual WaniKani check: %s", e)
            results["wanikani"] = {"status": "error", "message": str(e)}
            report_lines.append(f"WaniKani: Error ({str(e)})")
    else:
        logger.info("WANIKANI_API_KEY not configured. Skipping in manual check.")

    # Check Bunpro
    if BUNPRO_API_KEY:
        try:
            data = await fetch_bunpro_hourly_forecast()

            grammar_count = 0
            for timestamp_str, count in data.get("grammar", {}).items():
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if dt <= now:
                    grammar_count += count

            vocab_count = 0
            for timestamp_str, count in data.get("vocab", {}).items():
                dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if dt <= now:
                    vocab_count += count

            bp_current = grammar_count + vocab_count
            total_current += bp_current

            # Calculate next upcoming review time
            upcoming_bp_reviews: list[datetime] = []
            for category in ["grammar", "vocab"]:
                for timestamp_str, count in data.get(category, {}).items():
                    if count > 0:
                        dt = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                        if dt > now:
                            upcoming_bp_reviews.append(dt)

            bp_next_str = "None scheduled"
            if upcoming_bp_reviews:
                upcoming_bp_reviews.sort()
                next_time = upcoming_bp_reviews[0]
                next_times.append(next_time)
                local_next = next_time.astimezone(tz)
                bp_next_str = local_next.strftime("%I:%M %p")

            results["bunpro"] = {
                "current_reviews": bp_current,
                "next_review": bp_next_str,
                "grammar_reviews": grammar_count,
                "vocab_reviews": vocab_count,
            }
            if bp_current > 0:
                report_lines.append(
                    f"Bunpro: {bp_current} reviews due ({grammar_count} grammar, {vocab_count} vocab) (Next: {bp_next_str})"
                )
                has_reviews = True
            else:
                report_lines.append(f"Bunpro: Queue clean! (Next: {bp_next_str})")

        except Exception as e:
            logger.error("Failed manual Bunpro check: %s", e)
            results["bunpro"] = {"status": "error", "message": str(e)}
            report_lines.append(f"Bunpro: Error ({str(e)})")
    else:
        logger.info("BUNPRO_API_KEY not configured. Skipping in manual check.")

    if not results:
        return {
            "status": "error",
            "message": "Neither WANIKANI_API_KEY nor BUNPRO_API_KEY is configured.",
        }

    # Aggregate next review
    if next_times:
        next_times.sort()
        earliest_next = next_times[0].astimezone(tz)
        aggregate_next_str = earliest_next.strftime("%I:%M %p")
    else:
        aggregate_next_str = "None scheduled"

    # Construct and send the unified report
    status_report = "\n".join(report_lines)
    priority = "5" if has_reviews else "3"

    background_tasks.add_task(
        send_ntfy_push, status_report, priority, "", "Study Queue Status"
    )

    return {
        "status": "success",
        "current_reviews": total_current,
        "next_review": aggregate_next_str,
        **results,
    }
