# Privacy and community notice

This document describes this implementation. The server administrator and bot operator must identify their own contact details and actual hosting practices before publishing a privacy policy.

## Data flow

The bot receives messages in server channels it can access when Discord grants Message Content access. It ignores direct messages, bot/webhook messages, and configured exemptions. Message text is held transiently in the bounded event queue and Discord client cache for local checks. The spam tracker keeps content hashes, timestamps, message IDs, and channel IDs; it does not keep the original text. Hashes are not a guarantee of anonymity.

Persistent SQLite records contain server/member/moderator IDs, optional message/channel IDs, rule categories, action/status metadata, timestamps, AI category scores if enabled, moderator-entered reasons, and appeal text. Reasons and appeals may contain personal information supplied by the author. Normal conversation bodies and attachments are not persisted or downloaded.

The private moderation channel receives case metadata and links, without copied message bodies. Discord stores those bot messages under its own service behavior and server moderation practices. Operational logs contain IDs and action outcomes and rotate by file size; they have a separate lifetime from database rows. Only the host operator should have filesystem access to the database and logs. The application does not encrypt those files; use appropriate host access controls and encrypted storage where required.

## Leveling data

When enabled in a server, leveling stores cumulative XP, source totals, counters for awarded messages/voice minutes/reactions, cooldown timestamps, and an award ledger containing Discord IDs, source, event key, XP amount, and time. Recent text fingerprints suppress repeated-content farming without retaining the original text. Moderation exemptions and XP exclusions are separate; staff can earn XP from eligible conversation.

XP event records default to 90 days. XP profiles expire after 365 days without an award or correction. Recent-content hashes are removed after at most one hour plus the six-hour cleanup interval. XP retention is independent of the moderation-case retention. Leaderboards can include retained profiles of former members because the implementation does not subscribe to member-leave events.

Voice activity/XP tracking reads voice-channel participation and mute/deafen state; the bot does not join channels, record, transcribe, or access audio. Reaction activity/XP tracking reads reaction metadata and fetches the reacted-to message to check eligibility. Rank cards can fetch a member's 128-pixel Discord avatar from Discord's CDN; avatar bytes and rendered cards are not persisted.

Level-up notifications identify the member and level in the configured Discord channel without pinging anyone. Discord retains those messages separately. Manual XP corrections/reset actions produce moderation audit records. Leveling data is not sent to the AI provider.

`/xp-optout enabled:true` erases the caller's XP profile, award ledger, and recent-content records in that server, saving only a minimal server/user-ID preference marker to prevent future awards. `/xp-optout enabled:false` removes that marker and permits fresh progress. Existing reward roles are unchanged by opting out. Host backups need separate handling. Server removal deletes all leveling tables for that server.

## Independent activity tracking in 1.2

While leveling and activity tracking are enabled, the bot stores eligible message counts, connected/observed voice seconds, credited voice minutes, and reactions given/received even if voice/reaction XP switches are off. Message and reaction event IDs, participant IDs, timestamps and peer attribution provide retained-event deduplication and period boards. No historic activity is inferred from old XP. Connected voice can include solo/muted time; credited voice has stricter eligibility. Activity can be paused with `/levels tracking enabled:false`.

XP/activity events default to 90 days and inactive activity profiles to 365 days. All-time aggregates can outlive their individual events. Reaction removals do not subtract lifetime counts. Given/received deduplication and peer-attribution deletion cover retained events; after event pruning older counterpart aggregates cannot be traced back to an opted-out participant. Opt-out also deletes the caller's activity profile/events and retained peer attribution, while preserving a minimal preference marker. Existing roles, backups, Discord board/notification messages and separate operational logs remain subject to operator handling.

Leaderboards can be public in the configured board/announcement channel; other channels default to private and `private:true` forces a private board. Boards and statistics expose member identity plus aggregate activity/XP, including retained former members. Rank cards bundle an open-license Hindi/Latin font. Runtime health files contain process IDs, connection/processing timestamps and counters, with no token or message body.

## Optional external AI processing

AI is off by default. It requires an operator switch/key and an explicit per-server `/config ai enabled:true` setting. When both are enabled, selected messages that pass local checks are sent to OpenAI's Moderation API as text, limited to 4,000 characters. The bot adds no user IDs or conversation history, but the text itself may contain identifiers. The bot does not inspect images, voice, video, or file attachments.

Results are cached in bounded memory for up to five minutes under guild-scoped hashed keys. The bot does not train models. OpenAI's API data is excluded from model training by default unless the customer opts in; the current data-controls table lists no abuse-monitoring or application-state retention for `/v1/moderations`. Verify the endpoint-specific table and account configuration before enabling. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)

Do not enable provider training/data-sharing opt-ins for Discord message data. Discord's policy restricts training on API message content without express permission. [Discord Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy)

Do not submit known or suspected child sexual abuse material to the moderation service. Follow dedicated provider/Discord reporting and safeguarding processes; this bot does not provide a CSAM identification or reporting workflow. [Official moderation guidance](https://developers.openai.com/api/docs/guides/moderation)

## Retention and requests

The default database retention is 30 days. Cleanup runs at startup and every six hours. Cases expire by their creation time, and linked appeals are removed with them even when pending. Active warning counts use a window within that retention period. On server removal, the bot deletes that server's cases, appeals, and stored settings and clears transient rule state. Backups, operational logs, and Discord log-channel messages require separate operator retention handling. SQLite deletion does not promise immediate forensic erasure from filesystem pages or host snapshots.

Users can submit `/appeal` for their own retained cases while they can access commands in that server. Accepted appeals void the warning history entry; moderators use separate commands to reverse a timeout or ban. Banned, kicked, or timed-out members may need to contact moderators through the server's published appeal/contact route. The bot does not automatically notify users of appeal resolutions.

For access, correction, or deletion requests, contact the server's moderators and the bot operator. This version does not include an automated personal-data export/deletion command. The operator must implement a suitable request process and update backups/logs as appropriate.

## Suggested server notice

> This server uses Aegis to check messages for spam and configured phrase/link rules. In enforce mode it can delete messages, issue warnings, and time out members. AI, if enabled in this server, sends selected message text to OpenAI for moderation signals that our moderators review; AI does not automatically punish members. The bot stores limited case metadata and appeal text, normally for 30 days. Read `/rules` and `/privacy`. Appeal with `/appeal`, or contact our moderators through the server's published contact route if commands are unavailable.

The administrator should add the moderation contact, operator identity/contact, current AI setting, and any different retention before posting this notice.
