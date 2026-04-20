"""Fast-path handler for simple stat queries — bypasses LLM entirely."""
import html
import re
import db

_FOOTER = "\n\n<i>fetched directly from database</i>"

_BAIL_DATES = re.compile(
    r"\b(yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|last\s+\w+|on\s+the|\d{1,2}[/-]\d{1,2}|\d{4}-\d{2}-\d{2})\b", re.I
)
_BAIL_INTENT = re.compile(
    r"\b(ate|had|eat|drink|drank|log|logged|delete|remove|undo|update|change"
    r"|fix|add|burn|burned|played|did|exercise|pull|set)\b", re.I
)

_PAT_FULL = re.compile(
    r"(today.{0,10}(summary|stats|overview|full)|"
    r"(summary|stats|overview).{0,10}today|"
    r"how\s+(am\s+i|i\s+am|i'?m)\s+doing|"
    r"(my\s+)?(full\s+)?summary(\s+for\s+today)?)",
    re.I
)
_PAT_FOOD = re.compile(
    r"(what.{0,10}(i\s+eat|eaten).{0,10}today|"
    r"food.{0,10}today|today.{0,10}food|"
    r"today.{0,10}calorie|calorie.{0,10}today|"
    r"what.{0,5}i.{0,5}eat(\s+today)?)",
    re.I
)
_PAT_ACTIVITY = re.compile(
    r"(activity.{0,10}today|today.{0,10}activity|"
    r"workout.{0,10}today|today.{0,10}workout|"
    r"exercise.{0,10}today|today.{0,10}exercise)",
    re.I
)
_PAT_PROTEIN = re.compile(
    r"(protein.{0,10}today|today.{0,10}protein|"
    r"how\s+much\s+protein|protein\s+(so\s+far|left|remaining|target|intake)|"
    r"(hit|reach|meet).{0,10}protein)",
    re.I
)
_PAT_WEEK = re.compile(
    r"(week.{0,10}(summary|stats|overview)|"
    r"(summary|stats|overview).{0,10}week|"
    r"7.?day|last\s+7|past\s+week|this\s+week)",
    re.I
)


def _bail(text: str, user_cfg, all_users: dict) -> bool:
    other_names = [u.name for u in all_users.values() if u.db_prefix != user_cfg.db_prefix]
    for name in other_names:
        if re.search(rf"\b{re.escape(name)}\b", text, re.I):
            return True
    return bool(_BAIL_DATES.search(text))


def _col(val: str, width: int, right: bool = False) -> str:
    s = str(val)[:width]
    return s.rjust(width) if right else s.ljust(width)


def _food_table(entries: list) -> str:
    if not entries:
        return "  Nothing logged yet."
    has_protein = any(e["protein_g"] is not None for e in entries)
    rows = []
    for e in entries:
        name = html.escape(e["food"])[:32]
        kcal = str(int(e["calories"]))
        p = f"{e['protein_g']}g" if has_protein and e["protein_g"] is not None else ("—" if has_protein else "")
        mtype = e["meal_type"]
        if has_protein:
            rows.append(f"{name:<32}  {kcal:>5} kcal  {p:>6}  {mtype}")
        else:
            rows.append(f"{name:<32}  {kcal:>5} kcal  {mtype}")
    if has_protein:
        header = f"{'Food':<32}  {'kcal':>5}        {'prot':>6}  type"
        sep    = f"{'':-<32}  {'':-^5}        {'':-^6}  {'':-<6}"
    else:
        header = f"{'Food':<32}  {'kcal':>5}        type"
        sep    = f"{'':-<32}  {'':-^5}        {'':-<6}"
    return "<pre>" + "\n".join([header, sep] + rows) + "</pre>"


def _activity_table(entries: list) -> str:
    if not entries:
        return "  No activity logged yet."
    rows = []
    for e in entries:
        name = html.escape(e["activity"])[:30]
        kcal = str(int(e["calories_burned"]))
        dur  = f"{e['duration_mins']} min" if e["duration_mins"] else "—"
        rows.append(f"{name:<30}  {kcal:>5} kcal  {dur}")
    header = f"{'Activity':<30}  {'kcal':>5}        dur"
    sep    = f"{'':-<30}  {'':-^5}        {'':-<6}"
    return "<pre>" + "\n".join([header, sep] + rows) + "</pre>"


def _handle_full(user_cfg) -> str:
    today = db.today_ist()
    food  = db.get_food_log(user_cfg.db_prefix, today)
    acts  = db.get_activity_log(user_cfg.db_prefix, today)

    total_in  = round(sum(e["calories"] for e in food))
    total_out = round(sum(e["calories_burned"] for e in acts))
    net       = total_in - total_out
    goal      = user_cfg.net_calorie_goal
    has_p     = any(e["protein_g"] is not None for e in food)
    total_p   = round(sum(e["protein_g"] for e in food if e["protein_g"] is not None), 1)

    p_str = f", {total_p}g protein" if has_p and total_p else ""
    sign  = "+" if net >= 0 else ""

    lines = [
        f"<b>{today} — Today</b>",
        "",
        f"<b>Food — {total_in} kcal{p_str}</b>",
        _food_table(food),
        "",
        f"<b>Activity — {total_out} kcal burned</b>",
        _activity_table(acts),
        "",
        f"Net: <b>{sign}{net} kcal</b>  (goal: {goal} kcal net)",
    ]
    return "\n".join(lines) + _FOOTER


def _handle_food(user_cfg) -> str:
    today = db.today_ist()
    food  = db.get_food_log(user_cfg.db_prefix, today)
    total = round(sum(e["calories"] for e in food))
    has_p = any(e["protein_g"] is not None for e in food)
    total_p = round(sum(e["protein_g"] for e in food if e["protein_g"] is not None), 1)
    p_str = f", {total_p}g protein" if has_p and total_p else ""
    lines = [
        f"<b>{today} — Food log</b>",
        "",
        _food_table(food),
        f"\nTotal: <b>{total} kcal{p_str}</b>",
    ]
    return "\n".join(lines) + _FOOTER


def _handle_activity(user_cfg) -> str:
    today = db.today_ist()
    acts  = db.get_activity_log(user_cfg.db_prefix, today)
    total = round(sum(e["calories_burned"] for e in acts))
    lines = [
        f"<b>{today} — Activity</b>",
        "",
        _activity_table(acts),
        f"\nTotal burned: <b>{total} kcal</b>",
    ]
    return "\n".join(lines) + _FOOTER


def _handle_protein(user_cfg) -> str:
    today = db.today_ist()
    food  = db.get_food_log(user_cfg.db_prefix, today)
    consumed = round(sum(e["protein_g"] for e in food if e["protein_g"] is not None), 1)
    target   = getattr(user_cfg, "protein_target_g", 0)

    rows = [f"  • {html.escape(e['food'])}: {e['protein_g']}g" for e in food if e["protein_g"] is not None]
    breakdown = "<pre>" + "\n".join(rows) + "</pre>" if rows else "  No food logged yet."

    lines = [f"<b>{today} — Protein</b>", "", breakdown, ""]
    if target:
        remaining = round(target - consumed, 1)
        pct = int(consumed / target * 100)
        status = "Target hit!" if remaining <= 0 else f"{remaining}g to go"
        lines.append(f"Consumed: <b>{consumed}g</b> / {target}g target ({pct}%) — {status}")
    else:
        lines.append(f"Consumed: <b>{consumed}g</b> (no target set)")

    return "\n".join(lines) + _FOOTER


def _handle_week(user_cfg) -> str:
    rows = db.get_week_summary(user_cfg.db_prefix)
    if not rows:
        return "No data logged in the last 7 days." + _FOOTER
    table_rows = []
    for r in rows:
        net  = int(r["net"])
        sign = "+" if net >= 0 else ""
        table_rows.append(
            f"{r['date']}  {int(r['calories_in']):>6}  {int(r['calories_burned']):>6}  {sign}{net:>6}"
        )
    header = f"{'Date':<10}  {'in':>6}  {'out':>6}  {'net':>7}"
    sep    = f"{'':-<10}  {'':-^6}  {'':-^6}  {'':-^7}"
    table  = "<pre>" + "\n".join([header, sep] + table_rows) + "</pre>"
    return f"<b>7-day summary</b>\n\n{table}" + _FOOTER


def try_handle(text: str, user_cfg, all_users: dict) -> str | None:
    if re.search(r"\bai\b", text, re.I):
        return None
    if _bail(text, user_cfg, all_users):
        return None
    if _PAT_PROTEIN.search(text) and not _BAIL_INTENT.search(text):
        return _handle_protein(user_cfg)
    if _PAT_WEEK.search(text) and not _BAIL_INTENT.search(text):
        return _handle_week(user_cfg)
    if _PAT_FULL.search(text) and not _BAIL_INTENT.search(text):
        return _handle_full(user_cfg)
    if _PAT_FOOD.search(text) and not _BAIL_INTENT.search(text):
        return _handle_food(user_cfg)
    if _PAT_ACTIVITY.search(text) and not _BAIL_INTENT.search(text):
        return _handle_activity(user_cfg)
    return None
