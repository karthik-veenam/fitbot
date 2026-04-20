import json
from dataclasses import dataclass
from typing import Dict


@dataclass
class UserConfig:
    name: str
    db_prefix: str
    net_calorie_goal: int
    weight_kg: float = 0.0
    gender: str = ""
    protein_target_g: int = 0
    weight_goal_kg: float = 0.0
    bmr: int = 2000
    fitbit_access_token: str = ""
    fitbit_refresh_token: str = ""
    is_admin: bool = False
    group: str = "default"


@dataclass
class Config:
    telegram_bot_token: str
    xai_api_key: str
    telegram_users: Dict[str, UserConfig]
    fast_model: str = "grok-4.20-0309-non-reasoning"
    reasoning_model: str = "grok-4-1-fast-reasoning"
    fitbit_client_id: str = ""
    fitbit_client_secret: str = ""

    @classmethod
    def load(cls, path: str = "config.json") -> "Config":
        with open(path) as f:
            data = json.load(f)
        users = {
            cid: UserConfig(**u)
            for cid, u in data["telegram_users"].items()
        }
        return cls(
            telegram_bot_token=data["telegram_bot_token"],
            xai_api_key=data["xai_api_key"],
            telegram_users=users,
            fast_model=data.get("fast_model", "grok-4.20-0309-non-reasoning"),
            reasoning_model=data.get("reasoning_model", "grok-4-1-fast-reasoning"),
            fitbit_client_id=data.get("fitbit_client_id", ""),
            fitbit_client_secret=data.get("fitbit_client_secret", ""),
        )
