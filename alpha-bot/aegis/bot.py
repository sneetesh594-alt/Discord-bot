"""Lifecycle, explicitly scoped intents, and centralized interaction error handling."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack

import discord
from discord import app_commands
from discord.ext import commands

from aegis import __version__
from aegis.ai import AIClassifier
from aegis.config import Settings
from aegis.level_store import LevelRepository
from aegis.leveling import LevelingService
from aegis.security import UserError
from aegis.service import ModerationService
from aegis.store import Store

log = logging.getLogger(__name__)
EXTENSIONS = ("utility", "moderation", "admin", "leveling", "automod")


class CommandTree(app_commands.CommandTree):
    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            message = str(original)
        elif isinstance(original, app_commands.MissingPermissions):
            message = "You do not have permission to use this command."
        elif isinstance(original, app_commands.BotMissingPermissions):
            message = "The bot is missing: " + ", ".join(original.missing_permissions)
        elif isinstance(original, app_commands.CommandOnCooldown):
            message = f"Try again in {original.retry_after:.0f} seconds."
        elif isinstance(original, app_commands.CheckFailure):
            message = "This command is not available here or you lack permission."
        elif isinstance(original, discord.Forbidden):
            message = "Discord denied this action. Check permissions, channel overrides, and roles."
        elif isinstance(original, discord.NotFound):
            message = "The member, ban, or message could not be found."
        else:
            message = "The command failed. Ask the operator to check the bot's error counters/logs."
            log.error("Slash command failed (%s)", type(original).__name__)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class AegisBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        intents.voice_states = True
        intents.guild_reactions = True
        member_cache = discord.MemberCacheFlags.none()
        member_cache.voice = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
            member_cache_flags=member_cache,
            allowed_mentions=discord.AllowedMentions.none(),
            tree_cls=CommandTree,
            max_messages=100,  # bounded transient Discord cache; no persistent message bodies
        )
        self.settings = settings
        self.started_at = time.time()
        self.last_ready_at = None
        self.last_processed_at = None
        self.command_sync_completed = False
        self.health_lock = asyncio.Lock()
        self.store = Store(settings.database_path, settings.policy, settings.runtime.retention_days)
        self.classifier = AIClassifier(
            settings.ai, settings.api_key if settings.ai_enabled else "", settings.moderation_model
        )
        self.service = ModerationService(self, self.store, self.classifier, settings)
        self.leveling = LevelingService(self, LevelRepository(self.store, settings.leveling))

    async def setup_hook(self):
        await self.prepare(sync=self.settings.sync_commands)

    async def prepare(self, *, sync: bool):
        await self.store.open()
        await self.store.prune(self.settings.runtime.retention_days)
        await self.leveling.repo.prune(
            self.settings.runtime.xp_event_retention_days,
            self.settings.runtime.xp_profile_retention_days,
        )
        for extension in EXTENSIONS:
            await self.load_extension(f"aegis.cogs.{extension}")
        if sync:
            if self.settings.dev_guild_id:
                guild = discord.Object(id=self.settings.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            log.info("Synced %s top-level application commands", len(synced))
            self.command_sync_completed = True

    async def on_ready(self):
        self.last_ready_at = time.time()
        await self.write_health()
        log.info("Aegis connected as user=%s guilds=%s", self.user.id, len(self.guilds))

    async def write_health(self, *, stopped=False):
        cog = self.get_cog("AutoModeration")
        payload = {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
            "gateway_connected": self.is_ready() and not stopped,
            "stopped": stopped,
            "last_ready_at": self.last_ready_at,
            "last_message_processed_at": self.last_processed_at,
            "processed_messages": self.service.metrics["processed"],
            "processing_errors": self.service.metrics["processing_errors"],
            "queue_dropped": self.service.metrics["queue_dropped"],
            "queue_size": cog.queue.qsize() if cog else 0,
            "commands_synced": self.command_sync_completed,
        }
        path = self.settings.database_path.with_suffix(".health.json")

        def write():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)

        async with self.health_lock:
            await asyncio.to_thread(write)

    async def on_message(self, message):
        # Slash commands only. Cog listeners still receive server messages.
        pass

    async def on_guild_remove(self, guild):
        # Wait for in-flight member actions, then remove data. Queued automation
        # re-checks guild membership after acquiring its lock before writing.
        async with AsyncExitStack() as stack:
            for lock in self.service.locks:
                await stack.enter_async_context(lock)
            await self.store.remove_guild(guild.id)
            self.service.clear_guild(guild.id)
            self.classifier.clear_guild(guild.id)
            self.leveling.clear_guild(guild.id)
        log.info("Removed stored guild data guild=%s", guild.id)

    async def close(self):
        for extension in list(self.extensions):
            await self.unload_extension(extension)
        await self.classifier.close()
        await self.store.close()
        await super().close()
        await self.write_health(stopped=True)
