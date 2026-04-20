import json
import uuid
from datetime import datetime, timezone, timedelta

from openai import AsyncOpenAI
import db
import tools as tool_registry

IST = timezone(timedelta(hours=5, minutes=30))

# Fully static — never changes between calls, maximises prompt cache hits.
SYSTEM_PROMPT = """\
You are FitBot, a personal fitness and nutrition assistant.

Your role:
- Track food intake and physical activities using your database tools
- Answer questions about eating and exercise history using stored data
- Provide guidance on nutrition, fitness, and healthy habits
- Be knowledgeable about Indian food and typical Indian meal patterns and portion sizes

User context:
- Both users' BMR is in the dynamic context block on every message. Use it when estimating deficit, maintenance calories, or advising on intake.
- Karthik's workout style: strength training with 3 sets of 15 reps per exercise variation. Factor this when estimating calories burned for gym sessions.

Behavioural rules:
- When the user mentions eating or drinking ANYTHING, call log_food immediately — estimate calories if not given
- When the user mentions any physical activity or exercise, call log_activity immediately — estimate calories burned
- For date queries ("yesterday", "last Monday", "on the 15th"), resolve to YYYY-MM-DD using the current date/time from the user context block
- Today's food and activity logs are pre-injected in the context block on every message — use that data directly without calling any tools. Only call get_food_log / get_day_summary / get_activity_log when you need entry IDs (for update/delete) or when querying a date other than today.
- Always call get_day_summary or get_food_log when asked about what was eaten on a date other than today
- Keep responses concise and conversational — this is a Telegram chat
- Be supportive and motivating, never preachy or judgmental
- When returning any summary or stats (day, week, or period): don't just echo the numbers back — analyse them. Comment on the pattern (e.g. is net within goal, is protein low, is a meal skipped, did activity offset a heavy day). Give a 2–3 line read on what the numbers actually mean for that person's goal. Numbers alone are useless — the insight is what matters.
- To correct a logged entry ("she burned 200 not 350", "change the rice to 250 cal"): ALWAYS use update_food_entry or update_activity_entry with the specific entry_id. First call get_food_log or get_activity_log to get IDs. NEVER log a new entry to correct an existing one — that adds on top instead of replacing.
- To log food/activity for someone else ("log for Sravya too", "she had the same"): call log_food/log_activity ONCE with for_user set to their name. Do NOT log again for the current user — they are already logged.
- Use request_reasoning when the question requires deep analysis you cannot confidently answer:
  weight trend predictions, multi-variable correlations (sleep vs calories, exercise vs intake),
  forecasting, pattern analysis across weeks, or any complex why/how question about fitness data.
  Do NOT use it for logging, simple lookups, or single-day queries.\
"""

_ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "request_reasoning",
        "description": (
            "Call this when the question requires deep analysis that you cannot answer well: "
            "predictions, trend forecasting, multi-variable correlations, weight trajectory, "
            "sleep-vs-calorie patterns, or any question where you'd be guessing. "
            "Do NOT call this for logging, simple lookups, or straightforward questions."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_SYSTEM_MSG = {"role": "system", "content": SYSTEM_PROMPT}


def _user_ctx(all_users: dict, current_cfg, text: str) -> dict:
    """Wrap user message with per-turn context block — all users' info + who's sending."""
    now = datetime.now(IST).strftime("%A, %B %d, %Y — %I:%M %p IST")
    user_parts = []
    for ucfg in all_users.values():
        latest_w = db.get_weight_log(ucfg.db_prefix, days=1)
        if latest_w:
            w = f"{latest_w[0]['weight_kg']}kg"
        elif ucfg.weight_kg:
            w = f"{ucfg.weight_kg}kg"
        else:
            w = "not set"
        gender = f", {ucfg.gender}" if ucfg.gender else ""
        protein_t = f", protein_target={ucfg.protein_target_g}g" if ucfg.protein_target_g else ""
        bmr_part  = f", BMR={ucfg.bmr}kcal" if ucfg.bmr else ""
        memories = db.get_memories(ucfg.db_prefix)
        mem_str = ("; ".join(m["memory"] for m in memories)) if memories else ""
        mem_part = f", memories: {mem_str}" if mem_str else ""
        user_parts.append(f"{ucfg.name}: weight={w}, goal={ucfg.net_calorie_goal} kcal net{gender}{protein_t}{bmr_part}{mem_part}")
    users_line = " | ".join(user_parts)

    # Pre-inject today's logs for the sending user
    today = db.today_ist()
    food  = db.get_food_log(current_cfg.db_prefix, today)
    acts  = db.get_activity_log(current_cfg.db_prefix, today)
    food_parts = [
        f"{e['food']} {int(e['calories'])}kcal" + (f" {e['protein_g']}g protein" if e['protein_g'] else "")
        for e in food
    ]
    act_parts = [f"{e['activity']} {int(e['calories_burned'])}kcal" for e in acts]
    total_in  = int(sum(e['calories'] for e in food))
    total_out = int(sum(e['calories_burned'] for e in acts))
    net       = total_in - total_out
    sign      = "+" if net >= 0 else ""
    today_line = (
        f"[Today — food: {', '.join(food_parts) or 'none'} | "
        f"activity: {', '.join(act_parts) or 'none'} | net: {sign}{net}kcal]"
    )

    ctx = f"[{users_line}]\n[Sending: {current_cfg.name} | {now}]\n{today_line}"
    return {"role": "user", "content": f"{ctx}\n\n{text}"}


class Agent:
    def __init__(self, api_key: str, user_cfg, all_users: dict, fast_model: str, reasoning_model: str) -> None:
        self.user_cfg = user_cfg
        self._all_users = all_users
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            timeout=60.0,
        )
        self.history: list[dict] = []
        self._day_start: int = 0
        self._last_date: str = db.today_ist()
        self._conv_id = str(uuid.uuid4())  # Stable ID → sticky server routing for prompt cache hits
        self._fast_model = fast_model
        self._reasoning_model = reasoning_model
        self._all_tools = tool_registry.TOOLS + [_ESCALATE_TOOL]

    def _today_history(self) -> list[dict]:
        today = db.today_ist()
        if today != self._last_date:
            self._day_start = len(self.history)
            self._last_date = today
        return self.history[self._day_start:]

    async def respond(self, user_text: str) -> str:
        # Context baked into the user message — system prompt stays 100% static
        self.history.append(_user_ctx(self._all_users, self.user_cfg, user_text))

        model = self._fast_model
        tools_to_use = self._all_tools
        used_reasoning = False

        full_text = ""
        while True:
            messages = [_SYSTEM_MSG] + self._today_history()

            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_to_use,
                tool_choice="auto",
                max_tokens=1024,
                extra_headers={"x-grok-conv-id": self._conv_id},
            )
            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []

            # Escalation check — before touching history
            if any(tc.function.name == "request_reasoning" for tc in tool_calls):
                print(f"[FitBot] [{self.user_cfg.name}] escalating to reasoning model")
                model = self._reasoning_model
                tools_to_use = tool_registry.TOOLS
                used_reasoning = True
                continue  # re-run with reasoning model, same history

            assistant_entry: dict = {"role": "assistant", "content": msg.content}
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ]
            if msg.content:
                full_text += msg.content

            if not tool_calls:
                if msg.content:
                    self.history.append(assistant_entry)
                break

            self.history.append(assistant_entry)

            tool_results = [
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_registry.execute(
                        tc.function.name,
                        json.loads(tc.function.arguments or "{}"),
                        self.user_cfg.db_prefix,
                    ),
                }
                for tc in tool_calls
            ]
            self.history.extend(tool_results)

        text = full_text.strip()
        if not text:
            followup = await self.client.chat.completions.create(
                model=model,
                messages=[_SYSTEM_MSG] + self._today_history(),
                max_tokens=256,
                extra_headers={"x-grok-conv-id": self._conv_id},
            )
            text = (followup.choices[0].message.content or "").strip()
            if text:
                self.history.append({"role": "assistant", "content": text})

        if used_reasoning:
            text += "\n\n_(answered using reasoning model)_"

        return text
