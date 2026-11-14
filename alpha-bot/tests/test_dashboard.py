import asyncio
import base64
import hashlib
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from aegis.dashboard import SID, Dashboard, Session
from aegis.dashboard_config import DashboardConfig
from aegis.level_store import LevelRepository
from aegis.leveling import LevelingService
from aegis.leveling_config import LevelingConfig

GUILD, USER, ROLE = 2**60 + 1, 2**60 + 3, 2**60 + 5
ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
async def dashboard(store, settings):
    permissions = discord.Permissions(manage_guild=True, manage_roles=True, view_channel=True)
    guild = SimpleNamespace(id=GUILD, owner_id=USER, name="Dashboard test server")
    actor = SimpleNamespace(
        id=USER,
        display_name="Test member",
        guild_permissions=permissions,
        top_role=100,
        roles=[],
        guild=guild,
    )
    me = SimpleNamespace(id=999, guild_permissions=permissions, top_role=200)
    guild.me = me
    channel = SimpleNamespace(
        id=20,
        name="general",
        type=discord.ChannelType.text,
        permissions_for=lambda member: permissions,
    )
    role = SimpleNamespace(
        id=ROLE,
        name="Level five",
        managed=False,
        is_default=lambda: False,
        permissions=discord.Permissions.none(),
        guild=guild,
    )
    # Reward comparisons use Discord's role order; no reward mutation in these tests.
    guild.roles, guild.channels, guild.default_role = [], [channel], SimpleNamespace(id=GUILD)
    guild.get_role = lambda role_id: role if role_id == ROLE else None
    guild.get_channel = lambda channel_id: channel if channel_id == 20 else None
    guild.fetch_member = AsyncMock(return_value=actor)
    guild.get_member = lambda user_id: actor if user_id == USER else None
    locks = [asyncio.Lock() for _ in range(64)]
    bot = SimpleNamespace(
        store=store,
        settings=settings,
        user=me,
        last_processed_at=None,
        is_ready=lambda: True,
        get_guild=lambda gid: guild if gid == GUILD else None,
        service=SimpleNamespace(
            lock_for=lambda g, u: locks[hash((g, u)) % 64],
            audit=AsyncMock(),
            metrics=__import__("collections").Counter(),
        ),
    )
    bot.leveling = LevelingService(bot, LevelRepository(store, LevelingConfig()))
    await bot.leveling.repo.configure(GUILD, enabled=True, text_min_xp=20, text_max_xp=20)
    server = Dashboard(bot, DashboardConfig(client_id=999, client_secret="test-client-secret"))

    async def provider(method, path, *, token=None, data=None):
        if path == "/oauth2/token":
            return {
                "access_token": "test-access-token",
                "expires_in": 3600,
                "scope": "identify guilds",
            }
        if path == "/users/@me":
            return {"id": str(USER), "username": "Test member"}
        if path.startswith("/users/@me/guilds"):
            return [
                {"id": str(GUILD), "name": guild.name, "permissions": "32", "owner": False},
                {"id": "123", "name": "No access", "permissions": "0", "owner": False},
            ]
        raise AssertionError(path)

    server.request_discord = AsyncMock(side_effect=provider)
    client = TestClient(TestServer(server.app), headers={"Host": "127.0.0.1:8765"})
    await client.start_server()
    yield server, client, actor
    await client.close()
    await server.close()


def signed_in(server, client):
    session = Session(
        {"id": str(USER), "name": "Test member"}, "test-access-token", time.time() + 1000
    )
    server.sessions["test-session"] = session
    client.session.cookie_jar.update_cookies({SID: "test-session"}, response_url=URL(ORIGIN))
    return {"Origin": ORIGIN, "X-CSRF-Token": session.csrf}


async def test_missing_oauth_is_explicit_and_data_is_locked(dashboard):
    server, client, _ = dashboard
    server.config = replace(server.config, client_secret="")
    response = await client.get("/api/session")
    data = await response.json()
    assert data["authenticated"] is False and data["oauth_configured"] is False
    assert (await client.get("/auth/login", allow_redirects=False)).status == 503
    assert (await client.get("/api/guilds")).status == 401
    assert (await client.get(f"/api/guilds/{GUILD}/settings")).status == 401


async def test_oauth_binds_cookie_state_and_does_not_expose_tokens(dashboard):
    server, client, _ = dashboard
    response = await client.get("/auth/login", allow_redirects=False)
    target = URL(response.headers["Location"])
    assert target.host == "discord.com" and target.query["scope"] == "identify guilds"
    state = target.query["state"]
    assert target.query["code_challenge_method"] == "S256"
    assert response.cookies["alpha_oauth_state"]["httponly"]
    invalid = await client.get("/auth/callback?state=incorrect&code=code", allow_redirects=False)
    assert invalid.status == 403
    server.request_discord.assert_not_awaited()
    result = await client.get(
        f"/auth/callback?state={state}&code=code&iss=https://discord.com", allow_redirects=False
    )
    assert result.status == 302 and result.headers["Location"] == "/"
    exchange = server.request_discord.await_args_list[0].kwargs["data"]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == target.query["code_challenge"]
    assert result.cookies[SID]["httponly"] and result.cookies[SID]["samesite"] == "Lax"
    data = await (await client.get("/api/session")).json()
    assert data["authenticated"] and data["user"]["id"] == str(USER)
    assert "test-access-token" not in str(data) and "test-client-secret" not in str(data)
    assert (
        await client.get(f"/auth/callback?state={state}&code=code", allow_redirects=False)
    ).status == 403


async def test_guild_selection_and_settings_preserve_large_discord_ids(dashboard):
    server, client, _ = dashboard
    signed_in(server, client)
    data = await (await client.get("/api/guilds")).json()
    assert data["guilds"] == [
        {"id": str(GUILD), "name": "Dashboard test server", "bot_present": True}
    ]
    server.bot.leveling.repo.cache[GUILD] = replace(
        await server.bot.leveling.repo.settings(GUILD), reward_roles={"5": ROLE}
    )
    response = await client.get(f"/api/guilds/{GUILD}/settings")
    assert response.status == 200
    data = await response.json()
    assert data["leveling"]["reward_roles"]["5"] == str(ROLE)
    assert (await client.get("/api/guilds/123/settings")).status == 403


async def test_csrf_and_stale_write_are_rejected_and_a_good_save_is_audited(dashboard):
    server, client, _ = dashboard
    headers = signed_in(server, client)
    path = f"/api/guilds/{GUILD}/settings"
    settings = await (await client.get(path)).json()
    headers["If-Match"] = settings["revision"]
    assert (await client.put(path + "/leveling", json={"text_cooldown_seconds": 120})).status == 403
    bad_origin = {**headers, "Origin": "https://elsewhere.invalid"}
    assert (
        await client.put(
            path + "/leveling", headers=bad_origin, json={"text_cooldown_seconds": 120}
        )
    ).status == 403
    saved = await client.put(
        path + "/leveling", headers=headers, json={"text_cooldown_seconds": 120}
    )
    assert saved.status == 200, await saved.text()
    result = await saved.json()
    assert result["saved"] and result["case_id"]
    assert (await server.bot.leveling.repo.settings(GUILD)).text_cooldown_seconds == 120
    server.bot.service.audit.assert_awaited_once()
    stale = await client.put(
        path + "/leveling", headers=headers, json={"text_cooldown_seconds": 180}
    )
    assert stale.status == 409
    assert (await server.bot.leveling.repo.settings(GUILD)).text_cooldown_seconds == 120


async def test_permissions_are_rechecked_and_reward_settings_need_manage_roles(dashboard):
    server, client, actor = dashboard
    headers = signed_in(server, client)
    path = f"/api/guilds/{GUILD}/settings"
    data = await (await client.get(path)).json()
    headers["If-Match"] = data["revision"]
    actor.guild_permissions = discord.Permissions(manage_guild=True)
    assert (
        await client.put(path + "/leveling", headers=headers, json={"stack_rewards": False})
    ).status == 403
    actor.guild_permissions = discord.Permissions.none()
    assert (await client.get(path)).status == 403


async def test_validation_and_command_edit_conflicts_do_not_change_data(dashboard):
    server, client, _ = dashboard
    headers = signed_in(server, client)
    path = f"/api/guilds/{GUILD}/settings"
    initial = await (await client.get(path)).json()
    headers["If-Match"] = initial["revision"]
    for changes in (
        {"enabled": "yes"},
        {"text_cooldown_seconds": 0},
        {"unknown": True},
        {"ignored_role_ids": [True]},
    ):
        response = await client.put(path + "/leveling", headers=headers, json=changes)
        assert response.status == 400, await response.text()
    await server.bot.leveling.repo.configure(GUILD, text_min_xp=10)
    response = await client.put(path + "/leveling", headers=headers, json={"text_max_xp": 99})
    assert response.status == 409
    assert (await server.bot.leveling.repo.settings(GUILD)).text_max_xp == 20


async def test_web_rank_and_leaderboard_match_the_same_profile_without_role_writes(dashboard):
    server, client, _ = dashboard
    signed_in(server, client)
    await server.bot.leveling.repo.award(GUILD, USER, "manual", "seed", 77)
    await server.bot.leveling.activity.record(GUILD, USER, "text", "test-message")
    rank = await (await client.get(f"/api/guilds/{GUILD}/rank/{USER}")).json()
    board = await (await client.get(f"/api/guilds/{GUILD}/leaderboard")).json()
    assert rank["total_xp"] == 77 and rank["earned"] == 77 and rank["needed"] == 100
    assert board["rows"][0]["user_id"] == str(USER)
    assert board["rows"][0]["position"] == rank["rank"] == 1
    image = await client.get(f"/api/guilds/{GUILD}/rank/{USER}/image.png")
    assert image.status == 200 and (await image.read()).startswith(b"\x89PNG")
    server.bot.service.audit.assert_not_awaited()


async def test_logout_expiry_host_and_security_headers(dashboard):
    server, client, _ = dashboard
    headers = signed_in(server, client)
    assert (await client.get("/api/session", headers={"Host": "attacker.invalid"})).status == 421
    response = await client.get("/api/session")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert (await client.post("/auth/logout", headers=headers)).status == 200
    assert not (await (await client.get("/api/session")).json())["authenticated"]
    signed_in(server, client)
    server.sessions["test-session"].expires_at = time.time() - 1
    assert (await client.get(f"/api/guilds/{GUILD}/overview")).status == 401


async def test_unchanged_voice_fields_in_an_experience_save_preserve_the_session(dashboard):
    server, client, _ = dashboard
    headers = signed_in(server, client)
    path = f"/api/guilds/{GUILD}/settings"
    initial = await (await client.get(path)).json()
    headers["If-Match"] = initial["revision"]
    server.bot.leveling.voice_sessions[(GUILD, USER)] = (20, 100.0)
    response = await client.put(
        path + "/leveling", headers=headers, json={"text_min_xp": 15, "voice_min_members": 2}
    )
    assert response.status == 200
    assert server.bot.leveling.voice_sessions[(GUILD, USER)] == (20, 100.0)


async def test_removed_guild_cannot_be_recreated_by_an_inflight_save(dashboard):
    server, client, actor = dashboard
    headers = signed_in(server, client)
    path = f"/api/guilds/{GUILD}/settings"
    initial = await (await client.get(path)).json()
    headers["If-Match"] = initial["revision"]
    guild = server.bot.get_guild(GUILD)

    async def removed_after_fetch(_):
        server.bot.get_guild = lambda _: None
        return actor

    guild.fetch_member = AsyncMock(side_effect=removed_after_fetch)
    response = await client.put(path + "/leveling", headers=headers, json={"text_min_xp": 15})
    assert response.status == 410
    assert (await server.bot.leveling.repo.settings(GUILD)).text_min_xp == 20


def test_dashboard_config_keeps_secrets_out_of_repr_and_rejects_unsafe_origins():
    assert "secret-value" not in repr(DashboardConfig(client_secret="secret-value"))
    for values in (
        {"host": "0.0.0.0"},
        {"public_url": "http://evil.test:8765"},
        {"public_url": "https://127.0.0.1:8765"},
        {"port": True},
    ):
        with pytest.raises(ValueError):
            DashboardConfig(**values)
