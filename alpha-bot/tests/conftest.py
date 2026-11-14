from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from aegis.config import AIConfig, GuildPolicy, RuntimeConfig, Settings
from aegis.store import Store


@pytest.fixture
async def store(tmp_path):
    repository = Store(tmp_path / "test.sqlite3", GuildPolicy())
    await repository.open()
    yield repository
    await repository.close()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        token="fake-discord-token",
        api_key="",
        policy=GuildPolicy(),
        runtime=RuntimeConfig(),
        ai=AIConfig(),
        database_path=tmp_path / "bot.sqlite3",
        log_file=None,
        log_level="INFO",
        ai_enabled=False,
        moderation_model="omni-moderation-latest",
        dev_guild_id=None,
        sync_commands=False,
    )


def message_fixture():
    permissions = SimpleNamespace(
        administrator=False, manage_messages=False, moderate_members=False
    )
    me = SimpleNamespace(
        id=999, top_role=100, guild_permissions=SimpleNamespace(moderate_members=True)
    )
    guild = SimpleNamespace(id=1, me=me, owner_id=99, name="Test community")
    member = MagicMock(spec=discord.Member)
    member.id, member.guild, member.top_role = 10, guild, 1
    member.bot, member.roles, member.guild_permissions = False, [], permissions
    member.timed_out_until = None
    member.timeout, member.send = AsyncMock(), AsyncMock()
    message = SimpleNamespace(
        id=100,
        guild=guild,
        author=member,
        channel=SimpleNamespace(id=20),
        content="hello",
        mentions=[],
        role_mentions=[],
        delete=AsyncMock(),
        webhook_id=None,
    )
    return message


@pytest.fixture
def message():
    return message_fixture()
