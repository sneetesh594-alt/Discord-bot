# Version 1.2 validation — 14 November 2026

Current local release: **1.2.0**, Windows, Python 3.13.17, discord.py 2.7.1, Pillow 12.3.0.

- **183 tests passed**, including the five reproduced audit bugs, atomic map/set changes, fresh role permissions, independent activity counts, reaction attribution/opt-out, period/tie/placement behavior, voice continuity/disconnect/stalls, view ownership/rate limits/expiry, command aliases, health writes and retryable connection failures.
- Ruff lint and formatting passed. All PowerShell scripts parsed. Local configuration validation passed. All Cogs and nested command definitions load/serialize offline.
- A verified schema-v2 backup was migrated to v3 in a copy. Every original table was identical afterward: **4 cases, 1 XP profile and 20 total XP** preserved with settings/reward maps. New activity had zero invented profiles. Integrity and foreign-key checks passed. A backup of the migrated copy restored identically.
- The bundled-font Hindi/Latin rank card was rendered and visually inspected. The wheel contains the font, SIL OFL license and source attribution.
- The 1.2 wheel installed into the bot environment and a separate package target. From that target, all **32 command roots** loaded/serialized, bundled-font PNG rendering and offline CLI validation passed, and every packaged application/asset file matched the source. This was an offline install using existing verified dependencies, not a new clean dependency-environment test.
- **Live restart verified 14 November 2026, 12:27 IST.** Alpha 1.2 connected to the Gateway and synchronized all **32 command roots**. Discord API independently listed those commands; the public Gateway HTTP probe returned 200. A later heartbeat advanced with Gateway true, commands_synced true, zero processing errors/queue drops. The prior failed connection attempt is historical; its exact underlying cause was not established.
- Live schema **v3**, integrity OK, no foreign-key errors, **4 cases / 1 XP profile / 20 XP** preserved. A fresh backup preceded launch. New-session processed messages remain zero; no real-member slash flow, two-human voice credit, milestone role or notification delivery is claimed. The local terminal/supervisor requires the PC awake and online; cloud/startup-service deployment is not claimed.
- Browser smoke testing also stopped: the computer-use tool could not confidently determine the current browser URL to enforce policy. No further browser input was attempted.
- There are 23 existing dependency deprecation warnings from discord.py's Markdown helper on Python 3.13; tests pass. Docker, GitHub CI execution, Linux/Python 3.11, long-term uptime/throughput and real community-language false-positive rates remain unverified. AI is disabled and no real OpenAI request was made.
- Optional PyNaCl/davey startup warnings concern joining voice/audio. This bot observes Gateway voice-state metadata without joining or recording audio; optional audio support is not required for these features. Live two-human credited voice remains unverified.

See [upgrade/recovery](docs/UPGRADE-1.2.md), [current commands](docs/LEVELING.md), and the implementation report beside the source folder. Historical records below describe earlier builds, not current live status.

---

# Current verification: version 1.1

Verified on **13 November 2026** after adding Atom-style leveling:

- **156 automated tests passed**, including the existing moderation suite, XP boundaries, restart-safe cooldowns, replay/duplicate suppression, daily caps, scoped leaderboards, opt-out erasure, voice/reaction eligibility, role guards, moderation integration, rank-card output, and interaction limits.
- Ruff lint and formatting passed; the Python 1.1.0 wheel built successfully. The PNG rank-card preview was rendered and visually inspected.
- The v1-to-v2 migration preserved cases and moderation policies in a copy of the server database. A verified backup was taken again before live activation, and the live database passed integrity/foreign-key checks after migration.
- **Alpha bot connected to Discord**, with **26 top-level commands** synchronized to the test server. The new command definitions and milestone roles were independently verified through Discord's API.
- Text XP is enabled at **15–25 XP per eligible message**, with a **60-second cooldown**. Five zero-permission milestone roles and the existing bot-command announcement channel are configured. Moderation remains in its existing **observe** mode.
- Voice/reaction XP are implemented and covered by automated tests but remain disabled in this server. No live member voice/reaction award or automatic milestone notification was exercised during setup. No fake XP was inserted into member profiles for testing.
- Docker execution, CI execution on GitHub, Linux/Python 3.11 runtime behavior, and long-term throughput/uptime remain unverified locally. Fourteen warnings originate from discord.py's existing Markdown helper on Python 3.13; they do not fail tests.

Research and source links: [LEVELING_RESEARCH.md](docs/LEVELING_RESEARCH.md). Current commands: [LEVELING.md](docs/LEVELING.md).

## Original version 1.0 build record (historical)

Verified locally on **13 November 2026**, using **Windows and Python 3.13.17**. The dependency locks resolve discord.py 2.7.1 and OpenAI 2.54.0. All API integrations were exercised offline with mocks; no real token or API key was supplied.

## Results

| Check | Result |
|---|---|
| Automated behavior/integration suite | **89 tests passed** |
| Ruff lint | Passed |
| Ruff formatting | Passed |
| Offline configuration validation | Passed, with no Discord connection or AI request |
| All Cogs load and application commands serialize | Passed in the automated suite |
| Permission, protected-member, and cross-server case checks | Passed |
| Observe mode and automatic enforcement outcomes | Passed with mocked Discord members/messages |
| AI SDK request/response contract | Passed using the real SDK and mocked HTTP transport |
| AI caching, budgets, concurrency, outage circuit, and human-review routing | Passed |
| SQLite persistence, concurrent writes, retention, crash recovery, and appeals | Passed |
| Consistent online SQLite backup including WAL data | Passed |
| Windows setup/start scripts | PowerShell syntax parsed successfully |
| Python wheel build | Passed |
| Fresh wheel installation and import in a separate virtual environment | Passed |
| Offline CLI check from the fresh wheel installation | Passed |
| Tests from the extracted source ZIP | **89 tests passed** |
| Compose and GitHub Actions YAML parsing | Passed; execution limits below still apply |
| Source ZIP and wheel archive integrity | Checked during final packaging |

The suite reports 12 deprecation warnings inside discord.py's Markdown-escaping helper on Python 3.13. They concern a positional regular-expression argument in the dependency; no tests fail and no dependency code was patched.

## Limits of verification

- The bot has **not connected to a live Discord server**, registered commands with a real application, or moderated actual members. The application token, server invitation, intent toggle, role placement, and server smoke tests remain local operator setup steps.
- A real OpenAI moderation request has **not** been made. Account access, provider rate limits, and moderation accuracy on community messages are not established by the mock tests.
- Docker was unavailable in the build environment. The supplied Dockerfile/Compose deployment needs an execution test on the deployment host.
- The GitHub Actions workflow is supplied but has not run on a repository. Python 3.11/Linux execution is configured there, not claimed as already completed locally.
- The Windows setup/start scripts were parsed, not run end-to-end against a live token. Dependency installation, editable package installation, CLI checks, and wheel creation were verified separately in an isolated workspace environment.
- No measured throughput, production uptime, Hinglish classification accuracy, or false-positive rate is claimed. Use observe mode and the rollout process in `docs/RESEARCH.md` to evaluate those properties for your server.

## Reproduce the main checks

From the extracted project with its development dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\python.exe -m aegis --check
```
