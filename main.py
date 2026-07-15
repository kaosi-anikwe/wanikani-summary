import os
import asyncio
import httpx
from datetime import datetime, timezone
from fastapi import FastAPI

app = FastAPI()

# Configuration from Environment Variables
WANIKANI_API_KEY = os.getenv("WANIKANI_API_KEY")
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
DELAY = int(os.getenv("DELAY") or 1800) # 1800 seconds = 30 minutes

async def check_wanikani_reviews():
    if not WANIKANI_API_KEY or not NTFY_TOPIC:
        print("Missing environment variables.")
        return

    headers = {"Authorization": f"Bearer {WANIKANI_API_KEY}"}
    
    async with httpx.AsyncClient() as client:
        try:
            # 1. Fetch summary from WaniKani
            response = await client.get("https://api.wanikani.com/v2/summary", headers=headers)
            response.raise_for_status()
            data = response.json()
            
            # 2. Extract current reviews due
            now = datetime.now(timezone.utc)
            reviews_data = data["data"]["reviews"]
            
            current_reviews_count = 0
            for period in reviews_data:
                avail_time = datetime.fromisoformat(period["available_at"].replace("Z", "+00:00"))
                if avail_time <= now:
                    current_reviews_count += len(period["subject_ids"])
            
            # 3. If reviews are waiting, ping ntfy
            if current_reviews_count > 0:
                msg = f"🎏 You have {current_reviews_count} WaniKani reviews waiting! Clean your queue."
                ntfy_headers = {
                    "Title": "WaniKani Alert",
                    "Priority": "5",  # Max priority for Android
                    "Tags": "books,brain"
                }
                
                await client.post(
                    f"https://ntfy.sh/{NTFY_TOPIC}",
                    content=msg.encode("utf-8"),
                    headers=ntfy_headers
                )
                print(f"Sent notification for {current_reviews_count} reviews.")
            else:
                print("No reviews due at this time.")
                
        except Exception as e:
            print(f"Error checking reviews: {e}")

# The background task loop (Runs every 30 minutes)
async def schedule_checker():
    while True:
        await check_wanikani_reviews()
        await asyncio.sleep(DELAY)

@app.on_event("startup")
async def startup_event():
    # Start the checker task in the background on startup
    asyncio.create_task(schedule_checker())

@app.get("/")
def health_check():
    # Coolify needs a 200 OK response on some port to keep the container healthy
    return {"status": "running"}

