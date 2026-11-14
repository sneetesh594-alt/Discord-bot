# Alpha v1.3.1 â€” research, fixes and dashboard

**Date:** 14 November 2026  
**Validation:** 207 automated tests passed with zero test warnings, including 11 dashboard checks.  
**Dashboard:** local implementation complete and browser-tested against isolated test data. Real Discord OAuth setup is pending operator configuration.

## What the research established

The [Atom research and gap list](Alpha-Atom-Research-and-Gap-List-14-Nov-2026.md) distinguishes current official documentation, earlier live observations, Alpha reproductions and untested features. Atom-like command names do not imply identical formulas, activity eligibility, free-plan controls or every optional module. Alpha keeps its own branding and documented policies.

## Confirmed fixes

1. Another member's Given/Received/period/navigation action now applies to that member's private board, preserving the shared board.
2. A failed rank image render still delivers text XP/level/placement and increments a diagnostic counter.
3. Text-only command saves preserve unaffected voice intervals. Dashboard saves compare actual changed values, so unchanged voice fields sent with a text form also preserve timers.
4. Web settings use revision checks, including a second check at commit time, to reject stale saves after command/admin edits. Guild removal during an in-flight save is rejected.
5. Runtime health now records command completions/errors and timestamps. A stale old connected file is not treated as uptime. `Start-Alpha.cmd` provides an operator-owned bot/dashboard launcher; it is not a cloud host or startup service.

The exact user-observed Discord `/rank` and leaderboard symptoms were not described beyond command names, so full live reproduction of those symptoms is not claimed.

## Alpha Control Room

Local dashboard: **http://127.0.0.1:8765** while Alpha is running with the dashboard enabled.

- Discord authorization-code sign-in and manageable-server selector.
- Real overview, activity boards, rolling periods, placement, member-ID rank/PNG preview.
- Experience, cooldowns/caps, safe rewards, announcements, exclusions, multipliers and moderation forms.
- Shared SQLite/repository writes, current permission checks, CSRF/session/Origin controls, audit cases and stale-save conflicts.
- Responsive dark Alpha interface. No fake successful saves or live-looking production metrics when authentication/backend is unavailable.

The operator selected **Discord login** and **PC-only hosting**, and cannot set OAuth credentials now. Therefore the real page stays in locked setup mode until the Developer Portal redirect and local `DISCORD_CLIENT_SECRET` are configured. Nothing substitutes a different login or publishes the PC's API to the internet. Setup is in [DASHBOARD.md](../../docs/DASHBOARD.md).

## Evidence and remaining work

[Overview QA screenshot](Alpha-Dashboard-Overview-QA.jpg) Â· [Mobile QA screenshot](Alpha-Dashboard-Mobile-QA.jpg). Both contain temporary synthetic test data, not real member data.

Pending: real OAuth acceptance, real-server dashboard saves, live two-human voice eligibility and actual milestone delivery. Separate feature backlog: fully customizable card themes, formula migration, per-source exclusions, hosted/live public boards, richer announcement templates, imports, achievements, voting/collectibles, economy and games. These are listed honestly rather than shown as working controls.

No live XP reset, role permission change or moderation-mode change was made for this upgrade. Backups precede implementation. Credentials, databases, logs, virtual environments and QA-only fixture code are excluded from source distribution.

## Point-in-time live check

At **14 November 2026, 17:41 IST**, v1.3 connected and 33 command roots were API-verified. The real local dashboard returned HTTP 200, and unauthenticated data/login remained locked (401/503) with OAuth unconfigured. Live v3 database integrity/foreign keys passed; current administrator changes retained. This does not establish continuous uptime or live Discord OAuth/member command success.

## Warning and OAuth follow-up

The full suite also passed with deprecations treated as errors. Markdown escaping now uses a project-owned helper instead of the dependency's deprecated call path, with no warning suppression or site-packages edits. OAuth includes S256 PKCE. `Setup-OAuth.cmd` automates hidden local secret entry, preserving the bot token. The user's Portal setup report does not supply the missing local Client Secret; real sign-in remains blocked on that credential.

## Point-in-time live check

At **14 November 2026, 18:04 IST**, v1.3 connected and 33 command roots were API-verified. The real local dashboard returned HTTP 200, and unauthenticated data/login remained locked (401/503) with OAuth unconfigured. Live v3 database integrity/foreign keys passed; current administrator changes retained. This does not establish continuous uptime or live Discord OAuth/member command success.
