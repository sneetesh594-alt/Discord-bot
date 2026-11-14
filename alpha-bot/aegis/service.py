"""Execute moderation with a durable intent before action and an honest outcome."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, OrderedDict
from datetime import timedelta

import discord

from aegis.ai import AIClassifier
from aegis.config import Settings
from aegis.rules import MessageSample, RuleEngine, RuleHit
from aegis.security import clean, exempt, fresh_target, private_log_channel
from aegis.store import Store

log = logging.getLogger(__name__)


class ModerationService:
    def __init__(self, bot, store: Store, classifier: AIClassifier, settings: Settings):
        self.bot, self.store, self.ai, self.settings = bot, store, classifier, settings
        self.engine = RuleEngine(settings.runtime.state_max_users)
        # Fixed lock stripes keep concurrent moderation of one member serialized
        # without an indefinitely growing dictionary of locks.
        self.locks = [asyncio.Lock() for _ in range(256)]
        self.cooldowns: OrderedDict[tuple, tuple[float, int | None]] = OrderedDict()
        self.metrics: Counter = Counter()

    def lock_for(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self.locks[hash((guild_id, user_id)) % len(self.locks)]

    def clear_guild(self, guild_id: int) -> None:
        self.engine.clear_guild(guild_id)
        for key in list(self.cooldowns):
            if key[0] == guild_id:
                del self.cooldowns[key]

    async def audit(self, guild_id: int, case_id: int) -> None:
        case = await self.store.case(guild_id, case_id)
        if case is None:
            return
        log.info(
            "case=%s guild=%s user=%s source=%s action=%s status=%s",
            case_id,
            guild_id,
            case["user_id"],
            case["source"],
            case["action"],
            case["status"],
        )
        await self.notify(
            guild_id,
            f"Case #{case_id} · {case['status']}",
            f"Member ID: {case['user_id']}\nActor ID: {case['actor_id']}\n"
            f"Action: {case['action']}\nCategory: {case['category']}\n"
            f"Reason: {clean(case['reason'], 500)}\n"
            + (
                f"Message: https://discord.com/channels/{guild_id}/"
                f"{case['channel_id']}/{case['message_id']}"
                if case["message_id"]
                else ""
            ),
        )

    async def notify(self, guild_id: int, title: str, description: str) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        policy = await self.store.policy(guild_id)
        channel = guild.get_channel(policy.mod_log_channel_id) if guild else None
        if not isinstance(channel, discord.TextChannel) or not private_log_channel(channel):
            self.metrics["log_unavailable"] += 1
            return
        try:
            await channel.send(
                embed=discord.Embed(
                    title=title[:256],
                    description=description[:3500],
                    colour=discord.Colour.blurple(),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            self.metrics["log_failures"] += 1
            log.warning("Moderation log delivery failed guild=%s", guild_id)

    async def dm(self, member: discord.Member, case_id: int, reason: str) -> bool:
        try:
            await member.send(
                f"Moderation notice in **{clean(member.guild.name, 100)}**, case #{case_id}:\n"
                f"{clean(reason, 700)}\nUse `/appeal case_id:{case_id} reason:...` in that server, "
                "or contact its moderators if you cannot access commands.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except discord.HTTPException:
            return False

    async def warning_escalation(self, member: discord.Member, policy, case_id: int) -> str:
        warnings = await self.store.warnings(member.guild.id, member.id, policy.warning_days)
        if (
            len(warnings) < policy.warning_threshold
            or len(warnings) % policy.warning_threshold != 0
            or warnings[0]["id"] != case_id
        ):
            return "not_due"
        me = member.guild.me
        if (
            me is None
            or not me.guild_permissions.moderate_members
            or member.top_role >= me.top_role
            or member.guild_permissions.administrator
        ):
            return "skipped_permissions"
        if not await self.store.claim_escalation(member.guild.id, member.id, case_id):
            return "already_escalated"
        status = "unknown"
        try:
            until = discord.utils.utcnow() + timedelta(seconds=policy.warning_timeout_seconds)
            # Never shorten an existing, longer timeout.
            if member.timed_out_until and member.timed_out_until >= until:
                status = "already_timed_out"
                return status
            await member.timeout(until, reason=f"Aegis warning escalation; case #{case_id}")
            status = "timeout_applied"
            return status
        except discord.HTTPException as error:
            status = f"failed_{type(error).__name__}"
            return status
        finally:
            await self.store.finish_escalation(member.guild.id, member.id, case_id, status)

    async def manual(
        self,
        interaction: discord.Interaction,
        member_id: int,
        action: str,
        reason: str,
        *,
        seconds: int = 0,
    ) -> int:
        async with self.lock_for(interaction.guild_id, member_id):
            actor, member = await fresh_target(interaction, member_id, action)
            policy = await self.store.policy(interaction.guild_id)
            case_id = await self.store.create_case(
                guild_id=interaction.guild_id,
                user_id=member.id,
                actor_id=actor.id,
                source="manual",
                category="moderator_action",
                reason=reason,
                action=action,
                channel_id=interaction.channel_id,
                details={"seconds": seconds},
            )
            details = {"seconds": seconds}
            try:
                audit_reason = f"Aegis case #{case_id}; moderator {actor.id}; {reason}"[:500]
                if action == "timeout":
                    await member.timeout(timedelta(seconds=seconds), reason=audit_reason)
                elif action == "untimeout":
                    await member.timeout(None, reason=audit_reason)
                elif action == "kick":
                    await member.kick(reason=audit_reason)
                elif action == "ban":
                    await member.ban(reason=audit_reason, delete_message_seconds=0)
                await self.store.finish_case(
                    case_id, "completed", details, warning=action == "warn"
                )
                if action == "warn":
                    details["escalation"] = await self.warning_escalation(member, policy, case_id)
                    details["dm_delivered"] = await self.dm(member, case_id, reason)
                    status = (
                        "partial"
                        if details["escalation"].startswith(("failed", "skipped"))
                        else "completed"
                    )
                    await self.store.finish_case(case_id, status, details, warning=True)
                elif action in {"timeout", "kick", "ban"}:
                    details["dm_delivered"] = await self.dm(
                        member, case_id, f"{action.title()}: {reason}"
                    )
                    await self.store.finish_case(case_id, "completed", details)
            except discord.HTTPException as error:
                details["error"] = type(error).__name__
                await self.store.finish_case(case_id, "failed", details)
                await self.audit(interaction.guild_id, case_id)
                raise
            await self.audit(interaction.guild_id, case_id)
            return case_id

    def cooling_down(self, guild_id: int, user_id: int, category: str, now: float) -> bool:
        key = (guild_id, user_id, category)
        previous = self.cooldowns.get(key)
        if (
            previous is not None
            and now - previous[0] < self.settings.runtime.action_cooldown_seconds
        ):
            return True
        self.cooldowns[key] = (now, None)
        self.cooldowns.move_to_end(key)
        while len(self.cooldowns) > self.settings.runtime.state_max_users:
            self.cooldowns.popitem(last=False)
        return False

    async def process(
        self, message: discord.Message, received_at: float, *, edited: bool = False
    ) -> bool:
        if message.guild is None or not isinstance(message.author, discord.Member):
            return False
        parent_id = getattr(message.channel, "parent_id", None)
        async with self.lock_for(message.guild.id, message.author.id):
            if self.bot.get_guild(message.guild.id) is None:
                return False
            policy = await self.store.policy(message.guild.id)
            if exempt(message.author, policy, message.channel.id, parent_id):
                return True
            sample = MessageSample(
                message.guild.id,
                message.author.id,
                message.channel.id,
                message.id,
                message.content,
                len(message.mentions) + len(message.role_mentions),
                received_at,
            )
            # Keep the local path ahead of any external classifier call.
            hit = self.engine.check(sample, policy, track_spam=not edited)
            if hit:
                await self.local_action(message, hit, policy)
                return False
        if not (self.settings.ai_enabled and policy.ai_enabled):
            return True
        if not self.ai.eligible(message.content):
            self.ai.metrics["not_selected"] += 1
            return True
        result = await self.ai.classify(message.guild.id, message.content)
        if result is None or not result.needs_review(self.settings.ai.review_threshold):
            return True
        category = "support_review" if result.support_signal else "ai_review"
        async with self.lock_for(message.guild.id, message.author.id):
            if self.bot.get_guild(message.guild.id) is None:
                return False
            # Server settings may have changed while the network call was pending.
            current = await self.store.policy(message.guild.id)
            if not current.ai_enabled or exempt(
                message.author, current, message.channel.id, parent_id
            ):
                return True
            if self.cooling_down(message.guild.id, message.author.id, category, time.monotonic()):
                return False
            case_id = await self.store.create_case(
                guild_id=message.guild.id,
                user_id=message.author.id,
                actor_id=self.bot.user.id,
                source="ai",
                category=category,
                reason="AI signal requires human review",
                action="review",
                channel_id=message.channel.id,
                message_id=message.id,
            )
            await self.store.finish_case(
                case_id,
                "review",
                {
                    "categories": result.categories,
                    "scores": result.scores,
                    "model": result.model,
                    "support_signal": result.support_signal,
                },
            )
            await self.audit(message.guild.id, case_id)
            return False

    async def local_action(self, message: discord.Message, hit: RuleHit, policy) -> None:
        policy = await self.store.policy(message.guild.id)
        if exempt(
            message.author, policy, message.channel.id, getattr(message.channel, "parent_id", None)
        ):
            return
        now = time.monotonic()
        cooling = self.cooling_down(message.guild.id, message.author.id, hit.category, now)
        if cooling:
            # Repeated spam is still removed in enforce mode; suppress duplicate
            # warnings/timeouts/logs during the cooldown, never while observing.
            key = (message.guild.id, message.author.id, hit.category)
            previous_id = self.cooldowns[key][1]
            previous = await self.store.case(message.guild.id, previous_id) if previous_id else None
            # Do not perform an unaudited deletion after an observe->enforce switch.
            if policy.mode == "enforce" and previous and previous["status"] != "observed":
                try:
                    await message.delete()
                    self.metrics["cooldown_deletes"] += 1
                    counter = "additional_deletes"
                except discord.NotFound:
                    counter = "additional_already_deleted"
                except discord.HTTPException:
                    self.metrics["delete_failures"] += 1
                    counter = "additional_delete_failures"
                await self.store.add_case_counter(message.guild.id, previous_id, counter)
            return
        case_id = await self.store.create_case(
            guild_id=message.guild.id,
            user_id=message.author.id,
            actor_id=self.bot.user.id,
            source="local",
            category=hit.category,
            reason=hit.reason,
            action=hit.action,
            channel_id=message.channel.id,
            message_id=message.id,
        )
        self.cooldowns[(message.guild.id, message.author.id, hit.category)] = (now, case_id)
        if policy.mode == "observe":
            await self.store.finish_case(case_id, "observed", {"would_apply": hit.action})
            await self.audit(message.guild.id, case_id)
            return
        # Re-check the latest cached roles and permission overwrites immediately
        # before changing the message/member. REST failures remain authoritative.
        if exempt(
            message.author, policy, message.channel.id, getattr(message.channel, "parent_id", None)
        ):
            await self.store.finish_case(case_id, "cancelled", {"reason": "member_exempt"})
            return
        details = {}
        try:
            await message.delete()
            details["delete"] = "completed"
        except discord.NotFound:
            details["delete"] = "already_deleted"
        except discord.HTTPException as error:
            details["delete"] = type(error).__name__
        warning = hit.action == "delete_warn"
        if warning:
            # Record the warning before counting it for escalation.
            await self.store.finish_case(case_id, "pending", details, warning=True)
            details["escalation"] = await self.warning_escalation(message.author, policy, case_id)
            details["dm_delivered"] = await self.dm(message.author, case_id, hit.reason)
        else:
            me = message.guild.me
            if me is None or not me.guild_permissions.moderate_members:
                details["timeout"] = "skipped_permissions"
            else:
                try:
                    until = discord.utils.utcnow() + timedelta(seconds=policy.spam_timeout_seconds)
                    if message.author.timed_out_until and message.author.timed_out_until >= until:
                        details["timeout"] = "already_timed_out"
                    else:
                        await message.author.timeout(
                            until, reason=f"Aegis case #{case_id}: {hit.reason}"
                        )
                        details["timeout"] = "completed"
                except discord.HTTPException as error:
                    details["timeout"] = type(error).__name__
            details["dm_delivered"] = await self.dm(message.author, case_id, hit.reason)
        failed = (
            details["delete"] not in {"completed", "already_deleted"}
            or (not warning and details.get("timeout") not in {"completed", "already_timed_out"})
            or str(details.get("escalation", "")).startswith(("failed", "skipped"))
        )
        await self.store.finish_case(
            case_id, "partial" if failed else "completed", details, warning=warning
        )
        self.metrics["local_actions"] += 1
        await self.audit(message.guild.id, case_id)
