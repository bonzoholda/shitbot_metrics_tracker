import os
import httpx
import sqlite3
from datetime import datetime
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

DB_PATH = os.getenv("DATABASE_PATH", "/data/metrics.db")
CLIENT_LIST = "clients.txt"

app = FastAPI()

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
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

async def fetch_stats(url: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"{url}/api/signal")
            if res.status_code == 200:
                data = res.json()

                log_to_db({
                    "wallet": data.get("account_wallet"),
                    "timestamp": datetime.utcnow().isoformat(),
                    "portfolio_value": data.get("portfolio_value"),
                    "usdt_balance": data.get("usdt_balance"),
                    "wmatic_balance": data.get("wmatic_balance")
                })

                print(f"[{url}] Logged: {data['portfolio_value']} USDT")
            else:
                print(f"[{url}] Error: {res.status_code}")
    except Exception as e:
        print(f"[{url}] Failed: {e}")

def log_to_db(data):
    try:
        conn = get_connection()
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

async def track_loop():
    while True:
        try:
            with open(CLIENT_LIST, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
            tasks = [fetch_stats(url) for url in urls]
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[Tracker Error] {e}")
        await asyncio.sleep(60)

@app.on_event("startup")
async def start_tracking():
    init_db()
    asyncio.create_task(track_loop())

# ✅ New endpoint for main.py to use instead of direct DB access
@app.get("/api/user/{wallet}")
async def get_wallet_data(wallet: str):
    try:
        conn = get_connection()
        c = conn.cursor()

        c.execute("SELECT portfolio_value FROM portfolio_log WHERE wallet = ? ORDER BY timestamp ASC LIMIT 1", (wallet,))
        row = c.fetchone()
        baseline = row[0] if row else 1

        c.execute("""
            SELECT timestamp, portfolio_value
            FROM portfolio_log
            WHERE wallet = ?
            ORDER BY timestamp DESC
            LIMIT 90
        """, (wallet,))
        rows = c.fetchall()
        conn.close()

        data = [{"timestamp": r[0], "value": r[1]} for r in reversed(rows)]
        return { "data": data, "baseline": baseline }

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
