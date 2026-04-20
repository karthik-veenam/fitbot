import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = "fitbot.db"
IST = timezone(timedelta(hours=5, minutes=30))


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init(prefixes: list) -> None:
    with _conn() as conn:
        for p in prefixes:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}_memories (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      TEXT NOT NULL,
                    memory  TEXT NOT NULL,
                    source  TEXT DEFAULT 'reflection'
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}_calories_in (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    date      TEXT NOT NULL,
                    ts        TEXT NOT NULL,
                    food      TEXT NOT NULL,
                    calories  REAL NOT NULL,
                    protein_g REAL,
                    meal_type TEXT DEFAULT 'meal'
                )
            """)
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{p}_cin_date ON {p}_calories_in(date)"
            )
            # migrate: add protein_g if table existed without it
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({p}_calories_in)").fetchall()]
            if "protein_g" not in cols:
                conn.execute(f"ALTER TABLE {p}_calories_in ADD COLUMN protein_g REAL")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}_calories_out (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    date            TEXT NOT NULL,
                    ts              TEXT NOT NULL,
                    activity        TEXT NOT NULL,
                    calories_burned REAL NOT NULL,
                    duration_mins   INTEGER
                )
            """)
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{p}_cout_date ON {p}_calories_out(date)"
            )
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}_weight (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    date      TEXT NOT NULL UNIQUE,
                    ts        TEXT NOT NULL,
                    weight_kg REAL NOT NULL,
                    bmi       REAL,
                    source    TEXT DEFAULT 'fitbit'
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}_net_calories (
                    date         TEXT PRIMARY KEY,
                    calories_in  REAL NOT NULL DEFAULT 0,
                    calories_out REAL NOT NULL DEFAULT 0,
                    net          REAL NOT NULL DEFAULT 0
                )
            """)
            # Triggers: auto-update net_calories on insert/delete/update to either source table
            trigger_specs = [
                (f"{p}_calories_in",  "INSERT", "NEW.date"),
                (f"{p}_calories_in",  "DELETE", "OLD.date"),
                (f"{p}_calories_in",  "UPDATE", "NEW.date"),
                (f"{p}_calories_out", "INSERT", "NEW.date"),
                (f"{p}_calories_out", "DELETE", "OLD.date"),
                (f"{p}_calories_out", "UPDATE", "NEW.date"),
            ]
            for tbl, event, ref in trigger_specs:
                side = "cin" if "_in" in tbl else "cout"
                name = f"trg_{p}_{side}_{event.lower()}"
                conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {name}
                    AFTER {event} ON {tbl}
                    BEGIN
                        INSERT INTO {p}_net_calories (date, calories_in, calories_out, net)
                        VALUES (
                            {ref},
                            COALESCE((SELECT SUM(calories)        FROM {p}_calories_in  WHERE date = {ref}), 0),
                            COALESCE((SELECT SUM(calories_burned) FROM {p}_calories_out WHERE date = {ref}), 0),
                            COALESCE((SELECT SUM(calories)        FROM {p}_calories_in  WHERE date = {ref}), 0) -
                            COALESCE((SELECT SUM(calories_burned) FROM {p}_calories_out WHERE date = {ref}), 0)
                        )
                        ON CONFLICT(date) DO UPDATE SET
                            calories_in  = excluded.calories_in,
                            calories_out = excluded.calories_out,
                            net          = excluded.net;
                    END
                """)
        conn.commit()


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> str:
    return now_ist().strftime("%Y-%m-%d")


def log_food(prefix: str, food: str, calories: float,
             protein_g: float = None, meal_type: str = "meal", date: str = None) -> int:
    now = now_ist()
    d = date or now.strftime("%Y-%m-%d")
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO {prefix}_calories_in (date, ts, food, calories, protein_g, meal_type) "
            f"VALUES (?, ?, ?, ?, ?, ?)",
            (d, now.isoformat(), food, calories, protein_g, meal_type)
        )
        conn.commit()
        return cur.lastrowid


def log_activity(prefix: str, activity: str, calories_burned: float,
                 duration_mins: int = None, date: str = None) -> int:
    now = now_ist()
    d = date or now.strftime("%Y-%m-%d")
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO {prefix}_calories_out (date, ts, activity, calories_burned, duration_mins) "
            f"VALUES (?, ?, ?, ?, ?)",
            (d, now.isoformat(), activity, calories_burned, duration_mins)
        )
        conn.commit()
        return cur.lastrowid


def get_food_log(prefix: str, date: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, food, calories, protein_g, meal_type FROM {prefix}_calories_in "
            f"WHERE date = ? ORDER BY ts",
            (date,)
        ).fetchall()
    return [{"id": r[0], "food": r[1], "calories": r[2], "protein_g": r[3], "meal_type": r[4]} for r in rows]


def get_activity_log(prefix: str, date: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, activity, calories_burned, duration_mins FROM {prefix}_calories_out "
            f"WHERE date = ? ORDER BY ts",
            (date,)
        ).fetchall()
    return [{"id": r[0], "activity": r[1], "calories_burned": r[2], "duration_mins": r[3]} for r in rows]


def get_week_summary(prefix: str, days: int = 7) -> list:
    today = today_ist()
    lookback = f"-{days - 1} days"
    with _conn() as conn:
        in_rows = conn.execute(
            f"SELECT date, SUM(calories) FROM {prefix}_calories_in "
            f"WHERE date >= date(?, ?) GROUP BY date ORDER BY date",
            (today, lookback)
        ).fetchall()
        out_rows = conn.execute(
            f"SELECT date, SUM(calories_burned) FROM {prefix}_calories_out "
            f"WHERE date >= date(?, ?) GROUP BY date ORDER BY date",
            (today, lookback)
        ).fetchall()
    in_map = {r[0]: r[1] or 0 for r in in_rows}
    out_map = {r[0]: r[1] or 0 for r in out_rows}
    all_dates = sorted(set(list(in_map) + list(out_map)))
    return [
        {
            "date": d,
            "calories_in": round(in_map.get(d, 0), 1),
            "calories_burned": round(out_map.get(d, 0), 1),
            "net": round(in_map.get(d, 0) - out_map.get(d, 0), 1),
        }
        for d in all_dates
    ]


def update_food_entry(prefix: str, entry_id: int, food: str = None,
                      calories: float = None, protein_g: float = None,
                      meal_type: str = None) -> bool:
    sets, vals = [], []
    if food is not None:
        sets.append("food = ?"); vals.append(food)
    if calories is not None:
        sets.append("calories = ?"); vals.append(calories)
    if protein_g is not None:
        sets.append("protein_g = ?"); vals.append(protein_g)
    if meal_type is not None:
        sets.append("meal_type = ?"); vals.append(meal_type)
    if not sets:
        return False
    vals.append(entry_id)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE {prefix}_calories_in SET {', '.join(sets)} WHERE id = ?", vals
        )
        conn.commit()
    return cur.rowcount > 0


def update_activity_entry(prefix: str, entry_id: int, activity: str = None,
                          calories_burned: float = None, duration_mins: int = None) -> bool:
    sets, vals = [], []
    if activity is not None:
        sets.append("activity = ?"); vals.append(activity)
    if calories_burned is not None:
        sets.append("calories_burned = ?"); vals.append(calories_burned)
    if duration_mins is not None:
        sets.append("duration_mins = ?"); vals.append(duration_mins)
    if not sets:
        return False
    vals.append(entry_id)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE {prefix}_calories_out SET {', '.join(sets)} WHERE id = ?", vals
        )
        conn.commit()
    return cur.rowcount > 0


def delete_food_by_id(prefix: str, entry_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(f"DELETE FROM {prefix}_calories_in WHERE id = ?", (entry_id,))
        conn.commit()
    return cur.rowcount > 0


def delete_activity_by_id(prefix: str, entry_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(f"DELETE FROM {prefix}_calories_out WHERE id = ?", (entry_id,))
        conn.commit()
    return cur.rowcount > 0


def delete_last_food(prefix: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT id FROM {prefix}_calories_in ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return False
        conn.execute(f"DELETE FROM {prefix}_calories_in WHERE id = ?", (row[0],))
        conn.commit()
    return True


def delete_last_activity(prefix: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT id FROM {prefix}_calories_out ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return False
        conn.execute(f"DELETE FROM {prefix}_calories_out WHERE id = ?", (row[0],))
        conn.commit()
    return True


def weight_pulled_today(prefix: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT id FROM {prefix}_weight WHERE date = ?", (today_ist(),)
        ).fetchone()
    return row is not None


def log_weight(prefix: str, weight_kg: float, bmi: float = None, source: str = "fitbit") -> None:
    now = now_ist()
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO {prefix}_weight (date, ts, weight_kg, bmi, source) VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(date) DO UPDATE SET ts=excluded.ts, weight_kg=excluded.weight_kg, "
            f"bmi=excluded.bmi, source=excluded.source",
            (now.strftime("%Y-%m-%d"), now.isoformat(), weight_kg, bmi, source)
        )
        conn.commit()


def get_weight_log(prefix: str, days: int = 30) -> list:
    today = today_ist()
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT date, weight_kg, bmi, source FROM {prefix}_weight "
            f"WHERE date >= date(?, ? || ' days') ORDER BY date DESC",
            (today, f"-{days - 1}")
        ).fetchall()
    return [{"date": r[0], "weight_kg": r[1], "bmi": r[2], "source": r[3]} for r in rows]


def save_memory(prefix: str, memory: str, source: str = "manual") -> int:
    now = now_ist()
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO {prefix}_memories (ts, memory, source) VALUES (?, ?, ?)",
            (now.isoformat(), memory.strip(), source)
        )
        conn.commit()
        return cur.lastrowid


def get_memories(prefix: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, memory, source, ts FROM {prefix}_memories ORDER BY id"
        ).fetchall()
    return [{"id": r[0], "memory": r[1], "source": r[2], "ts": r[3]} for r in rows]


def delete_memory(prefix: str, memory_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(f"DELETE FROM {prefix}_memories WHERE id = ?", (memory_id,))
        conn.commit()
    return cur.rowcount > 0


def get_net_log(prefix: str, days: int = 7) -> list:
    today = today_ist()
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT date, calories_in, calories_out, net FROM {prefix}_net_calories "
            f"WHERE date >= date(?, ? || ' days') ORDER BY date",
            (today, f"-{days - 1}")
        ).fetchall()
    return [
        {"date": r[0], "calories_in": round(r[1], 1),
         "calories_out": round(r[2], 1), "net": round(r[3], 1)}
        for r in rows
    ]
