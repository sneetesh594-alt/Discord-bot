import asyncio
import json
import sqlite3
import time

import pytest

from aegis.config import GuildPolicy
from aegis.level_store import LevelRepository
from aegis.leveling_config import LevelingConfig
from aegis.store import SCHEMA, Store


@pytest.fixture
async def xp(store):
    repository = LevelRepository(store, LevelingConfig())
    await repository.configure(1, enabled=True, announcements=False)
    return repository


async def test_persistent_cooldown_exact_boundary_and_replayed_event(xp):
    assert (await xp.award(1, 10, "text", "1", 20, now=0)).total_xp == 20
    assert await xp.award(1, 10, "text", "2", 20, now=59.999) is None
    assert await xp.award(1, 10, "text", "1", 20, now=60) is None
    assert (await xp.award(1, 10, "text", "2", 20, now=60)).total_xp == 40


async def test_duplicate_text_cannot_alternate_to_bypass_recent_content_check(xp):
    assert await xp.award(1, 10, "text", "1", 20, digest="a", now=0)
    assert await xp.award(1, 10, "text", "2", 20, digest="b", now=60)
    assert await xp.award(1, 10, "text", "3", 20, digest="a", now=120) is None
    assert await xp.award(1, 10, "text", "4", 20, digest="a", now=300)


async def test_parallel_events_only_grant_once_per_cooldown_and_guilds_are_isolated(xp):
    results = await asyncio.gather(
        *(xp.award(1, 10, "text", str(i), 20, now=1000) for i in range(20))
    )
    assert sum(result is not None for result in results) == 1
    await xp.configure(2, enabled=True)
    assert await xp.award(2, 10, "text", "same-id", 30, now=1000)
    assert (await xp.profile(1, 10))["total_xp"] == 20
    assert (await xp.profile(2, 10))["total_xp"] == 30


async def test_daily_cap_clips_award_resets_utc_and_never_deducts_after_reconfiguration(xp):
    await xp.configure(1, text_daily_cap=30)
    assert (await xp.award(1, 10, "text", "a", 20, now=0)).amount == 20
    assert (await xp.award(1, 10, "text", "b", 20, now=60)).amount == 10
    await xp.configure(1, text_daily_cap=5)
    assert await xp.award(1, 10, "text", "c", 20, now=120) is None
    assert (await xp.profile(1, 10))["total_xp"] == 30
    assert (await xp.award(1, 10, "text", "d", 20, now=86400)).amount == 5


async def test_source_switches_and_independent_cooldowns(xp):
    assert await xp.award(1, 10, "voice", "v1", 5, now=1000) is None
    assert await xp.award(1, 10, "reaction", "r1", 2, now=1000) is None
    await xp.configure(1, voice_enabled=True, reaction_enabled=True)
    assert await xp.award(1, 10, "text", "t1", 20, now=1000)
    assert await xp.award(1, 10, "voice", "v1", 5, now=1000)
    assert await xp.award(1, 10, "reaction", "r1", 2, now=1000)
    assert await xp.award(1, 10, "reaction", "r1", 2, now=1060) is None
    assert (await xp.profile(1, 10))["total_xp"] == 27


async def test_disabled_leveling_keeps_progress_and_optout_erases_and_blocks_grants(xp, store):
    await xp.award(1, 10, "text", "1", 20, now=1000)
    await xp.configure(1, enabled=False)
    assert await xp.award(1, 10, "text", "2", 20, now=1060) is None
    assert (await xp.profile(1, 10))["total_xp"] == 20
    await xp.optout(1, 10, True)
    assert (await xp.profile(1, 10))["opted_out"]
    assert await xp.award(1, 10, "manual", "override", 100, now=1060) is None
    async with store.connection().execute("SELECT COUNT(*) FROM xp_events") as cursor:
        assert (await cursor.fetchone())[0] == 0
    await xp.optout(1, 10, False)
    await xp.configure(1, enabled=True)
    assert (await xp.award(1, 10, "text", "3", 20, now=1120)).total_xp == 20


async def test_adjustments_do_not_make_negative_balances_and_ties_are_deterministic(xp):
    await xp.award(1, 20, "manual", "a", 100, now=1000)
    await xp.award(1, 10, "manual", "b", 100, now=1000)
    assert (await xp.profile(1, 10))["rank"] == 1
    assert (await xp.profile(1, 20))["rank"] == 2
    deduction = await xp.award(1, 20, "manual", "c", -1000, now=1000)
    assert deduction.amount == -100 and deduction.total_xp == 0
    assert (await xp.profile(1, 20))["rank"] is None


async def test_recent_leaderboards_use_time_window_and_include_signed_corrections(xp):
    now = 20 * 86400
    await xp.award(1, 10, "manual", "old", 1000, now=now - 8 * 86400)
    await xp.award(1, 10, "manual", "new", 100, now=now - 86400)
    await xp.award(1, 10, "manual", "correction", -20, now=now - 3600)
    await xp.award(1, 20, "manual", "other", 90, now=now - 3600)
    assert (await xp.leaderboard(1))[0]["user_id"] == 10
    recent = await xp.leaderboard(1, 7, now=now)
    assert [row["user_id"] for row in recent] == [20, 10]
    assert recent[1]["xp"] == 80


async def test_xp_survives_moderation_retention_and_guild_removal_cascades(xp, store):
    await xp.award(1, 10, "manual", "a", 100, now=time.time() - 40 * 86400)
    await store.prune(30)
    assert (await xp.profile(1, 10))["total_xp"] == 100
    await store.remove_guild(1)
    assert (await xp.profile(1, 10))["total_xp"] == 0
    async with store.connection().execute("SELECT COUNT(*) FROM xp_events") as cursor:
        assert (await cursor.fetchone())[0] == 0


async def test_xp_retention_prunes_old_events_and_inactive_profiles(xp):
    now = time.time()
    await xp.award(1, 10, "manual", "old", 100, now=now - 100 * 86400)
    await xp.award(1, 20, "manual", "inactive", 50, now=now - 400 * 86400)
    await xp.prune(90, 365)
    assert (await xp.profile(1, 10))["total_xp"] == 100
    assert (await xp.profile(1, 20))["total_xp"] == 0
    assert not await xp.leaderboard(1, 31)


async def test_schema_v1_upgrade_preserves_existing_warning_and_settings(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO guild_settings VALUES (?,?)",
            (1, json.dumps(GuildPolicy(mode="enforce").to_dict())),
        )
        db.execute(
            """INSERT INTO cases(guild_id,user_id,actor_id,source,category,reason,action,
                   status,is_warning,created_at) VALUES (1,10,99,'manual','test','Keep me',
                   'warn','completed',1,?)""",
            (time.time(),),
        )
    store = Store(path, GuildPolicy())
    await store.open()
    try:
        assert (await store.policy(1)).mode == "enforce"
        assert (await store.warnings(1, 10, 30))[0]["reason"] == "Keep me"
        async with store.connection().execute("PRAGMA user_version") as cursor:
            assert (await cursor.fetchone())[0] == 3
        repo = LevelRepository(store, LevelingConfig(enabled=True))
        assert await repo.award(1, 10, "text", "a", 20)
    finally:
        await store.close()


async def test_cooldown_survives_a_full_store_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    first = Store(path, GuildPolicy())
    await first.open()
    repo = LevelRepository(first, LevelingConfig(enabled=True))
    await repo.award(1, 10, "text", "a", 20, now=1000)
    await first.close()
    second = Store(path, GuildPolicy())
    await second.open()
    try:
        repo = LevelRepository(second, LevelingConfig(enabled=True))
        assert await repo.award(1, 10, "text", "b", 20, now=1059) is None
        assert await repo.award(1, 10, "text", "b", 20, now=1060)
    finally:
        await second.close()
