"""Discord OAuth dashboard sharing the bot's repositories and permission rules."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import aiohttp
import discord
from aiohttp import web

from aegis.cards import rank_card
from aegis.config import GuildPolicy
from aegis.dashboard_config import DashboardConfig
from aegis.leaderboards import PERIODS, TITLES, score_label
from aegis.leveling import reward_error
from aegis.leveling_config import LevelingConfig
from aegis.levels import progress

log = logging.getLogger(__name__)
SID, STATE = "alpha_session", "alpha_oauth_state"
ID_FIELDS = {"announcement_channel_id", "public_leaderboard_channel_id", "mod_log_channel_id"}
ID_LISTS = {"ignored_role_ids", "ignored_channel_ids", "exempt_role_ids", "exempt_channel_ids"}
VOICE_FIELDS = {
    "enabled",
    "activity_enabled",
    "voice_enabled",
    "voice_min_members",
    "ignored_role_ids",
    "ignored_channel_ids",
}


def snowflake(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 2**64:
        raise web.HTTPBadRequest(reason="Invalid Discord ID")
    return int(value)


def revision(leveling, policy):
    payload = json.dumps([leveling.to_dict(), policy.to_dict()], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def wire_config(config):
    result = config.to_dict()
    for key in ID_FIELDS & result.keys():
        result[key] = str(result[key]) if result[key] is not None else None
    for key in ID_LISTS & result.keys():
        result[key] = list(map(str, result[key]))
    if "reward_roles" in result:
        result["reward_roles"] = {key: str(value) for key, value in result["reward_roles"].items()}
    return result


def parse_patch(cls, data):
    if not isinstance(data, dict) or not data or set(data) - {f.name for f in fields(cls)}:
        raise web.HTTPBadRequest(reason="Unknown or empty settings patch")
    result = dict(data)
    for key in ID_FIELDS & result.keys():
        result[key] = None if result[key] is None else snowflake(result[key])
    for key in ID_LISTS & result.keys():
        if not isinstance(result[key], list) or len(result[key]) > 200:
            raise web.HTTPBadRequest(reason="Exclusions must be a list of at most 200 IDs")
        result[key] = tuple(snowflake(value) for value in result[key])
    if "reward_roles" in result:
        if not isinstance(result["reward_roles"], dict) or len(result["reward_roles"]) > 50:
            raise web.HTTPBadRequest(reason="At most 50 reward mappings are supported")
        result["reward_roles"] = {
            key: snowflake(value) for key, value in result["reward_roles"].items()
        }
    return result


@dataclass(repr=False)
class Session:
    user: dict
    token: str = field(repr=False)
    expires_at: float
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    guilds: list = field(default_factory=list)
    guilds_at: float = 0


class Dashboard:
    def __init__(self, bot, config: DashboardConfig):
        self.bot, self.config = bot, config
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.states: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self.requests = OrderedDict()
        self.http = None
        self.runner = None
        self.card_slots = asyncio.Semaphore(2)
        self.member_slots = asyncio.Semaphore(4)
        self.names = OrderedDict()
        self.app = web.Application(middlewares=[self.protect], client_max_size=65536)
        self.app.add_routes(
            [
                web.get("/", self.index),
                web.get("/app.js", self.asset),
                web.get("/app.css", self.asset),
                web.get("/auth/login", self.login),
                web.get("/auth/callback", self.callback),
                web.post("/auth/logout", self.logout),
                web.get("/api/session", self.session_info),
                web.get("/api/guilds", self.guilds),
                web.get("/api/guilds/{guild}/overview", self.overview),
                web.get("/api/guilds/{guild}/settings", self.settings),
                web.put("/api/guilds/{guild}/settings/{section}", self.save),
                web.get("/api/guilds/{guild}/leaderboard", self.leaderboard),
                web.get("/api/guilds/{guild}/rank/{member}", self.rank),
                web.get("/api/guilds/{guild}/rank/{member}/image.png", self.rank_image),
            ]
        )

    @property
    def client_id(self):
        return self.config.client_id or getattr(self.bot.user, "id", None)

    @property
    def configured(self):
        return bool(self.client_id and self.config.client_secret)

    @web.middleware
    async def protect(self, request, handler):
        try:
            now = time.time()
            for key in [key for key, session in self.sessions.items() if session.expires_at <= now]:
                self.sessions.pop(key, None)
            for key in [key for key, (expiry, _) in self.states.items() if expiry <= now]:
                self.states.pop(key, None)
            if request.host != urlsplit(self.config.origin).netloc:
                raise web.HTTPMisdirectedRequest(reason="Use the configured dashboard address")
            key = (request.remote, int(time.monotonic() // 60))
            self.requests[key] = self.requests.get(key, 0) + 1
            self.requests.move_to_end(key)
            while len(self.requests) > 2000:
                self.requests.popitem(last=False)
            if self.requests[key] > 180:
                raise web.HTTPTooManyRequests(reason="Please wait before making more requests")
            response = await handler(request)
        except web.HTTPException as error:
            response = (
                error
                if error.status < 400
                else web.json_response({"error": error.reason}, status=error.status)
            )
        except (aiohttp.ClientError, TimeoutError, discord.HTTPException) as error:
            log.warning("Dashboard upstream failed (%s)", type(error).__name__)
            response = web.json_response(
                {"error": "Discord is unavailable; try again shortly"}, status=502
            )
        except Exception as error:
            log.error("Dashboard request failed (%s)", type(error).__name__)
            response = web.json_response(
                {"error": "Dashboard request failed; check operator logs"}, status=500
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; "
                "img-src 'self' https://cdn.discordapp.com; "
                "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'",
            }
        )
        return response

    def session(self, request, *, write=False):
        key = request.cookies.get(SID, "")
        session = self.sessions.get(key)
        if session is None or session.expires_at <= time.time():
            self.sessions.pop(key, None)
            raise web.HTTPUnauthorized(reason="Sign in with Discord to continue")
        if write and (
            request.headers.get("Origin") != self.config.origin
            or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf)
        ):
            raise web.HTTPForbidden(reason="Invalid request origin or CSRF token; reload this page")
        self.sessions.move_to_end(key)
        return session

    def cookie(self, response, name, value, max_age):
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            httponly=True,
            secure=self.config.origin.startswith("https:"),
            samesite="Lax",
            path="/",
        )

    async def request_discord(self, method, path, *, token=None, data=None):
        if self.http is None:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with self.http.request(
            method, "https://discord.com/api/v10" + path, headers=headers, data=data
        ) as response:
            if response.status in {401, 403}:
                raise web.HTTPUnauthorized(reason="Discord authorization expired; sign in again")
            if response.status >= 400:
                raise web.HTTPBadGateway(reason="Discord could not complete this request")
            return await response.json() if response.status != 204 else {}

    async def login(self, request):
        if not self.configured:
            raise web.HTTPServiceUnavailable(reason="OAuth setup is required on this PC")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.states[state] = (time.time() + 300, verifier)
        while len(self.states) > 200:
            self.states.popitem(last=False)
        response = web.Response(
            status=302,
            headers={
                "Location": "https://discord.com/oauth2/authorize?"
                + urlencode(
                    {
                        "client_id": str(self.client_id),
                        "response_type": "code",
                        "scope": "identify guilds",
                        "redirect_uri": self.config.redirect_uri,
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                    }
                )
            },
        )
        self.cookie(response, STATE, state, 300)
        return response

    async def callback(self, request):
        state = request.query.get("state", "")
        cookie = request.cookies.get(STATE, "")
        if (
            not state
            or not cookie
            or not secrets.compare_digest(state, cookie)
            or self.states.get(state, (0, ""))[0] <= time.time()
        ):
            raise web.HTTPForbidden(reason="Invalid or expired login state; start sign-in again")
        _, verifier = self.states.pop(state)
        if (
            not self.configured
            or request.query.get("error")
            or not request.query.get("code")
            or request.query.get("iss", "https://discord.com") != "https://discord.com"
        ):
            raise web.HTTPBadRequest(reason="Discord sign-in was cancelled or invalid")
        result = await self.request_discord(
            "POST",
            "/oauth2/token",
            data={
                "client_id": str(self.client_id),
                "client_secret": self.config.client_secret,
                "grant_type": "authorization_code",
                "code": request.query["code"],
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": verifier,
            },
        )
        if not {"identify", "guilds"} <= set(result.get("scope", "").split()):
            raise web.HTTPForbidden(reason="Discord identify and guilds permissions are required")
        user = await self.request_discord("GET", "/users/@me", token=result["access_token"])
        user = {
            "id": str(snowflake(user["id"])),
            "name": str(user.get("global_name") or user.get("username") or "Member")[:80],
        }
        lifetime = min(28800, int(result.get("expires_in", 0)) - 60)
        if lifetime <= 0:
            raise web.HTTPUnauthorized(reason="Discord returned an expired authorization")
        session_id = secrets.token_urlsafe(32)
        self.sessions.pop(request.cookies.get(SID, ""), None)
        self.sessions[session_id] = Session(user, result["access_token"], time.time() + lifetime)
        while len(self.sessions) > 1000:
            self.sessions.popitem(last=False)
        response = web.Response(status=302, headers={"Location": "/"})
        self.cookie(response, SID, session_id, lifetime)
        response.del_cookie(STATE)
        return response

    async def logout(self, request):
        self.session(request, write=True)
        self.sessions.pop(request.cookies.get(SID), None)
        response = web.json_response({"ok": True})
        response.del_cookie(SID)
        return response

    async def session_info(self, request):
        try:
            session = self.session(request)
        except web.HTTPUnauthorized:
            session = None
        return web.json_response(
            {
                "authenticated": bool(session),
                "oauth_configured": self.configured,
                "user": session.user if session else None,
                "csrf": session.csrf if session else None,
                "redirect_uri": self.config.redirect_uri,
                "bot_ready": self.bot.is_ready(),
            }
        )

    async def manageable(self, session, *, fresh=False):
        if fresh or time.time() - session.guilds_at > 30:
            session.guilds = await self.request_discord(
                "GET", "/users/@me/guilds?limit=200", token=session.token
            )
            session.guilds_at = time.time()
        return [
            g for g in session.guilds if g.get("owner") or int(g.get("permissions", 0)) & (8 | 32)
        ]

    async def guilds(self, request):
        session = self.session(request)
        guilds = await self.manageable(session)
        return web.json_response(
            {
                "guilds": [
                    {
                        "id": str(snowflake(g["id"])),
                        "name": str(g["name"]),
                        "bot_present": self.bot.get_guild(int(g["id"])) is not None,
                    }
                    for g in guilds
                ]
            }
        )

    async def authorize(self, request, *, write=False):
        session = self.session(request, write=write)
        guild_id = snowflake(request.match_info["guild"])
        if not self.bot.is_ready():
            raise web.HTTPServiceUnavailable(
                reason="Alpha is reconnecting; retry when the bot is online"
            )
        if not any(int(g["id"]) == guild_id for g in await self.manageable(session, fresh=write)):
            raise web.HTTPForbidden(reason="You need Manage Server for this server")
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(reason="Alpha is not installed in this server")
        actor = await guild.fetch_member(int(session.user["id"]))
        if not actor.guild_permissions.manage_guild:
            raise web.HTTPForbidden(reason="Your Manage Server permission is no longer available")
        return session, guild, actor

    async def overview(self, request):
        _, guild, _ = await self.authorize(request)
        async with self.bot.store.lock:
            db = self.bot.store.connection()
            async with db.execute(
                "SELECT COUNT(*),COALESCE(SUM(total_xp),0) FROM xp_profiles WHERE guild_id=?",
                (guild.id,),
            ) as cur:
                users, xp = await cur.fetchone()
            async with db.execute(
                "SELECT COALESCE(SUM(messages),0),COALESCE(SUM(eligible_voice_seconds),0),"
                "COALESCE(SUM(reactions_received),0) FROM activity_profiles WHERE guild_id=?",
                (guild.id,),
            ) as cur:
                messages, voice, reactions = await cur.fetchone()
            async with db.execute(
                "SELECT id,action,status,created_at FROM cases WHERE guild_id=? "
                "ORDER BY id DESC LIMIT 6",
                (guild.id,),
            ) as cur:
                cases = [dict(row) for row in await cur.fetchall()]
        return web.json_response(
            {
                "id": str(guild.id),
                "name": guild.name,
                "profiles": users,
                "total_xp": xp,
                "messages": messages,
                "voice_minutes": voice / 60,
                "reactions_received": reactions,
                "recent_actions": cases,
                "processing_errors": self.bot.service.metrics["processing_errors"],
                "processed_messages": self.bot.service.metrics["processed"],
                "last_message_at": self.bot.last_processed_at,
            }
        )

    async def settings(self, request):
        _, guild, actor = await self.authorize(request)
        async with self.bot.service.lock_for(guild.id, 0):
            if self.bot.get_guild(guild.id) is None:
                raise web.HTTPGone(reason="Alpha is no longer in this server")
            leveling = await self.bot.leveling.repo.settings(guild.id)
            policy = await self.bot.store.policy(guild.id)
        channels = [
            {"id": str(c.id), "name": c.name, "type": str(c.type)}
            for c in guild.channels
            if c.permissions_for(actor).view_channel
        ]
        roles = [
            {
                "id": str(r.id),
                "name": r.name,
                "reward_allowed": reward_error(r, guild.me, actor) is None,
            }
            for r in guild.roles
        ]
        return web.json_response(
            {
                "leveling": wire_config(leveling),
                "moderation": wire_config(policy),
                "revision": revision(leveling, policy),
                "channels": channels,
                "roles": roles,
                "can_manage_roles": actor.guild_permissions.manage_roles,
                "operator_ai_enabled": self.bot.settings.ai_enabled,
            }
        )

    async def save(self, request):
        _, guild, actor = await self.authorize(request, write=True)
        section = request.match_info["section"]
        cls = {"leveling": LevelingConfig, "moderation": GuildPolicy}.get(section)
        if cls is None:
            raise web.HTTPNotFound(reason="Unknown settings section")
        try:
            changes = parse_patch(cls, await request.json())
        except (json.JSONDecodeError, UnicodeError):
            raise web.HTTPBadRequest(reason="Expected a JSON settings object") from None
        expected = request.headers.get("If-Match", "").strip('"')
        if not expected:
            raise web.HTTPPreconditionRequired(reason="Reload settings before saving")
        if section == "leveling" and {"reward_roles", "stack_rewards"} & changes.keys():
            if (
                not actor.guild_permissions.manage_roles
                or not guild.me.guild_permissions.manage_roles
            ):
                raise web.HTTPForbidden(
                    reason="You and Alpha need Manage Roles for reward settings"
                )
            for role_id in changes.get("reward_roles", {}).values():
                role = guild.get_role(role_id)
                error = (
                    "Reward role no longer exists"
                    if role is None
                    else reward_error(role, guild.me, actor)
                )
                if error:
                    raise web.HTTPBadRequest(reason=error)
        if (
            section == "moderation"
            and changes.get("warning_days", 0) > self.bot.settings.runtime.retention_days
        ):
            raise web.HTTPBadRequest(reason="Warning window cannot exceed case retention")
        for key in ID_FIELDS & changes.keys():
            if changes[key] is not None:
                channel = guild.get_channel(changes[key])
                if channel is None or not channel.permissions_for(actor).view_channel:
                    raise web.HTTPBadRequest(reason="Select a visible channel in this server")
                if (
                    key == "mod_log_channel_id"
                    and channel.permissions_for(guild.default_role).view_channel
                ):
                    raise web.HTTPBadRequest(reason="Moderation logs require a private channel")
        async with self.bot.service.lock_for(guild.id, 0):
            if self.bot.get_guild(guild.id) is None:
                raise web.HTTPGone(reason="Alpha is no longer in this server")
            case_id = None
            try:
                async with self.bot.store.lock:
                    level = await self.bot.leveling.repo._settings(guild.id)
                    policy = await self.bot.store._policy(guild.id)
                    if revision(level, policy) != expected:
                        raise web.HTTPConflict(
                            reason="Settings changed elsewhere. Reload, review and save again"
                        )
                    candidate = replace(level if section == "leveling" else policy, **changes)
                    previous = level if section == "leveling" else policy
                    effective = {
                        key for key in changes if getattr(previous, key) != getattr(candidate, key)
                    }
                    if not effective:
                        return web.json_response(
                            {
                                "saved": True,
                                "case_id": None,
                                "revision": revision(level, policy),
                                "unchanged": True,
                            }
                        )
                case_id = await self.bot.store.create_case(
                    guild_id=guild.id,
                    user_id=actor.id,
                    actor_id=actor.id,
                    source="dashboard",
                    category="configuration",
                    reason="Dashboard settings: " + ", ".join(sorted(changes)),
                    action="configure_" + section,
                )
                async with self.bot.store.lock:
                    level = await self.bot.leveling.repo._settings(guild.id)
                    policy = await self.bot.store._policy(guild.id)
                    if revision(level, policy) != expected:
                        raise web.HTTPConflict(
                            reason="Settings changed elsewhere. Reload and review"
                        )
                    if section == "leveling":
                        level = await self.bot.leveling.repo._configure(guild.id, changes)
                    else:
                        db = self.bot.store.connection()
                        await db.execute(
                            "UPDATE guild_settings SET policy=? WHERE guild_id=?",
                            (json.dumps(candidate.to_dict()), guild.id),
                        )
                        await db.commit()
                        self.bot.store._policies[guild.id] = policy = candidate
                await self.bot.store.finish_case(case_id, "completed", {"fields": sorted(changes)})
            except ValueError as error:
                if case_id:
                    await self.bot.store.finish_case(case_id, "failed", {})
                raise web.HTTPBadRequest(reason=str(error)) from None
            except BaseException:
                if case_id:
                    await self.bot.store.finish_case(case_id, "cancelled", {})
                raise
            if section == "leveling" and VOICE_FIELDS & effective:
                self.bot.leveling.clear_voice(guild.id)
            await self.bot.service.audit(guild.id, case_id)
        return web.json_response(
            {"saved": True, "case_id": case_id, "revision": revision(level, policy)}
        )

    async def leaderboard(self, request):
        session, guild, _ = await self.authorize(request)
        metric, period = request.query.get("metric", "xp"), request.query.get("period", "all")
        if metric not in TITLES or period not in PERIODS:
            raise web.HTTPBadRequest(reason="Choose a supported metric and period")
        try:
            page = int(request.query.get("page", "1"))
            if not 1 <= page <= 100000:
                raise ValueError
        except ValueError:
            raise web.HTTPBadRequest(reason="Invalid page number") from None
        result = await self.bot.leveling.activity.ranking(
            guild.id, metric, PERIODS[period][0], page, user_id=int(session.user["id"])
        )
        names = await asyncio.gather(
            *(self.member_name(guild, row["user_id"]) for row in result["rows"])
        )
        for row, name in zip(result["rows"], names, strict=True):
            row["name"] = name
            row["user_id"] = str(row["user_id"])
            row["label"] = score_label(metric, row["score"])
            state = progress(row["total_xp"])
            row.update(level=state.level, progress=state.fraction)
        return web.json_response(result)

    async def member_name(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member.display_name
        key = (guild.id, user_id)
        cached = self.names.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        name = "Member " + str(user_id)
        async with self.member_slots:
            try:
                member = await asyncio.wait_for(guild.fetch_member(user_id), timeout=2)
                name = member.display_name
            except (discord.HTTPException, TimeoutError):
                pass
        self.names[key] = (name, time.monotonic() + 60)
        self.names.move_to_end(key)
        while len(self.names) > 1000:
            self.names.popitem(last=False)
        return name

    async def rank(self, request):
        _, guild, _ = await self.authorize(request)
        member_id = snowflake(request.match_info["member"])
        member = await guild.fetch_member(member_id)
        profile = await self.bot.leveling.repo.profile(guild.id, member_id)
        state = progress(profile["total_xp"])
        activity = await self.bot.leveling.activity.profile(guild.id, member_id)
        return web.json_response(
            {
                "id": str(member_id),
                "name": member.display_name,
                "total_xp": profile["total_xp"],
                "rank": profile["rank"],
                "level": state.level,
                "earned": state.earned,
                "needed": state.needed,
                "progress": state.fraction,
                "activity": {k: v for k, v in activity.items() if k not in {"guild_id", "user_id"}},
            }
        )

    async def rank_image(self, request):
        _, guild, _ = await self.authorize(request)
        member_id = snowflake(request.match_info["member"])
        member = await guild.fetch_member(member_id)
        profile = await self.bot.leveling.repo.profile(guild.id, member_id)
        avatar = None
        asset = getattr(member, "display_avatar", None)
        if asset is not None:
            try:
                avatar = await asyncio.wait_for(asset.replace(size=128, format="png").read(), 3)
                if len(avatar) > 1_000_000:
                    avatar = None
            except (discord.HTTPException, OSError, TimeoutError):
                pass
        async with self.card_slots:
            png = await asyncio.to_thread(
                rank_card,
                member.display_name,
                profile["total_xp"],
                profile["rank"],
                server=guild.name,
                username=str(member),
                avatar=avatar,
            )
        return web.Response(body=png, content_type="image/png")

    async def index(self, request):
        return web.FileResponse(Path(__file__).parent / "assets/dashboard/index.html")

    async def asset(self, request):
        return web.FileResponse(
            Path(__file__).parent / "assets/dashboard" / request.path.lstrip("/")
        )

    async def start(self):
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.config.host, self.config.port).start()
        log.info(
            "Alpha dashboard listening at %s (OAuth configured=%s)",
            self.config.origin,
            self.configured,
        )

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
        if self.http:
            await self.http.close()
        self.sessions.clear()
        self.states.clear()
