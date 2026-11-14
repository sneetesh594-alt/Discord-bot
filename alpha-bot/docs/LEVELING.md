# Alpha leveling 1.2

XP progress and tracked activity are separate. New activity starts at the schema-v3 tracking date; old XP-award counts are never presented as historical message or voice totals.

| Member command | Result |
|---|---|
| `/rank [member]` | Read-only rank card, level, total XP, placement and next milestone |
| `/leaderboard xp [period] [page] [private]` | Ten entries, lifetime level/progress and selected-period XP score |
| `/leaderboard text ...` | Eligible message counts, including messages between XP cooldowns |
| `/leaderboard voice ...` | Credited activity minutes, even when voice XP is disabled |
| `/leaderboard reaction ...` | Received ranking; Given/Received selector edits the same board |
| `/statistics [member]` | Activity counts, observed voice time, XP source totals and tracking date |
| `/placement leaderboard:all [member] [metric]` | Placement beyond the first ten entries; choose all/day/week/month |
| `/xp_needed [level] [member]` | Next level by default; remaining XP and bounded earning estimates |
| `/time` | Current observed voice session, eligibility and voice-XP switch |
| `/help [command]` | Command discovery and required/optional parameters |
| `/xp-optout enabled:true` | Delete XP/activity records and stop future tracking in this server |
| `/xp-optout enabled:false` | Opt back in with fresh progress |

The old flat `/leaderboard period page` has moved to `/xp-leaderboard period page`; Discord cannot host the flat command and subgroup syntax under the same root. `/xp-needed level` and `/levels` remain supported. A bare `/leaderboard` is a group: select xp, text, voice or reaction.

Boards are public only in the selected board channel (falling back to the announcement channel). Elsewhere they are private; `private:true` always requests a private reply. Other members clicking a public board receive their own private board. Controls have a one-second user limit and expire after three minutes. First/Previous/Next/Last, My position, Refresh and period selection are supported. Ties use member ID. Retained former-member profiles can appear; current-member-only filtering is not implemented.

Rank cards bundle Noto Sans Devanagari under SIL OFL 1.1 for portable Hindi and Latin text. Long names are shortened to fit. Unsupported scripts/emoji may need additional fonts. Without Attach Files the command still returns the text embed. Avatar bytes/cards are transient.

## Administration

Settings are available as `/levels ...` and `/config leveling ...` through the same handlers. They require Manage Server. Reward mapping, stacking, explicit role sync and XP/level changes also require Manage Roles, fresh actor/bot checks, safe reward roles and an audit reason where applicable.

| Command | Purpose |
|---|---|
| `/levels status` | Settings, counters and role mappings |
| `/levels enabled enabled:true` | Enable XP/activity; false pauses both without deleting history |
| `/levels tracking enabled:false` | Pause activity counts independently of XP awards |
| `/levels board-channel channel:#bot-commands` | Channel where boards default to public |
| `/levels text minimum:15 maximum:25 cooldown:60 daily_cap:5000` | Message XP settings |
| `/levels voice enabled:true xp_per_minute:5 participants:2 daily_cap:600` | Optional voice XP |
| `/levels reactions enabled:true xp:2 cooldown:60 daily_cap:50` | Optional received-reaction XP |
| `/levels announcements enabled:true channel:#bot-commands every:1` | Level-up channel and interval |
| `/levels reward level:5 role:@Level5` | Safe milestone mapping; `remove:true` deletes the mapping |
| `/levels stack enabled:false` | Keep only the highest eligible configured reward tier |
| `/levels ignore-channel`, `/levels ignore-role` | Exclude channel/category/role from XP and activity |
| `/levels multiplier percent:200 role:@Supporter` | Role bonus; a channel option is also available |
| `/levels bonus percent:200 hours:2` | Temporary bonus; 100 percent ends it |
| `/manage xp add/subtract entity amount reason` | Audited change, clamped at zero/maximum; entity is a member |
| `/manage xp set entity amount reason confirm:true` | Atomically set total XP |
| `/manage level set entity level reason confirm:true` | Set XP to that level's cumulative threshold |
| `/manage level reset entity reason confirm:true` | Reset XP history and reconcile rewards; activity survives |
| `/levels sync member reason` | Explicitly reconcile current configured rewards |

`/levels xp` and `/levels reset` remain compatibility entries. Omitted settings use the defaults shown in Discord; provide complete intended values. Changing a reward mapping does not strip a role that is no longer configured. Display commands never assign/remove roles. XP survives a failed role grant/announcement; inspect counters and use authorized sync to retry role reconciliation. Failed notification delivery has no durable retry queue in this release.

## Counting and earning policy

- Meaningful, non-command text from humans can count toward text activity even during an XP cooldown/cap. Bot/webhook messages, stale replays, exclusions, obvious duplicates/spam and local-rule matches do not qualify. Message IDs deduplicate retained events.
- Default text XP is 15–25 once per 60 seconds. Source daily caps use UTC. Role, channel and temporary multipliers apply before caps. Staff moderation exemptions are separate from leveling exclusions.
- The next-level cost from level L is `5*L*L + 50*L + 100`; level 1 is 100 total XP and level 5 is 1,150. Maximum level is 1,000. Admin adjustments are signed XP, never activity counts.
- Observed connected voice is separate from credited voice minutes. Solo/muted connected humans can accrue observed time. Credited minutes require a full interval with the configured number of active, non-bot, non-muted/deafened/suppressed/non-AFK participants. Third-person joins preserve uninterrupted participants' intervals. Disconnect/restart/stalls discard unseen gaps; no audio is accessed.
- Reaction activity records both giver and recipient when a human reacts to an eligible message at most seven days old. It counts one message/giver pair, independent of emoji. Self/bot reactions, pending or flagged messages and opted-out participants are excluded. Removal/re-addition does not subtract/add another count while the deduplication event is retained. Optional reaction XP still rewards only the message author and has its own cooldown/cap.
- Periods are rolling last 24 hours, 7 days and 31 days, not calendar resets. Period XP includes retained signed admin corrections; displayed levels use current lifetime XP. Estimates are eligible awards/minutes, never guaranteed wall-clock times.

## Retention and migration

Schema v3 adds activity tables and escalation claims while preserving v2 settings, XP, cases and rewards. XP/activity events default to 90 days; inactive profiles default to 365 days. Lifetime aggregates can therefore exceed retained event history. Reaction deduplication/peer-attribution erasure covers retained events; older counterpart aggregates cannot be reconstructed after event pruning. See [privacy](../PRIVACY.md).

Opt-out deletes XP/activity profiles, retained events, content hashes and retained peer attribution; it keeps only a minimal preference marker. Existing reward roles remain. Server removal clears server data. Backups and Discord messages have separate retention. See [upgrade/recovery](UPGRADE-1.2.md) before deployment or rollback.
