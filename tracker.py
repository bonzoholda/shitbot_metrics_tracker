import os
import httpx
import sqlite3
from datetime import datetime
import asyncio
from fastapi import FastAPI, HTTPException, Request, Query, APIRouter
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

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

router = APIRouter()

insert_counter = 0

# Function to get a connection for the metrics DB (portfolio_log)
def get_metrics_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

# Function to get a connection for the clients DB
def get_clients_connection():
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

def insert_new_log(wallet_address, portfolio_value):
    global insert_counter
    conn = get_metrics_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO portfolio_log (wallet_address, portfolio_value, timestamp)
        VALUES (?, ?, strftime('%s','now'))
    ''', (wallet_address, portfolio_value))
    conn.commit()
    conn.close()
    # Increase insert counter
    insert_counter += 1
    # Run cleanup every 50 inserts
    if insert_counter >= 50:
        cleanup_old_logs(wallet_address)
        insert_counter = 0  # reset counter

def cleanup_old_logs(wallet_address):
    conn = get_metrics_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM portfolio_log
        WHERE id NOT IN (
            SELECT id FROM portfolio_log
            WHERE wallet_address = ?
            ORDER BY timestamp DESC
            LIMIT 1440
        )
        AND wallet_address = ?;
    ''', (wallet_address, wallet_address))
    conn.commit()
    conn.close()


# Register client API (Post Request to register client URL and wallet)
class Client(BaseModel):
    url: str
    wallet: str

@app.get("/api/check_client")
async def check_client(wallet: str):
    conn = get_clients_connection()
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
    if not wallet or not url:
        return {"success": False, "message": "Wallet or URL missing"}

    conn = get_clients_connection()
    cursor = conn.cursor()

    # Create table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT UNIQUE,
            url TEXT
        )
    """)
    
    # Insert or ignore if already exists
    try:
        cursor.execute("INSERT OR IGNORE INTO clients (wallet, url) VALUES (?, ?)", (wallet, url))
        conn.commit()
    except Exception as e:
        conn.close()
        return {"success": False, "message": str(e)}
    conn.close()
    return {"success": True, "message": "Client registered"}




# Fetch portfolio data for a registered client using wallet
@router.get("/referrer")
async def get_client_data(wallet: str = Query(...)):
    print(f"Fetching data for wallet: {wallet}")

    if not wallet:
        raise HTTPException(status_code=400, detail="Wallet address is missing.")

    try:
        # Step 1: Look up client by wallet address
        conn = get_clients_connection()
        c = conn.cursor()

        c.execute("SELECT url FROM clients WHERE wallet = ?", (wallet,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Client not registered.")

        url = row[0]  # Use the actual URL associated with the wallet
        print(f"Resolved URL for wallet {wallet}: {url}")

        # Step 2: Fetch portfolio data from that wallet
        conn = get_metrics_connection()
        c = conn.cursor()

        c.execute("""
            SELECT portfolio_value FROM portfolio_log
            WHERE wallet = ?
            ORDER BY timestamp ASC
            LIMIT 1
        """, (wallet,))
        baseline_row = c.fetchone()
        baseline = baseline_row[0] if baseline_row else 1

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

# Track portfolio data from all registered clients periodically
async def track_loop():
    while True:
        try:
            # Get registered clients from the clients DB
            conn = get_clients_connection()
            c = conn.cursor()
            c.execute("SELECT url, wallet FROM clients")
            clients = [(row[0], row[1]) for row in c.fetchall()]
            conn.close()

            tasks = [fetch_stats(url, wallet) for url, wallet in clients]
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[Tracker Error] {e}")
        await asyncio.sleep(60)  # Run every minute

# Fetch stats from a client's signal URL and log them in the metrics DB
async def fetch_stats(url: str, wallet: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            print(f"Fetching stats from {url}/api/signal for wallet {wallet}")  # Debugging
            res = await client.get(f"{url}/api/signal")
            if res.status_code == 200:
                data = res.json()
                print(f"Fetched data: {data}")  # Debugging
                log_to_metrics_db({
                    "wallet": wallet,
                    "timestamp": datetime.utcnow().isoformat(),
                    "portfolio_value": data.get("portfolio_value"),
                    "usdt_balance": data.get("usdt_balance"),
                    "wmatic_balance": data.get("wmatic_balance")
                })
            else:
                print(f"[{url}] Error: {res.status_code}")
    except Exception as e:
        print(f"[{url}] Failed: {e}")

# Log fetched stats into the portfolio_log table of metrics DB
def log_to_metrics_db(data):
    print(f"Logging data to DB: {data}")  # Debugging
    try:
        conn = get_metrics_connection()
        c = conn.cursor()

        # Ensure that the portfolio_log table exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                portfolio_value REAL NOT NULL,
                usdt_balance REAL,
                wmatic_balance REAL
            )
        """)

        # Insert the data into portfolio_log
        c.execute("""
            INSERT INTO portfolio_log (wallet, timestamp, portfolio_value, usdt_balance, wmatic_balance)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data['wallet'],
            data['timestamp'],
            data['portfolio_value'],
            data['usdt_balance'],
            data['wmatic_balance']
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB Error] Failed to insert data: {e}")


# Initialize database when app starts
@app.on_event("startup")
async def start_tracking():
    print("Initializing databases...")  # Debugging
    init_clients_db()  # Initialize the clients DB
    asyncio.create_task(track_loop())  # Start the periodic tracking loop

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

@app.get("/test-cors")
async def test_cors():
    return {"message": "CORS is working!"}
