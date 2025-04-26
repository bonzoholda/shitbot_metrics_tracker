# tracker.py
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import aiosqlite
import asyncio
import httpx
import os

router = APIRouter()
DB_CLIENTS = "clients.db"
DB_METRICS = "metrics.db"

# Create DB tables if they don’t exist
async def init_db():
    async with aiosqlite.connect(DB_CLIENTS) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                wallet TEXT
            )
        """)
        await db.commit()

    async with aiosqlite.connect(DB_METRICS) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT,
                value REAL,
                timestamp TEXT
            )
        """)
        await db.commit()

@router.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scan_clients_periodically())

# === Models ===
class RegisterRequest(BaseModel):
    url: str
    wallet: Optional[str] = None

# === API Routes ===
@router.get("/referrer")
async def get_by_url(client: str):
    async with aiosqlite.connect(DB_CLIENTS) as db:
        cursor = await db.execute("SELECT wallet FROM clients WHERE url = ?", (client,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Client not registered")
        wallet = row[0]

    async with aiosqlite.connect(DB_METRICS) as db:
        cursor = await db.execute("""
            SELECT timestamp, value FROM portfolio_log
            WHERE wallet = ?
            ORDER BY timestamp ASC
        """, (wallet,))
        data = await cursor.fetchall()

        if not data:
            return {"data": [], "baseline": 0}

        baseline = data[0][1]
        formatted = [{"timestamp": t, "value": v} for t, v in data]
        return {"data": formatted, "baseline": baseline}

@router.get("/user/{wallet}")
async def get_by_wallet(wallet: str):
    async with aiosqlite.connect(DB_METRICS) as db:
        cursor = await db.execute("""
            SELECT timestamp, value FROM portfolio_log
            WHERE wallet = ?
            ORDER BY timestamp ASC
        """, (wallet,))
        data = await cursor.fetchall()

        if not data:
            return {"data": [], "baseline": 0}

        baseline = data[0][1]
        formatted = [{"timestamp": t, "value": v} for t, v in data]
        return {"data": formatted, "baseline": baseline}

@router.post("/register")
async def register_client(req: RegisterRequest):
    if not req.url:
        raise HTTPException(status_code=400, detail="Missing client URL")

    # Attempt to extract wallet from the URL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{req.url}/api/signal")
            data = res.json()
            wallet = data.get("wallet")
            if not wallet:
                raise ValueError("Wallet missing from client")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to query client: {e}")

    # Insert client
    try:
        async with aiosqlite.connect(DB_CLIENTS) as db:
            await db.execute("INSERT INTO clients (url, wallet) VALUES (?, ?)", (req.url, wallet))
            await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail="Client already registered.")

    return {"message": "Registered successfully"}

# === Background Scanner ===
async def scan_clients_periodically():
    while True:
        await scan_and_log()
        await asyncio.sleep(60)

async def scan_and_log():
    async with aiosqlite.connect(DB_CLIENTS) as db:
        cursor = await db.execute("SELECT url, wallet FROM clients")
        rows = await cursor.fetchall()

    for url, wallet in rows:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{url}/api/signal")
                data = res.json()
                value = float(data.get("portfolio_value", 0))

            timestamp = datetime.utcnow().isoformat()
            async with aiosqlite.connect(DB_METRICS) as db:
                await db.execute("""
                    INSERT INTO portfolio_log (wallet, value, timestamp)
                    VALUES (?, ?, ?)
                """, (wallet, value, timestamp))
                await db.commit()

        except Exception as e:
            print(f"[WARN] Failed to fetch from {url}: {e}")
