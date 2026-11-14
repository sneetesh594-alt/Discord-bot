"""Public transparency and operator health information."""

from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from aegis import __version__

log = logging.getLogger(__name__)


class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.health_loop.start()

    async def cog_unload(self):
        self.health_loop.cancel()

    @tasks.loop(seconds=30)
    async def health_loop(self):
        try:
            await self.bot.write_health()
        except OSError as error:
            log.error("Health file update failed (%s)", type(error).__name__)

    @app_commands.command(description="Check the bot's Discord Gateway latency")
    @app_commands.guild_only()
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Pong · {self.bot.latency * 1000:.0f} ms", ephemeral=True
        )

    @app_commands.command(description="Open Alpha's Discord-login server dashboard")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dashboard(self, interaction: discord.Interaction):
        config = self.bot.settings.dashboard
        text = (
            f"Alpha dashboard: {config.origin}\nOpen it on the PC running Alpha. "
            "Sign in with Discord and select a server you manage."
            if config.enabled
            else "The local dashboard is disabled. The operator can start it with Start-Alpha.cmd."
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(description="See the bot's commands and how to get started")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction, command: str | None = None):
        embed = discord.Embed(
            title="Alpha bot · moderation & levels", colour=discord.Colour.blurple()
        )
        embed.description = (
            "**Everyone:** `/rank`, `/statistics`, `/xp_needed`, `/time`, `/placement`, "
            "`/leaderboard xp`, `/leaderboard text`, `/leaderboard voice`, "
            "`/leaderboard reaction`, `/xp-optout`, `/ping`, `/rules`, `/privacy`, `/appeal`.\n"
            "Old `/xp-needed` and `/xp-leaderboard` remain available. Activity counts "
            "start with the new tracker; they are separate from XP.\n\n"
            "**Moderators:** `/warn`, `/warnings`, `/timeout`, `/mute`, `/untimeout`, "
            "`/kick`, `/ban`, `/unban`, `/case`, `/case-void`, `/review`, "
            "`/appeals`, `/appeal-resolve`\n\n"
            "**Manage Server:** `/setup`, `/config show`, `/config mode`, `/config ai`, "
            "`/config invites`, `/config phrase`, `/config domain`, "
            "`/config exempt-role`, `/config exempt-channel`, `/config thresholds`, `/status`, "
            "`/levels status`, `/config leveling ...` and `/levels` settings.\n"
            "`/dashboard` opens the local Discord-login dashboard.\n"
            "**Manage Server + Manage Roles:** `/manage xp add`, `subtract`, `set`; "
            "`/manage level set`, `reset`; `/levels reward`, `/levels sync`. "
            "Setting/resetting progress needs confirmation and a reason.\n\n"
            "Start with `/setup` and a private log channel. New servers use **observe** mode. "
            "AI signals always go to human review."
        )
        if command:
            parts = command.lstrip("/").split()
            selected = self.bot.tree.get_command(parts[0]) if parts else None
            for part in parts[1:]:
                selected = (
                    selected.get_command(part) if isinstance(selected, app_commands.Group) else None
                )
            if selected is None:
                await interaction.response.send_message(
                    "Command not found. Try /help with no argument.", ephemeral=True
                )
                return
            embed.title = f"/{selected.qualified_name}"
            embed.description = selected.description
            if isinstance(selected, app_commands.Group):
                embed.add_field(
                    name="Commands",
                    value="\n".join(f"`/{sub.qualified_name}`" for sub in selected.commands)[:1024],
                    inline=False,
                )
            else:
                options = "\n".join(
                    f"`{p.name}` · {'required' if p.required else 'optional'}"
                    for p in selected.parameters
                )
                embed.add_field(name="Options", value=options or "No options.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="View this server's current automated moderation rules")
    @app_commands.guild_only()
    async def rules(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        await interaction.followup.send(
            f"**Aegis moderation · {policy.mode} mode**\n"
            f"Flood: more than {policy.spam_max_messages} messages in "
            f"{policy.spam_window_seconds}s. Duplicates: {policy.duplicate_threshold}. "
            f"Same text across {policy.cross_channel_threshold} channels. "
            f"More than {policy.max_mentions} user/role mentions is mention spam.\n"
            f"Invite blocking: {policy.block_invites}. Server phrase and hostname rules apply.\n"
            f"In enforce mode spam may be deleted and timed out for "
            f"{policy.spam_timeout_seconds // 60} minutes. Phrase/link violations create warnings; "
            f"every {policy.warning_threshold} warnings in {policy.warning_days} days may "
            f"trigger a {policy.warning_timeout_seconds // 60}-minute timeout.\n"
            f"External AI review: {self.bot.settings.ai_enabled and policy.ai_enabled}. "
            "It never automatically punishes a member. Moderator and configured exemptions apply. "
            "Appeal using `/appeal`, or contact moderators if you cannot access this server.",
            ephemeral=True,
        )

    @app_commands.command(description="Learn what data the bot processes and retains")
    @app_commands.guild_only()
    async def privacy(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        enabled = self.bot.settings.ai_enabled and policy.ai_enabled
        await interaction.followup.send(
            "Aegis processes server message text in memory for local checks. It ignores DMs and "
            "bot/webhook messages. Moderation and XP have separate exemptions. "
            "It does not download attachments "
            "or store message bodies. Cases contain Discord IDs, rule categories, action results, "
            "AI scores when applicable, and moderator-supplied reasons. Appeals store your "
            f"submitted text. Cases and linked appeals expire after "
            f"{self.bot.settings.runtime.retention_days} days, with cleanup every 6 hours. "
            "Data for a server is removed when the bot leaves it. Host backups and Discord's "
            "own log messages have separate retention.\n"
            f"**External AI enabled here: {enabled}.** When enabled, selected message text "
            "(up to 4,000 characters) is sent to OpenAI's Moderation API without added user IDs "
            "or conversation history. Text itself may include personal information. "
            "The bot does not train models. Ask server moderators about their policy and "
            "the operator about data requests.\n"
            "Leveling stores XP and separate tracked message/voice/reaction counts, plus event "
            "metadata. Voice/reaction counts can work while their XP switches are off. Events "
            "default to 90-day retention; profiles expire after 365 days of inactivity. Voice "
            "uses connection/mute metadata, never audio. Rank cards read avatars transiently. "
            "`/xp-optout enabled:true` erases your XP/activity and stops tracking/earning. "
            "A minimal preference marker remains; earned roles are unchanged. "
            "See the shipped PRIVACY.md for details.",
            ephemeral=True,
        )

    @app_commands.command(
        description="Inspect queue health, skipped AI checks, and process counters"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        cog = self.bot.get_cog("AutoModeration")
        counters = json.dumps(dict(self.bot.service.metrics), sort_keys=True)
        ai = json.dumps(dict(self.bot.classifier.metrics), sort_keys=True)
        await interaction.response.send_message(
            f"Alpha {__version__} · Gateway connected: {self.bot.is_ready()}\n"
            f"Last message processed (UTC epoch): {self.bot.last_processed_at}\n"
            f"Queue: {cog.queue.qsize()}/{cog.queue.maxsize}\n"
            f"Workers: {sum(not task.done() for task in cog.workers)}\n"
            f"Process moderation counters: {counters}\nAI counters: {ai}\n"
            f"AI requests today (UTC, since process start): {self.bot.classifier.daily_count}\n"
            "These are aggregate counters for this bot process. A healthy Gateway does not "
            "prove coverage; check dropped events, errors, and permissions.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Utility(bot))
