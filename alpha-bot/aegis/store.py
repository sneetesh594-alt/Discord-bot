"""SQLite audit store: atomic writes, schema versioning, and bounded retention.

Message bodies are never stored. Reasons and appeal text are moderator/user supplied.
One process owns one connection; the lock prevents transactions interleaving.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import aiosqlite

from aegis.activity_store import MIGRATION_3
from aegis.config import GuildPolicy, from_mapping
from aegis.level_store import MIGRATION_2

SCHEMA = """
CREATE TABLE guild_settings (
    guild_id INTEGER PRIMARY KEY,
    policy TEXT NOT NULL
);
CREATE TABLE cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    details TEXT NOT NULL DEFAULT '{}',
    is_warning INTEGER NOT NULL DEFAULT 0,
    voided INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX cases_member ON cases(guild_id, user_id, created_at);
CREATE INDEX cases_created ON cases(created_at);
CREATE TABLE appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resolution TEXT,
    reviewer_id INTEGER,
    created_at REAL NOT NULL
);
PRAGMA user_version = 1;
"""


class Store:
    def __init__(self, path: Path, defaults: GuildPolicy, retention_days: int | None = None):
        self.path = path
        self.defaults = defaults
        self.retention_days = retention_days
        self.lock = asyncio.Lock()
        self.db: aiosqlite.Connection | None = None
        self._policies: dict[int, GuildPolicy] = {}

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.execute("PRAGMA busy_timeout=5000")
        async with self.db.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version == 0:
            await self.db.executescript(SCHEMA)
            version = 1
        if version == 1:
            await self.db.executescript(MIGRATION_2)
            version = 2
        if version == 2:
            await self.db.executescript(MIGRATION_3)
        elif version != 3:
            raise RuntimeError(f"Unsupported database schema version: {version}")
        # A crash can leave a recorded intent without a recorded result. Never retry
        # destructive actions automatically: report their outcome as unknown.
        await self.db.execute("UPDATE cases SET status='unknown' WHERE status='pending'")
        await self.db.execute(
            "UPDATE warning_escalations SET status='unknown' WHERE status='pending'"
        )
        await self.db.execute(
            "UPDATE cases SET status='partial' WHERE id IN "
            "(SELECT case_id FROM warning_escalations WHERE status='unknown')"
        )
        await self.db.commit()

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    def connection(self) -> aiosqlite.Connection:
        if self.db is None:
            raise RuntimeError("Database is not open")
        return self.db

    async def policy(self, guild_id: int) -> GuildPolicy:
        async with self.lock:
            return await self._policy(guild_id)

    async def _policy(self, guild_id: int) -> GuildPolicy:
        if guild_id in self._policies:
            return self._policies[guild_id]
        db = self.connection()
        async with db.execute(
            "SELECT policy FROM guild_settings WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        policy = from_mapping(GuildPolicy, json.loads(row[0])) if row else self.defaults
        clamped = self.retention_days is not None and policy.warning_days > self.retention_days
        if clamped:
            policy = replace(policy, warning_days=self.retention_days)
        if row is None or clamped:
            await db.execute(
                "INSERT INTO guild_settings(guild_id,policy) VALUES (?,?) "
                "ON CONFLICT(guild_id) DO UPDATE SET policy=excluded.policy",
                (guild_id, json.dumps(policy.to_dict())),
            )
            await db.commit()
        self._policies[guild_id] = policy
        return policy

    async def update_policy(self, guild_id: int, **changes: Any) -> GuildPolicy:
        async with self.lock:
            policy = replace(await self._policy(guild_id), **changes)
            db = self.connection()
            await db.execute(
                "UPDATE guild_settings SET policy=? WHERE guild_id=?",
                (json.dumps(policy.to_dict()), guild_id),
            )
            await db.commit()
            self._policies[guild_id] = policy
            return policy

    async def create_case(
        self,
        *,
        guild_id: int,
        user_id: int,
        actor_id: int,
        source: str,
        category: str,
        reason: str,
        action: str,
        channel_id: int | None = None,
        message_id: int | None = None,
        details: dict | None = None,
    ) -> int:
        async with self.lock:
            db = self.connection()
            cursor = await db.execute(
                """INSERT INTO cases (guild_id,user_id,actor_id,channel_id,message_id,
                source,category,reason,action,details,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    guild_id,
                    user_id,
                    actor_id,
                    channel_id,
                    message_id,
                    source,
                    category,
                    reason[:1000],
                    action,
                    json.dumps(details or {}),
                    time.time(),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def finish_case(
        self, case_id: int, status: str, details: dict, *, warning: bool = False
    ) -> None:
        async with self.lock:
            db = self.connection()
            await db.execute(
                "UPDATE cases SET status=?, details=?, is_warning=? WHERE id=?",
                (status, json.dumps(details), int(warning), case_id),
            )
            await db.commit()

    async def add_case_counter(self, guild_id: int, case_id: int, name: str) -> None:
        """Aggregate cooldown deletions/failures into their originating audit case."""
        async with self.lock:
            db = self.connection()
            async with db.execute(
                "SELECT details FROM cases WHERE guild_id=? AND id=?", (guild_id, case_id)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return
            details = json.loads(row[0])
            details[name] = int(details.get(name, 0)) + 1
            await db.execute(
                "UPDATE cases SET details=? WHERE guild_id=? AND id=?",
                (json.dumps(details), guild_id, case_id),
            )
            await db.commit()

    async def case(self, guild_id: int, case_id: int) -> dict | None:
        async with self.lock:
            async with self.connection().execute(
                "SELECT * FROM cases WHERE id=? AND guild_id=?", (case_id, guild_id)
            ) as cursor:
                row = await cursor.fetchone()
        return self.decode(row)

    @staticmethod
    def decode(row: Any) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        if "details" in result:
            result["details"] = json.loads(result["details"])
        return result

    async def warnings(self, guild_id: int, user_id: int, days: int) -> list[dict]:
        async with self.lock:
            async with self.connection().execute(
                """SELECT * FROM cases WHERE guild_id=? AND user_id=? AND is_warning=1
                AND voided=0 AND created_at>=? ORDER BY id DESC""",
                (guild_id, user_id, time.time() - days * 86400),
            ) as cursor:
                return [self.decode(row) for row in await cursor.fetchall()]

    async def claim_escalation(self, guild_id: int, user_id: int, case_id: int) -> bool:
        async with self.lock:
            cursor = await self.connection().execute(
                "INSERT OR IGNORE INTO warning_escalations VALUES (?,?,?,'pending',?)",
                (guild_id, user_id, case_id, time.time()),
            )
            await self.connection().commit()
            return cursor.rowcount == 1

    async def finish_escalation(self, guild_id, user_id, case_id, status):
        async with self.lock:
            await self.connection().execute(
                "UPDATE warning_escalations SET status=? "
                "WHERE guild_id=? AND user_id=? AND case_id=?",
                (status, guild_id, user_id, case_id),
            )
            await self.connection().commit()

    async def appeal_record(self, guild_id, appeal_id):
        async with self.lock:
            async with self.connection().execute(
                "SELECT * FROM appeals WHERE guild_id=? AND id=?", (guild_id, appeal_id)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def review_cases(self, guild_id: int) -> list[dict]:
        async with self.lock:
            async with self.connection().execute(
                """SELECT * FROM cases WHERE guild_id=? AND voided=0
                AND status IN ('review','observed','partial','failed','unknown')
                ORDER BY id DESC LIMIT 15""",
                (guild_id,),
            ) as cursor:
                return [self.decode(row) for row in await cursor.fetchall()]

    async def void_case(self, guild_id: int, case_id: int, reviewer_id: int | None = None) -> bool:
        async with self.lock:
            db = self.connection()
            async with db.execute(
                "SELECT details FROM cases WHERE guild_id=? AND id=? AND voided=0",
                (guild_id, case_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return False
            details = json.loads(row[0])
            details.update(voided_by=reviewer_id, voided_at=time.time())
            cursor = await db.execute(
                "UPDATE cases SET voided=1, details=? WHERE guild_id=? AND id=? AND voided=0",
                (json.dumps(details), guild_id, case_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def appeal(self, guild_id: int, case_id: int, user_id: int, text: str) -> int:
        async with self.lock:
            db = self.connection()
            async with db.execute(
                "SELECT user_id, voided FROM cases WHERE guild_id=? AND id=?",
                (guild_id, case_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] != user_id or row[1]:
                raise ValueError("No appealable case with that ID belongs to you in this server")
            try:
                cursor = await db.execute(
                    """INSERT INTO appeals(case_id,guild_id,user_id,text,created_at)
                    VALUES (?,?,?,?,?)""",
                    (case_id, guild_id, user_id, text[:1000], time.time()),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                await db.rollback()
                raise ValueError("This case already has an appeal") from None
            return cursor.lastrowid

    async def appeals(self, guild_id: int) -> list[dict]:
        async with self.lock:
            async with self.connection().execute(
                "SELECT * FROM appeals WHERE guild_id=? AND status='pending' ORDER BY id LIMIT 15",
                (guild_id,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def resolve_appeal(
        self, guild_id: int, appeal_id: int, reviewer_id: int, accept: bool, reason: str
    ) -> bool:
        async with self.lock:
            db = self.connection()
            async with db.execute(
                "SELECT case_id FROM appeals WHERE guild_id=? AND id=? AND status='pending'",
                (guild_id, appeal_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return False
            try:
                await db.execute(
                    """UPDATE appeals SET status=?,resolution=?,reviewer_id=?
                    WHERE id=? AND guild_id=?""",
                    (
                        "accepted" if accept else "rejected",
                        reason[:1000],
                        reviewer_id,
                        appeal_id,
                        guild_id,
                    ),
                )
                if accept:
                    await db.execute(
                        "UPDATE cases SET voided=1 WHERE id=? AND guild_id=?", (row[0], guild_id)
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            return True

    async def prune(self, days: int) -> int:
        async with self.lock:
            db = self.connection()
            cursor = await db.execute(
                "DELETE FROM cases WHERE created_at<?", (time.time() - days * 86400,)
            )
            await db.commit()
            return cursor.rowcount

    async def remove_guild(self, guild_id: int) -> None:
        async with self.lock:
            db = self.connection()
            await db.execute("DELETE FROM activity_guilds WHERE guild_id=?", (guild_id,))
            await db.execute("DELETE FROM cases WHERE guild_id=?", (guild_id,))
            await db.execute("DELETE FROM guild_settings WHERE guild_id=?", (guild_id,))
            await db.execute("DELETE FROM xp_profiles WHERE guild_id=?", (guild_id,))
            await db.execute("DELETE FROM xp_optouts WHERE guild_id=?", (guild_id,))
            await db.execute("DELETE FROM leveling_settings WHERE guild_id=?", (guild_id,))
            await db.commit()
            self._policies.pop(guild_id, None)
