import os
import asyncio
import httpx
from typing import Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks

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
        print("Background scheduler task cleanly cancelled.")

# 2. Pass the lifespan to the FastAPI instance
app = FastAPI(lifespan=lifespan)

# Configuration from Environment Variables
WANIKANI_API_KEY = os.getenv("WANIKANI_API_KEY")
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
DELAY = int(os.getenv("DELAY") or 1800) # 1800 seconds = 30 minutes

async def fetch_wanikani_data():
    """Helper to request the summary payload from WaniKani API."""
    if not WANIKANI_API_KEY:
        raise ValueError("Missing WANIKANI_API_KEY")
    
    headers = {"Authorization": f"Bearer {WANIKANI_API_KEY}"}
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.wanikani.com/v2/summary", headers=headers)
        response.raise_for_status()
        return response.json()

async def send_ntfy_push(message: str, priority: str = "5", tags: str = "books,brain"):
    """Helper to broadcast messages to your configured ntfy topic."""
    if not NTFY_TOPIC:
        print("Missing NTFY_TOPIC config. Skipping push notification.")
        return
    
    ntfy_headers = {
        "Title": "WaniKani Alert",
        "Priority": priority,
        "Tags": tags
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers=ntfy_headers
        )

async def check_wanikani_reviews():
    try:
        data = await fetch_wanikani_data()
        
        # Extract current reviews due
        now = datetime.now(timezone.utc)
        reviews_data = data["data"]["reviews"]
        
        current_reviews_count = 0
        for period in reviews_data:
            avail_time = datetime.fromisoformat(period["available_at"].replace("Z", "+00:00"))
            if avail_time <= now:
                current_reviews_count += len(period["subject_ids"])
        
        # If reviews are waiting, ping ntfy
        if current_reviews_count > 0:
            msg = f"🎏 You have {current_reviews_count} WaniKani reviews waiting! Clean your queue."
            await send_ntfy_push(msg, priority="5", tags="books,brain")
            print(f"Sent scheduled notification for {current_reviews_count} reviews.")
        else:
            print("No reviews due at this time.")
            
    except Exception as e:
        print(f"Error checking reviews: {e}")

async def schedule_checker():
    """Background loop with configurable delay."""
    while True:
        await check_wanikani_reviews()
        await asyncio.sleep(DELAY)

@app.get("/")
def health_check():
    # Coolify health check
    return {"status": "running"}

@app.post("/check")
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
            avail_time = datetime.fromisoformat(period["available_at"].replace("Z", "+00:00"))
            if avail_time <= now:
                current_reviews_count += len(period["subject_ids"])
                
        # Calculate next upcoming review time
        next_review_str = "None scheduled"
        upcoming_reviews = [p for p in reviews_data if datetime.fromisoformat(p["available_at"].replace("Z", "+00:00")) > now]
        if upcoming_reviews:
            # Sort chronologically to find the closest one
            upcoming_reviews.sort(key=lambda x: x["available_at"])
            next_time = datetime.fromisoformat(upcoming_reviews[0]["available_at"].replace("Z", "+00:00"))
            # Format nicely for reading
            local_next = next_time.astimezone()
            next_review_str = local_next.strftime('%I:%M %p')

        # Construct status report payload
        if current_reviews_count > 0:
            status_report = f"📊 Status: {current_reviews_count} reviews due right now!"
            priority = "5" # High priority to wake up sound engine
        else:
            status_report = f"✅ Queue clean! Next review is scheduled at {next_review_str}."
            priority = "3" # Normal priority notification

        # Queue the push notification response into FastAPI background tasks 
        # so the API call resolves instantly to the user
        background_tasks.add_task(send_ntfy_push, status_report, priority, "bar_chart,brain")
        
        return {
            "status": "success", 
            "current_reviews": current_reviews_count, 
            "next_review": next_review_str
        }

    except Exception as e:
        error_msg = f"Failed manual check: {str(e)}"
        background_tasks.add_task(send_ntfy_push, error_msg, "3", "warning")
        return {"status": "error", "message": str(e)}
