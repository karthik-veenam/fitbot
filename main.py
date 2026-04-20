import asyncio
import html
import re
import aiohttp
import uvicorn

from config import Config
import api as webapp
import db
import reflection
import shortcuts
import tools as tool_registry
from agent import Agent

_webapp_url: str = ""


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


async def _start_cloudflared() -> str:
    """Start cloudflared quick tunnel and return the public HTTPS URL."""
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


def _is_app_request(text: str) -> bool:
    return bool(re.search(r"(/app|open\s+(app|dashboard)|dashboard)", text, re.I))


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

    # Pin WebApp as the menu button for each user (one-tap access)
    if _webapp_url:
        async with aiohttp.ClientSession() as s:
            for chat_id in allowed:
                await s.post(
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
                    msg = update.get("message")
                    if not msg or "text" not in msg:
                        continue

                    from_id = str(msg["chat"]["id"])
                    if from_id not in allowed:
                        continue

                    text = msg["text"]
                    user_name = config.telegram_users[from_id].name
                    print(f"[FitBot] [{user_name}] {text!r}")

                    # /app command — send WebApp button
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
