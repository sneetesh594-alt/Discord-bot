# Atom-style leveling: research and implementation

Researched **13 November 2026**. The reference is **Atom at leveling.gg**, whose first-party documentation specifically covers XP, ranks, and community progression. Several Discord products use the name Atom; the leveling reference is identified here to make the scope clear.

This is an independent implementation for Alpha bot. Feature descriptions below distinguish documented Atom behavior from the choices made for this bot. No proprietary source, private dashboard, or undocumented XP formula was copied.

## What the primary sources establish

Atom describes cumulative XP with derived levels, configurable text XP ranges, independent voice eligibility controls, and a staged rollout beginning with text activity. Its own first-system guide recommends leaving reaction XP disabled until the community has chosen the behavior it wants to recognize. [Atom first leveling system](https://www.leveling.gg/guides/start/first-leveling-system)

Atom's public command reference includes `/rank`, `/leaderboard`, XP administration, statistics, and an XP-needed command. That supports familiar command names and member-facing progression information for this implementation. It does not establish an exact formula or scoring rate to reproduce. [Atom command reference](https://www.leveling.gg/commands)

Atom documents rank cards with configurable backgrounds and progress bars. Alpha provides a generated PNG card showing name/avatar, total XP, level, server placement, and progress, accompanied by a text embed. It does not include Atom's dashboard layout editor or premium banner options. [Atom rank cards](https://www.leveling.gg/features/rank-cards)

Atom documents XP leaderboards and premium live ranking periods. Alpha provides interactive slash-command pages for all-time totals and rolling 24-hour, 7-day, and 31-day windows. There is no hosted public leaderboard or automatically edited live leaderboard message. [Atom leaderboards](https://www.leveling.gg/features/leaderboards)

Atom supports level milestones and other reward types, including position and text-statistic roles. Alpha implements milestone roles, with stacking or highest-tier-only behavior. Position-based rewards, branched conditions, and achievement inventories are beyond this addition. [Atom reward roles](https://www.leveling.gg/features/reward-roles)

Atom's formula documentation describes several selectable curves, including quadratic, exponential, and linear models. Alpha uses one explicit, tested curve so progression cannot silently shift through an undocumented default. Alternative curves would require a planned progression migration. [Atom formula guide](https://www.leveling.gg/articles/formula)

Atom documents channel/scheduled multipliers and highest-position configured role ranges. Alpha instead selects the highest configured role **percentage**, combines it with a channel/category percentage, and optionally applies a temporary server-wide bonus. These are intentional implementation differences. [Atom multiplier guide](https://www.leveling.gg/guides/build/xp-multipliers)

Atom's voice troubleshooting guidance emphasizes completing a whole eligible interval and checking participants, exclusions, and mute/deafen conditions. Alpha's optional voice XP follows that principle: at least two eligible humans, a continuous minute, no AFK channel, and no muted/deafened/suppressed or timed-out participant. [Atom voice eligibility](https://www.leveling.gg/guides/fix/voice-xp-not-working)

## Alpha's progression rules

Text XP starts at **15–25 XP per eligible message**, with **60 seconds between awards per member**. These are Alpha's chosen defaults, not a claim about Atom's exact defaults. English, Hindi, and Hinglish conversation can qualify. Empty content, short filler, command-only/link-only content, bots, webhooks, timed-out members, ignored channels/roles, and local-rule matches do not qualify.

The additional cost to advance from level `L` to `L+1` is:

```text
5 × L² + 50 × L + 100 XP
```

Total XP is cumulative. Levels start at 0 and are capped at 1,000; the balance uses bounded integers and exact boundary calculations. Examples generated from this implementation:

| Reach level | Cumulative XP |
|---|---:|
| 1 | 100 |
| 2 | 255 |
| 5 | 1,150 |
| 10 | 4,675 |
| 20 | 23,850 |
| 35 | 101,675 |
| 50 | 268,375 |

Random awards, cooldowns, exclusions, daily caps, and multipliers affect actual time to a level. A level is an activity indicator; it does not establish trustworthiness or permission to moderate.

## Farming resistance and its limits

Cooldowns and award deduplication are persisted in SQLite, so reconnecting or restarting the bot does not reset them. A message ID earns once. Reusing the same normalized text within five minutes is suppressed even when another eligible message was sent in between. Recent content checks store hashes, not bodies. The XP worker runs after moderation and does not award for a local violation or an AI flag routed to review, including when moderation is observing rather than deleting.

Text, voice, and reaction sources have independent UTC daily XP caps: 5,000, 600, and 50 by default. Awards are clipped to the remaining allowance; reducing a cap never subtracts already-earned XP. Role/channel/event bonuses still respect these caps. No system can prove conversational quality from message length or a cooldown; alternating new filler, coordinated reactions, and multiple accounts remain community moderation issues.

Voice intervals are tracked in memory from observed Gateway state. Relevant join/leave or mute/deafen changes reset partial intervals in the affected channels. Disconnects discard partial intervals. Each tick awards at most one minute, and offline time is not backfilled. The bot reads voice state metadata; it never joins, records, transcribes, or listens to audio.

Optional reaction XP goes to the **message author**, not the person reacting. Self-reactions, bot activity, disallowed content/channels, and messages older than seven days are excluded. One reactor/message pair can earn once regardless of emoji changes or removal/re-addition, within the retained ledger. Any eligible reaction can count; administrators should keep this source off when they cannot explain what a reaction reward represents.

## Role and delivery checks

Role rewards require Manage Roles and suitable role placement. Alpha re-checks roles before each grant and rejects managed/default roles, staff/moderation permissions, and dangerous channel overrides. A role that is safe when configured can become unsafe later, so checking only at setup is insufficient. Unrelated member roles are never removed. Owners and members at or above the bot are protected from automated reward-role changes. [discord.py role operations](https://discordpy.readthedocs.io/en/stable/api.html#discord.Member.add_roles), [Discord role hierarchy](https://docs.discord.com/developers/topics/permissions)

Milestone notifications go to the configured channel or the activity channel and do not ping members or `@everyone`. Failed notifications or reward-role requests do not roll back XP. Pending delivery problems are exposed in leveling counters. Manual XP corrections are audited and do not publish simulated level-ups.

Voice States and Guild Reactions are standard Gateway intents. A small voice-only member cache supports participant checks; Guild Members and Presence intents remain disabled. No extra privileged-intent toggle is required for these optional sources. [discord.py intents and caching](https://discordpy.readthedocs.io/en/stable/intents.html)

## Persistence, retention, and rollout

Schema version 2 adds XP profiles, a signed award ledger, recent-content hashes, opt-out markers, and separate leveling settings. The v1-to-v2 migration adds tables without rebuilding moderation tables or resetting existing server policy. It is tested against both fixtures and a copy of Alpha's pre-upgrade database; a verified backup is kept before the live update.

The 30-day moderation retention does not erase XP totals. XP ledger entries default to 90 days, and profiles expire after 365 days without an award or correction. Server removal deletes both moderation and XP data. `/xp-optout enabled:true` deletes the caller's XP profile and ledger and preserves only the minimum opt-out marker; previously earned roles are unchanged. Members who leave a server are not detected through a Members intent in this release, so retained leaderboard entries may include former members until reset, opt-out, or inactivity expiry.

The existing server starts with text XP enabled and optional voice/reaction sources disabled. Level-ups use the existing bot-command channel. Milestone roles use zero base permissions and no new channel access. Moderation's observe/enforce setting is preserved independently. Validate `/rank`, `/leaderboard`, and `/levels status` first; expand activity sources only after reviewing exclusions, award pace, and reward roles.
