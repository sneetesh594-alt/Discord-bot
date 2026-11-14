import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from aegis.level_store import LevelRepository, XPAward
from aegis.leveling import LevelingService, reward_error
from aegis.leveling_config import LevelingConfig


@pytest.fixture
async def leveling(store, settings, message):
    message.author.is_timed_out.return_value = False
    message.author.display_name = "Test member"
    message.content = "Hello everyone in the community"
    locks = [asyncio.Lock() for _ in range(16)]
    bot = SimpleNamespace(
        settings=settings,
        store=store,
        user=SimpleNamespace(id=999),
        get_guild=lambda guild_id: message.guild if guild_id == 1 else None,
        service=SimpleNamespace(lock_for=lambda g, u: locks[hash((g, u)) % 16]),
    )
    repository = LevelRepository(store, LevelingConfig())
    await repository.configure(1, enabled=True, text_min_xp=20, text_max_xp=20, announcements=False)
    return LevelingService(bot, repository)


async def test_eligible_message_earns_xp_and_gateway_replay_does_not(leveling, message):
    await leveling.on_message(message, time.monotonic())
    await leveling.on_message(message, time.monotonic())
    profile = await leveling.repo.profile(1, 10)
    assert profile["total_xp"] == 20 and profile["text_awards"] == 1


@pytest.mark.parametrize("kind", ["empty", "short", "command", "link", "bot", "timeout", "ignored"])
async def test_ineligible_content_and_members_do_not_earn(leveling, message, kind):
    if kind in {"empty", "short", "command", "link"}:
        message.content = {
            "empty": "",
            "short": "hi",
            "command": "!spam command",
            "link": "https://example.com",
        }[kind]
    elif kind == "bot":
        message.author.bot = True
    elif kind == "timeout":
        message.author.is_timed_out.return_value = True
    else:
        await leveling.repo.configure(1, ignored_channel_ids=(message.channel.id,))
    await leveling.on_message(message, time.monotonic())
    assert (await leveling.repo.profile(1, 10))["total_xp"] == 0


async def test_local_phrase_filter_still_blocks_xp_for_moderators(leveling, message):
    message.author.guild_permissions.manage_messages = True
    await leveling.bot.store.update_policy(1, banned_phrases=("badword",))
    message.content = "This has a badword in it"
    await leveling.on_message(message, time.monotonic())
    assert (await leveling.repo.profile(1, 10))["total_xp"] == 0


async def test_normal_moderator_conversation_can_earn_xp(leveling, message):
    message.author.guild_permissions.manage_messages = True
    await leveling.on_message(message, time.monotonic())
    assert (await leveling.repo.profile(1, 10))["total_xp"] == 20


async def test_level_up_uses_configured_channel_and_does_not_ping(leveling, message):
    channel = SimpleNamespace(send=AsyncMock())
    message.guild.get_channel = lambda _: channel
    config = await leveling.repo.configure(1, announcements=True, announcement_channel_id=50)
    await leveling.after_award(message.author, message.channel, config, XPAward(80, 100, 20))
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title == "Level 1 reached!"
    assert kwargs["allowed_mentions"].everyone is False
    assert kwargs["allowed_mentions"].users is False


async def test_announcement_failure_keeps_earned_progress(leveling, message):
    response = SimpleNamespace(status=403, reason="Forbidden")
    channel = SimpleNamespace(send=AsyncMock(side_effect=discord.Forbidden(response, "No access")))
    message.guild.get_channel = lambda _: channel
    await leveling.repo.configure(1, announcements=True, announcement_channel_id=50)
    await leveling.repo.award(1, 10, "manual", "seed", 80)
    await leveling.on_message(message, time.monotonic())
    assert (await leveling.repo.profile(1, 10))["total_xp"] == 100
    assert leveling.metrics["announcement_failures"] == 1


async def test_voice_requires_a_continuous_full_minute_with_two_active_humans(leveling, message):
    guild = message.guild
    channel = SimpleNamespace(id=50, category_id=None)
    guild.afk_channel, guild.voice_channels = None, [channel]
    first = SimpleNamespace(id=10, bot=False, guild=guild, roles=[], is_timed_out=lambda: False)
    second = SimpleNamespace(id=11, bot=False, guild=guild, roles=[], is_timed_out=lambda: False)
    for member in (first, second):
        member.voice = SimpleNamespace(
            channel=channel,
            self_mute=False,
            self_deaf=False,
            mute=False,
            deaf=False,
            suppress=False,
        )
    channel.members = [first, second]
    await leveling.repo.configure(1, voice_enabled=True)
    await leveling.voice_tick(guild, monotonic_now=0, now=1000)
    await leveling.voice_tick(guild, monotonic_now=59, now=1059)
    assert (await leveling.repo.profile(1, 10))["total_xp"] == 0
    await leveling.voice_tick(guild, monotonic_now=60, now=1060)
    assert (await leveling.repo.profile(1, 10))["voice_xp"] == 5
    assert (await leveling.repo.profile(1, 11))["voice_xp"] == 5
    first.voice.self_mute = True
    await leveling.voice_tick(guild, monotonic_now=75, now=1075)
    await leveling.voice_tick(guild, monotonic_now=140, now=1140)
    assert (await leveling.repo.profile(1, 11))["voice_xp"] == 5
    first.voice.self_mute = False
    await leveling.voice_tick(guild, monotonic_now=150, now=1150)
    await leveling.voice_tick(guild, monotonic_now=210, now=1210)
    assert (await leveling.repo.profile(1, 11))["voice_xp"] == 10


async def test_reaction_xp_goes_to_recipient_and_self_reactions_are_excluded(leveling, message):
    message.created_at = datetime.now(UTC)
    message.channel.fetch_message = AsyncMock(return_value=message)
    message.guild.get_channel_or_thread = lambda _: message.channel
    reactor = SimpleNamespace(id=11, bot=False, roles=[], is_timed_out=lambda: False)
    payload = SimpleNamespace(guild_id=1, channel_id=20, message_id=100, user_id=11, member=reactor)
    await leveling.repo.configure(1, reaction_enabled=True)
    await leveling.reaction(payload)
    assert (await leveling.repo.profile(1, 10))["reaction_xp"] == 2
    assert (await leveling.repo.profile(1, 11))["total_xp"] == 0
    payload.member, payload.user_id = message.author, 10
    await leveling.reaction(payload)
    assert (await leveling.repo.profile(1, 10))["reaction_xp"] == 2


async def test_reactions_do_not_reward_a_message_flagged_by_ai(leveling, message):
    message.created_at = datetime.now(UTC)
    message.channel.fetch_message = AsyncMock(return_value=message)
    message.guild.get_channel_or_thread = lambda _: message.channel
    reactor = SimpleNamespace(id=11, bot=False, roles=[], is_timed_out=lambda: False)
    payload = SimpleNamespace(guild_id=1, channel_id=20, message_id=100, user_id=11, member=reactor)
    await leveling.repo.configure(1, reaction_enabled=True)
    case_id = await leveling.bot.store.create_case(
        guild_id=1,
        user_id=10,
        actor_id=999,
        source="ai",
        category="ai_review",
        reason="Review required",
        action="review",
        message_id=100,
    )
    await leveling.bot.store.finish_case(case_id, "review", {})
    await leveling.reaction(payload)
    assert (await leveling.repo.profile(1, 10))["total_xp"] == 0


async def test_reactions_skip_messages_with_moderation_still_in_flight(leveling, message):
    leveling.pending_messages.add((1, 100))
    await leveling.reaction(SimpleNamespace(guild_id=1, message_id=100, user_id=11))
    assert leveling.metrics["reaction_pending_moderation"] == 1


class Role:
    def __init__(self, role_id, position, guild, **permissions):
        self.id, self.position, self.guild = role_id, position, guild
        self.managed = False
        self.permissions = discord.Permissions(**permissions)

    def __ge__(self, other):
        return self.position >= other.position

    def is_default(self):
        return self.id == self.guild.id


def reward_fixture(message):
    guild = message.guild
    guild.channels = []
    guild.me.guild_permissions = discord.Permissions(manage_roles=True)
    guild.me.top_role = Role(999, 100, guild)
    message.author.top_role = Role(1, 1, guild)
    message.author.add_roles = AsyncMock()
    message.author.remove_roles = AsyncMock()
    guild.fetch_member = AsyncMock(return_value=message.author)
    roles = {10: Role(10, 2, guild), 20: Role(20, 3, guild), 30: Role(30, 4, guild)}
    guild.get_role = roles.get
    return roles


@pytest.mark.parametrize(
    "permission", ["administrator", "manage_roles", "ban_members", "manage_channels"]
)
def test_level_rewards_reject_staff_permissions(message, permission):
    roles = reward_fixture(message)
    roles[10].permissions.update(**{permission: True})
    assert reward_error(roles[10], message.guild.me)


def test_reward_roles_reject_managed_higher_roles_and_elevated_channel_overrides(message):
    roles = reward_fixture(message)
    role = roles[10]
    role.managed = True
    assert reward_error(role, message.guild.me)
    role.managed = False
    role.position = 101
    assert reward_error(role, message.guild.me)
    role.position = 2
    message.guild.channels = [
        SimpleNamespace(overwrites_for=lambda _: discord.PermissionOverwrite(manage_messages=True))
    ]
    assert reward_error(role, message.guild.me)


async def test_reward_stacking_highest_only_and_unrelated_roles_are_preserved(leveling, message):
    roles = reward_fixture(message)
    config = LevelingConfig(reward_roles={"1": 10, "5": 20}, stack_rewards=True)
    await leveling.reconcile_roles(message.author, 5, config)
    assert message.author.add_roles.await_count == 2
    message.author.add_roles.reset_mock()
    message.author.roles = [roles[10], roles[30]]
    await leveling.reconcile_roles(message.author, 5, replace(config, stack_rewards=False))
    assert message.author.add_roles.await_args.args[0].id == 20
    assert message.author.remove_roles.await_args.args[0].id == 10
    assert all(call.args[0].id != 30 for call in message.author.remove_roles.await_args_list)


async def test_reward_permission_changes_are_checked_again_at_grant_time(leveling, message):
    roles = reward_fixture(message)
    roles[10].permissions.administrator = True
    await leveling.reconcile_roles(message.author, 5, LevelingConfig(reward_roles={"1": 10}))
    message.author.add_roles.assert_not_awaited()
    assert leveling.metrics["reward_role_invalid"] == 1


async def test_server_owner_and_higher_members_do_not_receive_automatic_roles(leveling, message):
    reward_fixture(message)
    message.author.id = message.guild.owner_id
    await leveling.reconcile_roles(message.author, 5, LevelingConfig(reward_roles={"1": 10}))
    message.author.add_roles.assert_not_awaited()
