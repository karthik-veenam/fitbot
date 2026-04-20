import asyncio
import html
import json
import re
import aiohttp
import uvicorn

from config import Config, UserConfig
import api as webapp
import db
import reflection
import shortcuts
import tools as tool_registry
from agent import Agent

_webapp_url: str = ""

# Onboarding state
_pending: dict = {}    # chat_id -> {first_name, username}
_onboarding: dict = {} # chat_id -> {step: int, data: dict}

ONBOARD_STEPS = [
    ("name",             "What's your name?"),
    ("net_calorie_goal", "Daily net calorie goal in kcal? (e.g. <b>200</b> for mild deficit, <b>0</b> to maintain)"),
    ("weight_kg",        "Current weight in kg? (e.g. 75.5)"),
    ("gender",           "Gender? Reply <b>male</b> or <b>female</b>"),
]


def _md_to_html(text: str) -> str:
    blocks, placeholders = [], []
    def _save_block(m):
        blocks.append("<pre>" + html.escape(m.group(1).strip()) + "</pre>")
        placeholders.append(f"\x00BLK{len(blocks)-1}\x00")
        return placeholders[-1]
    text = re.sub(r"```(?:\w+\n)?(.*?)```", _save_block, text, flags=re.DOTALL)
    icodes = []
    def _save_inline(m):
        icodes.append("<code>" + html.escape(m.group(1)) + "</code>")
        return f"\x00INLINE{len(icodes)-1}\x00"
    text = re.sub(r"`([^`\n]+)`", _save_inline, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__",     r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", text)
    for i, block in enumerate(blocks):
        text = text.replace(f"\x00BLK{i}\x00", block)
    for i, code in enumerate(icodes):
        text = text.replace(f"\x00INLINE{i}\x00", code)
    return text


async def send_telegram(
    session: aiohttp.ClientSession, token: str, chat_id: str, text: str,
    is_html: bool = True, reply_markup: dict = None,
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if is_html:
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        await session.post(url, json=payload)
    except Exception as e:
        print(f"[FitBot] Telegram send error: {e}")


async def _answer_callback(session: aiohttp.ClientSession, token: str, callback_id: str) -> None:
    try:
        await session.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_id},
        )
    except Exception:
        pass


async def _start_cloudflared() -> str:
    proc = await asyncio.create_subprocess_exec(
        "cloudflared", "tunnel", "--url", "http://localhost:8000",
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
    )
    url_pattern = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
    print("[FitBot] Waiting for cloudflared tunnel...")
    while True:
        line = await asyncio.wait_for(proc.stderr.readline(), timeout=30)
        if not line:
            break
        decoded = line.decode(errors="ignore")
        match = url_pattern.search(decoded)
        if match:
            url = match.group()
            print(f"[FitBot] Tunnel ready: {url}")
            return url
    return ""


async def _set_menu_button(session: aiohttp.ClientSession, token: str, chat_id: str) -> None:
    if not _webapp_url:
        return
    await session.post(
        f"https://api.telegram.org/bot{token}/setChatMenuButton",
        json={
            "chat_id": chat_id,
            "menu_button": {
                "type": "web_app",
                "text": "📊",
                "web_app": {"url": _webapp_url},
            },
        },
    )


def _is_app_request(text: str) -> bool:
    return bool(re.search(r"(/app|open\s+(app|dashboard)|dashboard)", text, re.I))


def _admin_ids(config: Config) -> list:
    return [cid for cid, u in config.telegram_users.items() if u.is_admin]


def _unique_prefix(name: str, existing_prefixes: set) -> str:
    base = re.sub(r"[^a-z0-9]", "_", name.lower().strip()) + "_"
    prefix = base
    i = 2
    while prefix in existing_prefixes:
        prefix = f"{base}{i}_"
        i += 1
    return prefix


async def _notify_admins(
    session: aiohttp.ClientSession, token: str, config: Config,
    from_id: str, user_info: dict,
) -> None:
    fname = html.escape(user_info.get("first_name", "Unknown"))
    uname = user_info.get("username", "")
    uname_str = f" (@{html.escape(uname)})" if uname else ""
    text = (
        f"👤 New user wants to join FitBot:\n"
        f"<b>{fname}</b>{uname_str}\n"
        f"ID: <code>{from_id}</code>"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"approve:{from_id}"},
        {"text": "❌ Reject",  "callback_data": f"reject:{from_id}"},
    ]]}
    for admin_id in _admin_ids(config):
        await send_telegram(session, token, admin_id, text, reply_markup=markup)


async def _finalize_user(
    chat_id: str, data: dict, config: Config,
    agents: dict, allowed: set,
    session: aiohttp.ClientSession, token: str,
) -> None:
    existing_prefixes = {u.db_prefix for u in config.telegram_users.values()}
    db_prefix = _unique_prefix(data["name"], existing_prefixes)
    gender = data["gender"]
    bmr = 2000 if gender == "male" else 1500

    user_cfg = UserConfig(
        name=data["name"],
        db_prefix=db_prefix,
        net_calorie_goal=data["net_calorie_goal"],
        weight_kg=data["weight_kg"],
        gender=gender,
        bmr=bmr,
    )

    # Init DB tables
    db.init([db_prefix])

    # Update in-memory state
    config.telegram_users[chat_id] = user_cfg
    allowed.add(chat_id)
    agents[chat_id] = Agent(
        config.xai_api_key, user_cfg, config.telegram_users,
        config.fast_model, config.reasoning_model,
    )
    tool_registry.set_config(config)
    webapp.init(token, config.telegram_users)

    # Persist to config.json
    with open("config.json") as f:
        cfg_data = json.load(f)
    cfg_data["telegram_users"][chat_id] = {
        "name": user_cfg.name,
        "db_prefix": db_prefix,
        "net_calorie_goal": user_cfg.net_calorie_goal,
        "weight_kg": user_cfg.weight_kg,
        "gender": gender,
        "protein_target_g": 0,
        "weight_goal_kg": 0.0,
        "bmr": bmr,
        "is_admin": False,
        "group": "default",
    }
    with open("config.json", "w") as f:
        json.dump(cfg_data, f, indent=2)

    # Set dashboard menu button
    await _set_menu_button(session, token, chat_id)

    print(f"[FitBot] New user onboarded: {user_cfg.name} ({chat_id})")
    await send_telegram(
        session, token, chat_id,
        f"You're all set, <b>{html.escape(user_cfg.name)}</b>! 🎉\n\n"
        f"Start by telling me what you ate today, or tap 📊 to open your dashboard.",
    )


async def _handle_onboarding_step(
    from_id: str, text: str,
    config: Config, agents: dict, allowed: set,
    session: aiohttp.ClientSession, token: str,
) -> None:
    state = _onboarding[from_id]
    step  = state["step"]
    field, _ = ONBOARD_STEPS[step]

    # Validate + parse
    error = None
    value = None
    if field == "name":
        value = text.strip()
        if not value:
            error = "Name can't be empty. What's your name?"
    elif field == "net_calorie_goal":
        try:
            value = int(float(text.strip()))
        except ValueError:
            error = "Please enter a number, e.g. <b>200</b> or <b>0</b>."
    elif field == "weight_kg":
        try:
            value = float(text.strip())
            if value <= 0 or value > 300:
                raise ValueError
        except ValueError:
            error = "Please enter a valid weight in kg, e.g. <b>75.5</b>."
    elif field == "gender":
        v = text.strip().lower()
        if v in ("male", "female", "m", "f"):
            value = "male" if v.startswith("m") else "female"
        else:
            error = "Please reply <b>male</b> or <b>female</b>."

    if error:
        await send_telegram(session, token, from_id, error)
        return

    state["data"][field] = value
    state["step"] += 1

    if state["step"] < len(ONBOARD_STEPS):
        _, question = ONBOARD_STEPS[state["step"]]
        await send_telegram(session, token, from_id, question)
    else:
        del _onboarding[from_id]
        await _finalize_user(from_id, state["data"], config, agents, allowed, session, token)


async def _handle_callback(
    cb: dict, config: Config, agents: dict, allowed: set,
    session: aiohttp.ClientSession, token: str,
) -> None:
    await _answer_callback(session, token, cb["id"])
    data    = cb.get("data", "")
    admin_id = str(cb["from"]["id"])

    if data.startswith("approve:"):
        target_id = data.split(":", 1)[1]
        if target_id not in _pending:
            return
        user_info = _pending.pop(target_id)
        fname = html.escape(user_info.get("first_name", "there"))
        _onboarding[target_id] = {"step": 0, "data": {}}
        await send_telegram(session, token, target_id,
                            f"Hi {fname}! You've been approved 🎉\n\nLet's get you set up.\n\n"
                            + ONBOARD_STEPS[0][1])
        await send_telegram(session, token, admin_id,
                            f"✅ Approved <b>{fname}</b>. Onboarding started.")

    elif data.startswith("reject:"):
        target_id = data.split(":", 1)[1]
        user_info = _pending.pop(target_id, {})
        fname = html.escape(user_info.get("first_name", "there"))
        await send_telegram(session, token, target_id,
                            "Sorry, you're not authorised to use this bot.")
        await send_telegram(session, token, admin_id,
                            f"❌ Rejected <b>{fname}</b>.")


async def run(config: Config) -> None:
    global _webapp_url

    prefixes = [u.db_prefix for u in config.telegram_users.values()]
    db.init(prefixes)
    tool_registry.set_config(config)
    webapp.init(config.telegram_bot_token, config.telegram_users)

    agents = {
        chat_id: Agent(config.xai_api_key, user_cfg, config.telegram_users,
                       config.fast_model, config.reasoning_model)
        for chat_id, user_cfg in config.telegram_users.items()
    }

    allowed = set(config.telegram_users.keys())
    token   = config.telegram_bot_token

    # Start FastAPI
    uv_config = uvicorn.Config(webapp.app, host="127.0.0.1", port=8000, log_level="warning")
    uv_server = uvicorn.Server(uv_config)
    asyncio.create_task(uv_server.serve())

    # Start cloudflared tunnel
    _webapp_url = await _start_cloudflared()

    # Pin WebApp as the menu button for each user
    if _webapp_url:
        async with aiohttp.ClientSession() as s:
            for chat_id in allowed:
                await _set_menu_button(s, token, chat_id)
        print(f"[FitBot] Menu button set for {len(allowed)} users")

    async def _send(chat_id: str, text: str) -> None:
        async with aiohttp.ClientSession() as s:
            await send_telegram(s, token, chat_id, text)

    asyncio.create_task(reflection.run_nightly(config, _send))

    print("[FitBot] Started. Polling Telegram...")
    offset = 0
    poll_timeout = aiohttp.ClientTimeout(total=40)
    async with aiohttp.ClientSession(timeout=poll_timeout) as session:
        while True:
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                async with session.get(url, params={"timeout": 30, "offset": offset}) as resp:
                    data = await resp.json()

                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    # Handle inline button callbacks (approve/reject)
                    cb = update.get("callback_query")
                    if cb:
                        try:
                            await _handle_callback(cb, config, agents, allowed, session, token)
                        except Exception as e:
                            print(f"[FitBot] Callback error: {e}")
                        continue

                    msg = update.get("message")
                    if not msg or "text" not in msg:
                        continue

                    from_id = str(msg["chat"]["id"])
                    text    = msg["text"]

                    # Unknown user — queue for admin approval
                    if from_id not in allowed:
                        if from_id not in _pending and from_id not in _onboarding:
                            user_info = {
                                "first_name": msg.get("from", {}).get("first_name", ""),
                                "username":   msg.get("from", {}).get("username", ""),
                            }
                            _pending[from_id] = user_info
                            fname = html.escape(user_info["first_name"] or "there")
                            await send_telegram(session, token, from_id,
                                                f"Hi {fname}! Your request has been sent to the admin. "
                                                f"You'll hear back shortly.")
                            await _notify_admins(session, token, config, from_id, user_info)
                            print(f"[FitBot] Unknown user queued: {from_id}")
                        continue

                    # User in onboarding flow
                    if from_id in _onboarding:
                        try:
                            await _handle_onboarding_step(
                                from_id, text, config, agents, allowed, session, token)
                        except Exception as e:
                            print(f"[FitBot] Onboarding error for {from_id}: {e}")
                        continue

                    user_name = config.telegram_users[from_id].name
                    print(f"[FitBot] [{user_name}] {text!r}")

                    # /app command
                    if _is_app_request(text):
                        if _webapp_url:
                            await send_telegram(
                                session, token, from_id,
                                "📊",
                                reply_markup={"inline_keyboard": [[
                                    {"text": "📊 Dashboard",
                                     "web_app": {"url": _webapp_url}}
                                ]]}
                            )
                        else:
                            await send_telegram(session, token, from_id,
                                                "Dashboard is still starting up, try again in a moment.")
                        continue

                    agent = agents[from_id]
                    try:
                        user_cfg = config.telegram_users[from_id]
                        reply = shortcuts.try_handle(text, user_cfg, config.telegram_users)
                        if reply is None:
                            reply = _md_to_html(await agent.respond(text))
                        await send_telegram(session, token, from_id, reply)
                    except Exception as e:
                        print(f"[FitBot] Agent error for {user_name}: {e}")
                        await send_telegram(session, token, from_id,
                                            "Sorry, hit an error processing that. Please try again.")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[FitBot] Poll error: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    config = Config.load()
    asyncio.run(run(config))
