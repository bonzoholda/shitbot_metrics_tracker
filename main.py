from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import sqlite3

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/user/{wallet}")
def get_user_data(wallet: str):
    conn = sqlite3.connect("metrics.db")
    c = conn.cursor()

    c.execute("SELECT portfolio_value FROM portfolio_log WHERE wallet = ? ORDER BY timestamp ASC LIMIT 1", (wallet,))
    row = c.fetchone()
    baseline = row[0] if row else 1

    c.execute("""
        SELECT timestamp, portfolio_value
        FROM portfolio_log
        WHERE wallet = ?
        GROUP BY DATE(timestamp)
        ORDER BY timestamp DESC
        LIMIT 90
    """, (wallet,))
    rows = c.fetchall()
    conn.close()

    data = [{"timestamp": r[0], "value": r[1]} for r in reversed(rows)]
    return { "data": data, "baseline": baseline }
