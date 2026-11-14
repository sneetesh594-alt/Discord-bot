import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from aegis.ai import AIClassifier, AIResult
from aegis.config import AIConfig
from aegis.rules import RuleHit
from aegis.service import ModerationService


def make_service(store, settings, message):
    bot = SimpleNamespace(user=SimpleNamespace(id=999), get_guild=lambda _: message.guild)
    service = ModerationService(bot, store, AIClassifier(AIConfig(), "", "test"), settings)
    service.audit = AsyncMock()
    return service


async def test_observe_mode_creates_case_without_deletion_warning_or_timeout(
    store, settings, message
):
    service = make_service(store, settings, message)
    await service.local_action(message, RuleHit("flood", "Rate exceeded", "spam"), settings.policy)
    message.delete.assert_not_awaited()
    message.author.timeout.assert_not_awaited()
    message.author.send.assert_not_awaited()
    rows = await store.review_cases(1)
    assert len(rows) == 1 and rows[0]["status"] == "observed"
    assert not await store.warnings(1, 10, 30)


async def test_enforced_spam_deletes_and_times_out_with_persistent_outcome(
    store, settings, message
):
    policy = await store.update_policy(1, mode="enforce")
    service = make_service(store, settings, message)
    await service.local_action(message, RuleHit("flood", "Rate exceeded", "spam"), policy)
    message.delete.assert_awaited_once()
    message.author.timeout.assert_awaited_once()
    row = await store.case(1, 1)
    assert row["status"] == "completed" and row["details"]["timeout"] == "completed"
    assert not row["is_warning"]


async def test_failed_timeout_is_visible_and_deletion_is_not_reported_as_full_success(
    store, settings, message
):
    policy = await store.update_policy(1, mode="enforce")
    service = make_service(store, settings, message)
    message.guild.me.guild_permissions.moderate_members = False
    await service.local_action(message, RuleHit("flood", "Rate exceeded", "spam"), policy)
    row = await store.case(1, 1)
    assert row["status"] == "partial"
    assert row["details"]["timeout"] == "skipped_permissions"


async def test_cooldown_deletes_are_aggregated_without_repeated_timeouts(store, settings, message):
    policy = await store.update_policy(1, mode="enforce")
    service = make_service(store, settings, message)
    hit = RuleHit("flood", "Rate exceeded", "spam")
    await service.local_action(message, hit, policy)
    await service.local_action(message, hit, policy)
    assert message.delete.await_count == 2
    message.author.timeout.assert_awaited_once()
    assert (await store.case(1, 1))["details"]["additional_deletes"] == 1


async def test_automatic_warning_escalation_is_persistent(store, settings, message):
    policy = await store.update_policy(1, mode="enforce", warning_threshold=2)
    service = make_service(store, settings, message)
    first = RuleHit("banned_phrase", "Phrase rule", "delete_warn")
    second = RuleHit("invite", "Invite rule", "delete_warn")
    await service.local_action(message, first, policy)
    message.author.timeout.assert_not_awaited()
    await service.local_action(message, second, policy)
    message.author.timeout.assert_awaited_once()
    assert len(await store.warnings(1, 10, 30)) == 2
    assert (await store.case(1, 2))["details"]["escalation"] == "timeout_applied"


async def test_changing_to_observe_cancels_stale_enforce_policy(store, settings, message):
    stale = await store.update_policy(1, mode="enforce")
    await store.update_policy(1, mode="observe")
    service = make_service(store, settings, message)
    await service.local_action(message, RuleHit("flood", "rate", "spam"), stale)
    message.delete.assert_not_awaited()
    assert (await store.case(1, 1))["status"] == "observed"


async def test_ai_flags_in_enforce_mode_only_create_human_review_case(store, settings, message):
    settings = replace(settings, ai_enabled=True)
    await store.update_policy(1, mode="enforce", ai_enabled=True)
    service = make_service(store, settings, message)
    service.ai.eligible = lambda content: True
    service.ai.classify = AsyncMock(
        return_value=AIResult(True, ("self-harm/intent",), {"self-harm/intent": 0.98}, "test-model")
    )
    await service.process(message, time.monotonic())
    message.delete.assert_not_awaited()
    message.author.timeout.assert_not_awaited()
    row = await store.case(1, 1)
    assert row["action"] == "review" and row["category"] == "support_review"
    assert not row["is_warning"]


async def test_local_violations_never_call_ai_and_privacy_opt_in_is_required(
    store, settings, message
):
    settings = replace(settings, ai_enabled=True)
    service = make_service(store, settings, message)
    service.ai.eligible = lambda content: True
    service.ai.classify = AsyncMock()
    await service.process(message, time.monotonic())
    service.ai.classify.assert_not_awaited()
    await store.update_policy(1, ai_enabled=True)
    message.mentions = list(range(6))
    await service.process(message, time.monotonic())
    service.ai.classify.assert_not_awaited()


async def test_exempt_moderators_are_not_processed(store, settings, message):
    service = make_service(store, settings, message)
    message.author.guild_permissions.manage_messages = True
    message.mentions = list(range(10))
    await service.process(message, time.monotonic())
    assert not await store.review_cases(1)


async def test_manual_warn_works_in_observe_mode_and_tracks_blocked_dm(
    store, settings, message, monkeypatch
):
    service = make_service(store, settings, message)
    actor = SimpleNamespace(id=50)
    monkeypatch.setattr(
        "aegis.service.fresh_target", AsyncMock(return_value=(actor, message.author))
    )
    response = SimpleNamespace(status=403, reason="Forbidden")
    message.author.send.side_effect = discord.Forbidden(response, "Cannot send messages")
    interaction = SimpleNamespace(guild_id=1, channel_id=20)
    case_id = await service.manual(interaction, 10, "warn", "Test reason")
    row = await store.case(1, case_id)
    assert row["status"] == "completed" and row["is_warning"]
    assert row["details"]["dm_delivered"] is False


async def test_manual_discord_failure_remains_in_audit(store, settings, message, monkeypatch):
    service = make_service(store, settings, message)
    actor = SimpleNamespace(id=50)
    monkeypatch.setattr(
        "aegis.service.fresh_target", AsyncMock(return_value=(actor, message.author))
    )
    response = SimpleNamespace(status=403, reason="Forbidden")
    message.author.timeout.side_effect = discord.Forbidden(response, "Role changed")
    with pytest.raises(discord.Forbidden):
        await service.manual(
            SimpleNamespace(guild_id=1, channel_id=20), 10, "timeout", "Reason", seconds=60
        )
    assert (await store.case(1, 1))["status"] == "failed"


async def test_parent_channel_exemption_applies_to_threads(store, settings, message):
    service = make_service(store, settings, message)
    await store.update_policy(1, exempt_channel_ids=(50,))
    message.channel.parent_id = 50
    message.mentions = list(range(10))
    await service.process(message, time.monotonic())
    assert not await store.review_cases(1)


async def test_ai_response_after_bot_leaves_does_not_recreate_guild_data(store, settings, message):
    import asyncio

    settings = replace(settings, ai_enabled=True)
    await store.update_policy(1, ai_enabled=True)
    service = make_service(store, settings, message)
    service.ai.eligible = lambda _: True
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_classification(*args):
        started.set()
        await release.wait()
        return AIResult(True, ("hate",), {"hate": 0.99})

    service.ai.classify = delayed_classification
    task = asyncio.create_task(service.process(message, time.monotonic()))
    await asyncio.wait_for(started.wait(), timeout=2)
    service.bot.get_guild = lambda _: None
    await store.remove_guild(1)
    release.set()
    await task
    async with store.connection().execute("SELECT COUNT(*) FROM guild_settings") as cursor:
        assert (await cursor.fetchone())[0] == 0
    assert not await store.review_cases(1)
