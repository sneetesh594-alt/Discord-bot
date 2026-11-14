"""Moderator commands, case inspection, and user appeals."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from aegis.security import UserError, clean, fresh_target


class Confirmation(discord.ui.View):
    def __init__(self, actor_id: int):
        super().__init__(timeout=45)
        self.actor_id = actor_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message(
                "Only the requesting moderator can confirm.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm action", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.stop()


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def execute(self, interaction, member, action, reason, seconds=0, confirm=False):
        if not reason.strip():
            raise UserError("Provide a moderation reason.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await fresh_target(interaction, member.id, action)
        if confirm:
            view = Confirmation(interaction.user.id)
            await interaction.edit_original_response(
                content=f"{action.title()} member **{clean(str(member), 100)}** "
                f"(ID {member.id})?\nReason: {clean(reason, 700)}",
                view=view,
            )
            await view.wait()
            if not view.confirmed:
                await interaction.edit_original_response(
                    content="Action cancelled or expired.", view=None
                )
                return
        case_id = await self.bot.service.manual(
            interaction, member.id, action, reason.strip(), seconds=seconds
        )
        case = await self.bot.store.case(interaction.guild_id, case_id)
        await interaction.edit_original_response(
            content=f"Case #{case_id}: {action} — {case['status']}. "
            "Use `/case` for delivery and escalation details.",
            view=None,
        )

    @app_commands.command(description="Record a warning; repeated warnings may trigger a timeout")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.execute(interaction, member, "warn", reason)

    @app_commands.command(description="Timeout a member for 1 minute to 28 days")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.execute(interaction, member, "timeout", reason, minutes * 60)

    @app_commands.command(description="Mute a member using Discord's built-in timeout")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.execute(interaction, member, "timeout", reason, minutes * 60)

    @app_commands.command(description="Remove a member's timeout")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.execute(interaction, member, "untimeout", reason)

    @app_commands.command(description="Kick a member after confirmation")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.execute(interaction, member, "kick", reason, confirm=True)

    @app_commands.command(description="Ban a member after confirmation; preserves message history")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 500],
    ):
        await self.execute(interaction, member, "ban", reason, confirm=True)

    @app_commands.command(description="Unban a user by numeric Discord user ID")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: app_commands.Range[str, 1, 20],
        reason: app_commands.Range[str, 1, 500],
    ):
        if not user_id.isdigit() or not 0 < int(user_id) < 2**63:
            raise UserError("Provide a valid numeric Discord user ID.")
        if not reason.strip():
            raise UserError("Provide a moderation reason.")
        await interaction.response.defer(ephemeral=True)
        actor = await interaction.guild.fetch_member(interaction.user.id)
        me = await interaction.guild.fetch_member(self.bot.user.id)
        if not actor.guild_permissions.ban_members or not me.guild_permissions.ban_members:
            raise UserError("Both you and the bot need Ban Members.")
        case_id = await self.bot.store.create_case(
            guild_id=interaction.guild_id,
            user_id=int(user_id),
            actor_id=actor.id,
            source="manual",
            category="moderator_action",
            reason=reason,
            action="unban",
        )
        try:
            await interaction.guild.unban(
                discord.Object(id=int(user_id)), reason=f"Aegis case #{case_id}: {reason}"[:500]
            )
        except discord.HTTPException as error:
            await self.bot.store.finish_case(case_id, "failed", {"error": type(error).__name__})
            await self.bot.service.audit(interaction.guild_id, case_id)
            raise
        await self.bot.store.finish_case(case_id, "completed", {})
        await self.bot.service.audit(interaction.guild_id, case_id)
        await interaction.followup.send(
            f"Unbanned user {user_id}. Case #{case_id}.", ephemeral=True
        )

    @app_commands.command(description="Show a member's active warnings within the warning window")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.store.policy(interaction.guild_id)
        rows = await self.bot.store.warnings(interaction.guild_id, member.id, policy.warning_days)
        text = "\n".join(f"#{r['id']}: {clean(r['reason'], 120)}" for r in rows[:15])
        await interaction.followup.send(
            f"{len(rows)} active warnings in {policy.warning_days} days.\n{text or 'No warnings.'}",
            ephemeral=True,
        )

    @app_commands.command(description="Inspect a case's actions, AI scores, and outcome")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def case(self, interaction: discord.Interaction, case_id: app_commands.Range[int, 1]):
        import json

        await interaction.response.defer(ephemeral=True)
        row = await self.bot.store.case(interaction.guild_id, case_id)
        if row is None:
            raise UserError("Case not found in this server (it may have expired).")
        details = clean(json.dumps(row["details"], ensure_ascii=False), 1300)
        embed = discord.Embed(title=f"Case #{case_id}", colour=discord.Colour.blurple())
        embed.description = (
            f"Member ID: {row['user_id']}\nAction: {row['action']}\nStatus: {row['status']}\n"
            f"Category: {row['category']}\nVoided: {bool(row['voided'])}\n"
            f"Reason: {clean(row['reason'], 500)}\nDetails: {details}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description="List the 15 most recent cases needing moderator attention")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def review(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.store.review_cases(interaction.guild_id)
        text = "\n".join(
            f"#{r['id']} · {r['category']} · {r['status']} · {r['user_id']}" for r in rows
        )
        await interaction.followup.send(text or "No cases need review.", ephemeral=True)

    @app_commands.command(
        name="case-void", description="Void a case's warning; actions remain in history"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def case_void(
        self, interaction: discord.Interaction, case_id: app_commands.Range[int, 1]
    ):
        await interaction.response.defer(ephemeral=True)
        case = await self.bot.store.case(interaction.guild_id, case_id)
        if case is None:
            raise UserError("Case not found in this server.")
        async with self.bot.service.lock_for(interaction.guild_id, case["user_id"]):
            changed = await self.bot.store.void_case(
                interaction.guild_id, case_id, reviewer_id=interaction.user.id
            )
        if not changed:
            raise UserError("Case not found or already voided.")
        await self.bot.service.notify(
            interaction.guild_id, f"Case #{case_id} voided", f"Reviewer ID: {interaction.user.id}"
        )
        await interaction.followup.send(
            "Case voided. Use `/untimeout` or `/unban` separately if an action needs reversal.",
            ephemeral=True,
        )

    @app_commands.command(description="Appeal one of your own moderation cases")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 60)
    async def appeal(
        self,
        interaction: discord.Interaction,
        case_id: app_commands.Range[int, 1],
        reason: app_commands.Range[str, 1, 1000],
    ):
        if not reason.strip():
            raise UserError("Explain why you are appealing.")
        await interaction.response.defer(ephemeral=True)
        try:
            appeal_id = await self.bot.store.appeal(
                interaction.guild_id, case_id, interaction.user.id, reason.strip()
            )
        except ValueError as error:
            raise UserError(str(error)) from None
        await self.bot.service.notify(
            interaction.guild_id,
            f"Appeal #{appeal_id} submitted",
            f"Case #{case_id}; member ID {interaction.user.id}",
        )
        await interaction.followup.send(
            f"Appeal #{appeal_id} saved for moderator review.", ephemeral=True
        )

    @app_commands.command(description="Read the oldest 15 pending appeals")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def appeals(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.store.appeals(interaction.guild_id)
        embed = discord.Embed(title="Pending appeals")
        for row in rows:
            embed.add_field(
                name=f"Appeal #{row['id']} · case #{row['case_id']}",
                value=f"User {row['user_id']}: {clean(row['text'], 280)}",
                inline=False,
            )
        if not rows:
            embed.description = "No pending appeals."
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="appeal-resolve", description="Accept or reject an appeal with a reason"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def appeal_resolve(
        self,
        interaction: discord.Interaction,
        appeal_id: app_commands.Range[int, 1],
        accept: bool,
        reason: app_commands.Range[str, 1, 500],
    ):
        if not reason.strip():
            raise UserError("Provide a resolution reason.")
        await interaction.response.defer(ephemeral=True)
        appeal = await self.bot.store.appeal_record(interaction.guild_id, appeal_id)
        if appeal is None:
            raise UserError("Pending appeal not found in this server.")
        async with self.bot.service.lock_for(interaction.guild_id, appeal["user_id"]):
            changed = await self.bot.store.resolve_appeal(
                interaction.guild_id, appeal_id, interaction.user.id, accept, reason
            )
        if not changed:
            raise UserError("Pending appeal not found in this server.")
        await self.bot.service.notify(
            interaction.guild_id,
            f"Appeal #{appeal_id} resolved",
            f"Accepted: {accept}; reviewer ID: {interaction.user.id}",
        )
        await interaction.followup.send(
            "Appeal resolved. Acceptance voids the warning. "
            "Use `/untimeout` or `/unban` separately to reverse an active restriction.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Moderation(bot))
