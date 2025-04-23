import httpx
import sqlite3
from datetime import datetime
import asyncio

DB_NAME = "metrics.db"
CLIENT_LIST = "clients.txt"

def init_db():
    conn = sqlite3.connect(DB_NAME)
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
            res = await client.get(f"{url}/api/stats")
            if res.status_code == 200:
                data = res.json()
                log_to_db(data)
                print(f"[{url}] Logged: {data['portfolio_value']}")
            else:
                print(f"[{url}] Error: {res.status_code}")
    except Exception as e:
        print(f"[{url}] Failed: {e}")

def log_to_db(data):
    conn = sqlite3.connect(DB_NAME)
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

async def track_loop():
    while True:
        try:
            with open(CLIENT_LIST, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
            tasks = [fetch_stats(url) for url in urls]
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[Tracker Error] {e}")

        await asyncio.sleep(600)  # 10 minutes

if __name__ == "__main__":
    init_db()
    asyncio.run(track_loop())
