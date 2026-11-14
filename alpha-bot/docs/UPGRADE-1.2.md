# Upgrade to Alpha 1.2 and recovery

1. Stop the known old bot process. Keep its source and `.env` locally; credentials are excluded from distribution packages.
2. Create a new verified backup with `python scripts/backup.py --database data/aegis.sqlite3 --output backups/before-v12.sqlite3`. The backup API includes committed WAL data. Rehearse migration on a copy first.
3. Install this release using the existing setup/dependencies. Run the automated checks and `python -m aegis --check` from the project folder. The offline check does not migrate data or connect.
4. Start once with `SYNC_COMMANDS=true` in the same command scope as before. Schema v1/v2 upgrades additively to v3. Verify old cases, XP, reward maps, observe/enforce and source switches; activity starts at the migration date.
5. After successful command sync, set `SYNC_COMMANDS=false` for later launches. Use `/leaderboard xp`, `/statistics`, `/rank`, `/xp_needed`, `/placement leaderboard:all` and `/time` for smoke checks. Do not use real-member resets/warnings as smoke tests.

`scripts/start.ps1` launches one foreground process. `scripts/run-supervised.ps1 -SyncCommands` also restarts nonzero process failures with bounded backoff; bad credentials/configuration or a held instance lock stop it. It is a local process supervisor: the PC/session must remain awake and online. It is not a Windows startup service, cloud deployment or external uptime monitor. Stop its terminal with Ctrl+C before another launch.

The database-specific OS lock prevents duplicate CLI processes and releases after a crash. `data/aegis.health.json` records version, PID, last-ready time, Gateway state, sync result, last processed message time, queue depth/drop and processing errors. A recent heartbeat plus connected state proves a running connection; increasing processed counters demonstrate message handling. A stale file alone proves neither. `/status` exposes process counters. Discord reconnection is handled by discord.py; missing events during downtime are not backfilled.

Audit intents persist before Discord actions. After a crash incomplete actions are marked unknown/partial and are not automatically repeated. This avoids duplicate punishment. Failed role reconciliation can be retried explicitly with `/levels sync`; failed DMs, announcements and audit delivery have no persistent retry queue yet.

## Role permissions

The invite requests View Channel, Send Messages, Send in Threads, Embed Links, Attach Files, Read History, Manage Messages, Moderate Members, Kick Members, Ban Members and Manage Roles. Administrator is unnecessary. Place the bot above members/reward roles; protect staff/owner roles. Review private-channel overrides before reducing permissions.

If a shared bot role grants Administrator, review its other users first. Do not globally edit that shared role to fix one bot. A server owner can grant Alpha's dedicated integration role the listed permissions and remove only Alpha's shared Administrator-granting membership. Bots cannot edit their own managed integration role or a role at/above their highest role through the ordinary role API; owner action may be necessary.

## Rollback

Stop the bot and preserve the current database plus WAL/SHM siblings in a recovery directory. Restore the verified pre-v3 backup together with its matching pre-upgrade source; ensure stale WAL/SHM files are not left beside the restored database. Never run v1.1 code on a v3 database. Review any activity/cases created after the backup before accepting their loss. Verify settings and a retained case after recovery. The implementation rehearsal verified migration and backup restoration on copies; it did not overwrite the live database as a rollback drill.

## Deferred scope

Hosted dashboard/public Top 100 page, current-member-only filtering, calendar seasons, pinned live boards, card themes, richer announcements, durable delivery retries, imports, achievements, scheduled multipliers, economy/crates and trivia remain separate work. Their commands are not advertised as implemented.
