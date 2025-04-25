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

# Register client API (Post Request to register client URL)
class Client(BaseModel):
    url: str

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

# Fetch portfolio data for a registered client
@app.get("/api/referrer")
async def get_client_data(request: Request):
    referrer = request.headers.get('Referer')
    if not referrer:
        raise HTTPException(status_code=400, detail="Referrer URL is missing.")
    
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

# Initialize database when app starts
@app.on_event("startup")
async def start_tracking():
    init_clients_db()  # Initialize the clients DB
    asyncio.create_task(track_loop())
