# Atom working research and Alpha gap list

Reviewed **14 November 2026**. This review combines current first-party guides with the eight Discord member-command flows and dashboard screens observed in the earlier live review. Documentation, earlier live observations and current Alpha probes are distinct evidence. Premium/admin operations, formula migration and real eligible voice sessions have not been live-tested in Atom.

## Working model

1. Administrator signs in, selects a server, configures earning and eligibility, pacing, recognition and delivery. Member commands display the resulting XP and activity. Settings must not merely change a web preview.
2. Text XP uses a configured range, per-member award cooldown, eligibility and multipliers. Atom's documented base maximum is exclusive; highest-position configured role ranges override the everyone range. Alpha's inclusive range and role percentages are different policies, not verified Atom parity. [Text XP](https://www.leveling.gg/guides/build/text-xp)
3. Voice uses complete eligible intervals, participant/state requirements and its own source switch. A quick join is not proof of an award. Text can inherit category multipliers; Atom documents direct-channel voice multipliers. Alpha currently applies its shared category policy to voice too. [Voice troubleshooting](https://www.leveling.gg/guides/fix/voice-xp-not-working), [multipliers](https://www.leveling.gg/guides/build/xp-multipliers)
4. `/leaderboard xp/text/voice/reaction` are distinct views. Earlier live checks established text messages, voice minutes and reaction given/received counts; XP is separate. `/placement` refers to Atom live leaderboards; Alpha's rolling-period placement has different semantics. [Command reference](https://www.leveling.gg/guides/reference/commands)
5. Rank Card V2 contains identity, rank, levels and XP progress; its documented background/banner/colors/footer controls are more extensive than Alpha's fixed theme. Alpha must keep its own branding. [Rank cards](https://www.leveling.gg/guides/build/rank-cards)
6. Atom documents multiple selectable XP curves and projections. Changing a curve can change the interpretation of existing XP, so it is a migration rather than a cosmetic toggle. Alpha uses one documented cumulative quadratic curve. [Formula](https://www.leveling.gg/articles/formula)
7. Achievements, vote effects, collectibles, imports, economy and games are separate systems. A visible menu does not establish tested operation or free-plan availability. Import is documented as destructive to existing Atom leveling records. [Commands](https://www.leveling.gg/commands)

## Current Alpha findings

| Priority | Finding / gap | Evidence and action |
|---|---|---|
| P0 | Runtime status can be stale while still saying connected | Stored heartbeat last advanced at 12:47 IST; previous terminal session is unavailable. Do not claim current uptime from that file. Provide an operator-owned launcher and show heartbeat age/last command activity. |
| P1 | Other members' leaderboard select actions are dropped on private forks | Callback creates a fork from the old mode/period and skips the selected action. Reproduce and preserve the requested action without editing the original board. |
| P1 | Rank-card render failure can prevent any rank response | Render exceptions currently escape after defer. Add a tested text fallback and diagnostic counter. A mocked offline test is not a live command reproduction. |
| P1 | Unrelated setting saves clear voice intervals | `changed()` clears all guild voice sessions even for text-only changes. Preserve unaffected intervals. |
| P1 | No dashboard | Add Discord OAuth, server selection, overview, true activity boards, rank preview, typed settings, exclusions/multipliers/rewards, conflict detection and audit metadata. |
| P1 | Admin authorization must apply to web writes | Verify the selected guild and current member permissions server-side; role settings additionally require Manage Roles and safe hierarchy checks. |
| P1 | OAuth/hosting configuration unavailable | User selected Discord login and PC-only hosting, and cannot set Client Secret/redirect now. Build/test the local implementation; keep unauthenticated data and writes locked. Real OAuth sign-in remains an explicit external setup step. |
| P2 | Fixed card theme, single formula, unscoped exclusions | Document actual Alpha policy. Theme editors, selectable formula migrations and per-source exclusions need their own implementation and acceptance tests. |
| P2 | Hosted/live boards, richer announcements, imports, achievements, voting/economy | Inventory as separate modules; do not expose non-working controls or claim full Atom parity. |

## Dashboard acceptance

- Real Discord authorization-code login using `identify guilds`, bound/one-use OAuth state, HTTP-only sessions and CSRF-checked writes. Discord tokens stay server-side. [Discord OAuth2](https://docs.discord.com/developers/topics/oauth2)
- Only manageable guilds where Alpha is present may be configured; each write rechecks membership and permissions. Discord IDs remain strings in browser JSON.
- Forms load real server settings; Save persists to the same repository used by commands, validates inputs, records actor/action and rejects stale versions instead of overwriting concurrent command changes.
- Correct units and stable rank/leaderboard ordering; no hidden role changes on views.
- With missing OAuth configuration, show setup status and deny data/write routes. No fake successful save or fake connected metrics.
- Local callback: `http://127.0.0.1:8765/auth/callback`. PC/session must remain running. Remote access needs appropriate HTTPS hosting; a GitHub source upload is not bot hosting.

Implementation and test results will be recorded separately. No claim is made that all Atom features or the user's reported live `/rank` and leaderboard symptoms have already been fixed.

## Implementation follow-through

v1.3 reproductions/fixes, dashboard coverage, 197-test result and remaining live checks are recorded in [the implementation report](Alpha-Bot-v1.3-Fixes-and-Dashboard.md). The first three code findings now have passing regressions; runtime continuity still depends on the operator-owned PC session.
