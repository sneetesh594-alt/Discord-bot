# Alpha Control Room — local Discord dashboard

Version 1.3.1 includes a local dashboard sharing the bot's real SQLite repositories. The current deployment choice is the user's PC; it is not an internet-hosted or always-on service.

## Open it

Run `Start-Alpha.cmd` from the project directory, keep that window open, then visit **http://127.0.0.1:8765**. The launcher enables the dashboard and starts the restart supervisor with command synchronization. Stop it with Ctrl+C. The OS instance lock rejects duplicate bot processes; stop a known older instance before launching. Running in your own window avoids depending on a Codex tool terminal staying alive. Sleeping/shutting down the PC still stops service.

Without OAuth setup, the page shows setup status and locks server data/settings. The operator has said they cannot configure OAuth yet, so **real Discord sign-in is not currently activated or live-verified**.

## One-time Discord OAuth setup

1. In the Discord Developer Portal, choose the **Alpha bot application**, then **OAuth2**.
2. Register this exact redirect: `http://127.0.0.1:8765/auth/callback`.
3. Run `Setup-OAuth.cmd` and paste the OAuth Client Secret at its hidden local prompt. Alternatively put `DISCORD_CLIENT_SECRET=your_actual_client_secret` in the local `.env`. This is the application's OAuth Client Secret, not the bot token. Keep it out of chat and GitHub. `DISCORD_CLIENT_ID` may be left blank when running with the connected bot; its ID is used automatically.
4. Restart Alpha, refresh the page and select **Continue with Discord**. Requested scopes are `identify guilds`; the dashboard does not request your email or DMs.
5. Select a server you manage where Alpha is installed. Manage Server is required for data/settings; reward changes also require current Manage Roles permission and safe reward roles.

Alternative launch from PowerShell:

```powershell
$env:DASHBOARD_ENABLED='true'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-supervised.ps1 -SyncCommands
```

The server binds only to loopback. Use the configured exact address; other Host headers are rejected. The current implementation serves local HTTP, not TLS. Sharing an internet-facing dashboard needs a separately designed HTTPS host/deployment, OAuth redirect and access policy; changing the bind address is intentionally rejected.

## Available pages

- Discord login and manageable-server selection.
- Overview: real aggregate XP/activity, recent audit action metadata and processing counters.
- Leaderboards: XP/messages/credited voice/reactions received or given, rolling periods, pagination and personal placement.
- Experience/cooldowns: source switches, XP rates, eligible participants and daily caps.
- Level rewards: safe milestone role mappings and tier stacking. Existing roles update on awards or explicit authorized sync; configuration does not mass-edit members.
- Announcements: enablement, destination/interval and public leaderboard channel.
- Exclusions and multipliers: the current Alpha policy, including global activity exclusions and percentage maps.
- Rank card: real profile/placement, PNG render and member-ID lookup.
- Moderation: observe/enforce, invite rule, phrases/domains, thresholds, private log destination and per-server AI opt-in. The operator's global AI switch still applies.

Save validates the selected server and current actor permissions, checks the last loaded settings revision and writes to the same repositories as commands. Stale edits return a conflict: Reload, review and save again. Valid saves have audit cases. Unchanged values do not clear voice intervals or create a new audit case. Read views never grant/remove roles.

## Authentication and data

OAuth uses S256 PKCE and state is one-use, browser-bound and valid for five minutes. User access tokens stay in bounded server memory, not browser storage or SQLite. Sessions last at most eight hours and end on logout/restart; expired sessions are rejected and discarded during subsequent requests. Session cookies are HTTP-only and SameSite Lax; local HTTP is restricted to loopback. Writes additionally require the expected Origin and CSRF token. The callback and access logs do not record OAuth query values.

Discord IDs remain strings in browser JSON. Output uses escaped text. Responses deny framing, use a restrictive CSP and no-store caching. There is no CORS or unauthenticated settings API. Member name lookups are bounded and cached transiently; inaccessible/former members fall back to an ID label. Avatar bytes and PNGs remain transient.

Temporary browser QA used an isolated database and synthetic Discord identity/server, visibly labeled TEST DATA. It verified server selection, a real settings save/audit, updated overview, rank PNG, a reactions-given empty state and mobile layout. That does not establish real Discord OAuth success, real role grants or production voice awards.

## Remaining Atom differences

Alpha's formula/ranges, exclusions and multiplier policies remain documented Alpha policies. Full theme customization, formula migrations, scoped exclusions, hosted/live public boards, import, achievements, vote/collectible effects and games are separate modules. See the research/gap report beside the project. No inactive controls pretend these systems are implemented.
