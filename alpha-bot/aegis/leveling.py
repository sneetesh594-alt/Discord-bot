"""XP eligibility, reward-role guards, and opt-in announcements."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
import uuid
from collections import Counter
from dataclasses import dataclass

import discord

from aegis.activity_store import ActivityRepository
from aegis.level_store import LevelRepository, XPAward
from aegis.levels import meaningful_text, multiplied_xp, voice_eligible
from aegis.pending import PendingMessages
from aegis.rules import MessageSample, RuleEngine, normalize
from aegis.security import clean

log = logging.getLogger(__name__)


@dataclass
class VoiceObservation:
    channel_id: int
    started_at: float
    last_at: float


DANGEROUS_REWARD_FLAGS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "moderate_members",
    "kick_members",
    "ban_members",
    "view_audit_log",
    "mention_everyone",
    "mute_members",
    "deafen_members",
    "move_members",
    "manage_events",
    "manage_expressions",
    "manage_threads",
)


def reward_error(role, me, actor=None) -> str | None:
    if role.is_default() or role.managed:
        return "Choose a normal, unmanaged reward role."
    if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
        return "The bot needs Manage Roles and a highest role above the reward."
    if actor is not None and actor.id != actor.guild.owner_id and role >= actor.top_role:
        return "Your highest role must be above the reward role."
    if any(getattr(role.permissions, flag, False) for flag in DANGEROUS_REWARD_FLAGS):
        return "Level rewards cannot grant staff, moderation, or administrative permissions."
    for channel in role.guild.channels:
        allowed, _ = channel.overwrites_for(role).pair()
        if any(getattr(allowed, flag, False) for flag in DANGEROUS_REWARD_FLAGS):
            return "A channel override gives this role staff permissions; choose another role."
    return None


class LevelingService:
    def __init__(self, bot, repository: LevelRepository):
        self.bot, self.repo = bot, repository
        self.rules = RuleEngine(bot.settings.runtime.state_max_users)
        self.metrics: Counter = Counter()
        self.voice_sessions: dict[tuple[int, int], tuple[int, float]] = {}
        self.observed_sessions: dict[tuple[int, int], VoiceObservation] = {}
        self.voice_locks = [asyncio.Lock() for _ in range(64)]
        self.run_id = uuid.uuid4().hex
        self.activity = ActivityRepository(repository.store)
        self.cards_active = 0
        self.pending_messages = PendingMessages()

    def ignored(self, member, channel, config) -> bool:
        return (
            member.bot
            or member.is_timed_out()
            or channel.id in config.ignored_channel_ids
            or getattr(channel, "parent_id", None) in config.ignored_channel_ids
            or getattr(channel, "category_id", None) in config.ignored_channel_ids
            or any(role.id in config.ignored_role_ids for role in member.roles)
        )

    async def on_message(self, message, received_at: float):
        if (
            not message.guild
            or not isinstance(message.author, discord.Member)
            or message.webhook_id
        ):
            return
        if getattr(message, "type", discord.MessageType.default) not in {
            discord.MessageType.default,
            discord.MessageType.reply,
        }:
            return
        async with self.bot.service.lock_for(message.guild.id, message.author.id):
            if self.bot.get_guild(message.guild.id) is None:
                return
            config = await self.repo.settings(message.guild.id)
            if not config.enabled or self.ignored(message.author, message.channel, config):
                return
            now = time.time()
            created = getattr(message, "created_at", None)
            if created is not None and now - created.timestamp() > 300:
                return
            if not meaningful_text(message.content, config.min_characters):
                return
            policy = await self.bot.store.policy(message.guild.id)
            hit = self.rules.check(
                MessageSample(
                    message.guild.id,
                    message.author.id,
                    message.channel.id,
                    message.id,
                    message.content,
                    len(message.mentions) + len(message.role_mentions),
                    received_at,
                ),
                policy,
            )
            if hit:
                self.metrics["rule_skips"] += 1
                return
            if config.activity_enabled:
                await self.activity.record(
                    message.guild.id, message.author.id, "text", str(message.id), now=now
                )
            amount = multiplied_xp(
                random.randint(config.text_min_xp, config.text_max_xp),
                message.author,
                message.channel,
                config,
                now,
            )
            award = await self.repo.award(
                message.guild.id,
                message.author.id,
                "text",
                str(message.id),
                amount,
                now=now,
                digest=hashlib.sha256(normalize(message.content).encode()).hexdigest(),
            )
            if award:
                await self.after_award(message.author, message.channel, config, award)

    async def after_award(self, member, channel, config, award: XPAward, *, announce=True):
        self.metrics["awards"] += 1
        await self.reconcile_roles(member, award.level, config)
        crossed_reward = any(
            award.previous_level < int(level) <= award.level for level in config.reward_roles
        )
        crossed_interval = (
            award.previous_level // config.announce_every < award.level // config.announce_every
        )
        if not (
            announce
            and config.announcements
            and award.level > award.previous_level
            and (crossed_reward or crossed_interval)
        ):
            return
        destination = (
            member.guild.get_channel(config.announcement_channel_id)
            if config.announcement_channel_id
            else channel
        )
        if destination is None or not hasattr(destination, "send"):
            self.metrics["announcement_failures"] += 1
            return
        embed = discord.Embed(
            title=f"Level {award.level} reached!",
            description=f"**{clean(member.display_name, 80)}** reached **level {award.level}**.\n"
            f"Total XP: **{award.total_xp:,}** · Keep the conversation going.",
            colour=discord.Colour.from_rgb(84, 227, 207),
        )
        try:
            await destination.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            self.metrics["level_ups"] += 1
        except discord.HTTPException:
            self.metrics["announcement_failures"] += 1
            log.warning("Level-up delivery failed guild=%s user=%s", member.guild.id, member.id)

    async def reconcile_roles(self, member, level: int, config, *, reset=False, force_remove=False):
        if not config.reward_roles:
            return "no_rewards"
        try:
            member = await member.guild.fetch_member(member.id)
            me = member.guild.me
            if me is None or member.id == member.guild.owner_id or member.top_role >= me.top_role:
                self.metrics["reward_protected_member"] += 1
                return "protected"
            status = "completed"
            entries = sorted((int(tier), role_id) for tier, role_id in config.reward_roles.items())
            eligible = [(tier, role_id) for tier, role_id in entries if tier <= level and not reset]
            if not config.stack_rewards:
                eligible = eligible[-1:]
            wanted = {role_id for _, role_id in eligible}
            existing = {role.id for role in member.roles}
            configured = {role_id for _, role_id in entries}
            remove = (
                (existing & configured) - wanted
                if reset or force_remove or not config.stack_rewards
                else set()
            )
            for role_id in sorted(wanted - existing):
                role = member.guild.get_role(role_id)
                if role is None or reward_error(role, me):
                    self.metrics["reward_role_invalid"] += 1
                    status = "partial"
                    continue
                await member.add_roles(role, reason=f"Alpha bot level {level} reward", atomic=True)
            for role_id in sorted(remove):
                role = member.guild.get_role(role_id)
                if role is None or reward_error(role, me):
                    self.metrics["reward_role_invalid"] += 1
                    status = "partial"
                    continue
                await member.remove_roles(
                    role, reason="Alpha bot level reward reconciliation", atomic=True
                )
            return status
        except discord.HTTPException as error:
            self.metrics["reward_failures"] += 1
            log.warning(
                "Level reward failed guild=%s user=%s (%s)",
                member.guild.id,
                member.id,
                type(error).__name__,
            )
            return "failed"

    def clear_guild(self, guild_id: int):
        self.rules.clear_guild(guild_id)
        self.repo.cache.pop(guild_id, None)
        self.clear_voice(guild_id)
        self.pending_messages.clear_guild(guild_id)

    def clear_voice(self, guild_id: int):
        for sessions in (self.voice_sessions, self.observed_sessions):
            for key in list(sessions):
                if key[0] == guild_id:
                    del sessions[key]

    def clear_member(self, guild_id, user_id):
        self.voice_sessions.pop((guild_id, user_id), None)
        self.observed_sessions.pop((guild_id, user_id), None)

    def disconnect(self):
        self.voice_sessions.clear()
        self.observed_sessions.clear()

    async def _observe_voice(self, guild, member, session, monotonic_now, now):
        delta = int(monotonic_now - session.last_at)
        if delta < 1:
            return
        session.last_at += delta
        # A stalled/disconnected observer never invents offline participation.
        if delta > 90:
            session.started_at = monotonic_now
            session.last_at = monotonic_now
            self.voice_sessions.pop((guild.id, member.id), None)
            return
        await self.activity.record(
            guild.id,
            member.id,
            "observed_voice",
            f"{self.run_id}:{member.id}:{session.channel_id}:{monotonic_now:.6f}",
            delta,
            now=now,
        )

    async def voice_update(self, member, before, after):
        guild = member.guild
        async with self.voice_locks[guild.id % len(self.voice_locks)]:
            if self.bot.get_guild(guild.id) is None:
                self.clear_voice(guild.id)
                return
            monotonic_now, now = time.monotonic(), time.time()
            config = await self.repo.settings(guild.id)
            if before.channel != after.channel:
                key = (guild.id, member.id)
                session = self.observed_sessions.pop(key, None)
                if session and config.enabled and config.activity_enabled:
                    async with self.bot.service.lock_for(guild.id, member.id):
                        if self.bot.get_guild(guild.id) is not None:
                            await self._observe_voice(guild, member, session, monotonic_now, now)
                self.voice_sessions.pop(key, None)
            await self._voice_tick(guild, config, monotonic_now, now)

    async def voice_tick(self, guild, *, monotonic_now=None, now=None):
        monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
        now = time.time() if now is None else now
        async with self.voice_locks[guild.id % len(self.voice_locks)]:
            if self.bot.get_guild(guild.id) is None:
                return
            config = await self.repo.settings(guild.id)
            await self._voice_tick(guild, config, monotonic_now, now)

    async def _voice_tick(self, guild, config, monotonic_now, now):
        if not config.enabled or not (config.voice_enabled or config.activity_enabled):
            self.clear_voice(guild.id)
            return
        active, observed = set(), set()
        for channel in guild.voice_channels:
            eligible = [
                member for member in channel.members if voice_eligible(member, channel, config)
            ]
            eligible_ids = (
                {m.id for m in eligible} if len(eligible) >= config.voice_min_members else set()
            )
            for member in channel.members:
                if self.ignored(member, channel, config):
                    continue
                key = (guild.id, member.id)
                async with self.bot.service.lock_for(guild.id, member.id):
                    if self.bot.get_guild(guild.id) is None:
                        continue
                    if (await self.repo.profile(guild.id, member.id))["opted_out"]:
                        continue
                    if config.activity_enabled:
                        observed.add(key)
                        observation = self.observed_sessions.get(key)
                        if observation is None or observation.channel_id != channel.id:
                            if (
                                len(self.observed_sessions)
                                < self.bot.settings.runtime.state_max_users
                            ):
                                self.observed_sessions[key] = VoiceObservation(
                                    channel.id, monotonic_now, monotonic_now
                                )
                        else:
                            await self._observe_voice(
                                guild, member, observation, monotonic_now, now
                            )
                    if member.id not in eligible_ids:
                        continue
                    active.add(key)
                    session = self.voice_sessions.get(key)
                    if session is None or session[0] != channel.id:
                        if len(self.voice_sessions) < self.bot.settings.runtime.state_max_users:
                            self.voice_sessions[key] = (channel.id, monotonic_now)
                        continue
                    if monotonic_now - session[1] < 60:
                        continue
                    self.voice_sessions[key] = (channel.id, monotonic_now)
                    event_key = f"{self.run_id}:{member.id}:{channel.id}:{now:.6f}"
                    if config.activity_enabled:
                        await self.activity.record(
                            guild.id, member.id, "voice", event_key, 60, now=now
                        )
                    if not config.voice_enabled:
                        continue
                    amount = multiplied_xp(config.voice_xp_per_minute, member, channel, config, now)
                    award = await self.repo.award(
                        guild.id,
                        member.id,
                        "voice",
                        event_key,
                        amount,
                        now=now,
                    )
                    if award:
                        await self.after_award(member, channel, config, award)
        for key in list(self.voice_sessions):
            if key[0] == guild.id and key not in active:
                del self.voice_sessions[key]
        for key in list(self.observed_sessions):
            if key[0] == guild.id and key not in observed:
                del self.observed_sessions[key]

    async def reaction(self, payload):
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or payload.user_id == self.bot.user.id:
            return
        if (guild.id, payload.message_id) in self.pending_messages:
            self.metrics["reaction_pending_moderation"] += 1
            return
        async with self.bot.service.lock_for(guild.id, 0):
            if self.bot.get_guild(guild.id) is None:
                return
            config = await self.repo.settings(guild.id)
        if not (config.enabled and (config.reaction_enabled or config.activity_enabled)):
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        try:
            reactor = payload.member or await guild.fetch_member(payload.user_id)
            if self.ignored(reactor, channel, config):
                return
            message = await channel.fetch_message(payload.message_id)
            if message.webhook_id or message.author.bot or message.author.id == reactor.id:
                return
            if time.time() - message.created_at.timestamp() > 7 * 86400:
                return
            author = (
                message.author
                if isinstance(message.author, discord.Member)
                else await guild.fetch_member(message.author.id)
            )
            if self.ignored(author, channel, config):
                return
            policy = await self.bot.store.policy(guild.id)
            if not meaningful_text(message.content, config.min_characters) or self.rules.check(
                MessageSample(
                    guild.id,
                    author.id,
                    channel.id,
                    message.id,
                    message.content,
                    len(message.mentions) + len(message.role_mentions),
                    time.monotonic(),
                ),
                policy,
                track_spam=False,
            ):
                return
            async with self.bot.service.lock_for(guild.id, author.id):
                if self.bot.get_guild(guild.id) is None:
                    return
                if (
                    guild.id,
                    message.id,
                ) in self.pending_messages or await self.repo.message_flagged(guild.id, message.id):
                    return
                now = time.time()
                if config.activity_enabled:
                    await self.activity.reaction(
                        guild.id, message.id, reactor.id, author.id, now=now
                    )
                if not config.reaction_enabled:
                    return
                amount = multiplied_xp(config.reaction_xp, author, channel, config, now)
                # One giver/recipient message pair, irrespective of emoji or remove/re-add.
                award = await self.repo.award(
                    guild.id, author.id, "reaction", f"{message.id}:{reactor.id}", amount, now=now
                )
                if award:
                    await self.after_award(author, channel, config, award)
        except discord.HTTPException:
            self.metrics["reaction_fetch_failures"] += 1
