import asyncio
import json
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from aegis.activity_store import ActivityRepository
from aegis.cogs.automod import AutoModeration
from aegis.cogs.leveling import Leveling
from aegis.leaderboards import LeaderboardView, board_embed
from aegis.level_store import MIGRATION_2, LevelRepository
from aegis.leveling import LevelingService
from aegis.leveling_config import LevelingConfig
from aegis.rules import MessageSample, RuleEngine
from aegis.runtime import InstanceLock
from aegis.security import UserError
from aegis.service import ModerationService
from aegis.store import SCHEMA, Store


@pytest.fixture
async def progression(store, settings, message):
    locks = [asyncio.Lock() for _ in range(256)]
    message.author.is_timed_out.return_value = False
    message.author.display_name = "Test member"
    message.content = "A useful community conversation"
    bot = SimpleNamespace(
        settings=settings,
        store=store,
        user=SimpleNamespace(id=999),
        get_guild=lambda guild: message.guild if guild == 1 else None,
        service=SimpleNamespace(
            lock_for=lambda g, u: locks[hash((g, u)) % 256], audit=AsyncMock(), metrics=Counter()
        ),
    )
    repo = LevelRepository(store, LevelingConfig())
    await repo.configure(1, enabled=True, text_min_xp=20, text_max_xp=20, announcements=False)
    service = LevelingService(bot, repo)
    bot.leveling = service
    return service


def interaction(message, user=None):
    return SimpleNamespace(
        guild_id=1,
        guild=message.guild,
        channel_id=20,
        channel=message.channel,
        user=user or message.author,
        app_permissions=discord.Permissions.none(),
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


async def test_zero_warnings_never_escalate_and_threshold_is_claimed_once(store, settings, message):
    bot = SimpleNamespace(user=SimpleNamespace(id=999), get_guild=lambda _: message.guild)
    service = ModerationService(bot, store, SimpleNamespace(), settings)
    policy = await store.update_policy(1, warning_threshold=2)
    assert await service.warning_escalation(message.author, policy, 99) == "not_due"
    message.author.timeout.assert_not_awaited()
    for _ in range(2):
        case = await store.create_case(
            guild_id=1,
            user_id=10,
            actor_id=99,
            source="manual",
            category="test",
            reason="Synthetic regression",
            action="warn",
        )
        await store.finish_case(case, "completed", {}, warning=True)
    assert await service.warning_escalation(message.author, policy, case) == "timeout_applied"
    restarted = ModerationService(bot, store, SimpleNamespace(), settings)
    assert await restarted.warning_escalation(message.author, policy, case) == "already_escalated"
    message.author.timeout.assert_awaited_once()
    await store.void_case(1, case)
    assert await service.warning_escalation(message.author, policy, case) == "not_due"


async def test_rank_reset_race_cannot_restore_roles(progression, message):
    service = progression
    await service.repo.award(1, 10, "manual", "seed", 1150)
    captured, resume = asyncio.Event(), asyncio.Event()
    real_profile = service.repo.profile

    async def stale_profile(*args):
        result = await real_profile(*args)
        captured.set()
        await resume.wait()
        return result

    service.repo.profile = stale_profile
    service.reconcile_roles = AsyncMock()
    cog = Leveling(service.bot)
    task = asyncio.create_task(Leveling.rank.callback(cog, interaction(message)))
    await asyncio.wait_for(captured.wait(), 2)
    await service.repo.reset(1, 10)
    resume.set()
    await asyncio.wait_for(task, 2)
    service.reconcile_roles.assert_not_awaited()
    assert (await real_profile(1, 10))["total_xp"] == 0


async def test_atomic_map_and_exclusion_changes_survive_concurrency(progression):
    repo = progression.repo
    await asyncio.gather(
        *(repo.patch_map(1, "reward_roles", str(n), n + 100) for n in range(1, 21))
    )
    await asyncio.gather(*(repo.patch_set(1, "ignored_channel_ids", n, True) for n in range(1, 21)))
    repo.cache.clear()
    config = await repo.settings(1)
    assert len(config.reward_roles) == 20
    assert config.ignored_channel_ids == tuple(range(1, 21))
    assert config.text_min_xp == 20


async def test_messages_count_during_xp_cooldown_but_not_replays_or_commands(progression, message):
    await progression.on_message(message, time.monotonic())
    message.id, message.content = 101, "Another different useful contribution"
    await progression.on_message(message, time.monotonic())
    await progression.on_message(message, time.monotonic())
    message.id, message.content = 102, "/placement"
    await progression.on_message(message, time.monotonic())
    assert (await progression.activity.profile(1, 10))["messages"] == 2
    xp = await progression.repo.profile(1, 10)
    assert xp["total_xp"] == 20 and xp["text_awards"] == 1


def voice_members(message, count=2):
    guild = message.guild
    channel = SimpleNamespace(id=50, category_id=None)
    guild.afk_channel, guild.voice_channels = None, [channel]
    members = []
    for user in range(10, 10 + count):
        member = SimpleNamespace(
            id=user,
            guild=guild,
            bot=False,
            roles=[],
            is_timed_out=lambda: False,
            display_name=f"Member {user}",
        )
        member.voice = SimpleNamespace(
            channel=channel,
            self_mute=False,
            self_deaf=False,
            mute=False,
            deaf=False,
            suppress=False,
        )
        members.append(member)
    channel.members = members
    return channel, members


async def test_third_join_preserves_voice_progress_and_stats_work_with_voice_xp_off(
    progression, message, monkeypatch
):
    channel, members = voice_members(message)
    await progression.voice_tick(message.guild, monotonic_now=0, now=1000)
    await progression.voice_tick(message.guild, monotonic_now=59, now=1059)
    third = SimpleNamespace(
        id=12, guild=message.guild, bot=False, roles=[], is_timed_out=lambda: False
    )
    third.voice = SimpleNamespace(
        channel=channel, self_mute=False, self_deaf=False, mute=False, deaf=False, suppress=False
    )
    channel.members.append(third)
    monkeypatch.setattr("aegis.leveling.time.monotonic", lambda: 59)
    monkeypatch.setattr("aegis.leveling.time.time", lambda: 1059)
    before = SimpleNamespace(
        channel=None, self_mute=False, self_deaf=False, mute=False, deaf=False, suppress=False
    )
    await Leveling(progression.bot).on_voice_state_update(third, before, third.voice)
    assert progression.voice_sessions[(1, 10)][1] == 0
    await progression.voice_tick(message.guild, monotonic_now=60, now=1060)
    assert (await progression.activity.profile(1, 10))["eligible_voice_seconds"] == 60
    assert (await progression.activity.profile(1, 10))["voice_seconds"] == 60
    assert (await progression.activity.profile(1, 12))["eligible_voice_seconds"] == 0
    assert (await progression.repo.profile(1, 10))["total_xp"] == 0
    await Leveling(progression.bot).on_disconnect()
    assert not progression.voice_sessions and not progression.observed_sessions
    await progression.voice_tick(message.guild, monotonic_now=4000, now=5000)
    assert (await progression.activity.profile(1, 10))["voice_seconds"] == 60


async def test_voice_eligibility_loss_resets_affected_members_only(progression, message):
    channel, members = voice_members(message, 3)
    await progression.voice_tick(message.guild, monotonic_now=0, now=1000)
    members[2].voice.self_mute = True
    await progression.voice_tick(message.guild, monotonic_now=30, now=1030)
    assert (1, 12) not in progression.voice_sessions
    assert progression.voice_sessions[(1, 10)][1] == 0
    await progression.voice_tick(message.guild, monotonic_now=60, now=1060)
    assert (await progression.activity.profile(1, 10))["eligible_voice_seconds"] == 60
    assert (await progression.activity.profile(1, 12))["eligible_voice_seconds"] == 0
    assert (await progression.activity.profile(1, 12))["voice_seconds"] == 60


async def test_reaction_counts_are_independent_idempotent_and_optout_erases_peers(
    progression, message
):
    message.created_at = datetime.now(UTC)
    message.channel.fetch_message = AsyncMock(return_value=message)
    message.guild.get_channel_or_thread = lambda _: message.channel
    reactor = SimpleNamespace(id=11, bot=False, roles=[], is_timed_out=lambda: False)
    payload = SimpleNamespace(guild_id=1, channel_id=20, message_id=100, user_id=11, member=reactor)
    await progression.reaction(payload)
    await progression.reaction(payload)  # emoji changes/re-additions cannot farm counts
    assert (await progression.activity.profile(1, 11))["reactions_given"] == 1
    assert (await progression.activity.profile(1, 10))["reactions_received"] == 1
    assert (await progression.repo.profile(1, 10))["total_xp"] == 0
    await progression.repo.optout(1, 11, True)
    assert (await progression.activity.profile(1, 10))["reactions_received"] == 0
    await progression.reaction(payload)
    assert (await progression.activity.profile(1, 11))["reactions_given"] == 0


async def test_queued_overlapping_edit_keeps_pending_marker_until_all_checks_finish(
    progression, message
):
    first_release, second_release, second_started, first_done = [asyncio.Event() for _ in range(4)]
    calls = 0

    async def process(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await first_release.wait()
            first_done.set()
        else:
            second_started.set()
            await second_release.wait()
        return False

    progression.bot.service.process = AsyncMock(side_effect=process)
    message.channel.fetch_message = AsyncMock(return_value=message)
    message.guild.get_channel_or_thread = lambda _: message.channel
    cog = AutoModeration(progression.bot)
    cog.enqueue((message, 1.0, False))
    cog.enqueue((SimpleNamespace(guild_id=1, channel_id=20, message_id=100), 2.0, True))
    assert (1, 100) in progression.pending_messages
    workers = [asyncio.create_task(cog.worker()) for _ in range(2)]
    try:
        await asyncio.wait_for(second_started.wait(), 2)
        first_release.set()
        await asyncio.wait_for(first_done.wait(), 2)
        assert (1, 100) in progression.pending_messages
        await progression.reaction(SimpleNamespace(guild_id=1, user_id=11, message_id=100))
        assert progression.metrics["reaction_pending_moderation"] == 1
        second_release.set()
        await asyncio.wait_for(cog.queue.join(), 2)
        assert (1, 100) not in progression.pending_messages
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


async def test_ranking_ties_personal_placement_and_periods_are_consistent(progression):
    now = time.time()
    for user in range(1, 13):
        await progression.repo.award(1, user, "manual", f"seed:{user}", 100, now=now)
        await progression.activity.record(1, user, "text", f"message:{user}", now=now)
    result = await progression.activity.ranking(1, "xp", page=2, user_id=12)
    assert result["pages"] == 2 and result["placement"]["position"] == 12
    assert [row["user_id"] for row in result["rows"]] == [11, 12]
    assert (await progression.repo.profile(1, 12))["rank"] == 12
    await progression.repo.award(1, 12, "manual", "old", 200, now=now - 3 * 86400)
    result = await progression.activity.ranking(1, "xp", days=1, user_id=12, now=now)
    assert result["placement"]["score"] == 100
    assert (await progression.activity.ranking(1, "text", days=1, now=now))["count"] == 12
    assert (await progression.activity.ranking(1, "xp", page=999))["page"] == 2


async def test_xp_reset_preserves_activity_and_atomic_set_uses_the_current_balance(progression):
    await progression.activity.record(1, 10, "text", "before-reset")
    await progression.repo.award(1, 10, "manual", "seed", 20)
    result = await progression.repo.award(1, 10, "manual", "set", 0, set_to=1150)
    assert result.amount == 1130 and result.level == 5
    await progression.repo.reset(1, 10)
    assert (await progression.activity.profile(1, 10))["messages"] == 1
    assert (await progression.repo.profile(1, 10))["total_xp"] == 0


async def test_given_received_view_switch_and_public_fork_are_read_only(progression, message):
    await progression.activity.reaction(1, 100, 11, 10)
    await progression.activity.reaction(1, 101, 12, 10)
    view = LeaderboardView(progression.activity, 1, "Community", 10, "received")
    received = await view.render()
    assert "2 reactions" in received.description
    own = interaction(message)
    await view.update(own, metric="given")
    updated = own.edit_original_response.await_args.kwargs["embed"]
    assert "Reactions given" in updated.title and "<@11>" in updated.description
    outsider = interaction(message, SimpleNamespace(id=12))
    outsider.followup.send.return_value = SimpleNamespace(edit=AsyncMock())
    assert not await view.interaction_check(outsider)
    assert outsider.followup.send.await_args.kwargs["ephemeral"] is True
    outsider.edit_original_response.assert_not_awaited()
    assert not await view.interaction_check(outsider)  # shared fork rate limit
    assert outsider.followup.send.await_count == 1
    await view.on_timeout()
    assert all(item.disabled for item in view.children)


def test_period_xp_card_uses_lifetime_level_and_not_window_score():
    data = {
        "rows": [{"user_id": 10, "position": 1, "score": 5, "total_xp": 1150}],
        "placement": None,
        "tracking_since": None,
        "page": 1,
        "pages": 1,
        "count": 1,
    }
    embed = board_embed("Community", "xp", "week", data)
    assert "Lvl. 5" in embed.description and "5 XP" in embed.description


async def test_fresh_admin_permissions_prevent_xp_writes(progression, message):
    actor = SimpleNamespace(id=99, guild_permissions=discord.Permissions(manage_guild=True))
    me = SimpleNamespace(id=999, guild_permissions=discord.Permissions(manage_roles=True))
    message.guild.fetch_member = AsyncMock(side_effect=lambda user: actor if user == 99 else me)
    with pytest.raises(UserError, match="Manage Roles"):
        await Leveling.xp.callback(
            Leveling(progression.bot), interaction(message, actor), message.author, 100, "Test"
        )
    assert (await progression.repo.profile(1, 10))["total_xp"] == 0


async def test_v2_migration_preserves_xp_cases_and_does_not_invent_activity(tmp_path):
    path = tmp_path / "v2.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.executescript(MIGRATION_2)
        db.execute("INSERT INTO leveling_settings VALUES (1,?)", (json.dumps({"enabled": True}),))
        db.execute(
            "INSERT INTO xp_profiles(guild_id,user_id,total_xp,text_xp,text_awards,updated_at) "
            "VALUES (1,10,20,20,1,?)",
            (time.time(),),
        )
        db.execute(
            "INSERT INTO cases(guild_id,user_id,actor_id,source,category,reason,action,"
            "status,is_warning,created_at) "
            "VALUES (1,10,99,'manual','test','Preserve','warn','completed',1,?)",
            (time.time(),),
        )
    from aegis.config import GuildPolicy

    store = Store(path, GuildPolicy())
    await store.open()
    try:
        repo = LevelRepository(store, LevelingConfig())
        assert (await repo.profile(1, 10))["total_xp"] == 20
        assert len(await store.warnings(1, 10, 30)) == 1
        profile = await ActivityRepository(store).profile(1, 10)
        assert profile["messages"] == 0 and profile["tracking_since"] is not None
        assert (await repo.settings(1)).enabled is True
        async with store.connection().execute("PRAGMA integrity_check") as cursor:
            assert (await cursor.fetchone())[0] == "ok"
    finally:
        await store.close()


def test_instance_lock_blocks_duplicate_local_process_and_releases(tmp_path):
    path = tmp_path / "instance.sqlite3"
    with InstanceLock(path):
        with pytest.raises(RuntimeError, match="Another Alpha"):
            with InstanceLock(path):
                pytest.fail("Duplicate instance unexpectedly acquired the lock")
    with InstanceLock(path):
        assert path.with_suffix(".lock").is_file()


@pytest.mark.parametrize(
    "text,matched",
    [
        ("badword", True),
        ("b@dword", True),
        ("b.a.d.w.o.r.d", True),
        ("bass class classroom", False),
        ("email@example.com", False),
    ],
)
def test_reviewed_obfuscation_and_benign_token_boundaries(text, matched):
    from aegis.config import GuildPolicy

    engine = RuleEngine()
    sample = MessageSample(1, 10, 20, 100, text, 0, 1)
    hit = engine.check(sample, GuildPolicy(banned_phrases=("badword", "ass")))
    assert bool(hit) is matched


async def test_xp_needed_matches_card_and_does_not_advertise_disabled_voice(progression, message):
    await progression.repo.award(1, 10, "manual", "seed", 77)
    context = interaction(message)
    await Leveling.xp_needed.callback(Leveling(progression.bot), context)
    answer = context.followup.send.await_args.args[0]
    assert "23" in answer and "remaining" in answer
    assert "Voice:" not in answer


async def test_time_reports_observed_eligible_session_without_awarding_xp(
    progression, message, monkeypatch
):
    _, members = voice_members(message)
    await progression.voice_tick(message.guild, monotonic_now=0, now=1000)
    monkeypatch.setattr("aegis.cogs.leveling.time.monotonic", lambda: 35)
    context = interaction(message, members[0])
    await Leveling.voice_time.callback(Leveling(progression.bot), context)
    answer = context.followup.send.await_args.args[0]
    assert "0m 35s" in answer and "**eligible**" in answer
    assert "**disabled**" in answer
    assert (await progression.repo.profile(1, 10))["total_xp"] == 0


async def test_public_default_is_limited_to_configured_channel(progression, message):
    cog = Leveling(progression.bot)
    await progression.repo.configure(1, public_leaderboard_channel_id=20)
    public = interaction(message)
    public.followup.send.return_value = SimpleNamespace(edit=AsyncMock())
    await cog.show_board(public)
    assert public.followup.send.await_args.kwargs["ephemeral"] is False
    private = interaction(message)
    private.channel_id = 21
    private.followup.send.return_value = SimpleNamespace(edit=AsyncMock())
    await cog.show_board(private)
    assert private.followup.send.await_args.kwargs["ephemeral"] is True


async def test_stalled_voice_observer_does_not_display_or_credit_offline_gap(
    progression, message, monkeypatch
):
    _, members = voice_members(message)
    await progression.voice_tick(message.guild, monotonic_now=0, now=1000)
    monkeypatch.setattr("aegis.cogs.leveling.time.monotonic", lambda: 500)
    context = interaction(message, members[0])
    await Leveling.voice_time.callback(Leveling(progression.bot), context)
    assert "0m 0s" in context.followup.send.await_args.args[0]
    await progression.voice_tick(message.guild, monotonic_now=500, now=1500)
    stats = await progression.activity.profile(1, members[0].id)
    assert stats["voice_seconds"] == stats["eligible_voice_seconds"] == 0
    assert progression.observed_sessions[(1, members[0].id)].started_at == 500


async def test_xp_needed_excluded_channel_has_no_false_earning_estimate(progression, message):
    await progression.repo.configure(1, ignored_channel_ids=(20,))
    context = interaction(message)
    await Leveling.xp_needed.callback(Leveling(progression.bot), context)
    answer = context.followup.send.await_args.args[0]
    assert "excluded" in answer and "Text:" not in answer
