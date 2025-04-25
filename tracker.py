import os
import httpx
import sqlite3
from datetime import datetime
import asyncio
from fastapi import FastAPI, HTTPException, Request, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

# Paths and DB initialization
DB_PATH = os.getenv("DATABASE_PATH", "/data/metrics.db")  # Original metrics DB
CLIENT_DB_PATH = os.getenv("CLIENT_DATABASE_PATH", "/data/clients.db")  # New clients DB

app = FastAPI()

# Allow specific origins for CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://shitbotmetricstracker-production.up.railway.app"],  # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)

# Function to get a connection for the metrics DB (portfolio_log)
def get_metrics_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

# Function to get a connection for the clients DB
def get_clients_connection():
    os.makedirs(os.path.dirname(CLIENT_DB_PATH), exist_ok=True)
    return sqlite3.connect(CLIENT_DB_PATH, check_same_thread=False)

# Function to initialize the clients DB with a default client URL
def init_clients_db():
    conn = get_clients_connection()
    c = conn.cursor()
    # Create the clients table if it doesn't exist
    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE
        )
    """)
    
    # Insert default client URL if it doesn't already exist
    default_url = "https://shitbotdextrader-production.up.railway.app"
    c.execute("SELECT COUNT(*) FROM clients WHERE url = ?", (default_url,))
    if c.fetchone()[0] == 0:
        print(f"Inserting default client URL: {default_url}")  # Debugging
        c.execute("INSERT INTO clients (url) VALUES (?)", (default_url,))
        conn.commit()
    
    conn.close()


# Register client API (Post Request to register client URL)
class Client(BaseModel):
    url: str

@app.post("/api/register")
async def register_client(client: Client):
    print(f"Attempting to register client: {client.url}")
    
    try:
        # Open the database connection and ensure it's closed automatically
        with get_clients_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO clients (url) VALUES (?)", (client.url,))
            conn.commit()
        
        print(f"Client {client.url} registered successfully.")
        return {"message": "Client registered successfully."}
    except sqlite3.IntegrityError:
        print(f"Client {client.url} already registered.")
        raise HTTPException(status_code=400, detail="Client already registered.")
    except sqlite3.OperationalError as e:
        # This captures 'database is locked' and other operational errors
        print(f"[Error] SQLite operational error: {e}")
        raise HTTPException(status_code=500, detail="Database is locked, please try again.")
    except Exception as e:
        print(f"[Error] {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

# Fetch portfolio data for a registered client
from urllib.parse import urlparse

@app.get("/api/referrer")
async def get_client_data(client: str = Query(...)):
    referrer = client.rstrip("/")
    print(f"Raw referrer: {referrer}")

    if not referrer:
        raise HTTPException(status_code=400, detail="Referrer URL is missing.")

    try:
        # Step 1: Look up wallet for this client
        conn = get_clients_connection()
        c = conn.cursor()
        print("Clients in DB:")
        for row in c.execute("SELECT url FROM clients"):
            print(f"- {row[0]}")

        c.execute("SELECT wallet FROM clients WHERE url = ?", (referrer,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Client not registered.")

        wallet = row[0]  # ✅ Use the actual wallet address
        print(f"Resolved wallet: {wallet}")

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
            c.execute("SELECT url FROM clients")
            urls = [row[0] for row in c.fetchall()]
            conn.close()

            tasks = [fetch_stats(url) for url in urls]
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[Tracker Error] {e}")
        await asyncio.sleep(60)  # Run every minute

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

        # Ensure that the portfolio_log table exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                portfolio_value REAL NOT NULL,
                usdt_balance REAL,
                wmatic_balance REAL,
                pol_balance REAL
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
