import os
import httpx
import sqlite3
from datetime import datetime
import asyncio
from fastapi import FastAPI, HTTPException, Request, Query, APIRouter
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import logging
from tracker import router as tracker_router  # ✅ Import the router now
from fastapi.responses import JSONResponse

# Paths and DB initialization
DB_PATH = os.getenv("DATABASE_PATH", "/data/metrics.db")  # Original metrics DB
CLIENT_DB_PATH = os.getenv("CLIENT_DATABASE_PATH", "/data/clients.db")  # New clients DB

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

# Function to get a connection for the metrics DB (portfolio_log)
async def get_metrics_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)
    
# Function to get a connection for the clients DB
async def get_clients_connection():
    os.makedirs(os.path.dirname(CLIENT_DB_PATH), exist_ok=True)
    return sqlite3.connect(CLIENT_DB_PATH, check_same_thread=False)

# Function to initialize the clients DB with a default client URL and wallet
def init_clients_db():
    conn = get_clients_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            wallet TEXT NOT NULL
        )
    ''')
    # Optional: Insert a default client for testing
    default_url = "https://shitbotdextrader-production.up.railway.app"
    default_wallet = "0x5cd16AC5946fb83Bf8F7d3B861D88ed40660811B"
    # Only insert if it doesn't already exist
    c.execute('SELECT 1 FROM clients WHERE url = ? AND wallet = ?', (default_url, default_wallet))
    if not c.fetchone():
        c.execute('INSERT INTO clients (url, wallet) VALUES (?, ?)', (default_url, default_wallet))
        print(f"Default client registered: {default_wallet} at {default_url}")
    conn.commit()
    conn.close()

# Register client API (Post Request to register client URL and wallet)
class Client(BaseModel):
    url: str
    wallet: str

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ✅ New endpoint for main.py to use instead of direct DB access
@app.get("/api/user/{wallet}")
async def get_wallet_data(wallet: str):
    try:
        conn = get_metrics_connection()
        c = conn.cursor()

        c.execute("SELECT portfolio_value FROM portfolio_log WHERE wallet = ? ORDER BY timestamp ASC LIMIT 1", (wallet,))
        row = c.fetchone()
        baseline = row[0] if row else 1

        c.execute("""
            SELECT timestamp, portfolio_value
            FROM portfolio_log
            WHERE wallet = ?
            ORDER BY timestamp DESC
            LIMIT 1440
        """, (wallet,))
        rows = c.fetchall()
        conn.close()

        data = [{"timestamp": r[0], "value": r[1]} for r in reversed(rows)]
        return { "data": data, "baseline": baseline }

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

@app.get("/api/check_client")
async def check_client(wallet: str):
    conn = sqlite3.connect('clients.db')
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM clients WHERE wallet = ?", (wallet,))
    exists = cursor.fetchone() is not None
    conn.close()
    return {"exists": exists}


@app.post("/api/register_client")
async def register_client(request: Request):
    data = await request.json()
    wallet = data.get("wallet")
    url = data.get("url")

    print(f"Received registration request: wallet={wallet}, url={url}")

    if not wallet or not url:
        return {"status": "error", "message": "Missing wallet or url"}

    conn = sqlite3.connect("clients.db")
    c = conn.cursor()

    # Check if client already exists
    c.execute("SELECT * FROM clients WHERE url = ? AND wallet = ?", (url, wallet))
    existing = c.fetchone()

    if existing:
        conn.close()
        print(f"Client already exists: {wallet} at {url}")
        return {"status": "exists", "message": "Client already registered"}

    # If not exists, insert new client
    c.execute("INSERT INTO clients (url, wallet) VALUES (?, ?)", (url, wallet))
    conn.commit()
    conn.close()

    print(f"Client registered successfully: {wallet} at {url}")
    return {"status": "success", "message": "Client registered"}



@app.options("/api/register_client")
async def options_register_client():
    response = JSONResponse(content={"message": "CORS preflight OK"})
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response


# Initialize database when app starts
@app.on_event("startup")
async def monitor_clients():
    print("Initializing databases...")  # Debugging
    init_clients_db()  # Initialize the clients DB
