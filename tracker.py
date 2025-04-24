import httpx
import sqlite3
import os
from datetime import datetime
import asyncio
from fastapi import FastAPI

# ✅ Use shared SQLite file path from Railway env var
DB_PATH = os.getenv("DATABASE_PATH", "metrics.db")
CLIENT_LIST = "clients.txt"

app = FastAPI()

def get_connection():
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

def log_to_db(data):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO portfolio_log (wallet, timestamp, portfolio_value, usdt_balance, wmatic_balance)
        VALUES (?, ?, ?, ?, ?)
    """, (
        data['wallet'],
        datetime.utcnow().isoformat(),
        data['portfolio_value'],
        data['usdt_balance'],
        data['wmatic_balance']
    ))
    conn.commit()
    conn.close()

async def fetch_stats(url: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"{url}/api/signal")
            if res.status_code == 200:
                data = res.json()

                log_to_db({
                    "wallet": data["account_wallet"],
                    "portfolio_value": data["portfolio_value"],
                    "usdt_balance": data["usdt_balance"],
                    "wmatic_balance": data["wmatic_balance"]
                })

                print(f"[{url}] Logged: {data['portfolio_value']} USDT")
            else:
                print(f"[{url}] Error: {res.status_code}")
    except Exception as e:
        print(f"[{url}] Failed: {e}")

async def track_loop():
    while True:
        try:
            with open(CLIENT_LIST, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
            tasks = [fetch_stats(url) for url in urls]
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[Tracker Error] {e}")

        await asyncio.sleep(60)  # every minute

@app.on_event("startup")
async def start_tracking():
    init_db()
    asyncio.create_task(track_loop())
