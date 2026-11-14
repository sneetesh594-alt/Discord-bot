"""Documented integer XP curve and deterministic eligibility helpers."""

import re
from dataclasses import dataclass

from aegis.rules import normalize

MAX_LEVEL = 1000
MAX_XP = 9_000_000_000_000_000


def total_for_level(level: int) -> int:
    if type(level) is not int or not 0 <= level <= MAX_LEVEL:
        raise ValueError("Level must be between 0 and 1000")
    # Sum 5*n*n + 50*n + 100 for n=0..level-1, using integers only.
    return 5 * level * (level - 1) * (2 * level - 1) // 6 + 25 * level * (level - 1) + 100 * level


def level_from_xp(xp: int) -> int:
    low, high = 0, MAX_LEVEL
    while low < high:
        middle = (low + high + 1) // 2
        if total_for_level(middle) <= xp:
            low = middle
        else:
            high = middle - 1
    return low


@dataclass(frozen=True)
class Progress:
    total_xp: int
    level: int
    earned: int
    needed: int

    @property
    def fraction(self):
        return min(1.0, self.earned / self.needed) if self.needed else 1.0


def progress(xp: int) -> Progress:
    level = level_from_xp(xp)
    floor = total_for_level(level)
    return Progress(
        xp, level, xp - floor, total_for_level(level + 1) - floor if level < MAX_LEVEL else 0
    )


def meaningful_text(content: str, minimum: int) -> bool:
    if content.lstrip().startswith(("/", "!")):
        return False
    without_links = re.sub(r"https?://\S+|<[@#][!&]?\d+>|<a?:\w+:\d+>", "", content)
    return sum(char.isalnum() for char in normalize(without_links)) >= minimum


def multiplied_xp(base: int, member, channel, config, now: float) -> int:
    role_percent = max(
        (
            config.role_multipliers[str(role.id)]
            for role in member.roles
            if str(role.id) in config.role_multipliers
        ),
        default=100,
    )
    channel_percent = config.channel_multipliers.get(
        str(channel.id),
        config.channel_multipliers.get(
            str(getattr(channel, "parent_id", None)),
            config.channel_multipliers.get(str(getattr(channel, "category_id", None)), 100),
        ),
    )
    bonus = config.bonus_percent if now < config.bonus_until else 100
    return max(1, base * role_percent * channel_percent * bonus // 1_000_000)


def voice_eligible(member, channel, config) -> bool:
    voice = member.voice
    return (
        not member.bot
        and voice is not None
        and voice.channel == channel
        and channel != member.guild.afk_channel
        and not any(
            getattr(voice, name, False)
            for name in ("self_mute", "self_deaf", "mute", "deaf", "suppress")
        )
        and not member.is_timed_out()
        and channel.id not in config.ignored_channel_ids
        and getattr(channel, "category_id", None) not in config.ignored_channel_ids
        and not any(role.id in config.ignored_role_ids for role in member.roles)
    )
