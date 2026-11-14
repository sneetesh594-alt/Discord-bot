"""Activity counts are independent of XP awards; never reconstruct unseen history."""

from __future__ import annotations

import math
import time
from collections import OrderedDict

MIGRATION_3 = """
BEGIN IMMEDIATE;
CREATE TABLE activity_guilds(guild_id INTEGER PRIMARY KEY, started_at REAL NOT NULL);
INSERT INTO activity_guilds SELECT guild_id,unixepoch('now') FROM leveling_settings;
CREATE TABLE activity_profiles(
 guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
 messages INTEGER NOT NULL DEFAULT 0,voice_seconds INTEGER NOT NULL DEFAULT 0,
 eligible_voice_seconds INTEGER NOT NULL DEFAULT 0,
 reactions_given INTEGER NOT NULL DEFAULT 0,reactions_received INTEGER NOT NULL DEFAULT 0,
 started_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(guild_id,user_id),
 FOREIGN KEY(guild_id) REFERENCES activity_guilds(guild_id) ON DELETE CASCADE
);
CREATE TABLE activity_events(
 guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,kind TEXT NOT NULL,
 event_key TEXT NOT NULL,amount INTEGER NOT NULL,created_at REAL NOT NULL,peer_id INTEGER,
 PRIMARY KEY(guild_id,user_id,kind,event_key),
 FOREIGN KEY(guild_id,user_id) REFERENCES activity_profiles(guild_id,user_id) ON DELETE CASCADE
);
CREATE INDEX activity_recent ON activity_events(guild_id,kind,created_at,user_id);
CREATE INDEX activity_peer ON activity_events(guild_id,peer_id);
CREATE TABLE warning_escalations(
 guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,case_id INTEGER NOT NULL,
 status TEXT NOT NULL,created_at REAL NOT NULL,
 PRIMARY KEY(guild_id,user_id,case_id),
 FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE
);
PRAGMA user_version=3;
COMMIT;
"""

KINDS = {
    "text": "messages",
    "observed_voice": "voice_seconds",
    "voice": "eligible_voice_seconds",
    "given": "reactions_given",
    "received": "reactions_received",
}
METRICS = {"text": "text", "voice": "voice", "given": "given", "received": "received"}


class ActivityRepository:
    def __init__(self, store):
        self.store = store
        self.view_requests = OrderedDict()

    def allow_view(self, guild, user, now):
        key = (guild, user)
        previous = self.view_requests.get(key)
        if previous is not None and now - previous < 1:
            return False
        self.view_requests[key] = now
        self.view_requests.move_to_end(key)
        while len(self.view_requests) > 10000:
            self.view_requests.popitem(last=False)
        return True

    async def _opted_out(self, db, guild_id, users):
        marks = ",".join("?" for _ in users)
        async with db.execute(
            f"SELECT 1 FROM xp_optouts WHERE guild_id=? AND user_id IN ({marks}) LIMIT 1",
            (guild_id, *users),
        ) as cursor:
            return bool(await cursor.fetchone())

    async def _record(self, db, guild, user, kind, key, amount, now, peer=None):
        await db.execute("INSERT OR IGNORE INTO activity_guilds VALUES (?,?)", (guild, now))
        await db.execute(
            "INSERT OR IGNORE INTO activity_profiles(guild_id,user_id,started_at,updated_at) "
            "VALUES (?,?,?,?)",
            (guild, user, now, now),
        )
        cursor = await db.execute(
            "INSERT OR IGNORE INTO activity_events VALUES (?,?,?,?,?,?,?)",
            (guild, user, kind, key, amount, now, peer),
        )
        if cursor.rowcount != 1:
            return False
        column = KINDS[kind]  # closed internal identifiers
        await db.execute(
            f"UPDATE activity_profiles SET {column}={column}+?,updated_at=? "
            "WHERE guild_id=? AND user_id=?",
            (amount, now, guild, user),
        )
        return True

    async def record(self, guild, user, kind, key, amount=1, *, now=None):
        if kind not in KINDS or type(amount) is not int or not 1 <= amount <= 3600:
            raise ValueError("Invalid activity")
        if not key or len(key) > 200:
            raise ValueError("Invalid activity event key")
        now = time.time() if now is None else now
        async with self.store.lock:
            db = self.store.connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                if await self._opted_out(db, guild, (user,)):
                    await db.rollback()
                    return False
                result = await self._record(db, guild, user, kind, key, amount, now)
                await db.commit()
                return result
            except BaseException:
                await db.rollback()
                raise

    async def reaction(self, guild, message, giver, recipient, *, now=None):
        if giver == recipient:
            return False
        now = time.time() if now is None else now
        async with self.store.lock:
            db = self.store.connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                if await self._opted_out(db, guild, (giver, recipient)):
                    await db.rollback()
                    return False
                key = f"{message}:{giver}"
                given = await self._record(db, guild, giver, "given", key, 1, now, recipient)
                await self._record(db, guild, recipient, "received", key, 1, now, giver)
                await db.commit()
                return given
            except BaseException:
                await db.rollback()
                raise

    async def profile(self, guild, user):
        async with self.store.lock:
            db = self.store.connection()
            async with db.execute(
                "SELECT * FROM activity_profiles WHERE guild_id=? AND user_id=?", (guild, user)
            ) as cursor:
                row = await cursor.fetchone()
            result = dict(row) if row else {column: 0 for column in KINDS.values()}
            async with db.execute(
                "SELECT started_at FROM activity_guilds WHERE guild_id=?", (guild,)
            ) as cursor:
                started = await cursor.fetchone()
            result["tracking_since"] = started[0] if started else None
            result.setdefault("started_at", None)
            return result

    async def erase_member_locked(self, db, guild, user):
        """Called inside the XP opt-out transaction; remove peer attribution too."""
        async with db.execute(
            "SELECT user_id,kind,SUM(amount) AS amount FROM activity_events "
            "WHERE guild_id=? AND peer_id=? AND user_id<>? GROUP BY user_id,kind",
            (guild, user, user),
        ) as cursor:
            affected = await cursor.fetchall()
        for row in affected:
            column = KINDS[row["kind"]]
            await db.execute(
                f"UPDATE activity_profiles SET {column}=MAX(0,{column}-?) "
                "WHERE guild_id=? AND user_id=?",
                (row["amount"], guild, row["user_id"]),
            )
        await db.execute(
            "DELETE FROM activity_events WHERE guild_id=? AND peer_id=?", (guild, user)
        )
        await db.execute(
            "DELETE FROM activity_profiles WHERE guild_id=? AND user_id=?", (guild, user)
        )

    async def ranking(self, guild, metric="xp", days=0, page=1, *, user_id=None, now=None):
        if metric not in {"xp", *METRICS} or days not in {0, 1, 7, 31} or page < 1:
            raise ValueError("Choose a supported metric, period and page")
        now = time.time() if now is None else now
        parameters = [guild]
        if metric == "xp":
            if days:
                scores = (
                    "SELECT user_id,SUM(amount) AS score FROM xp_events "
                    "WHERE guild_id=? AND created_at>=? GROUP BY user_id HAVING score>0"
                )
                parameters.append(now - days * 86400)
            else:
                scores = (
                    "SELECT user_id,total_xp AS score FROM xp_profiles "
                    "WHERE guild_id=? AND total_xp>0"
                )
        elif days:
            scores = (
                "SELECT user_id,SUM(amount) AS score FROM activity_events "
                "WHERE guild_id=? AND kind=? AND created_at>=? GROUP BY user_id HAVING score>0"
            )
            parameters.extend((METRICS[metric], now - days * 86400))
        else:
            column = KINDS[METRICS[metric]]
            scores = (
                f"SELECT user_id,{column} AS score FROM activity_profiles "
                f"WHERE guild_id=? AND {column}>0"
            )
        cte = (
            f"WITH scores AS ({scores}), ranked AS (SELECT user_id,score,"
            "ROW_NUMBER() OVER (ORDER BY score DESC,user_id) AS position FROM scores) "
        )
        async with self.store.lock:
            db = self.store.connection()
            async with db.execute(cte + "SELECT COUNT(*) FROM ranked", parameters) as cursor:
                count = (await cursor.fetchone())[0]
            pages = max(1, math.ceil(count / 10))
            page = min(page, pages)
            async with db.execute(
                cte + "SELECT r.*,COALESCE(p.total_xp,0) AS total_xp FROM ranked r "
                "LEFT JOIN xp_profiles p ON p.guild_id=? AND p.user_id=r.user_id "
                "ORDER BY r.position LIMIT 10 OFFSET ?",
                (*parameters, guild, (page - 1) * 10),
            ) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            async with db.execute(
                cte + "SELECT position,score FROM ranked WHERE user_id=?", (*parameters, user_id)
            ) as cursor:
                own = await cursor.fetchone()
            async with db.execute(
                "SELECT started_at FROM activity_guilds WHERE guild_id=?", (guild,)
            ) as cursor:
                started = await cursor.fetchone()
            return {
                "rows": rows,
                "page": page,
                "pages": pages,
                "count": count,
                "placement": dict(own) if own else None,
                "tracking_since": started[0] if started else None,
            }

    async def prune_locked(self, db, event_cutoff, profile_cutoff):
        await db.execute("DELETE FROM activity_events WHERE created_at<?", (event_cutoff,))
        await db.execute("DELETE FROM activity_profiles WHERE updated_at<?", (profile_cutoff,))
