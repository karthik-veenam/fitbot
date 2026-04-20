"""Fitbit weight fetch — synchronous, stdlib only."""
import base64
import json
import urllib.parse
import urllib.request

_BASE = "https://api.fitbit.com"


def _token_headers(client_id: str, client_secret: str) -> dict:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"}


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> tuple:
    resp = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{_BASE}/oauth2/token",
        data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode(),
        headers=_token_headers(client_id, client_secret),
        method="POST",
    )).read())
    return resp["access_token"], resp["refresh_token"]


def _get_weight(access_token: str) -> tuple:
    """Returns (weight_kg, bmi) for today, or raises if no data."""
    resp = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{_BASE}/1/user/-/body/log/weight/date/today/7d.json",
        headers={"Authorization": f"Bearer {access_token}"},
    )).read())
    entries = resp.get("weight", [])
    if not entries:
        raise ValueError("No weight data in Fitbit for last 7 days")
    latest = entries[-1]
    return float(latest["weight"]), latest.get("bmi")


def fetch_today_weight(
    client_id: str, client_secret: str,
    access_token: str, refresh_token: str,
) -> tuple:
    """Returns (weight_kg, bmi, new_access_token, new_refresh_token).
    Auto-refreshes the access token on 401."""
    try:
        w, bmi = _get_weight(access_token)
        return w, bmi, access_token, refresh_token
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
    # Token expired — refresh and retry
    new_at, new_rt = _refresh(client_id, client_secret, refresh_token)
    w, bmi = _get_weight(new_at)
    return w, bmi, new_at, new_rt


def save_tokens(config_path: str, user_name: str, access_token: str, refresh_token: str) -> None:
    """Persist updated tokens back to config.json."""
    with open(config_path) as f:
        cfg = json.load(f)
    for user in cfg["telegram_users"].values():
        if user.get("name") == user_name:
            user["fitbit_access_token"] = access_token
            user["fitbit_refresh_token"] = refresh_token
            break
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2)
