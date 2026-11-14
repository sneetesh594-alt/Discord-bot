"""Persisted, per-server settings managed through Discord slash commands."""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from aegis.security import UserError, private_log_channel


class Administration(commands.Cog):
    config = app_commands.Group(
        name="config",
        description="Manage this server's moderation settings",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot):
        self.bot = bot

    async def save(self, interaction, **changes):
        await self.bot.store.update_policy(interaction.guild_id, **changes)
        # Save configuration changes as audit cases without recording secret/content data.
        case_id = await self.bot.store.create_case(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            actor_id=interaction.user.id,
            source="configuration",
            category="configuration",
            reason="Updated settings: " + ", ".join(changes),
            action="configure",
        )
        await self.bot.store.finish_case(case_id, "completed", {"fields": list(changes)})
        await self.bot.service.audit(interaction.guild_id, case_id)
        self.bot.service.clear_guild(interaction.guild_id)
        await interaction.followup.send(
            f"Settings saved for this server. Case #{case_id}.", ephemeral=True
        )

    @app_commands.command(description="Set a private moderation log channel and keep observe mode")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, log_channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        me = interaction.guild.me
        perms = log_channel.permissions_for(me)
        if not private_log_channel(log_channel):
            raise UserError("Hide this log channel from @everyone before selecting it.")
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError("The bot needs View Channel, Send Messages, and Embed Links there.")
        await self.save(interaction, mod_log_channel_id=log_channel.id, mode="observe")

    @config.command(name="show", description="View this server's current moderation policy")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        # An attached JSON file handles up to 200 phrase/exemption entries safely.
        import io

        data = json.dumps(policy.to_dict(), ensure_ascii=False, indent=2)
        await interaction.followup.send(
            f"Mode: **{policy.mode}**; external AI: **"
            f"{self.bot.settings.ai_enabled and policy.ai_enabled}**",
            file=discord.File(io.BytesIO(data.encode("utf-8")), filename="server-policy.json"),
            ephemeral=True,
        )

    @config.command(
        name="mode", description="Switch automatic local rules between observe and enforce"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mode(self, interaction: discord.Interaction, mode: str):
        if mode not in {"observe", "enforce"}:
            raise UserError("Choose observe or enforce.")
        await interaction.response.defer(ephemeral=True)
        if mode == "enforce":
            policy = await self.bot.store.policy(interaction.guild_id)
            channel = interaction.guild.get_channel(policy.mod_log_channel_id)
            if not isinstance(channel, discord.TextChannel) or not private_log_channel(channel):
                raise UserError("Run /setup with a private moderation log channel first.")
            me = interaction.guild.me
            if (
                not me.guild_permissions.manage_messages
                or not me.guild_permissions.moderate_members
            ):
                raise UserError("Enforce mode needs Manage Messages and Moderate Members.")
        await self.save(interaction, mode=mode)

    @mode.autocomplete("mode")
    async def mode_autocomplete(self, interaction, current):
        return [
            app_commands.Choice(name=m, value=m)
            for m in ("observe", "enforce")
            if current.lower() in m
        ]

    @config.command(
        name="ai", description="Opt this server into external AI review (global switch required)"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ai(self, interaction: discord.Interaction, enabled: bool):
        if enabled and not self.bot.settings.ai_enabled:
            raise UserError(
                "The operator must first configure AI_ENABLED and OPENAI_API_KEY locally."
            )
        await interaction.response.defer(ephemeral=True)
        await self.save(interaction, ai_enabled=enabled)

    @config.command(name="invites", description="Enable or disable the Discord invite rule")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def invites(self, interaction: discord.Interaction, blocked: bool):
        await interaction.response.defer(ephemeral=True)
        await self.save(interaction, block_invites=blocked)

    @config.command(
        name="phrase", description="Add or remove a whole phrase (English, Hindi, or Hinglish)"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def phrase(
        self,
        interaction: discord.Interaction,
        phrase: app_commands.Range[str, 1, 64],
        add: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        phrases = list(policy.banned_phrases)
        phrase = phrase.strip().casefold()
        if not phrase or not any(c.isalpha() for c in phrase):
            raise UserError("A phrase must contain letters.")
        if add and phrase not in phrases:
            phrases.append(phrase)
        elif not add and phrase in phrases:
            phrases.remove(phrase)
        try:
            await self.save(interaction, banned_phrases=phrases)
        except ValueError:
            raise UserError("Phrase list limit is 200.") from None

    @config.command(
        name="domain", description="Block or unblock an exact hostname and its subdomains"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def domain(
        self,
        interaction: discord.Interaction,
        domain: app_commands.Range[str, 1, 253],
        blocked: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        domains = list(policy.blocked_domains)
        domain = domain.strip().lower()
        if blocked and domain not in domains:
            domains.append(domain)
        elif not blocked and domain in domains:
            domains.remove(domain)
        try:
            await self.save(interaction, blocked_domains=domains)
        except ValueError:
            raise UserError(
                "Use a lowercase ASCII hostname, e.g. scam.example; maximum 200."
            ) from None

    @config.command(
        name="exempt-role", description="Add or remove a role exemption from automatic checks"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def exempt_role(
        self, interaction: discord.Interaction, role: discord.Role, exempt: bool = True
    ):
        if role.is_default():
            raise UserError("Do not exempt @everyone; use observe mode to pause enforcement.")
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        ids = set(policy.exempt_role_ids)
        ids.add(role.id) if exempt else ids.discard(role.id)
        if len(ids) > 200:
            raise UserError("Maximum 200 role exemptions.")
        await self.save(interaction, exempt_role_ids=sorted(ids))

    @config.command(name="exempt-channel", description="Add or remove a text channel exemption")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def exempt_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel, exempt: bool = True
    ):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        ids = set(policy.exempt_channel_ids)
        ids.add(channel.id) if exempt else ids.discard(channel.id)
        if len(ids) > 200:
            raise UserError("Maximum 200 channel exemptions.")
        await self.save(interaction, exempt_channel_ids=sorted(ids))

    @config.command(
        name="thresholds", description="Tune spam, mentions, and warning escalation for this server"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def thresholds(
        self,
        interaction: discord.Interaction,
        messages: app_commands.Range[int, 2, 100] = 5,
        window_seconds: app_commands.Range[int, 1, 120] = 10,
        mentions: app_commands.Range[int, 1, 100] = 5,
        warnings: app_commands.Range[int, 1, 100] = 3,
        timeout_minutes: app_commands.Range[int, 1, 40320] = 5,
    ):
        await interaction.response.defer(ephemeral=True)
        await self.save(
            interaction,
            spam_max_messages=messages,
            spam_window_seconds=window_seconds,
            max_mentions=mentions,
            warning_threshold=warnings,
            spam_timeout_seconds=timeout_minutes * 60,
        )


async def setup(bot):
    await bot.add_cog(Administration(bot))
