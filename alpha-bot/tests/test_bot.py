import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from aegis.bot import AegisBot
from aegis.cogs.automod import AutoModeration


async def test_all_cogs_load_and_commands_serialize_without_network(settings):
    async with AegisBot(settings) as bot:
        await bot.prepare(sync=False)
        commands = bot.tree.get_commands()
        names = {command.name for command in commands}
        assert {"warn", "timeout", "ban", "appeal", "config", "setup", "privacy"} <= names
        for command in commands:
            payload = command.to_dict(bot.tree)
            assert len(payload["description"]) <= 100
            assert payload.get("dm_permission") is False or payload.get("contexts") == [0]
            if isinstance(command, discord.app_commands.Group):
                assert all(
                    sub.checks
                    for sub in command.walk_commands()
                    if isinstance(sub, discord.app_commands.Command)
                )
        warn = bot.tree.get_command("warn")
        assert warn.checks and warn.default_permissions.manage_messages
        assert bot.intents.message_content
        assert not bot.intents.members and not bot.intents.presences
        assert bot.intents.voice_states and bot.intents.guild_reactions
        assert {"rank", "leaderboard", "xp-needed", "xp-optout", "levels"} <= names
        assert {"xp_needed", "statistics", "time", "placement", "xp-leaderboard", "manage"} <= names
        boards = bot.tree.get_command("leaderboard")
        assert {sub.name for sub in boards.commands} == {"xp", "text", "voice", "reaction"}
        alias = bot.tree.get_command("config").get_command("leveling")
        assert alias.get_command("reward").binding is bot.get_cog("Leveling")
        assert (
            alias.get_command("reward").checks
            == bot.tree.get_command("levels").get_command("reward").checks
        )


def test_queue_is_bounded_and_drops_are_visible(settings):
    bot = SimpleNamespace(
        settings=settings, service=SimpleNamespace(metrics=__import__("collections").Counter())
    )
    cog = AutoModeration(bot)
    for _ in range(settings.runtime.queue_size + 3):
        cog.enqueue((None, 0, False))
    assert cog.queue.qsize() == settings.runtime.queue_size
    assert bot.service.metrics["queue_dropped"] == 3


async def test_dm_bot_and_webhook_messages_are_not_queued(settings):
    bot = SimpleNamespace(settings=settings)
    cog = AutoModeration(bot)
    for guild, is_bot, webhook in [
        (None, False, None),
        (object(), True, None),
        (object(), False, 123),
    ]:
        await cog.on_message(
            SimpleNamespace(guild=guild, author=SimpleNamespace(bot=is_bot), webhook_id=webhook)
        )
    assert cog.queue.empty()


async def test_uncached_edit_fetches_member_when_rest_returns_plain_user(settings, message):
    import asyncio

    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    member = message.author
    message.author = SimpleNamespace(id=member.id, bot=False)
    guild = SimpleNamespace(
        get_channel_or_thread=lambda _: channel, fetch_member=AsyncMock(return_value=member)
    )
    service = SimpleNamespace(process=AsyncMock(), metrics=__import__("collections").Counter())
    bot = SimpleNamespace(settings=settings, service=service, get_guild=lambda _: guild)
    cog = AutoModeration(bot)
    task = asyncio.create_task(cog.worker())
    cog.enqueue((SimpleNamespace(guild_id=1, channel_id=20, message_id=100), 1.0, True))
    await asyncio.wait_for(cog.queue.join(), timeout=2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    guild.fetch_member.assert_awaited_once_with(member.id)
    service.process.assert_awaited_once_with(message, 1.0, edited=True)


async def test_health_file_distinguishes_connection_processing_and_stopped_state(settings):
    async with AegisBot(settings) as bot:
        await bot.prepare(sync=False)
        bot.command_sync_completed = True
        bot.last_processed_at = 1234.5
        bot.service.metrics["processed"] = 2
        bot.service.metrics["processing_errors"] = 1
        await asyncio.gather(*(bot.write_health() for _ in range(5)))
        path = settings.database_path.with_suffix(".health.json")
        result = json.loads(path.read_text())
        assert result["version"] == "1.3.1"
        assert result["commands_synced"] is True
        assert result["gateway_connected"] is False  # No Gateway connection in this test.
        assert result["last_message_processed_at"] == 1234.5
        assert result["processed_messages"] == 2 and result["processing_errors"] == 1
        assert settings.token not in path.read_text()
    result = json.loads(path.read_text())
    assert result["stopped"] is True and result["gateway_connected"] is False


def test_startup_network_timeout_is_retryable_without_exposing_token(settings, monkeypatch, capsys):
    from aegis import __main__ as cli

    async def unavailable(_):
        raise TimeoutError()

    monkeypatch.setattr("sys.argv", ["aegis"])
    monkeypatch.setattr(cli.Settings, "load", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "configure", lambda _: None)
    monkeypatch.setattr(cli, "run", unavailable)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    error = capsys.readouterr().err
    assert "connection failed" in error and settings.token not in error
