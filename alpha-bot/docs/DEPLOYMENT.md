# Deployment and maintenance

## Local operation

Run `scripts/setup.ps1` on Windows, edit `.env`, and run `scripts/start.ps1` from the extracted project. The setup script installs the pinned requirements and project into a local `.venv`. Never commit `.env`. Keep the bot token and any AI key in the host's secret/environment configuration.

Use an always-on machine. Sleeping the PC, disconnecting its network, or closing its terminal stops protection. A process manager or the provided Compose restart policy can restart a failed process. The application reconnects ordinary Discord Gateway disconnects through discord.py. Review `/status` for incomplete coverage, not just `/ping`.

## Docker Compose

Docker Engine/Desktop with the Compose plugin must be installed separately. From this folder, create `.env`, supply the token, and configure `DEV_GUILD_ID`/command sync as described in the README.

```bash
docker compose config --quiet
docker compose build
docker compose run --rm bot python -m aegis --check
docker compose up -d
docker compose logs --tail=100 bot
```

The image runs as an unprivileged user, publishes no ports, uses a read-only root filesystem, and writes only to the named data/log volumes. The config is mounted read-only. Outbound HTTPS and Discord Gateway WebSocket access are required. No inbound HTTPS endpoint is used.

Run **one replica only** with this SQLite/local-state architecture. `restart: unless-stopped` restarts process failures; it does not prove that message handling is healthy. There is no external uptime monitor or automatic host backup configured by this project.

Docker was not available in the build environment, so the image/Compose deployment needs its first execution on your host. Do not treat a supplied Dockerfile as a completed deployment test.

After command registration succeeds, set `SYNC_COMMANDS=false` and recreate the container. Leave it true for the next launch when command definitions change. Guild-scoped development commands and global commands occupy separate scopes; if moving from development to global registration, remove old guild registrations with Discord tooling or a deliberate maintenance script to avoid duplicate commands. This project never deletes all command registrations automatically.

## Consistent database backups

Do not copy just the main SQLite file while the bot is running: committed transactions may still be in the WAL. The supplied script uses SQLite's backup API and verifies the destination.

```powershell
.\.venv\Scripts\python.exe scripts\backup.py --database data\aegis.sqlite3 --output backups\aegis-2026-11-13.sqlite3
```

Choose a new filename for each backup. The script refuses to overwrite an existing destination. Store backups outside the application host when practical, restrict access, and enforce a backup lifetime compatible with your published privacy practices. Schedule backups using your host's scheduler only after choosing that policy.

For Docker, create a backup inside the data volume and copy it out:

```bash
docker compose exec bot python scripts/backup.py --database /app/data/aegis.sqlite3 --output /app/data/backup-2026-11-13.sqlite3
docker compose cp bot:/app/data/backup-2026-11-13.sqlite3 ./aegis-backup-2026-11-13.sqlite3
```

Remove temporary in-volume backup copies through your normal host maintenance process after verifying your external copy, so the volume does not accumulate them indefinitely.

For a local restore, stop the bot first and preserve the current database plus any `-wal`/`-shm` siblings in a separate recovery folder. Restore the verified backup to the configured database path, ensure no stale WAL/SHM files remain at that path, restore the correct file ownership, then start one bot process. Container restores require the same steps on the named volume with the service stopped. Check `/config show`, a retained `/case`, and the logs after restoration.

## Updates and diagnostics

Before an update: back up the database, install new dependencies in a test environment, run the test/lint suite, and review changes to policy and schema handling. Keep the lockfiles with the corresponding source version. The schema is currently version 3; version 1/2 databases upgrade through additive XP/activity migrations, and unsupported future versions fail startup. Further schema changes need explicit migrations rather than silently recreating the database. A rollback to a pre-v3 release requires restoring its matching pre-upgrade database with the bot stopped.

To deliberately refresh lockfiles with `uv`:

```bash
uv pip compile --universal --python-version 3.11 pyproject.toml --output-file requirements.txt
uv pip compile --universal --python-version 3.11 --extra dev pyproject.toml --output-file requirements-dev.txt
```

| Symptom | Check |
|---|---|
| Login rejected | Use a bot token, not an application secret; reset the token in the portal if needed. |
| No message flags | Enable Message Content, check observe/enforce settings, test as a regular member, inspect exemptions and queue errors. |
| Slash commands absent | Verify invitation scope, `DEV_GUILD_ID`, `SYNC_COMMANDS`, and successful startup sync. |
| Cannot timeout/kick/ban | Check bot role position, actor role position, guild permissions, and protected/administrator targets. |
| Cannot delete | Check Manage Messages and channel overrides; earlier burst messages are not automatically purged. |
| No Discord audit message | Configure a private log channel and allow the bot to view/send/embed; check `/status` and the persisted case. |
| AI checks skipped | Both enablement switches, API key, sampling, budget, concurrency, and outage counters. A skipped check is not a clean classification. |
| Member cannot appeal | Publish a moderator contact route for kicked/banned/timed-out users. |

## Before a real server rollout

Use the README's private-server smoke test to validate Discord permissions and behavior. Review detections in observe mode with bilingual moderators. Publish your rules/privacy/appeal route and check the current Message Content review requirements in the Developer Portal. Hosting reliability, moderation accuracy on actual community language, and recovery operations need to be verified on your deployment.

Version 1.2 adds an OS instance lock, local restart supervisor and health heartbeat. See [upgrade and recovery](UPGRADE-1.2.md) for command migration, permission review and rollback.
