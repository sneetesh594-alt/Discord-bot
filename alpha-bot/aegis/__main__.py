"""Launch, offline configuration validation, and least-privilege invite generation."""

from __future__ import annotations

import argparse
import asyncio
import logging

import aiohttp
import discord

from aegis.bot import AegisBot
from aegis.config import Settings
from aegis.logging_config import configure
from aegis.runtime import InstanceLock


def invite_url(application_id: int) -> str:
    permissions = discord.Permissions(
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=True,
        embed_links=True,
        read_message_history=True,
        manage_messages=True,
        moderate_members=True,
        kick_members=True,
        ban_members=True,
        manage_roles=True,
        attach_files=True,
    )
    return discord.utils.oauth_url(
        application_id, permissions=permissions, scopes=("bot", "applications.commands")
    )


async def run(settings: Settings):
    async with AegisBot(settings) as bot:
        await bot.start(settings.token, reconnect=True)


def main():
    parser = argparse.ArgumentParser(description="Aegis Discord moderation bot")
    parser.add_argument(
        "--check", action="store_true", help="Validate config offline; no token needed"
    )
    parser.add_argument("--invite", type=int, metavar="APPLICATION_ID", help="Print bot invite URL")
    args = parser.parse_args()
    if args.invite is not None:
        if args.invite <= 0:
            parser.error("APPLICATION_ID must be positive")
        print(invite_url(args.invite))
        return
    try:
        settings = Settings.load(require_token=not args.check)
    except (ValueError, OSError, TypeError) as error:
        parser.exit(2, f"Configuration error: {error}\n")
    if args.check:
        print("Configuration valid. No Discord connection or AI request was made.")
        return
    configure(settings)
    try:
        with InstanceLock(settings.database_path):
            asyncio.run(run(settings))
    except discord.LoginFailure:
        parser.exit(2, "Discord rejected the bot token. Reset it in the Developer Portal.\n")
    except discord.PrivilegedIntentsRequired:
        parser.exit(2, "Enable Message Content Intent for this app in the Developer Portal.\n")
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped by operator")
    except (aiohttp.ClientError, TimeoutError) as error:
        parser.exit(
            1,
            f"Discord connection failed ({type(error).__name__}). "
            "Check network access and retry, or use scripts/run-supervised.ps1.\n",
        )
    except RuntimeError as error:
        parser.exit(2, f"Startup error: {error}\n")


if __name__ == "__main__":
    main()
