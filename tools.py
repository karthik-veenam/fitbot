import json
import db
import fitbit as fitbit_client

_cfg = None


def set_config(cfg) -> None:
    global _cfg
    _cfg = cfg


def _try_pull_fitbit_weight(user_prefix: str) -> None:
    if not _cfg:
        return
    user_cfg = next(
        (u for u in _cfg.telegram_users.values() if u.db_prefix == user_prefix), None
    )
    if not user_cfg or not user_cfg.fitbit_refresh_token:
        return
    try:
        w, bmi, new_at, new_rt = fitbit_client.fetch_today_weight(
            _cfg.fitbit_client_id, _cfg.fitbit_client_secret,
            user_cfg.fitbit_access_token, user_cfg.fitbit_refresh_token,
        )
        db.log_weight(user_prefix, w, bmi, "fitbit")
        fitbit_client.save_tokens("config.json", user_cfg.name, new_at, new_rt)
        # Update in-memory tokens so subsequent calls in same session work
        user_cfg.fitbit_access_token = new_at
        user_cfg.fitbit_refresh_token = new_rt
        print(f"[FitBot] Fitbit weight pulled for {user_prefix}: {w}kg")
    except Exception as e:
        print(f"[FitBot] Fitbit pull failed for {user_prefix}: {e}")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "log_food",
            "description": (
                "Log a food or drink entry for the user. Call this whenever the user mentions eating "
                "or drinking anything. Use your nutrition knowledge to estimate calories if not given. "
                "Be accurate with Indian food portions — e.g. 1 medium idli ~75 kcal, 1 dosa ~160 kcal, "
                "1 chapati ~100 kcal, 1 cup cooked rice ~200 kcal, 1 cup dal ~150 kcal. "
                "If the user asks to log food for someone else (e.g. 'log for Sravya too'), set for_user to that person's name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "food": {"type": "string", "description": "Name and description of the food/drink"},
                    "calories": {"type": "number", "description": "Calorie count (estimate if not given)"},
                    "meal_type": {
                        "type": "string",
                        "enum": ["breakfast", "lunch", "dinner", "snack", "meal"],
                        "description": "Meal type",
                    },
                    "protein_g": {
                        "type": "number",
                        "description": "Protein in grams. Use your knowledge of food composition to estimate if not given.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date as YYYY-MM-DD. Omit to use today.",
                    },
                    "for_user": {
                        "type": "string",
                        "description": "Log for a different user by name (e.g. 'Sravya'). Omit to log for the current user.",
                    },
                },
                "required": ["food", "calories", "protein_g"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_activity",
            "description": (
                "Log a physical activity or exercise. Call this whenever the user mentions working out, "
                "exercising, walking, running, playing sports, or any physical activity. "
                "Estimate calories burned based on typical values for the activity type and duration. "
                "If the user asks to log activity for someone else, set for_user to that person's name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "activity": {"type": "string", "description": "Name and description of the activity"},
                    "calories_burned": {"type": "number", "description": "Estimated calories burned"},
                    "duration_mins": {
                        "type": "integer",
                        "description": "Duration in minutes (if mentioned)",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date as YYYY-MM-DD. Omit to use today.",
                    },
                    "for_user": {
                        "type": "string",
                        "description": "Log for a different user by name (e.g. 'Sravya'). Omit to log for the current user.",
                    },
                },
                "required": ["activity", "calories_burned"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_day_summary",
            "description": (
                "Get a complete summary for a given day: all food entries, all activity entries, "
                "total calories in, total calories burned, and net calories vs goal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date as YYYY-MM-DD. Omit to use today."},
                    "for_user": {"type": "string", "description": "Get data for a different user by name (e.g. 'Sravya'). Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_food_log",
            "description": "Get all food entries logged for a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date as YYYY-MM-DD. Omit to use today."},
                    "for_user": {"type": "string", "description": "Get data for a different user by name. Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity_log",
            "description": "Get all exercise/activity entries logged for a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date as YYYY-MM-DD. Omit to use today."},
                    "for_user": {"type": "string", "description": "Get data for a different user by name. Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_week_summary",
            "description": "Get daily calorie totals (calories in, burned, net) for the last 7 days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "for_user": {"type": "string", "description": "Get data for a different user by name. Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_net_log",
            "description": (
                "Read the pre-computed net calories table — shows daily calories_in, calories_out, "
                "and net for the last N days. Use this for quick balance overviews."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of past days to return (default 7)."},
                    "for_user": {"type": "string", "description": "Get data for a different user by name. Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weight_log",
            "description": "Get the user's weight history from the weight table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of past days to return (default 30)."},
                    "for_user": {"type": "string", "description": "Get data for a different user by name. Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pull_fitbit_weight",
            "description": (
                "Manually pull today's weight from Fitbit and store it. "
                "Use this when the user asks to sync weight, weight is missing, "
                "or explicitly asks to pull from Fitbit."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_target",
            "description": (
                "Update a user's personal target. Call when the user says things like "
                "'set my protein target to 150g', 'change my calorie goal to 1800', "
                "'my protein goal is 120g'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["protein_g", "calories", "weight_goal", "bmr"],
                        "description": "'protein_g' for daily protein target, 'calories' for net calorie goal, 'weight_goal' for target body weight in kg, 'bmr' for basal metabolic rate in kcal/day",
                    },
                    "value": {"type": "number", "description": "The new target value"},
                    "for_user": {"type": "string", "description": "Set for a different user by name. Omit for current user."},
                },
                "required": ["target", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_deficit_summary",
            "description": (
                "Compute accumulated calorie deficit, fat loss equivalent, and daily breakdown "
                "for a given period. Call when the user asks about deficit, fat burned, calories "
                "below maintenance, 'how much fat have I lost', 'what's my deficit this week', etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of past days to cover (default 7). Use 30 for 'this month'.",
                        "default": 7,
                    },
                    "for_user": {
                        "type": "string",
                        "description": "Query for a different user by name. Omit for current user.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Save a long-term memory about the user that persists across conversations. "
                "Call this when the user says 'remember', 'note that', 'always', 'I always', "
                "'I never', 'I prefer', or states any personal preference or habit worth keeping."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory": {
                        "type": "string",
                        "description": "Short factual statement about the user (e.g. 'dislikes skipping breakfast', 'prefers dal over sambhar')",
                    },
                    "for_user": {
                        "type": "string",
                        "description": "Save memory for a different user by name. Omit for current user.",
                    },
                },
                "required": ["memory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": "Delete a saved memory by its ID. Call get_memories first to find the ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "integer", "description": "ID of the memory to delete"},
                },
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memories",
            "description": "List all saved long-term memories for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "for_user": {"type": "string", "description": "Get memories for a different user. Omit for current user."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_activity_entry",
            "description": (
                "Update a specific activity entry by its ID. Use this to correct calories_burned, "
                "duration, or activity name. Always call get_activity_log first to get entry IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "integer", "description": "The ID of the activity entry to update"},
                    "activity": {"type": "string", "description": "Updated activity name (omit to keep unchanged)"},
                    "calories_burned": {"type": "number", "description": "Updated calories burned (omit to keep unchanged)"},
                    "duration_mins": {"type": "integer", "description": "Updated duration in minutes (omit to keep unchanged)"},
                },
                "required": ["entry_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_food_entry",
            "description": (
                "Update a specific food entry by its ID. Use this to correct calories or food name "
                "for a specific logged entry. Always call get_food_log first to get the entry IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "integer", "description": "The ID of the food entry to update"},
                    "food": {"type": "string", "description": "Updated food name/description (omit to keep unchanged)"},
                    "calories": {"type": "number", "description": "Updated calorie count (omit to keep unchanged)"},
                    "protein_g": {"type": "number", "description": "Updated protein in grams (omit to keep unchanged)"},
                    "meal_type": {
                        "type": "string",
                        "enum": ["breakfast", "lunch", "dinner", "snack", "meal"],
                        "description": "Updated meal type (omit to keep unchanged)",
                    },
                },
                "required": ["entry_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_last_entry",
            "description": (
                "Delete a food or activity entry. Provide entry_id to delete a specific entry by ID "
                "(preferred — always get IDs via get_food_log first). "
                "Omit entry_id to delete the most recently logged entry of that type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_type": {
                        "type": "string",
                        "enum": ["food", "activity"],
                        "description": "Whether to delete a food entry or activity entry",
                    },
                    "entry_id": {
                        "type": "integer",
                        "description": "ID of the specific entry to delete. Omit to delete the last entry.",
                    },
                },
                "required": ["entry_type"],
            },
        },
    },
]


def _resolve_prefix(for_user: str | None, default_prefix: str) -> str:
    """Resolve a user name to their db_prefix, or return default_prefix."""
    if not for_user or not _cfg:
        return default_prefix
    name_lower = for_user.strip().lower()
    for u in _cfg.telegram_users.values():
        if u.name.lower() == name_lower:
            return u.db_prefix
    return default_prefix


def execute(name: str, args: dict, user_prefix: str) -> str:
    today = db.today_ist()
    try:
        if name == "log_food":
            date = args.get("date") or today
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            row_id = db.log_food(
                target, args["food"], float(args["calories"]),
                float(args["protein_g"]) if args.get("protein_g") is not None else None,
                args.get("meal_type", "meal"), date
            )
            if not db.weight_pulled_today(target):
                _try_pull_fitbit_weight(target)
            return json.dumps({"success": True, "id": row_id, "food": args["food"],
                               "calories": args["calories"], "date": date,
                               "logged_for": target})

        if name == "log_activity":
            date = args.get("date") or today
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            row_id = db.log_activity(
                target, args["activity"], float(args["calories_burned"]),
                args.get("duration_mins"), date
            )
            if not db.weight_pulled_today(target):
                _try_pull_fitbit_weight(target)
            return json.dumps({"success": True, "id": row_id, "activity": args["activity"],
                               "calories_burned": args["calories_burned"], "date": date,
                               "logged_for": target})

        if name == "get_day_summary":
            date = args.get("date") or today
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            food = db.get_food_log(target, date)
            activities = db.get_activity_log(target, date)
            total_in = sum(f["calories"] for f in food)
            total_out = sum(a["calories_burned"] for a in activities)
            return json.dumps({
                "date": date,
                "user": target,
                "total_calories_in": round(total_in, 1),
                "total_calories_burned": round(total_out, 1),
                "net_calories": round(total_in - total_out, 1),
                "food_entries": food,
                "activity_entries": activities,
            })

        if name == "get_food_log":
            date = args.get("date") or today
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            entries = db.get_food_log(target, date)
            return json.dumps({"date": date, "user": target, "entries": entries,
                               "total_calories": round(sum(e["calories"] for e in entries), 1)})

        if name == "get_activity_log":
            date = args.get("date") or today
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            entries = db.get_activity_log(target, date)
            return json.dumps({"date": date, "user": target, "entries": entries,
                               "total_burned": round(sum(e["calories_burned"] for e in entries), 1)})

        if name == "get_week_summary":
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            return json.dumps({"user": target, "weekly_summary": db.get_week_summary(target)})

        if name == "get_net_log":
            days = int(args.get("days") or 7)
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            return json.dumps({"user": target, "net_log": db.get_net_log(target, days)})

        if name == "get_weight_log":
            days = int(args.get("days") or 30)
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            return json.dumps({"user": target, "weight_log": db.get_weight_log(target, days)})

        if name == "pull_fitbit_weight":
            _try_pull_fitbit_weight(user_prefix)
            latest = db.get_weight_log(user_prefix, days=1)
            if latest:
                return json.dumps({"success": True, "weight_kg": latest[0]["weight_kg"],
                                   "bmi": latest[0]["bmi"], "date": latest[0]["date"]})
            return json.dumps({"success": False, "error": "No weight data pulled"})

        if name == "set_target":
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            user_cfg = next((u for u in _cfg.telegram_users.values() if u.db_prefix == target), None) if _cfg else None
            if not user_cfg:
                return json.dumps({"error": "User not found"})
            value = float(args["value"])
            if args["target"] == "protein_g":
                user_cfg.protein_target_g = int(value)
            elif args["target"] == "weight_goal":
                user_cfg.weight_goal_kg = value
            elif args["target"] == "bmr":
                user_cfg.bmr = int(value)
            else:
                user_cfg.net_calorie_goal = int(value)
            # Persist to config.json
            import json as _json
            with open("config.json") as f:
                cfg_data = _json.load(f)
            for cid, u in cfg_data["telegram_users"].items():
                if u.get("name") == user_cfg.name:
                    if args["target"] == "protein_g":
                        u["protein_target_g"] = int(value)
                    elif args["target"] == "weight_goal":
                        u["weight_goal_kg"] = value
                    elif args["target"] == "bmr":
                        u["bmr"] = int(value)
                    else:
                        u["net_calorie_goal"] = int(value)
                    break
            with open("config.json", "w") as f:
                _json.dump(cfg_data, f, indent=2)
            return json.dumps({"success": True, "target": args["target"], "value": value})

        if name == "get_deficit_summary":
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            days = int(args.get("days", 7))
            user_cfg = next((u for u in _cfg.telegram_users.values() if u.db_prefix == target), None) if _cfg else None
            bmr = user_cfg.bmr if user_cfg else 2000
            rows = db.get_week_summary(target, days=days)
            daily = []
            for r in rows:
                deficit = round(max(0, bmr - r["net"]), 1)
                daily.append({"date": r["date"], "net": r["net"], "deficit": deficit})
            accumulated = round(sum(d["deficit"] for d in daily))
            fat_kg = round(accumulated / 7700, 3)
            full_kgs = int(accumulated // 7700)
            to_next_kg = round(7700 - (accumulated % 7700))
            return json.dumps({
                "bmr": bmr, "days": days,
                "accumulated_deficit_kcal": accumulated,
                "fat_loss_kg": fat_kg,
                "full_kg_milestones": full_kgs,
                "kcal_to_next_kg": to_next_kg,
                "daily": daily,
            })

        if name == "save_memory":
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            mid = db.save_memory(target, args["memory"], source="manual")
            return json.dumps({"success": True, "id": mid, "memory": args["memory"]})

        if name == "forget_memory":
            success = db.delete_memory(user_prefix, int(args["memory_id"]))
            return json.dumps({"success": success})

        if name == "get_memories":
            target = _resolve_prefix(args.get("for_user"), user_prefix)
            return json.dumps({"memories": db.get_memories(target)})

        if name == "update_activity_entry":
            success = db.update_activity_entry(
                user_prefix, int(args["entry_id"]),
                args.get("activity"),
                float(args["calories_burned"]) if args.get("calories_burned") is not None else None,
                int(args["duration_mins"]) if args.get("duration_mins") is not None else None,
            )
            return json.dumps({"success": success, "entry_id": args["entry_id"]})

        if name == "update_food_entry":
            success = db.update_food_entry(
                user_prefix, int(args["entry_id"]),
                args.get("food"),
                float(args["calories"]) if args.get("calories") is not None else None,
                float(args["protein_g"]) if args.get("protein_g") is not None else None,
                args.get("meal_type"),
            )
            return json.dumps({"success": success, "entry_id": args["entry_id"]})

        if name == "delete_last_entry":
            entry_type = args["entry_type"]
            entry_id = args.get("entry_id")
            if entry_id is not None:
                success = (
                    db.delete_food_by_id(user_prefix, int(entry_id))
                    if entry_type == "food"
                    else db.delete_activity_by_id(user_prefix, int(entry_id))
                )
            else:
                success = (
                    db.delete_last_food(user_prefix)
                    if entry_type == "food"
                    else db.delete_last_activity(user_prefix)
                )
            return json.dumps({"success": success, "deleted": entry_type, "entry_id": entry_id})

        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})
