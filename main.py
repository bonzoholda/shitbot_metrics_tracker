from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import httpx
import os
import logging

from tracker import router as tracker_router  # ✅ Import the router now

app = FastAPI()

# ✅ CORS middleware added immediately after app init
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # (or your exact dashboard URL later)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ✅ Include /api routes from tracker.py
app.include_router(tracker_router, prefix="/api")

TRACKER_API_URL = os.getenv("TRACKER_API_URL", "https://tracker-worker.up.railway.app")

logger = logging.getLogger(__name__)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/user/{wallet}")
async def get_user_data(wallet: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"{TRACKER_API_URL}/api/user/{wallet}")
            if res.status_code == 200:
                return res.json()
            else:
                logger.error(f"[Tracker API] Error {res.status_code}: {res.text}")
                raise HTTPException(status_code=502, detail="Tracker service error.")
    except httpx.RequestError as e:
        logger.error(f"[Tracker API] Request failed for wallet {wallet}: {e}")
        raise HTTPException(status_code=503, detail="Unable to connect to tracker service.")
