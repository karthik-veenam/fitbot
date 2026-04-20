"""FastAPI backend for FitBot Mini App."""
import hashlib
import hmac
import json
import os
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"

# Populated at startup by main.py
_bot_token: str = ""
_user_map: dict = {}  # telegram_id (str) -> db_prefix


def init(bot_token: str, telegram_users: dict) -> None:
    global _bot_token, _user_map
    _bot_token = bot_token
    _user_map = {cid: ucfg.db_prefix for cid, ucfg in telegram_users.items()}


def _validate_init_data(init_data: str) -> str | None:
    """Validate Telegram initData and return db_prefix, or None if invalid."""
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", "")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", _bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        user = json.loads(parsed.get("user", "{}"))
        tg_id = str(user.get("id", ""))
        return _user_map.get(tg_id)
    except Exception:
        return None


def _get_prefix(request: Request) -> str:
    init_data = request.headers.get("X-Init-Data", "")
    prefix = _validate_init_data(init_data)
    if not prefix:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return prefix


@app.get("/api/today")
def get_today(request: Request):
    prefix = _get_prefix(request)
    today = db.today_ist()
    food  = db.get_food_log(prefix, today)
    acts  = db.get_activity_log(prefix, today)
    total_in  = round(sum(e["calories"] for e in food), 1)
    total_out = round(sum(e["calories_burned"] for e in acts), 1)
    protein   = round(sum(e["protein_g"] for e in food if e["protein_g"] is not None), 1)
    return {
        "date": today,
        "calories_in": total_in,
        "calories_out": total_out,
        "net": round(total_in - total_out, 1),
        "protein_g": protein,
        "food": food,
        "activities": acts,
    }


@app.get("/api/week")
def get_week(request: Request, days: int = 7):
    if days not in (7, 10, 14, 30):
        days = 7
    prefix = _get_prefix(request)
    from tools import _cfg
    ucfg = next((u for u in _cfg.telegram_users.values() if u.db_prefix == prefix), None) if _cfg else None
    bmr = ucfg.bmr if ucfg else 2000
    rows = db.get_week_summary(prefix, days=days)
    accumulated_deficit = round(sum(max(0, bmr - r["net"]) for r in rows))
    fat_loss_kg = round(accumulated_deficit / 7700, 2)
    next_kg_pct = round((accumulated_deficit % 7700) / 7700 * 100, 1)
    return {
        "week": rows,
        "bmr": bmr,
        "accumulated_deficit": accumulated_deficit,
        "fat_loss_kg": fat_loss_kg,
        "next_kg_pct": next_kg_pct,
    }


@app.get("/api/weight")
def get_weight(request: Request):
    prefix = _get_prefix(request)
    return {"weight": db.get_weight_log(prefix, days=30)}


@app.get("/api/config")
def get_config(request: Request):
    """Return user-specific targets for the frontend."""
    from tools import _cfg
    prefix = _get_prefix(request)
    if not _cfg:
        return {"calorie_goal": 2000, "protein_target": 0}
    ucfg = next((u for u in _cfg.telegram_users.values() if u.db_prefix == prefix), None)
    return {
        "calorie_goal": ucfg.net_calorie_goal if ucfg else 2000,
        "protein_target": ucfg.protein_target_g if ucfg else 0,
        "weight_goal": ucfg.weight_goal_kg if ucfg else 0,
        "bmr": ucfg.bmr if ucfg else 2000,
        "name": ucfg.name if ucfg else "",
    }


# Serve static files (must be after API routes)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
