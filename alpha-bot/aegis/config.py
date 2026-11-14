"""Validated settings. Secrets stay in environment variables and out of repr/logs."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

import yaml
from dotenv import load_dotenv

from aegis.leveling_config import LevelingConfig


@dataclass(frozen=True)
class GuildPolicy:
    mode: str = "observe"
    mod_log_channel_id: int | None = None
    ai_enabled: bool = False
    block_invites: bool = False
    banned_phrases: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    exempt_role_ids: tuple[int, ...] = ()
    exempt_channel_ids: tuple[int, ...] = ()
    spam_window_seconds: int = 10
    spam_max_messages: int = 5
    duplicate_threshold: int = 3
    cross_channel_threshold: int = 3
    max_mentions: int = 5
    spam_timeout_seconds: int = 300
    warning_threshold: int = 3
    warning_days: int = 30
    warning_timeout_seconds: int = 600

    def __post_init__(self) -> None:
        if self.mode not in {"observe", "enforce"}:
            raise ValueError("policy.mode must be observe or enforce")
        for name in ("ai_enabled", "block_invites"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"policy.{name} must be true or false")
        for name in ("banned_phrases", "blocked_domains", "exempt_role_ids", "exempt_channel_ids"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or len(value) > 200:
                raise ValueError(f"policy.{name} must be a list with at most 200 entries")
            object.__setattr__(self, name, tuple(value))
        for phrase in self.banned_phrases:
            if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 64:
                raise ValueError("Banned phrases must be nonempty strings of at most 64 characters")
        for domain in self.blocked_domains:
            if not isinstance(domain, str) or not re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                r"[a-z]{2,63}",
                domain,
            ):
                raise ValueError("Blocked domains must be lowercase ASCII hostnames without paths")
        for name in ("exempt_role_ids", "exempt_channel_ids"):
            if any(type(i) is not int or i <= 0 for i in getattr(self, name)):
                raise ValueError(f"policy.{name} must contain positive integer IDs")
        if self.mod_log_channel_id is not None and (
            type(self.mod_log_channel_id) is not int or self.mod_log_channel_id <= 0
        ):
            raise ValueError("mod_log_channel_id must be a positive integer or null")
        limits = {
            "spam_window_seconds": (1, 120),
            "spam_max_messages": (2, 100),
            "duplicate_threshold": (2, 100),
            "cross_channel_threshold": (2, 100),
            "max_mentions": (1, 100),
            "spam_timeout_seconds": (1, 2419200),
            "warning_threshold": (1, 100),
            "warning_days": (1, 365),
            "warning_timeout_seconds": (1, 2419200),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"policy.{name} must be an integer from {low} to {high}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeConfig:
    retention_days: int = 30
    workers: int = 4
    queue_size: int = 500
    state_max_users: int = 10000
    action_cooldown_seconds: int = 30
    xp_event_retention_days: int = 90
    xp_profile_retention_days: int = 365

    def __post_init__(self) -> None:
        for name, high in {
            "retention_days": 365,
            "workers": 16,
            "queue_size": 10000,
            "state_max_users": 100000,
            "action_cooldown_seconds": 3600,
            "xp_event_retention_days": 365,
            "xp_profile_retention_days": 3650,
        }.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= high:
                raise ValueError(f"runtime.{name} must be an integer from 1 to {high}")
        if self.xp_event_retention_days < 31:
            raise ValueError("XP event retention must cover the 31-day leaderboard")


@dataclass(frozen=True)
class AIConfig:
    requests_per_minute: int = 30
    requests_per_day: int = 1000
    concurrency: int = 2
    timeout_seconds: int = 5
    cache_ttl_seconds: int = 300
    cache_size: int = 1000
    sample_rate: float = 0.1
    review_threshold: float = 0.7

    def __post_init__(self) -> None:
        for name, high in {
            "requests_per_minute": 10000,
            "requests_per_day": 1000000,
            "concurrency": 16,
            "timeout_seconds": 30,
            "cache_ttl_seconds": 3600,
            "cache_size": 100000,
        }.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= high:
                raise ValueError(f"ai.{name} must be an integer from 1 to {high}")
        for name in ("sample_rate", "review_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError(f"ai.{name} must be between 0 and 1")


T = TypeVar("T")


def from_mapping(cls: type[T], value: dict[str, Any]) -> T:
    if not isinstance(value, dict):
        raise ValueError(f"{cls.__name__} must be a mapping")
    unknown = set(value) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {', '.join(sorted(unknown))}")
    return cls(**value)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).lower().strip()
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true or false")
    return raw in {"true", "1"}


def env_id(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{name} must be a positive Discord ID or blank")
    return int(raw)


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    api_key: str = field(repr=False)
    policy: GuildPolicy
    runtime: RuntimeConfig
    ai: AIConfig
    database_path: Path
    log_file: Path | None
    log_level: str
    ai_enabled: bool
    moderation_model: str
    dev_guild_id: int | None
    sync_commands: bool
    leveling: LevelingConfig = field(default_factory=LevelingConfig)

    @classmethod
    def load(cls, *, require_token: bool = True) -> Settings:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        path = Path(os.getenv("CONFIG_PATH", "config.yaml"))
        if not path.is_file():
            raise ValueError(f"Configuration not found: {path}")
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            raise ValueError(f"Invalid YAML in {path}") from None
        if not isinstance(document, dict) or set(document) - {
            "policy",
            "runtime",
            "ai",
            "leveling",
        }:
            raise ValueError("Config must contain only policy, runtime, ai, and leveling mappings")
        policy_section = document.get("policy", {})
        if not isinstance(policy_section, dict):
            raise ValueError("policy must be a mapping")
        policy_data = policy_section.copy()
        if env_id("MOD_LOG_CHANNEL_ID") is not None:
            policy_data["mod_log_channel_id"] = env_id("MOD_LOG_CHANNEL_ID")
        policy = from_mapping(GuildPolicy, policy_data)
        runtime = from_mapping(RuntimeConfig, document.get("runtime", {}))
        ai = from_mapping(AIConfig, document.get("ai", {}))
        leveling = from_mapping(LevelingConfig, document.get("leveling", {}))
        if policy.warning_days > runtime.retention_days:
            raise ValueError("warning_days must not exceed retention_days")
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        key = os.getenv("OPENAI_API_KEY", "").strip()
        enabled = env_bool("AI_ENABLED")
        if require_token and (not token or token.startswith("replace_")):
            raise ValueError("Set DISCORD_BOT_TOKEN in your local .env file")
        if enabled and not key:
            raise ValueError("AI_ENABLED requires OPENAI_API_KEY")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid LOG_LEVEL")
        return cls(
            token=token,
            api_key=key,
            policy=policy,
            runtime=runtime,
            ai=ai,
            database_path=Path(os.getenv("DATABASE_PATH", "data/aegis.sqlite3")),
            log_file=Path(os.getenv("LOG_FILE", "logs/aegis.log"))
            if os.getenv("LOG_FILE", "logs/aegis.log").strip()
            else None,
            log_level=level,
            ai_enabled=enabled,
            moderation_model=os.getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest"),
            dev_guild_id=env_id("DEV_GUILD_ID"),
            sync_commands=env_bool("SYNC_COMMANDS", True),
            leveling=leveling,
        )
