# Alpha Discord Bot

Alpha v1.2 source is in [alpha-bot](alpha-bot/README.md).

Includes moderation, persistent cases/appeals, text XP, safe milestone roles,
Hindi/Latin rank cards, XP/text/voice/reaction leaderboards, activity statistics,
audited administration, setup scripts, Docker configuration and 183 passing local tests.

- [Setup and configuration](alpha-bot/README.md)
- [Leveling commands](alpha-bot/docs/LEVELING.md)
- [Validation and known limits](alpha-bot/VALIDATION.md)
- [Blueprints and implementation report](alpha-bot/docs/project)
- [Privacy](alpha-bot/PRIVACY.md)

For Windows setup, open PowerShell in `alpha-bot` and run `scripts/setup.ps1`.
Supply credentials in a local `.env` using `.env.example`; credentials, runtime
databases, logs and virtual environments are excluded from this upload.

The repository also contains earlier JavaScript bot files at its root. Alpha's
Python application runs from the `alpha-bot` directory.

The uploaded snapshot is versioned source, not always-on hosting. The current
local bot session requires its PC and session to stay awake and online.
