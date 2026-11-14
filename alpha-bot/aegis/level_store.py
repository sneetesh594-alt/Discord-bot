"""Transactional XP ledger, persistent cooldowns, and schema-v1 upgrade."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from aegis.activity_store import ActivityRepository
from aegis.config import from_mapping
from aegis.leveling_config import LevelingConfig
from aegis.levels import MAX_XP, level_from_xp

if TYPE_CHECKING:
    from aegis.store import Store

MIGRATION_2 = """
BEGIN IMMEDIATE;
CREATE TABLE leveling_settings(guild_id INTEGER PRIMARY KEY, settings TEXT NOT NULL);
CREATE TABLE xp_profiles(
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    total_xp INTEGER NOT NULL DEFAULT 0 CHECK(total_xp>=0),
    text_xp INTEGER NOT NULL DEFAULT 0, voice_xp INTEGER NOT NULL DEFAULT 0,
    reaction_xp INTEGER NOT NULL DEFAULT 0, manual_xp INTEGER NOT NULL DEFAULT 0,
    text_awards INTEGER NOT NULL DEFAULT 0, voice_awards INTEGER NOT NULL DEFAULT 0,
    reaction_awards INTEGER NOT NULL DEFAULT 0,
    last_text REAL, last_voice REAL, last_reaction REAL, updated_at REAL NOT NULL,
    PRIMARY KEY(guild_id,user_id)
);
CREATE INDEX xp_ranking ON xp_profiles(guild_id,total_xp DESC,user_id);
CREATE INDEX cases_message_lookup ON cases(guild_id,message_id);
CREATE TABLE xp_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, source TEXT NOT NULL, event_key TEXT NOT NULL,
    amount INTEGER NOT NULL, created_at REAL NOT NULL,
    UNIQUE(guild_id,source,event_key),
    FOREIGN KEY(guild_id,user_id) REFERENCES xp_profiles(guild_id,user_id) ON DELETE CASCADE
);
CREATE INDEX xp_events_time ON xp_events(guild_id,created_at,user_id);
CREATE INDEX xp_events_member ON xp_events(guild_id,user_id,source,created_at);
CREATE TABLE xp_recent_content(
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, digest TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(guild_id,user_id,digest),
    FOREIGN KEY(guild_id,user_id) REFERENCES xp_profiles(guild_id,user_id) ON DELETE CASCADE
);
CREATE TABLE xp_optouts(guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
                       PRIMARY KEY(guild_id,user_id));
PRAGMA user_version=2;
COMMIT;
"""


@dataclass(frozen=True)
class XPAward:
    previous_xp: int
    total_xp: int
    amount: int

    @property
    def previous_level(self):
        return level_from_xp(self.previous_xp)

    @property
    def level(self):
        return level_from_xp(self.total_xp)


class LevelRepository:
    def __init__(self, store: Store, defaults: LevelingConfig):
        self.store, self.defaults = store, defaults
        self.cache: dict[int, LevelingConfig] = {}

    async def settings(self, guild_id: int) -> LevelingConfig:
        async with self.store.lock:
            return await self._settings(guild_id)

    async def _settings(self, guild_id: int) -> LevelingConfig:
        if guild_id in self.cache:
            return self.cache[guild_id]
        db = self.store.connection()
        async with db.execute(
            "SELECT settings FROM leveling_settings WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        settings = from_mapping(LevelingConfig, json.loads(row[0])) if row else self.defaults
        if row is None:
            await db.execute(
                "INSERT INTO leveling_settings VALUES (?,?)",
                (guild_id, json.dumps(settings.to_dict())),
            )
            await db.commit()
        self.cache[guild_id] = settings
        return settings

    async def configure(self, guild_id: int, **changes) -> LevelingConfig:
        async with self.store.lock:
            return await self._configure(guild_id, changes)

    async def _configure(self, guild_id, changes):
        settings = replace(await self._settings(guild_id), **changes)
        db = self.store.connection()
        await db.execute(
            "UPDATE leveling_settings SET settings=? WHERE guild_id=?",
            (json.dumps(settings.to_dict()), guild_id),
        )
        await db.commit()
        self.cache[guild_id] = settings
        return settings

    async def patch_map(self, guild_id, field, key, value):
        if field not in {"reward_roles", "role_multipliers", "channel_multipliers"}:
            raise ValueError("Invalid map setting")
        async with self.store.lock:
            values = dict(getattr(await self._settings(guild_id), field))
            if value is None:
                values.pop(str(key), None)
            else:
                values[str(key)] = value
            return await self._configure(guild_id, {field: values})

    async def patch_set(self, guild_id, field, value, present):
        if field not in {"ignored_role_ids", "ignored_channel_ids"}:
            raise ValueError("Invalid exclusion setting")
        async with self.store.lock:
            values = set(getattr(await self._settings(guild_id), field))
            values.add(value) if present else values.discard(value)
            return await self._configure(guild_id, {field: tuple(sorted(values))})

    async def award(
        self,
        guild_id: int,
        user_id: int,
        source: str,
        event_key: str,
        amount: int,
        *,
        now: float | None = None,
        digest: str | None = None,
        set_to: int | None = None,
    ) -> XPAward | None:
        """Deduplicate, enforce cooldown/cap, append ledger and balance in one transaction."""
        if source not in {"text", "voice", "reaction", "manual"}:
            raise ValueError("Invalid XP source")
        if set_to is not None and (
            source != "manual" or type(set_to) is not int or not 0 <= set_to <= MAX_XP
        ):
            raise ValueError("Invalid XP target")
        if (
            type(amount) is not int
            or abs(amount) > 1_000_000
            or not event_key
            or len(event_key) > 200
        ):
            raise ValueError("Invalid XP award")
        if source != "manual" and amount <= 0:
            return None
        now = time.time() if now is None else now
        async with self.store.lock:
            config = await self._settings(guild_id)
            if not config.enabled and source != "manual":
                return None
            if source in {"voice", "reaction"} and not getattr(config, source + "_enabled"):
                return None
            db = self.store.connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT 1 FROM xp_optouts WHERE guild_id=? AND user_id=?", (guild_id, user_id)
                ) as cursor:
                    opted_out = await cursor.fetchone()
                if opted_out:
                    await db.rollback()
                    return None
                async with db.execute(
                    "SELECT * FROM xp_profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id)
                ) as cursor:
                    row = await cursor.fetchone()
                previous = int(row["total_xp"]) if row else 0
                if set_to is not None:
                    amount = set_to - previous
                if source != "manual":
                    cooldown = (
                        60 if source == "voice" else getattr(config, source + "_cooldown_seconds")
                    )
                    last = row["last_" + source] if row else None
                    if last is not None and now - last < cooldown:
                        await db.rollback()
                        return None
                    if source == "text" and digest:
                        async with db.execute(
                            """SELECT 1 FROM xp_recent_content WHERE guild_id=? AND user_id=?
                            AND digest=? AND created_at>?""",
                            (guild_id, user_id, digest, now - config.duplicate_window_seconds),
                        ) as cursor:
                            repeated = await cursor.fetchone()
                        if repeated:
                            await db.rollback()
                            return None
                    utc_day = int(now // 86400) * 86400
                    async with db.execute(
                        """SELECT COALESCE(SUM(amount),0) FROM xp_events WHERE guild_id=?
                        AND user_id=? AND source=? AND created_at>=?""",
                        (guild_id, user_id, source, utc_day),
                    ) as cursor:
                        earned_today = (await cursor.fetchone())[0]
                    amount = min(
                        amount, max(0, getattr(config, source + "_daily_cap") - earned_today)
                    )
                amount = max(-previous, min(amount, MAX_XP - previous))
                if amount == 0:
                    await db.rollback()
                    return None
                await db.execute(
                    "INSERT OR IGNORE INTO xp_profiles(guild_id,user_id,updated_at) VALUES (?,?,?)",
                    (guild_id, user_id, now),
                )
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO xp_events(guild_id,user_id,source,event_key,amount,
                    created_at) VALUES (?,?,?,?,?,?)""",
                    (guild_id, user_id, source, event_key, amount, now),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return None
                # Column names are a closed internal set, never administrator input.
                if source == "manual":
                    statement = (
                        "UPDATE xp_profiles SET total_xp=total_xp+?, manual_xp=manual_xp+?, "
                        "updated_at=? WHERE guild_id=? AND user_id=?"
                    )
                    parameters = (amount, amount, now, guild_id, user_id)
                else:
                    statement = (
                        f"UPDATE xp_profiles SET total_xp=total_xp+?, {source}_xp={source}_xp+?, "
                        f"{source}_awards={source}_awards+1, last_{source}=?, updated_at=? "
                        "WHERE guild_id=? AND user_id=?"
                    )
                    parameters = (amount, amount, now, now, guild_id, user_id)
                await db.execute(statement, parameters)
                if source == "text" and digest:
                    await db.execute(
                        "DELETE FROM xp_recent_content WHERE guild_id=? AND user_id=? "
                        "AND created_at<?",
                        (guild_id, user_id, now - config.duplicate_window_seconds),
                    )
                    await db.execute(
                        "INSERT OR REPLACE INTO xp_recent_content VALUES (?,?,?,?)",
                        (guild_id, user_id, digest, now),
                    )
                await db.commit()
                return XPAward(previous, previous + amount, amount)
            except BaseException:
                await db.rollback()
                raise

    async def profile(self, guild_id: int, user_id: int) -> dict:
        async with self.store.lock:
            db = self.store.connection()
            async with db.execute(
                "SELECT * FROM xp_profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
            result = (
                dict(row)
                if row
                else {
                    "total_xp": 0,
                    "text_awards": 0,
                    "voice_awards": 0,
                    "reaction_awards": 0,
                    "text_xp": 0,
                    "voice_xp": 0,
                    "reaction_xp": 0,
                    "manual_xp": 0,
                }
            )
            result["rank"] = None
            if result["total_xp"] > 0:
                async with db.execute(
                    """SELECT COUNT(*)+1 FROM xp_profiles WHERE guild_id=? AND
                    (total_xp>? OR (total_xp=? AND user_id<?))""",
                    (guild_id, result["total_xp"], result["total_xp"], user_id),
                ) as cursor:
                    result["rank"] = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT 1 FROM xp_optouts WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            ) as cursor:
                result["opted_out"] = bool(await cursor.fetchone())
            return result

    async def message_flagged(self, guild_id: int, message_id: int) -> bool:
        async with self.store.lock:
            async with self.store.connection().execute(
                "SELECT 1 FROM cases WHERE guild_id=? AND message_id=? AND voided=0 "
                "AND source IN ('local','ai') LIMIT 1",
                (guild_id, message_id),
            ) as cursor:
                return bool(await cursor.fetchone())

    async def leaderboard(
        self, guild_id: int, days: int = 0, page: int = 1, *, now=None
    ) -> list[dict]:
        if days not in {0, 1, 7, 31} or not 1 <= page <= 1000:
            raise ValueError("Invalid leaderboard period or page")
        async with self.store.lock:
            if days:
                query = """SELECT user_id,SUM(amount) AS xp FROM xp_events WHERE guild_id=?
                AND created_at>=? GROUP BY user_id HAVING xp>0 ORDER BY xp DESC,user_id
                LIMIT 10 OFFSET ?"""
                parameters = (
                    guild_id,
                    (time.time() if now is None else now) - days * 86400,
                    (page - 1) * 10,
                )
            else:
                query = """SELECT user_id,total_xp AS xp FROM xp_profiles WHERE guild_id=? AND
                total_xp>0 ORDER BY total_xp DESC,user_id LIMIT 10 OFFSET ?"""
                parameters = (guild_id, (page - 1) * 10)
            async with self.store.connection().execute(query, parameters) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def optout(self, guild_id: int, user_id: int, enabled: bool):
        async with self.store.lock:
            db = self.store.connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                if enabled:
                    await ActivityRepository(self.store).erase_member_locked(db, guild_id, user_id)
                    await db.execute(
                        "INSERT OR IGNORE INTO xp_optouts VALUES (?,?)", (guild_id, user_id)
                    )
                    await db.execute(
                        "DELETE FROM xp_profiles WHERE guild_id=? AND user_id=?",
                        (guild_id, user_id),
                    )
                else:
                    await db.execute(
                        "DELETE FROM xp_optouts WHERE guild_id=? AND user_id=?", (guild_id, user_id)
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def reset(self, guild_id: int, user_id: int):
        async with self.store.lock:
            db = self.store.connection()
            await db.execute(
                "DELETE FROM xp_profiles WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            )
            await db.commit()

    async def prune(self, event_days: int, profile_days: int):
        async with self.store.lock:
            db = self.store.connection()
            now = time.time()
            await db.execute(
                "DELETE FROM xp_events WHERE created_at<?", (now - event_days * 86400,)
            )
            await db.execute(
                "DELETE FROM xp_profiles WHERE updated_at<?", (now - profile_days * 86400,)
            )
            await db.execute("DELETE FROM xp_recent_content WHERE created_at<?", (now - 3600,))
            await ActivityRepository(self.store).prune_locked(
                db, now - event_days * 86400, now - profile_days * 86400
            )
            await db.commit()
