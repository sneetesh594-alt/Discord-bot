"""Permission and role hierarchy checks, shared by commands and automation."""

from __future__ import annotations

import discord
from discord import app_commands

from aegis.markdown import escape_markdown

PERMISSIONS = {
    "warn": "manage_messages",
    "timeout": "moderate_members",
    "untimeout": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
    "unban": "ban_members",
}


class UserError(app_commands.AppCommandError):
    """A safe, actionable error message that can be shown to a user."""


def target_error(
    *,
    actor_id: int,
    target_id: int,
    owner_id: int,
    bot_id: int,
    actor_role: object,
    target_role: object,
    bot_role: object,
    target_admin: bool,
    action: str,
) -> str | None:
    if target_id in {owner_id, bot_id, actor_id}:
        return "You cannot moderate the server owner, yourself, or this bot."
    if actor_id != owner_id and actor_role <= target_role:
        return "Your highest role must be above the member's highest role."
    if bot_role <= target_role:
        return "Move the bot's role above the member's highest role."
    if action in {"timeout", "untimeout"} and target_admin:
        return "Discord does not allow timeouts for administrators."
    return None


async def fresh_target(
    interaction: discord.Interaction, member_id: int, action: str
) -> tuple[discord.Member, discord.Member]:
    guild = interaction.guild
    if guild is None or interaction.client.user is None:
        raise UserError("Run this command in a server.")
    # Re-fetch at execution time, including after a destructive-action confirmation.
    actor = await guild.fetch_member(interaction.user.id)
    me = await guild.fetch_member(interaction.client.user.id)
    target = await guild.fetch_member(member_id)
    permission = PERMISSIONS[action]
    if not getattr(actor.guild_permissions, permission):
        raise UserError(f"You need the {permission.replace('_', ' ')} permission.")
    if action != "warn" and not getattr(me.guild_permissions, permission):
        raise UserError(f"The bot needs the {permission.replace('_', ' ')} permission.")
    error = target_error(
        actor_id=actor.id,
        target_id=target.id,
        owner_id=guild.owner_id,
        bot_id=me.id,
        actor_role=actor.top_role,
        target_role=target.top_role,
        bot_role=me.top_role,
        target_admin=target.guild_permissions.administrator,
        action=action,
    )
    if error:
        raise UserError(error)
    return actor, target


def exempt(member: discord.Member, policy, channel_id: int, parent_id: int | None = None) -> bool:
    me = member.guild.me
    return (
        member.bot
        or member.id == member.guild.owner_id
        or member.guild_permissions.administrator
        or member.guild_permissions.manage_messages
        or member.guild_permissions.moderate_members
        or channel_id in policy.exempt_channel_ids
        or parent_id in policy.exempt_channel_ids
        or any(role.id in policy.exempt_role_ids for role in member.roles)
        or me is None
        or member.top_role >= me.top_role
    )


def private_log_channel(channel: discord.TextChannel) -> bool:
    return not channel.permissions_for(channel.guild.default_role).view_channel


def clean(text: str, limit: int = 1000) -> str:
    return escape_markdown(discord.utils.escape_mentions(text))[:limit]
