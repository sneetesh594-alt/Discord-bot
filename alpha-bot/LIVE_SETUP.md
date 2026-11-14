# Current deployment status — 14 November 2026

Alpha **1.2.0 is connected to Discord**, verified **14 November 2026, 12:27 IST** from fresh Gateway-ready logs and advancing health heartbeats. All **32 top-level commands** synchronized successfully and were independently listed through Discord's API. The current runtime is a local supervised Codex terminal session; operational session/PID details are in the private workspace record.

The live database is schema **v3**, integrity/foreign-key checks passed, with **4 cases, 1 XP profile and 20 total XP** preserved. Observe mode, AI off, text XP 15–25/60 seconds, voice/reaction XP off and reward maps remain. The latest heartbeat has zero processing errors and queue drops. No new member message has been processed in this session yet; this does not establish live member-flow or role/announcement delivery.

The earlier failed connection and stale process were followed by this successful restart. Fresh backup taken before launch. This local supervisor needs the PC/session awake and online. Try `/ping`, `/rank`, `/leaderboard xp`, `/statistics` and `/xp_needed`; no real-member moderation/reset is needed for smoke tests.

For a later restart, stop the known existing session first, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-supervised.ps1 -SyncCommands
```

Verify a recent connected `data/aegis.health.json` and `/status`; a stale connected file alone does not prove uptime. See [upgrade and rollback](docs/UPGRADE-1.2.md).

The earlier audit found Administrator grants from both the shared BOTS role and Alpha integration role. No live permission changes were made in 1.2. Review these with the owner; do not edit the shared role globally. The invite now requests the feature permissions, including Manage Roles/Attach Files, without Administrator. Rotate the token previously shared in chat in the Developer Portal and replace its local `.env` value; no credential is included in the release package.

Browser smoke verification was blocked by the tool's inability to identify the current browser URL confidently. No live slash-command response is claimed.

---

## Historical version 1.1 setup record

# Alpha bot: verified live setup

Verified on 13 November 2026 after the initial offline build checks.

- Discord application/bot: **Alpha bot**, ID `1570789160405565500`.
- Server: **Prime Gaming Community 🇮🇳**, ID `1483405540666376314`.
- Bot membership and Message Content access: confirmed through Discord's API.
- Discord Gateway: connected successfully with one server.
- Slash commands: **26 top-level commands** registered in the test server, then independently listed through Discord's API after the 1.1 leveling upgrade.
- Moderation mode: **observe**. Local rule matches create review records; automatic deletion/timeouts are disabled in this mode. Explicit moderator commands still execute.
- Existing private moderation-log channel: `┃🔨│moderation-logs`, ID `1483413955773861999`. Its visibility for `@everyone` was checked before configuring it.
- External AI: disabled; no OpenAI key was provided.

The bot runs locally in an active Codex terminal session. The computer must remain awake, online, and running that session. This is not a cloud deployment or a Windows startup service.

Use `/ping`, `/help`, `/rules`, and `/status` in the server. Automated detection/enforcement against real messages and members has not yet been exercised live.

The bot's highest assigned role is now **🤖 BOTS**, at position **60**, above **59 roles**. OWNER, Co-Owner, and Oficial Team remain above it. Manage Messages, Moderate Members, Kick Members, and Ban Members are effective for the bot. Review detections before using `/config mode mode:enforce`. Discord role restrictions still apply to manual commands.

## Leveling upgrade

Version **1.1.0** is connected with text XP enabled: **15–25 XP**, at most once per **60 seconds** for an eligible message. Level-up announcements go to **┃🤖│bot-commands**. Bot, staff, ticket and log areas are excluded from XP by the initial setup.

Cosmetic reward roles **Level 5**, **Level 10**, **Level 20**, **Level 35**, and **Level 50** are configured. Each was created with zero permissions and no channel overrides, and each sits below the bot. No member XP or reward-role assignments were fabricated for testing. Voice and reaction XP stay disabled until configured through `/levels`.

Use `/rank`, `/leaderboard`, `/xp-needed`, and `/levels status`. See [the leveling guide](docs/LEVELING.md) for controls and [the research](docs/LEVELING_RESEARCH.md) for the Atom comparison and implementation decisions.

To restart later from this project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Stop the existing bot terminal before starting another instance. The local `.env` contains the bot credential and is excluded from source archives. `SYNC_COMMANDS` is now false because registration succeeded; set it true for one launch when command definitions change.
