"""Bounded event queue, edited-message checks, and scheduled retention cleanup."""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)


class AutoModeration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=bot.settings.runtime.queue_size)
        self.workers: list[asyncio.Task] = []

    async def cog_load(self):
        self.workers = [
            asyncio.create_task(self.worker(), name=f"automod-{i}")
            for i in range(self.bot.settings.runtime.workers)
        ]
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        # Drop transient references to message bodies at shutdown.
        while not self.queue.empty():
            item = self.queue.get_nowait()
            self.mark_pending(item, add=False)
            self.queue.task_done()

    def mark_pending(self, queued, *, add):
        item, _, edited = queued
        leveling = getattr(self.bot, "leveling", None)
        pending = getattr(leveling, "pending_messages", None)
        if pending is not None and item is not None:
            key = (item.guild_id, item.message_id) if edited else (item.guild.id, item.id)
            pending.add(key) if add else pending.discard(key)

    def enqueue(self, item):
        try:
            self.queue.put_nowait(item)
            self.mark_pending(item, add=True)
        except asyncio.QueueFull:
            self.bot.service.metrics["queue_dropped"] += 1
            # First occurrence and every hundred thereafter, without content.
            dropped = self.bot.service.metrics["queue_dropped"]
            if dropped == 1 or dropped % 100 == 0:
                log.warning("Moderation queue full; skipped events=%s", dropped)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild and not message.author.bot and not message.webhook_id:
            self.enqueue((message, time.monotonic(), False))

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        # Raw events cover uncached/old messages. Ignore embed-only updates.
        if payload.guild_id and "content" in payload.data:
            self.enqueue((payload, time.monotonic(), True))

    async def worker(self):
        while True:
            item, received_at, edited = await self.queue.get()
            try:
                if edited:
                    guild = self.bot.get_guild(item.guild_id)
                    channel = guild.get_channel_or_thread(item.channel_id) if guild else None
                    if channel is None or not hasattr(channel, "fetch_message"):
                        continue
                    try:
                        message = await channel.fetch_message(item.message_id)
                    except discord.HTTPException:
                        self.bot.service.metrics["edit_fetch_failed"] += 1
                        continue
                    if not isinstance(message.author, discord.Member) and not message.author.bot:
                        try:
                            message.author = await guild.fetch_member(message.author.id)
                        except discord.HTTPException:
                            self.bot.service.metrics["edit_fetch_failed"] += 1
                            continue
                else:
                    message = item
                if self.bot.get_guild(message.guild.id) is None:
                    continue
                leveling = getattr(self.bot, "leveling", None)
                can_earn_xp = await self.bot.service.process(message, received_at, edited=edited)
                if can_earn_xp and not edited and leveling is not None:
                    await leveling.on_message(message, received_at)
                self.bot.service.metrics["processed"] += 1
                self.bot.last_processed_at = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Avoid tracebacks including third-party HTTP bodies or message text.
                self.bot.service.metrics["processing_errors"] += 1
                log.error("Moderation worker failed (%s)", type(error).__name__)
            finally:
                self.mark_pending((item, received_at, edited), add=False)
                self.queue.task_done()

    @tasks.loop(hours=6)
    async def cleanup(self):
        try:
            count = await self.bot.store.prune(self.bot.settings.runtime.retention_days)
            log.info("Retention cleanup removed %s cases", count)
        except Exception as error:
            log.error("Retention cleanup failed (%s)", type(error).__name__)

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AutoModeration(bot))
