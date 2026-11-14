# Alpha Discord Bot

A working Python bot built from the supplied Atom-like moderation blueprint. It includes slash commands, persistent warnings/cases/appeals, rolling-window spam checks, English/Hindi/Hinglish phrase rules, and optional OpenAI moderation signals.

**Version 1.2 implements the reviewed Atom-style command flow:** separate XP, text, voice and reaction leaderboards, Given/Received selection, pagination, statistics, placement, voice-session status, Unicode rank cards and audited XP/level administration. It also fixes warning escalation, stale rank role writes, concurrent settings updates, voice interval resets and overlapping moderation checks. See [leveling commands](docs/LEVELING.md), [upgrade and recovery](docs/UPGRADE-1.2.md), and [validation](VALIDATION.md).

**Version 1.3.1 includes Alpha Control Room:** a local Discord-login dashboard with server selection, real settings saves, activity boards, rank previews, rewards, exclusions, multipliers, announcements and moderation controls. It fixes private-board action forwarding, rank-image failure fallback and unnecessary voice timer resets. 207 tests pass with no test warnings. Use `Setup-OAuth.cmd` for the hidden local credential prompt. Start with `Start-Alpha.cmd` and read [dashboard setup](docs/DASHBOARD.md). Real Discord sign-in needs an OAuth Client Secret and the registered callback; without them the dashboard stays in locked setup mode.

**New servers start in observe mode.** Automatic local checks record cases without deleting messages or timing out members. Moderators can enable enforcement after reviewing results. Manual moderation commands execute in either mode. AI signals always require human review.

## Start on Windows

Install Python 3.11 or newer, extract this folder, and open PowerShell in it. Python 3.13 was used for the local verification.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
notepad .env
```

Put your real Discord **bot token** in `.env` on your own computer. Keep `AI_ENABLED=false` for the initial launch. Do not send tokens in chat. Setup creates `.env` only if it does not already exist.

Create the application at the [Discord Developer Portal](https://discord.com/developers/applications). Under **Bot**, enable **Message Content Intent**. Members and Presence intents are not needed for the implemented features. Content access must be enabled both in the portal and code. [Discord Gateway documentation](https://docs.discord.com/developers/events/gateway)

Enable Developer Mode in your Discord client to copy IDs. Set `DEV_GUILD_ID` to a private test server's ID for initial slash-command testing. Leave it blank to register commands globally. Keep `SYNC_COMMANDS=true` for the first launch and after changing commands; switch it to `false` after a successful sync.

Generate the invitation using the **Application ID** from the portal:

```powershell
.\.venv\Scripts\python.exe -m aegis --invite YOUR_NUMERIC_APPLICATION_ID
```

Open the generated URL and install the bot in a server you manage. The invitation requests the two scopes `bot` and `applications.commands`. It requests message management, member moderation, kick/ban, role management, file attachment and the text-channel permissions used by the bot, without Administrator.

Move the bot role **above the members it will moderate**. Discord applies role restrictions in addition to permissions. [Discord permissions](https://docs.discord.com/developers/topics/permissions)

Validate and start:

```powershell
.\.venv\Scripts\python.exe -m aegis --check
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

`--check` validates local settings without connecting to Discord or making an AI request. Run the bot in a terminal or on an always-on host. Stop a local session with Ctrl+C.

In your test server:

1. Create a moderation log channel hidden from `@everyone`. Give the bot View Channel, Send Messages, and Embed Links there; allow only the moderator roles you intend to see cases.
2. Run `/setup log_channel:#your-private-channel`. This selects the log channel and sets observe mode.
3. Run `/ping`, `/config show`, `/rules`, and `/privacy`.
4. From a **regular test member**, send six different messages within ten seconds, or three duplicates. Owners, moderators, bots, and members at or above the bot role are exempt from automatic checks.
5. Inspect `/review` and `/case`. Test a warning and an appeal with your test account.
6. When the settings match your server's policy, run `/config mode mode:enforce`.

## Linux / macOS installation

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps .
cp .env.example .env
# Edit .env with your local token and optional development guild ID.
.venv/bin/python -m aegis --check
.venv/bin/python -m aegis
```

Run from the project folder so `config.yaml` and `.env` are found. You can also use `python bot.py` after installing dependencies.

## Commands

All commands run in servers. Moderation and personal views reply privately. Leaderboards default to public only in the configured leaderboard channel (or announcement channel); elsewhere they are private, and `private:true` always requests a private board. Destructive kick/ban commands require a confirmation from the invoking moderator and re-check roles and permissions at execution time.

| Command | Purpose | Caller permission |
|---|---|---|
| `/ping`, `/help [command]`, `/rules`, `/privacy` | Health, help, and transparency | Everyone |
| `/rank`, `/statistics`, `/xp_needed`, `/time`, `/placement` | Progress and activity views | Everyone |
| `/dashboard` | Open the local Discord-login server dashboard | Manage Server |
| `/leaderboard xp/text/voice/reaction` | Four rankings with period selection and pagination | Everyone |
| `/manage xp add/subtract/set`, `/manage level set/reset` | Audited progression changes; set/reset require confirmation | Manage Server + Manage Roles |
| `/config leveling ...`, `/levels ...` | Shared leveling settings and compatibility entry | Manage Server; role-affecting operations also require Manage Roles |
| `/appeal case_id reason` | Appeal your own case in this server | Everyone; cooldown applies |
| `/warn member reason` | Persist a warning and attempt a notice DM | Manage Messages |
| `/warnings member` | Active warnings within the configured window | Manage Messages |
| `/timeout member minutes reason`, `/mute ...` | Built-in Discord timeout | Moderate Members |
| `/untimeout member reason` | Remove a timeout | Moderate Members |
| `/kick member reason` | Confirm, then kick | Kick Members |
| `/ban member reason` | Confirm, then ban; does not purge message history | Ban Members |
| `/unban user_id reason` | Unban a numeric user ID | Ban Members |
| `/case case_id`, `/review` | Inspect results and recent review cases | Manage Messages |
| `/case-void case_id` | Void a warning without deleting its history | Manage Messages |
| `/appeals`, `/appeal-resolve appeal_id accept reason` | Review and resolve appeals | Manage Messages |
| `/setup log_channel` | Select a private log channel; set observe mode | Manage Server |
| `/config show` | Download this server's current policy JSON | Manage Server |
| `/config mode`, `/config ai`, `/config invites` | Automatic enforcement, AI opt-in, invite rule | Manage Server |
| `/config phrase`, `/config domain` | Add/remove phrase or hostname rules | Manage Server |
| `/config exempt-role`, `/config exempt-channel` | Add/remove automation exemptions | Manage Server |
| `/config thresholds` | Tune spam limits and warning escalation | Manage Server |
| `/status` | Queue, errors, AI skips, and aggregate process counters | Manage Server |

Discord's command visibility settings are accompanied by runtime permission checks. Role hierarchy checks additionally protect the owner, bot, invoking moderator, and equal/higher-role targets. Timeouts are limited to 28 days by Discord. [discord.py interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [Member timeout API](https://discordpy.readthedocs.io/en/stable/api.html#discord.Member.timeout)

## Automatic moderation

The default local rules detect:

- More than 5 messages in a rolling 10 seconds, the third duplicate, or identical normalized text in 3 channels from one member.
- More than 5 unique user/role mentions. This counter does not treat `@everyone` as a user/role mention; configure Discord's native AutoMod mention protections as needed.
- Configured whole phrases, with Unicode normalization, common digit substitutions, zero-width-character removal, and bounded Latin spacing/repetition matching. Hindi combining marks are preserved.
- Discord invites, when enabled, and configured blocked hostnames including their subdomains. URLs are parsed without fetching them.

In enforce mode, spam deletes the triggering message and applies a five-minute timeout. Phrase/link violations delete the message and create a warning. Every third active warning in 30 days can trigger a ten-minute timeout. Warnings issued manually also count. Failed DMs do not erase a valid warning. Escalation will not shorten an existing longer timeout. Automatic kick/ban is deliberately absent.

Earlier messages in a spam burst are not retroactively purged. During a 30-second per-category cooldown, further triggering messages can be deleted without repeating warnings/timeouts; deletion/failure counts are added to the originating case. Edited messages are checked without adding to flood counters. Cross-channel detection covers one member repeating identical normalized text, not coordinated raids across multiple accounts.

Server owners, bots, webhooks, members with Manage Messages or Moderate Members, equal/higher-role members, and configured role/channel exemptions are excluded from automation. Manual commands still enforce their own permission and role checks. A parent text-channel exemption also covers its threads.

## Optional AI review

1. Read [PRIVACY.md](PRIVACY.md), publish the server notice there, and review provider data settings.
2. Set `AI_ENABLED=true` and `OPENAI_API_KEY` in the local `.env`.
3. Restart, then run `/config ai enabled:true` in each server opting in.

The implementation calls `AsyncOpenAI.moderations.create` with `omni-moderation-latest`; it does not need a general chat model. The moderation endpoint is currently free, but account rate limits still apply. [Official moderation guide](https://developers.openai.com/api/docs/guides/moderation), [model information](https://developers.openai.com/api/docs/models/omni-moderation-latest)

Messages that pass local rules are selected by a small keyword heuristic or sampling (`sample_rate: 0.1` by default). Setting the sample rate to 1.0 selects all eligible text, still subject to the configured budgets and concurrency limit. Heuristics and sampling miss some harmful content. This is not a claim of complete AI coverage.

Only up to 4,000 characters from one message are sent. No attachments, member IDs, or conversation history are added. Content itself can contain names or other personal information. Responses are cached in bounded memory under guild-scoped hashes. Rate budgets default to 30/minute and 1,000/UTC day and reset on process restart. Outages open a 60-second circuit; checks skipped due to outages, budgets, or concurrency are exposed by `/status`. Local checks continue. AI never deletes, warns, times out, kicks, or bans. Self-harm category flags create supportive review cases.

## Configuration and storage

`config.yaml` documents all global defaults, queue limits, and AI controls. `.env.example` documents all environment variables. Invalid settings and misspelled keys fail validation. Global `policy` values initialize **new** servers; existing server policies are stored in SQLite and changed with `/config`. Global `runtime` and `ai` settings take effect after restart. `/config thresholds` sets every parameter shown in that command to its provided/default value, rather than changing just one value.

SQLite data lives in `data/aegis.sqlite3`; operational logs rotate in `logs/`. Cases record action intents before Discord requests, then completion, partial success, failure, or unknown outcomes after a crash. They contain metadata rather than message bodies. Cases and linked appeals expire after 30 days, with cleanup on startup and every six hours. Warning windows cannot exceed the default retention. See [deployment and backups](docs/DEPLOYMENT.md).

## Development and verification

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

The tests use temporary databases and mocked Discord/OpenAI transports. They exercise behavior without real credentials, moderation API calls, or changes to a live server. See [VALIDATION.md](VALIDATION.md) for the checks actually run and their limits. CI configuration is provided; it has not run on your GitHub account.

| Location | Responsibility |
|---|---|
| `aegis/bot.py`, `aegis/__main__.py` | Lifecycle, intents, slash registration, entry point |
| `aegis/cogs/` | Moderator/configuration/utility commands and bounded event workers |
| `aegis/rules.py` | Local spam and content rules |
| `aegis/ai.py` | Optional classifier, cache, rate budgets, and circuit breaker |
| `aegis/service.py`, `aegis/security.py` | Actions, audit outcomes, role and permission guards |
| `aegis/store.py` | SQLite schema, per-server settings, cases, warnings, appeals, retention |
| `tests/` | Behavior and integration tests |
| `scripts/`, `Dockerfile`, `compose.yaml` | Setup, start, backups, and hosting |
| `docs/RESEARCH.md` | Primary-source research and changes to the supplied blueprint |

This release uses one bot process and one local SQLite database. A web dashboard, fun/economy commands, image/voice content analysis, cross-account raid detection, and distributed sharding are outside this release. Optional voice XP uses connection metadata only and does not analyze audio.
