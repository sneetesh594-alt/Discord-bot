"""Member progression commands and bounded voice/reaction workers."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import math
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from aegis.cards import rank_card
from aegis.leaderboards import PERIODS, TITLES, LeaderboardView, score_label
from aegis.leveling import reward_error
from aegis.levels import MAX_LEVEL, multiplied_xp, progress, total_for_level, voice_eligible
from aegis.security import UserError, clean

log = logging.getLogger(__name__)


class Leveling(commands.Cog):
    leaderboards = app_commands.Group(
        name="leaderboard", description="XP and activity rankings", guild_only=True
    )
    manage = app_commands.Group(
        name="manage",
        description="Audited XP and level administration",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True, manage_roles=True),
    )
    manage_xp = app_commands.Group(name="xp", description="Adjust XP balances", parent=manage)
    manage_level = app_commands.Group(
        name="level", description="Set or reset a member level", parent=manage
    )
    levels = app_commands.Group(
        name="levels",
        description="Manage XP, announcements, and level rewards",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot):
        self.bot = bot
        self.service = bot.leveling
        self.repo = self.service.repo
        self.reactions: asyncio.Queue = asyncio.Queue(maxsize=250)
        self.workers = []

    async def cog_load(self):
        config = self.bot.tree.get_command("config")
        self.config_alias = None
        if isinstance(config, app_commands.Group):
            alias = app_commands.Group(
                name="leveling",
                description="Leveling settings; /levels compatibility",
                guild_only=True,
            )
            for original in self.levels.commands:
                if original.name in {"xp", "reset"}:
                    continue
                cloned = copy.copy(original)
                cloned.binding = self
                cloned.checks = list(original.checks)
                alias.add_command(cloned)
            config.add_command(alias)
            self.config_alias = alias
        self.workers = [
            asyncio.create_task(self.reaction_worker(), name=f"reaction-xp-{index}")
            for index in range(2)
        ]
        self.voice_loop.start()
        self.cleanup.start()

    async def cog_unload(self):
        config = self.bot.tree.get_command("config")
        if (
            isinstance(config, app_commands.Group)
            and config.get_command("leveling") is self.config_alias
        ):
            config.remove_command("leveling")
        self.voice_loop.cancel()
        self.cleanup.cancel()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.service.disconnect()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.guild_id:
            try:
                self.reactions.put_nowait(payload)
            except asyncio.QueueFull:
                self.service.metrics["reaction_queue_drops"] += 1

    async def reaction_worker(self):
        while True:
            payload = await self.reactions.get()
            try:
                await self.service.reaction(payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.service.metrics["reaction_errors"] += 1
                log.error("Reaction XP worker failed (%s)", type(error).__name__)
            finally:
                self.reactions.task_done()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        flags = ("self_mute", "self_deaf", "mute", "deaf", "suppress")
        if before.channel == after.channel and all(
            getattr(before, f) == getattr(after, f) for f in flags
        ):
            return
        await self.service.voice_update(member, before, after)

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.service.disconnect()

    @tasks.loop(seconds=15)
    async def voice_loop(self):
        if not self.bot.is_ready():
            self.service.disconnect()
            return
        for guild in self.bot.guilds:
            try:
                await self.service.voice_tick(guild)
            except Exception as error:
                self.service.metrics["voice_errors"] += 1
                log.error("Voice XP tick failed guild=%s (%s)", guild.id, type(error).__name__)

    @voice_loop.before_loop
    async def before_voice(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=6)
    async def cleanup(self):
        try:
            await self.repo.prune(
                self.bot.settings.runtime.xp_event_retention_days,
                self.bot.settings.runtime.xp_profile_retention_days,
            )
        except Exception as error:
            log.error("XP retention cleanup failed (%s)", type(error).__name__)

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @app_commands.command(description="Show your level, rank card, and progress to the next level")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 20)
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        member = member or interaction.user
        profile = await self.repo.profile(interaction.guild_id, member.id)
        state = progress(profile["total_xp"])
        config = await self.repo.settings(interaction.guild_id)
        embed = discord.Embed(
            title=f"{clean(member.display_name, 100)} · Level {state.level}",
            colour=discord.Colour.from_rgb(84, 227, 207),
        )
        next_step = (
            f"{state.needed - state.earned:,} XP to level {state.level + 1}"
            if state.needed
            else "Maximum level reached"
        )
        embed.description = (
            f"**{profile['total_xp']:,} XP** · Rank **{profile['rank'] or 'unranked'}**\n"
            f"{next_step}"
        )
        embed.add_field(
            name="Activity earning XP",
            value=(
                f"XP-awarded messages: {profile['text_awards']:,}\n"
                f"XP-credited minutes: {profile['voice_awards']:,}\n"
                f"Rewarded reactions received: {profile['reaction_awards']:,}"
            ),
        )
        if not config.enabled:
            embed.set_footer(text="XP earning is currently paused for this server.")
        elif profile["opted_out"] and member.id == interaction.user.id:
            embed.set_footer(text="You opted out. Use /xp-optout enabled:false to start fresh.")
        file = None
        if interaction.app_permissions.attach_files and self.service.cards_active < 2:
            self.service.cards_active += 1
            try:
                avatar = None
                try:
                    avatar = await asyncio.wait_for(
                        member.display_avatar.replace(size=128, format="png").read(), timeout=3
                    )
                    if len(avatar) > 1_000_000:
                        avatar = None
                except (discord.HTTPException, OSError, TimeoutError):
                    pass
                png = await asyncio.to_thread(
                    rank_card,
                    member.display_name,
                    profile["total_xp"],
                    profile["rank"],
                    avatar=avatar,
                    server=interaction.guild.name,
                    username=str(member),
                    next_reward=next(
                        (
                            f"Level {tier}"
                            for tier in sorted(map(int, config.reward_roles))
                            if tier > state.level
                        ),
                        "",
                    ),
                )
                file = discord.File(io.BytesIO(png), filename="rank-card.png")
                embed.set_image(url="attachment://rank-card.png")
            except (OSError, ValueError, RuntimeError) as error:
                self.service.metrics["card_render_errors"] += 1
                log.error("Rank image render failed (%s)", type(error).__name__)
                embed.set_footer(
                    text="Rank image unavailable; your XP and placement are shown above."
                )
            finally:
                self.service.cards_active -= 1
        if file:
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def show_board(self, interaction, metric="xp", period="all", page=1, private=False):
        if period not in PERIODS:
            raise UserError("Choose all, day, week or month.")
        config = await self.repo.settings(interaction.guild_id)
        public_channel = config.public_leaderboard_channel_id or config.announcement_channel_id
        ephemeral = private or interaction.channel_id != public_channel
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        view = LeaderboardView(
            self.service.activity,
            interaction.guild_id,
            interaction.guild.name,
            interaction.user.id,
            metric,
            period,
            page,
        )
        view.message = await interaction.followup.send(
            embed=await view.render(),
            view=view,
            ephemeral=ephemeral,
            wait=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @leaderboards.command(name="xp", description="XP ranking with levels and progress")
    @app_commands.checks.cooldown(2, 20)
    @app_commands.choices(
        period=[app_commands.Choice(name=v[1], value=k) for k, v in PERIODS.items()]
    )
    async def board_xp(
        self,
        interaction: discord.Interaction,
        period: str = "all",
        page: app_commands.Range[int, 1, 100000] = 1,
        private: bool = False,
    ):
        await self.show_board(interaction, "xp", period, page, private)

    @leaderboards.command(name="text", description="Rank by tracked messages, independently of XP")
    @app_commands.checks.cooldown(2, 20)
    @app_commands.choices(
        period=[app_commands.Choice(name=v[1], value=k) for k, v in PERIODS.items()]
    )
    async def board_text(
        self,
        interaction: discord.Interaction,
        period: str = "all",
        page: app_commands.Range[int, 1, 100000] = 1,
        private: bool = False,
    ):
        await self.show_board(interaction, "text", period, page, private)

    @leaderboards.command(
        name="voice", description="Rank by credited voice minutes, independently of XP"
    )
    @app_commands.checks.cooldown(2, 20)
    @app_commands.choices(
        period=[app_commands.Choice(name=v[1], value=k) for k, v in PERIODS.items()]
    )
    async def board_voice(
        self,
        interaction: discord.Interaction,
        period: str = "all",
        page: app_commands.Range[int, 1, 100000] = 1,
        private: bool = False,
    ):
        await self.show_board(interaction, "voice", period, page, private)

    @leaderboards.command(name="reaction", description="Rank by unique reactions given or received")
    @app_commands.checks.cooldown(2, 20)
    @app_commands.choices(
        period=[app_commands.Choice(name=v[1], value=k) for k, v in PERIODS.items()]
    )
    async def board_reaction(
        self,
        interaction: discord.Interaction,
        period: str = "all",
        page: app_commands.Range[int, 1, 100000] = 1,
        private: bool = False,
    ):
        await self.show_board(interaction, "received", period, page, private)

    @app_commands.command(
        name="xp-leaderboard", description="Compatibility entry for the XP leaderboard"
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 20)
    @app_commands.choices(
        period=[
            app_commands.Choice(name="All time", value="all"),
            app_commands.Choice(name="Last 24 hours", value="day"),
            app_commands.Choice(name="Last 7 days", value="week"),
            app_commands.Choice(name="Last 31 days", value="month"),
        ]
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        period: str = "all",
        page: app_commands.Range[int, 1, 1000] = 1,
    ):
        await self.show_board(interaction, "xp", period, page, private=True)

    @app_commands.command(
        name="statistics", description="View tracked activity and separate XP source totals"
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 20)
    async def statistics(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        member = member or interaction.user
        activity = await self.service.activity.profile(interaction.guild_id, member.id)
        xp = await self.repo.profile(interaction.guild_id, member.id)
        embed = discord.Embed(
            title=f"{clean(member.display_name, 80)} · Statistics", colour=0x976AF5
        )
        embed.add_field(name="Text (messages)", value=f"{activity['messages']:,}")
        embed.add_field(
            name="Voice (credited minutes)", value=f"{activity['eligible_voice_seconds'] / 60:,.1f}"
        )
        embed.add_field(
            name="Reactions received / given",
            value=f"{activity['reactions_received']:,} / {activity['reactions_given']:,}",
        )
        embed.add_field(
            name="Observed connected voice",
            value=(
                f"{activity['voice_seconds'] // 3600:,}h "
                f"{(activity['voice_seconds'] % 3600) // 60}m "
                f"{activity['voice_seconds'] % 60}s"
            ),
            inline=False,
        )
        embed.add_field(
            name="XP breakdown",
            value=(
                f"Total: **{xp['total_xp']:,}**\nText {xp['text_xp']:,} · "
                f"Voice {xp['voice_xp']:,} · Reaction {xp['reaction_xp']:,} · "
                f"Admin {xp['manual_xp']:+,}"
            ),
            inline=False,
        )
        embed.add_field(
            name="XP-awarded activity (includes older award history)",
            value=(
                f"Messages {xp['text_awards']:,} · Minutes {xp['voice_awards']:,} · "
                f"Reactions {xp['reaction_awards']:,}"
            ),
            inline=False,
        )
        since = activity["tracking_since"]
        embed.description = (
            f"Activity tracking started <t:{int(since)}:F>. Earlier counts are not reconstructed."
            if since
            else "No activity tracked yet. Earlier counts are not reconstructed."
        )
        if xp["opted_out"]:
            embed.set_footer(text="This member opted out of XP and activity tracking.")
        await interaction.followup.send(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        name="placement", description="Find a member's place outside the first leaderboard page"
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 20)
    @app_commands.choices(
        leaderboard=[app_commands.Choice(name=v[1], value=k) for k, v in PERIODS.items()],
        metric=[app_commands.Choice(name=v, value=k) for k, v in TITLES.items()],
    )
    async def placement(
        self,
        interaction: discord.Interaction,
        leaderboard: str,
        member: discord.Member | None = None,
        metric: str = "xp",
    ):
        if leaderboard not in PERIODS or metric not in TITLES:
            raise UserError("Choose a supported period and metric.")
        await interaction.response.defer(ephemeral=True)
        member = member or interaction.user
        result = await self.service.activity.ranking(
            interaction.guild_id, metric, PERIODS[leaderboard][0], user_id=member.id
        )
        own = result["placement"]
        text = (
            f"**{clean(member.display_name, 80)}** · {TITLES[metric]} · {PERIODS[leaderboard][1]}\n"
        )
        text += (
            f"Rank **#{own['position']}** of {result['count']} · "
            f"{score_label(metric, own['score'])}"
            if own
            else "No positive tracked entry for this board yet."
        )
        await interaction.followup.send(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        name="time", description="View your current observed voice session and eligibility"
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 20)
    async def voice_time(self, interaction: discord.Interaction):
        member = interaction.user
        await interaction.response.defer(ephemeral=True)
        config = await self.repo.settings(interaction.guild_id)
        xp = await self.repo.profile(interaction.guild_id, member.id)
        if xp["opted_out"]:
            text = "You opted out. Current voice sessions are not tracked."
        elif not config.enabled or not config.activity_enabled:
            text = "Activity tracking is paused here."
        elif not member.voice or not member.voice.channel:
            text = "You are not in a voice channel. /statistics shows previously tracked time."
        elif self.service.ignored(member, member.voice.channel, config):
            text = "This member or voice channel is excluded from activity tracking."
        else:
            channel = member.voice.channel
            session = self.service.observed_sessions.get((interaction.guild_id, member.id))
            now = time.monotonic()
            if session and (session.channel_id != channel.id or now - session.last_at > 90):
                session = None
            age = max(0, now - session.started_at) if session else 0
            eligible = (
                voice_eligible(member, channel, config)
                and sum(voice_eligible(m, channel, config) for m in channel.members)
                >= config.voice_min_members
            )
            text = (
                f"Observed current session: **{int(age) // 60}m {int(age) % 60}s**.\n"
                f"Activity eligibility: **{'eligible' if eligible else 'not eligible'}**; "
                f"needs {config.voice_min_members} active humans, no mute/deafen/AFK/exclusions.\n"
                f"Voice XP: **{'enabled' if config.voice_enabled else 'disabled'}**. "
                "Only observed time is counted; offline history is not backfilled."
                + (" Waiting for the next tracking tick." if session is None else "")
            )
        await interaction.followup.send(text, ephemeral=True)

    async def show_xp_needed(self, interaction, level=None, member=None):
        await interaction.response.defer(ephemeral=True)
        member = member or interaction.user
        profile = await self.repo.profile(interaction.guild_id, member.id)
        current = progress(profile["total_xp"])
        level = min(MAX_LEVEL, current.level + 1) if level is None else level
        if not 1 <= level <= MAX_LEVEL:
            raise UserError("Choose a target level from 1 to 1000.")
        target = total_for_level(level)
        remaining = max(0, target - profile["total_xp"])
        config = await self.repo.settings(interaction.guild_id)
        text = (
            f"**{clean(member.display_name, 80)}** · Level **{level}** "
            f"needs **{target:,} total XP**.\n**{remaining:,} XP remaining**."
        )
        if not config.enabled or profile["opted_out"]:
            text += "\nXP earning is paused or this member has opted out."
        elif self.service.ignored(member, interaction.channel, config):
            text += "\nThis member or channel is excluded; no earning estimate applies here."
        elif remaining:
            low = multiplied_xp(
                config.text_min_xp, member, interaction.channel, config, time.time()
            )
            high = multiplied_xp(
                config.text_max_xp, member, interaction.channel, config, time.time()
            )
            text += (
                f"\nText: approximately **{math.ceil(remaining / high):,}–"
                f"{math.ceil(remaining / low):,} eligible awards** in this channel; "
                f"{config.text_cooldown_seconds}s cooldown, caps/exclusions apply."
            )
            if config.voice_enabled:
                text += (
                    f"\nVoice: about **{math.ceil(remaining / config.voice_xp_per_minute):,} "
                    "eligible minutes at the base rate**; channel/role bonuses "
                    "and caps may change this."
                )
            text += "\nEstimates are not guaranteed wall-clock times."
        await interaction.followup.send(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        name="xp_needed",
        description="See remaining XP for your next level or an optional target/member",
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 20)
    async def xp_needed(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 1000] | None = None,
        member: discord.Member | None = None,
    ):
        await self.show_xp_needed(interaction, level, member)

    @app_commands.command(
        name="xp-needed", description="See the total and remaining XP for a target level"
    )
    @app_commands.guild_only()
    async def xp_needed_legacy(
        self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]
    ):
        await self.show_xp_needed(interaction, level)

    @app_commands.command(
        name="xp-optout", description="Opt out and delete your XP records, or opt back in fresh"
    )
    @app_commands.guild_only()
    async def xp_optout(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        async with self.bot.service.lock_for(interaction.guild_id, interaction.user.id):
            await self.repo.optout(interaction.guild_id, interaction.user.id, enabled)
            if enabled:
                self.service.clear_member(interaction.guild_id, interaction.user.id)
        await interaction.followup.send(
            "Your XP and tracked activity were deleted; future tracking/earning is disabled. "
            "Earned roles are unchanged."
            if enabled
            else "XP earning is allowed again. Your progress starts fresh.",
            ephemeral=True,
        )

    async def changed(self, interaction, *, map_change=None, set_change=None, **changes):
        try:
            if map_change:
                field, key, value = map_change
                await self.repo.patch_map(interaction.guild_id, field, key, value)
                changes = {field: "updated"}
            elif set_change:
                field, value, present = set_change
                await self.repo.patch_set(interaction.guild_id, field, value, present)
                changes = {field: "updated"}
            else:
                await self.repo.configure(interaction.guild_id, **changes)
        except ValueError as error:
            raise UserError(str(error)) from None
        if set(changes) & {
            "enabled",
            "activity_enabled",
            "voice_enabled",
            "voice_min_members",
            "ignored_role_ids",
            "ignored_channel_ids",
        }:
            self.service.clear_voice(interaction.guild_id)
        case_id = await self.bot.store.create_case(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            actor_id=interaction.user.id,
            source="leveling",
            category="configuration",
            reason="Changed XP settings: " + ", ".join(changes),
            action="configure_levels",
        )
        await self.bot.store.finish_case(case_id, "completed", {"fields": list(changes)})
        await self.bot.service.audit(interaction.guild_id, case_id)
        await interaction.followup.send(
            f"Leveling settings saved. Case #{case_id}.", ephemeral=True
        )

    @levels.command(
        name="status", description="View leveling settings, rewards, and XP health counters"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        config = await self.repo.settings(interaction.guild_id)
        file = discord.File(
            io.BytesIO(json.dumps(config.to_dict(), indent=2).encode()),
            filename="leveling-settings.json",
        )
        await interaction.followup.send(
            f"XP earning: **{config.enabled}** · "
            f"Text: **{config.text_min_xp}–{config.text_max_xp}** "
            f"per {config.text_cooldown_seconds}s\nVoice: **{config.voice_enabled}** · "
            f"Reaction: **{config.reaction_enabled}**\n"
            f"Process counters: {dict(self.service.metrics)}",
            file=file,
            ephemeral=True,
        )

    @levels.command(
        name="enabled", description="Pause or enable XP earning without losing member progress"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def enabled(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.changed(interaction, enabled=enabled)

    @levels.command(name="text", description="Set message XP range, cooldown, and daily XP limit")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def text(
        self,
        interaction: discord.Interaction,
        minimum: app_commands.Range[int, 1, 1000] = 15,
        maximum: app_commands.Range[int, 1, 1000] = 25,
        cooldown: app_commands.Range[int, 15, 3600] = 60,
        daily_cap: app_commands.Range[int, 1, 100000] = 5000,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.changed(
            interaction,
            text_min_xp=minimum,
            text_max_xp=maximum,
            text_cooldown_seconds=cooldown,
            text_daily_cap=daily_cap,
        )

    @levels.command(
        name="voice", description="Configure voice XP; needs at least two active human members"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def voice(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        xp_per_minute: app_commands.Range[int, 1, 100] = 5,
        participants: app_commands.Range[int, 2, 25] = 2,
        daily_cap: app_commands.Range[int, 1, 100000] = 600,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.changed(
            interaction,
            voice_enabled=enabled,
            voice_xp_per_minute=xp_per_minute,
            voice_min_members=participants,
            voice_daily_cap=daily_cap,
        )

    @levels.command(
        name="reactions",
        description="Configure XP for receiving reactions; self-reactions earn nothing",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reactions_config(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        xp: app_commands.Range[int, 1, 100] = 2,
        cooldown: app_commands.Range[int, 15, 3600] = 60,
        daily_cap: app_commands.Range[int, 1, 10000] = 50,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.changed(
            interaction,
            reaction_enabled=enabled,
            reaction_xp=xp,
            reaction_cooldown_seconds=cooldown,
            reaction_daily_cap=daily_cap,
        )

    @levels.command(
        name="announcements", description="Choose a level-up channel or pause announcements"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def announcements(
        self,
        interaction: discord.Interaction,
        enabled: bool = True,
        channel: discord.TextChannel | None = None,
        every: app_commands.Range[int, 1, 100] = 1,
    ):
        await interaction.response.defer(ephemeral=True)
        if enabled and channel:
            permissions = channel.permissions_for(interaction.guild.me)
            if not (
                permissions.view_channel and permissions.send_messages and permissions.embed_links
            ):
                raise UserError("The bot needs View Channel, Send Messages, and Embed Links there.")
        await self.changed(
            interaction,
            announcements=enabled,
            announcement_channel_id=channel.id if channel else None,
            announce_every=every,
        )

    @levels.command(
        name="reward", description="Map a level to a safe role below both you and the bot"
    )
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def reward(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 1000],
        role: discord.Role,
        remove: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        actor = await interaction.guild.fetch_member(interaction.user.id)
        me = await interaction.guild.fetch_member(self.bot.user.id)
        if not actor.guild_permissions.manage_guild or not actor.guild_permissions.manage_roles:
            raise UserError("You need Manage Server and Manage Roles.")
        error = reward_error(role, me, actor)
        if error and not remove:
            raise UserError(error)
        await self.changed(
            interaction, map_change=("reward_roles", str(level), None if remove else role.id)
        )

    @levels.command(
        name="stack", description="Keep all earned reward roles or only the highest configured tier"
    )
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def stack(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.require_role_admin(interaction)
        await self.changed(interaction, stack_rewards=enabled)

    @levels.command(
        name="ignore-channel", description="Exclude a channel or category from every XP source"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel,
        ignored: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.changed(interaction, set_change=("ignored_channel_ids", channel.id, ignored))

    @levels.command(name="ignore-role", description="Exclude a role from every XP source")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ignore_role(
        self, interaction: discord.Interaction, role: discord.Role, ignored: bool = True
    ):
        await interaction.response.defer(ephemeral=True)
        await self.changed(interaction, set_change=("ignored_role_ids", role.id, ignored))

    @levels.command(
        name="multiplier", description="Set an XP multiplier for one role or channel (100 = normal)"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def multiplier(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 50, 500],
        role: discord.Role | None = None,
        channel: discord.TextChannel | discord.VoiceChannel | discord.CategoryChannel | None = None,
    ):
        if (role is None) == (channel is None):
            raise UserError("Select exactly one role or channel.")
        await interaction.response.defer(ephemeral=True)
        name = "role_multipliers" if role else "channel_multipliers"
        target = str((role or channel).id)
        await self.changed(
            interaction, map_change=(name, target, None if percent == 100 else percent)
        )

    @levels.command(
        name="tracking",
        description="Pause or resume independent activity counts without changing XP switches",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def tracking(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.changed(interaction, activity_enabled=enabled)

    @levels.command(
        name="board-channel", description="Choose where leaderboard replies are public by default"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def board_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise UserError("The bot needs View Channel, Send Messages and Embed Links there.")
        await self.changed(interaction, public_leaderboard_channel_id=channel.id)

    @levels.command(
        name="bonus",
        description="Start a temporary server-wide XP bonus or end it with 100 percent",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bonus(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 100, 500] = 200,
        hours: app_commands.Range[int, 1, 168] = 1,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.changed(
            interaction,
            bonus_percent=percent,
            bonus_until=int(time.time()) + hours * 3600 if percent > 100 else 0,
        )

    @levels.command(
        name="xp",
        description="Adjust a member's XP with a recorded reason; never announces a fake level-up",
    )
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def xp(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, -100000, 100000],
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.adjust_xp(interaction, member, amount, reason)

    async def require_role_admin(self, interaction):
        actor = await interaction.guild.fetch_member(interaction.user.id)
        me = await interaction.guild.fetch_member(self.bot.user.id)
        if not (actor.guild_permissions.manage_guild and actor.guild_permissions.manage_roles):
            raise UserError("You need Manage Server and Manage Roles.")
        if not me.guild_permissions.manage_roles:
            raise UserError("The bot needs Manage Roles for reward-affecting changes.")
        return actor

    async def adjust_xp(self, interaction, member, amount, reason, *, set_to=None):
        if (not amount and set_to is None) or member.bot or not reason.strip():
            raise UserError("Choose a human member, a nonzero amount, and a reason.")
        await interaction.response.defer(ephemeral=True)
        await self.require_role_admin(interaction)
        async with self.bot.service.lock_for(interaction.guild_id, member.id):
            case_id = await self.bot.store.create_case(
                guild_id=interaction.guild_id,
                user_id=member.id,
                actor_id=interaction.user.id,
                source="leveling",
                category="xp_adjustment",
                reason=reason,
                action="adjust_xp",
            )
            award = await self.repo.award(
                interaction.guild_id, member.id, "manual", f"case:{case_id}", amount, set_to=set_to
            )
            role_result = "not_required"
            if award:
                config = await self.repo.settings(interaction.guild_id)
                role_result = await self.service.reconcile_roles(
                    member, award.level, config, force_remove=award.amount < 0
                )
            details = {
                "requested": amount,
                "set_to": set_to,
                "applied": award.amount if award else 0,
                "roles": role_result,
            }
            await self.bot.store.finish_case(
                case_id,
                ("partial" if role_result in {"partial", "failed", "protected"} else "completed")
                if award
                else "cancelled",
                details,
            )
            await self.bot.service.audit(interaction.guild_id, case_id)
        await interaction.followup.send(
            f"Applied {details['applied']:+,} XP. Case #{case_id}. "
            + (
                f"New total: {award.total_xp:,}. Role sync: {role_result}."
                if award
                else "Opt-out or balance limits prevented a change."
            ),
            ephemeral=True,
        )

    @levels.command(
        name="reset", description="Reset one member's XP history; confirm:true is required"
    )
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def reset(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 500],
        confirm: bool = False,
    ):
        if not confirm or not reason.strip():
            raise UserError(
                "This deletes the member's XP history. Supply a reason and confirm:true."
            )
        await interaction.response.defer(ephemeral=True)
        await self.require_role_admin(interaction)
        async with self.bot.service.lock_for(interaction.guild_id, member.id):
            case_id = await self.bot.store.create_case(
                guild_id=interaction.guild_id,
                user_id=member.id,
                actor_id=interaction.user.id,
                source="leveling",
                category="xp_reset",
                reason=reason,
                action="reset_xp",
            )
            await self.repo.reset(interaction.guild_id, member.id)
            config = await self.repo.settings(interaction.guild_id)
            role_result = await self.service.reconcile_roles(member, 0, config, reset=True)
            await self.bot.store.finish_case(
                case_id,
                "partial" if role_result in {"partial", "failed", "protected"} else "completed",
                {"roles": role_result},
            )
            await self.bot.service.audit(interaction.guild_id, case_id)
        await interaction.followup.send(
            f"XP history reset; tracked activity preserved. "
            f"Role sync: {role_result}. Case #{case_id}.",
            ephemeral=True,
        )

    @levels.command(name="sync", description="Explicitly reconcile a member's level reward roles")
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def sync_rewards(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 500],
    ):
        if not reason.strip():
            raise UserError("Provide a reason.")
        await interaction.response.defer(ephemeral=True)
        await self.require_role_admin(interaction)
        async with self.bot.service.lock_for(interaction.guild_id, member.id):
            profile = await self.repo.profile(interaction.guild_id, member.id)
            config = await self.repo.settings(interaction.guild_id)
            case_id = await self.bot.store.create_case(
                guild_id=interaction.guild_id,
                user_id=member.id,
                actor_id=interaction.user.id,
                source="leveling",
                category="role_sync",
                reason=reason,
                action="sync_rewards",
            )
            result = await self.service.reconcile_roles(
                member,
                progress(profile["total_xp"]).level,
                config,
                reset=profile["opted_out"],
                force_remove=True,
            )
            await self.bot.store.finish_case(
                case_id,
                "completed" if result in {"completed", "no_rewards"} else "partial",
                {"roles": result},
            )
            await self.bot.service.audit(interaction.guild_id, case_id)
        await interaction.followup.send(f"Role sync: {result}. Case #{case_id}.", ephemeral=True)

    @manage_xp.command(name="add", description="Add XP with an audit reason")
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def manage_add(
        self,
        interaction: discord.Interaction,
        entity: discord.Member,
        amount: app_commands.Range[int, 1, 100000],
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.adjust_xp(interaction, entity, amount, reason)

    @manage_xp.command(name="subtract", description="Subtract XP without going below zero")
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def manage_subtract(
        self,
        interaction: discord.Interaction,
        entity: discord.Member,
        amount: app_commands.Range[int, 1, 100000],
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.adjust_xp(interaction, entity, -amount, reason)

    @manage_xp.command(name="set", description="Set an XP balance; confirm:true is required")
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def manage_set(
        self,
        interaction: discord.Interaction,
        entity: discord.Member,
        amount: app_commands.Range[int, 0, 1000000],
        reason: app_commands.Range[str, 1, 500],
        confirm: bool = False,
    ):
        if not confirm:
            raise UserError("Setting XP can reduce progress. Supply confirm:true and a reason.")
        await self.adjust_xp(interaction, entity, 0, reason, set_to=amount)

    @manage_level.command(
        name="set", description="Set a level using this server's XP curve; confirm:true required"
    )
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def manage_level_set(
        self,
        interaction: discord.Interaction,
        entity: discord.Member,
        level: app_commands.Range[int, 0, 1000],
        reason: app_commands.Range[str, 1, 500],
        confirm: bool = False,
    ):
        if not confirm:
            raise UserError(
                "Setting a level can reduce progress. Supply confirm:true and a reason."
            )
        await self.adjust_xp(interaction, entity, 0, reason, set_to=total_for_level(level))

    @manage_level.command(
        name="reset", description="Reset one member's XP history; activity remains"
    )
    @app_commands.checks.has_permissions(manage_guild=True, manage_roles=True)
    async def manage_reset(
        self,
        interaction: discord.Interaction,
        entity: discord.Member,
        reason: app_commands.Range[str, 1, 500],
        confirm: bool = False,
    ):
        await self.reset.callback(self, interaction, entity, reason, confirm)


async def setup(bot):
    await bot.add_cog(Leveling(bot))
