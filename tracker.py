import os
import httpx
import sqlite3
from datetime import datetime
import asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Paths and DB initialization
DB_PATH = os.getenv("DATABASE_PATH", "/data/metrics.db")  # Original metrics DB
CLIENT_DB_PATH = os.getenv("CLIENT_DATABASE_PATH", "/data/clients.db")  # New clients DB

app = FastAPI()

# Function to get a connection for the metrics DB (portfolio_log)
def get_metrics_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

# Function to get a connection for the clients DB
def get_clients_connection():
    os.makedirs(os.path.dirname(CLIENT_DB_PATH), exist_ok=True)
    return sqlite3.connect(CLIENT_DB_PATH)

# Function to initialize the metrics DB for portfolio data
def init_metrics_db():
    conn = get_metrics_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT,
            timestamp TEXT,
            portfolio_value REAL,
            usdt_balance REAL,
            wmatic_balance REAL
        )
    """)
    conn.commit()
    conn.close()

# Function to initialize the clients DB
def init_clients_db():
    conn = get_clients_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE
        )
    """)
    conn.commit()
    conn.close()

# Fetch stats from a client's signal URL and log them in the metrics DB
async def fetch_stats(url: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            print(f"Fetching stats from {url}/api/signal")  # Debugging
            res = await client.get(f"{url}/api/signal")
            if res.status_code == 200:
                data = res.json()
                print(f"Fetched data: {data}")  # Debugging
                log_to_metrics_db({
                    "wallet": data.get("account_wallet"),
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

# Periodic tracking loop to fetch stats for registered clients
async def track_loop():
    while True:
        try:
            # Get registered clients from the clients DB
            conn = get_clients_connection()
            c = conn.cursor()
            c.execute("SELECT url FROM clients")
            urls = [row[0] for row in c.fetchall()]
            conn.close()

            tasks = [fetch_stats(url) for url in urls]
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[Tracker Error] {e}")
        await asyncio.sleep(60)

@app.on_event("startup")
async def start_tracking():
    init_metrics_db()  # Initialize the portfolio metrics DB
    init_clients_db()  # Initialize the clients DB
    asyncio.create_task(track_loop())

# New endpoint for registering clients by URL
@app.post("/api/register")
async def register_client(client: Client):
    try:
        conn = get_clients_connection()
        c = conn.cursor()
        c.execute("INSERT INTO clients (url) VALUES (?)", (client.url,))
        conn.commit()
        conn.close()
        return {"message": "Client registered successfully."}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Client already registered.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

# New endpoint to fetch portfolio data for a registered client
@app.get("/api/referrer")
async def get_client_data(request: Request):
    referrer = request.headers.get('Referer')
    if not referrer:
        raise HTTPException(status_code=400, detail="Referrer URL is missing.")
    
    # Check if the referrer URL is registered in the 'clients' table
    try:
        conn = get_clients_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM clients WHERE url = ?", (referrer,))
        client = c.fetchone()
        if client is None:
            raise HTTPException(status_code=404, detail="Client not registered.")
        
        # Fetch portfolio data for the client's wallet (referrer URL)
        conn = get_metrics_connection()
        c = conn.cursor()
        c.execute("SELECT portfolio_value FROM portfolio_log WHERE wallet = ? ORDER BY timestamp ASC LIMIT 1", (referrer,))
        row = c.fetchone()
        baseline = row[0] if row else 1

        c.execute("""
            SELECT timestamp, portfolio_value
            FROM portfolio_log
            WHERE wallet = ?
            ORDER BY timestamp DESC
            LIMIT 1440
        """, (referrer,))
        rows = c.fetchall()
        conn.close()

        data = [{"timestamp": r[0], "value": r[1]} for r in reversed(rows)]
        return { "data": data, "baseline": baseline }

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

# Pydantic model for client registration
class Client(BaseModel):
    url: str
