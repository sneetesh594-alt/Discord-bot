"""Per-server XP policy. Defaults are intentionally independent of moderation."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class LevelingConfig:
    enabled: bool = False
    activity_enabled: bool = True
    public_leaderboard_channel_id: int | None = None
    text_min_xp: int = 15
    text_max_xp: int = 25
    text_cooldown_seconds: int = 60
    min_characters: int = 5
    duplicate_window_seconds: int = 300
    text_daily_cap: int = 5000
    voice_enabled: bool = False
    voice_xp_per_minute: int = 5
    voice_min_members: int = 2
    voice_daily_cap: int = 600
    reaction_enabled: bool = False
    reaction_xp: int = 2
    reaction_cooldown_seconds: int = 60
    reaction_daily_cap: int = 50
    announcement_channel_id: int | None = None
    announcements: bool = True
    announce_every: int = 1
    ignored_channel_ids: tuple[int, ...] = ()
    ignored_role_ids: tuple[int, ...] = ()
    reward_roles: dict[str, int] = field(default_factory=dict)
    stack_rewards: bool = True
    role_multipliers: dict[str, int] = field(default_factory=dict)
    channel_multipliers: dict[str, int] = field(default_factory=dict)
    bonus_percent: int = 100
    bonus_until: int = 0

    def __post_init__(self):
        for name in (
            "enabled",
            "activity_enabled",
            "voice_enabled",
            "reaction_enabled",
            "announcements",
            "stack_rewards",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        limits = {
            "text_min_xp": (1, 1000),
            "text_max_xp": (1, 1000),
            "text_cooldown_seconds": (15, 3600),
            "min_characters": (1, 100),
            "duplicate_window_seconds": (60, 3600),
            "text_daily_cap": (1, 100000),
            "voice_xp_per_minute": (1, 100),
            "voice_min_members": (2, 25),
            "voice_daily_cap": (1, 100000),
            "reaction_xp": (1, 100),
            "reaction_cooldown_seconds": (15, 3600),
            "reaction_daily_cap": (1, 10000),
            "announce_every": (1, 100),
            "bonus_percent": (100, 500),
            "bonus_until": (0, 9999999999),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        if self.text_min_xp > self.text_max_xp:
            raise ValueError("Minimum text XP must not exceed maximum text XP")
        if self.announcement_channel_id is not None and (
            type(self.announcement_channel_id) is not int or self.announcement_channel_id <= 0
        ):
            raise ValueError("Announcement channel must be a positive ID or null")
        if self.public_leaderboard_channel_id is not None and (
            type(self.public_leaderboard_channel_id) is not int
            or self.public_leaderboard_channel_id <= 0
        ):
            raise ValueError("Leaderboard channel must be a positive ID or null")
        for name in ("ignored_channel_ids", "ignored_role_ids"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or len(values) > 200:
                raise ValueError(f"{name} must contain at most 200 IDs")
            if any(type(value) is not int or value <= 0 for value in values):
                raise ValueError(f"{name} must contain positive integer IDs")
            object.__setattr__(self, name, tuple(values))
        for name in ("reward_roles", "role_multipliers", "channel_multipliers"):
            values = getattr(self, name)
            if not isinstance(values, dict) or len(values) > 50:
                raise ValueError(f"{name} must be a mapping with at most 50 entries")
            for key, value in values.items():
                if not isinstance(key, str) or not key.isdigit() or int(key) <= 0:
                    raise ValueError(f"{name} keys must be positive numeric strings")
                if type(value) is not int or value <= 0:
                    raise ValueError(f"{name} values must be positive integers")
                if name == "reward_roles" and int(key) > 1000:
                    raise ValueError("Reward levels must not exceed 1000")
                if name != "reward_roles" and not 50 <= value <= 500:
                    raise ValueError("Multipliers must be 50–500 percent")

    def to_dict(self):
        return asdict(self)
