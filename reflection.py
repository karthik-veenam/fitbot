"""Nightly reflection loop — runs at 11 PM IST, extracts memories from the day's logs."""
import asyncio
import json
from datetime import datetime, timezone, timedelta

from openai import AsyncOpenAI
import db

IST = timezone(timedelta(hours=5, minutes=30))

_REFLECTION_PROMPT = """\
You are analyzing a day's fitness data for {name} to extract long-term memory.

Today's data:
{data}

Already saved memories (do NOT duplicate these):
{existing}

Task: Extract facts about {name}'s eating habits, food preferences, activity patterns, or \
personal context that are worth remembering permanently. Focus on recurring patterns and \
strong preferences — not one-off events.

Rules:
- Only add things NOT already covered by existing memories
- Be specific and concise (e.g. "eats tomato rice for lunch most days", "plays badminton ~45 min")
- Return a JSON array of short memory strings (max 3), or [] if nothing new to add
- Return ONLY valid JSON, no explanation

Example output: ["prefers light lunches under 400 kcal", "plays badminton regularly on weekday mornings"]
"""


async def _reflect_for_user(client: AsyncOpenAI, reasoning_model: str, user_cfg, chat_id: str,
                             send_fn) -> None:
    today = db.today_ist()
    food       = db.get_food_log(user_cfg.db_prefix, today)
    activities = db.get_activity_log(user_cfg.db_prefix, today)

    if not food and not activities:
        return

    week     = db.get_week_summary(user_cfg.db_prefix)
    existing = db.get_memories(user_cfg.db_prefix)

    data = json.dumps({
        "today_food": food,
        "today_activities": activities,
        "week_summary": week,
    })
    existing_str = "\n".join(f"- {m['memory']}" for m in existing) or "None yet."

    prompt = _REFLECTION_PROMPT.format(
        name=user_cfg.name, data=data, existing=existing_str
    )

    try:
        resp = await client.chat.completions.create(
            model=reasoning_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        new_memories = json.loads(resp.choices[0].message.content.strip())
        if not isinstance(new_memories, list):
            return
        new_memories = [m for m in new_memories if isinstance(m, str) and m.strip()]
    except Exception as e:
        print(f"[FitBot] Reflection parse error for {user_cfg.name}: {e}")
        return

    if not new_memories:
        print(f"[FitBot] Reflection: nothing new for {user_cfg.name}")
        return

    for memory in new_memories:
        db.save_memory(user_cfg.db_prefix, memory, source="reflection")
        print(f"[FitBot] Reflection saved for {user_cfg.name}: {memory}")

    bullet_list = "\n".join(f"• {m}" for m in new_memories)
    await send_fn(chat_id, f"<b>Memory updated</b> ({len(new_memories)} new insight{'s' if len(new_memories) > 1 else ''}):\n{bullet_list}")


async def run_nightly(config, send_fn) -> None:
    """Background task: waits until 11 PM IST each night, then reflects for all users."""
    client = AsyncOpenAI(
        api_key=config.xai_api_key,
        base_url="https://api.x.ai/v1",
        timeout=60.0,
    )
    # Build chat_id → user_cfg mapping
    users = {chat_id: ucfg for chat_id, ucfg in config.telegram_users.items()}

    while True:
        now    = datetime.now(IST)
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        wait   = (target - now).total_seconds()
        print(f"[FitBot] Reflection scheduled in {wait/3600:.1f}h")
        await asyncio.sleep(wait)

        print("[FitBot] Running nightly reflection...")
        for chat_id, user_cfg in users.items():
            try:
                await _reflect_for_user(client, config.reasoning_model, user_cfg, chat_id, send_fn)
            except Exception as e:
                print(f"[FitBot] Reflection error for {user_cfg.name}: {e}")
