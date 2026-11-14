import asyncio
import time

import pytest

from aegis.config import GuildPolicy
from aegis.store import Store


async def make_case(store, guild_id=1, user_id=10):
    return await store.create_case(
        guild_id=guild_id,
        user_id=user_id,
        actor_id=99,
        source="manual",
        category="test",
        reason="A reason",
        action="warn",
    )


async def test_per_guild_settings_survive_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    first = Store(path, GuildPolicy())
    await first.open()
    await first.update_policy(1, mode="enforce", banned_phrases=["example"])
    assert (await first.policy(2)).mode == "observe"
    await first.close()
    second = Store(path, GuildPolicy())
    await second.open()
    assert (await second.policy(1)).mode == "enforce"
    assert (await second.policy(1)).banned_phrases == ("example",)
    await second.close()


async def test_warnings_are_not_observations_or_voided_cases(store):
    observed, warned = await make_case(store), await make_case(store)
    await store.finish_case(observed, "observed", {})
    await store.finish_case(warned, "completed", {}, warning=True)
    assert [r["id"] for r in await store.warnings(1, 10, 30)] == [warned]
    assert await store.case(2, warned) is None
    assert not await store.void_case(2, warned)
    assert await store.void_case(1, warned)
    assert not await store.warnings(1, 10, 30)


async def test_appeal_access_and_resolution_are_guild_scoped(store):
    case_id = await make_case(store)
    await store.finish_case(case_id, "completed", {}, warning=True)
    with pytest.raises(ValueError):
        await store.appeal(2, case_id, 10, "wrong guild")
    with pytest.raises(ValueError):
        await store.appeal(1, case_id, 11, "wrong user")
    appeal_id = await store.appeal(1, case_id, 10, "Please review")
    with pytest.raises(ValueError):
        await store.appeal(1, case_id, 10, "duplicate")
    assert not await store.resolve_appeal(2, appeal_id, 99, True, "no")
    assert await store.resolve_appeal(1, appeal_id, 99, True, "Accepted")
    assert not await store.resolve_appeal(1, appeal_id, 99, False, "again")
    assert not await store.warnings(1, 10, 30)
    assert not await store.appeals(1)
    assert (await store.case(1, case_id))["voided"] == 1


async def test_rejecting_appeal_keeps_warning(store):
    case_id = await make_case(store)
    await store.finish_case(case_id, "completed", {}, warning=True)
    appeal_id = await store.appeal(1, case_id, 10, "Review")
    await store.resolve_appeal(1, appeal_id, 99, False, "Upheld")
    assert len(await store.warnings(1, 10, 30)) == 1


async def test_retention_cascades_to_appeals_and_removal_is_scoped(store):
    old, other = await make_case(store), await make_case(store, guild_id=2)
    await store.appeal(1, old, 10, "old appeal")
    await store.connection().execute(
        "UPDATE cases SET created_at=? WHERE id=?", (time.time() - 40 * 86400, old)
    )
    await store.connection().commit()
    assert await store.prune(30) == 1
    assert not await store.appeals(1)
    assert await store.case(2, other)
    await store.remove_guild(1)
    assert await store.case(2, other)
    await store.remove_guild(2)
    assert await store.case(2, other) is None


async def test_concurrent_writes_do_not_lose_settings_or_cases(store):
    ids = await asyncio.gather(*(make_case(store, user_id=i) for i in range(30)))
    assert len(set(ids)) == 30
    await asyncio.gather(
        store.update_policy(1, mode="enforce"), store.update_policy(1, block_invites=True)
    )
    policy = await store.policy(1)
    assert policy.mode == "enforce" and policy.block_invites


async def test_interrupted_cases_are_marked_unknown_on_restart(tmp_path):
    path = tmp_path / "interrupted.sqlite3"
    first = Store(path, GuildPolicy())
    await first.open()
    case_id = await make_case(first)
    await first.close()
    second = Store(path, GuildPolicy())
    await second.open()
    assert (await second.case(1, case_id))["status"] == "unknown"
    await second.close()


async def test_reduced_retention_clamps_existing_warning_window(tmp_path):
    path = tmp_path / "retention.sqlite3"
    first = Store(path, GuildPolicy())
    await first.open()
    await first.policy(1)
    await first.close()
    second = Store(path, GuildPolicy(warning_days=7), retention_days=7)
    await second.open()
    assert (await second.policy(1)).warning_days == 7
    await second.close()


async def test_voiding_case_records_reviewer_in_durable_details(store):
    case_id = await make_case(store)
    assert await store.void_case(1, case_id, reviewer_id=55)
    assert (await store.case(1, case_id))["details"]["voided_by"] == 55
