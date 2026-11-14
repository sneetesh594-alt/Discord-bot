"""Read-only, owner-scoped leaderboard controls with private forks for other members."""

from __future__ import annotations

import asyncio
import logging
import time

import discord

from aegis.levels import progress
from aegis.markdown import escape_markdown

log = logging.getLogger(__name__)
PERIODS = {
    "all": (0, "All time"),
    "day": (1, "Last 24 hours"),
    "week": (7, "Last 7 days"),
    "month": (31, "Last 31 days"),
}
TITLES = {
    "xp": "XP",
    "text": "Text",
    "voice": "Voice",
    "given": "Reactions given",
    "received": "Reactions received",
}


def score_label(metric, value):
    if metric == "voice":
        return f"{value / 60:,.1f} credited minutes"
    return (
        f"{value:,} "
        + {"xp": "XP", "text": "messages", "given": "reactions", "received": "reactions"}[metric]
    )


def board_embed(guild_name, metric, period, data):
    embed = discord.Embed(title=f"Alpha · {TITLES[metric]} leaderboard", colour=0x976AF5)
    lines = [f"**{escape_markdown(guild_name[:90])}** · {PERIODS[period][1]}", ""]
    for row in data["rows"]:
        label = f"**#{row['position']}** · <@{row['user_id']}>"
        if metric == "xp":
            state = progress(row["total_xp"])
            filled = min(10, int(state.fraction * 10))
            bar = "▰" * filled + "▱" * (10 - filled)
            lines.append(
                f"{label} · **Lvl. {state.level}**\n{bar} {state.fraction:.0%} · "
                f"{score_label(metric, row['score'])}"
            )
        else:
            lines.append(f"{label} · **{score_label(metric, row['score'])}**")
    if not data["rows"]:
        lines.append(
            "No tracked entries yet. Join a useful conversation; "
            "eligible activity will appear here."
        )
    embed.description = "\n".join(lines)
    own = data["placement"]
    embed.add_field(
        name="Your placement",
        value=(
            f"**#{own['position']}** · {score_label(metric, own['score'])}"
            if own
            else "No positive entry for this metric and period yet."
        ),
        inline=False,
    )
    since = data["tracking_since"]
    if metric != "xp":
        embed.add_field(
            name="Activity tracking",
            value=(
                f"Tracked since <t:{int(since)}:D>. Earlier activity is not reconstructed."
                if since
                else "Activity tracking has not started here yet."
            ),
            inline=False,
        )
    embed.set_footer(
        text=f"Page {data['page']} / {data['pages']} · {data['count']} entries · "
        "Ties: member ID · Controls expire after 3 minutes"
    )
    if metric == "xp" and period != "all":
        embed.add_field(
            name="XP period",
            value="Signed admin corrections are included. Levels/progress use current total XP.",
            inline=False,
        )
    return embed


class LeaderboardView(discord.ui.View):
    def __init__(
        self, repository, guild_id, guild_name, owner_id, metric="xp", period="all", page=1
    ):
        super().__init__(timeout=180)
        if metric not in TITLES or period not in PERIODS:
            raise ValueError("Invalid leaderboard view")
        self.repo, self.guild_id, self.guild_name = repository, guild_id, guild_name
        self.owner_id, self.metric, self.period, self.page = owner_id, metric, period, page
        self.message = None
        self.last_click = 0.0
        self.lock = asyncio.Lock()
        self.data = None
        self.period_select = discord.ui.Select(
            placeholder="Ranking period",
            row=0,
            options=[
                discord.SelectOption(label=label, value=key, default=key == period)
                for key, (_, label) in PERIODS.items()
            ],
        )
        self.period_select.callback = self.choose_period
        self.add_item(self.period_select)
        self.reaction_select = None
        if metric in {"given", "received"}:
            self.reaction_select = discord.ui.Select(
                placeholder="Reaction activity",
                row=1,
                options=[
                    discord.SelectOption(label=TITLES[key], value=key, default=key == metric)
                    for key in ("received", "given")
                ],
            )
            self.reaction_select.callback = self.choose_reaction
            self.add_item(self.reaction_select)

    async def render(self):
        self.data = await self.repo.ranking(
            self.guild_id, self.metric, PERIODS[self.period][0], self.page, user_id=self.owner_id
        )
        self.page = self.data["page"]
        self.first.disabled = self.previous.disabled = self.page == 1
        self.next_page.disabled = self.last.disabled = self.page == self.data["pages"]
        for option in self.period_select.options:
            option.default = option.value == self.period
        if self.reaction_select:
            for option in self.reaction_select.options:
                option.default = option.value == self.metric
        return board_embed(self.guild_name, self.metric, self.period, self.data)

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "This board belongs to another server.", ephemeral=True
            )
            return False
        if not self.repo.allow_view(self.guild_id, interaction.user.id, time.monotonic()):
            await interaction.response.send_message(
                "Please wait a second between updates.", ephemeral=True
            )
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.defer(ephemeral=True, thinking=True)
            own = LeaderboardView(
                self.repo,
                self.guild_id,
                self.guild_name,
                interaction.user.id,
                self.metric,
                self.period,
                self.page,
            )
            # The requested action belongs to the private fork. Preserve it rather
            # than making a member select the same option a second time.
            action = getattr(interaction, "data", None) or {}
            component = action.get("custom_id")
            values = action.get("values", [])
            if component == self.period_select.custom_id and values and values[0] in PERIODS:
                own.period, own.page = values[0], 1
            elif (
                self.reaction_select
                and component == self.reaction_select.custom_id
                and values
                and values[0] in {"given", "received"}
            ):
                own.metric, own.page = values[0], 1
            elif component == self.next_page.custom_id:
                own.page += 1
            elif component == self.previous.custom_id:
                own.page = max(1, own.page - 1)
            elif component == self.first.custom_id:
                own.page = 1
            elif component == self.last.custom_id and self.data:
                own.page = self.data["pages"]
            elif component == self.my_position.custom_id:
                data = await self.repo.ranking(
                    self.guild_id, own.metric, PERIODS[own.period][0], user_id=own.owner_id
                )
                placement = data["placement"]
                own.page = (placement["position"] - 1) // 10 + 1 if placement else 1
            own.message = await interaction.followup.send(
                content="Your own private board; its controls affect only this view.",
                embed=await own.render(),
                view=own,
                ephemeral=True,
                wait=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        now = time.monotonic()
        if now - self.last_click < 1:
            await interaction.response.send_message(
                "Please wait a second between updates.", ephemeral=True
            )
            return False
        self.last_click = now
        return True

    async def update(self, interaction, *, page=None, period=None, metric=None):
        await interaction.response.defer()
        async with self.lock:
            if page is not None:
                self.page = max(1, page)
            if period is not None:
                self.period, self.page = period, 1
            if metric is not None:
                self.metric, self.page = metric, 1
            await interaction.edit_original_response(
                embed=await self.render(),
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def choose_period(self, interaction):
        await self.update(interaction, period=self.period_select.values[0])

    async def choose_reaction(self, interaction):
        await self.update(interaction, metric=self.reaction_select.values[0])

    @discord.ui.button(label="First", row=2)
    async def first(self, interaction, button):
        await self.update(interaction, page=1)

    @discord.ui.button(label="Previous", row=2)
    async def previous(self, interaction, button):
        await self.update(interaction, page=self.page - 1)

    @discord.ui.button(label="Next", row=2)
    async def next_page(self, interaction, button):
        await self.update(interaction, page=self.page + 1)

    @discord.ui.button(label="Last", row=2)
    async def last(self, interaction, button):
        await self.update(interaction, page=self.data["pages"] if self.data else 1)

    @discord.ui.button(label="My position", style=discord.ButtonStyle.primary, row=3)
    async def my_position(self, interaction, button):
        await interaction.response.defer()
        async with self.lock:
            current = await self.repo.ranking(
                self.guild_id, self.metric, PERIODS[self.period][0], user_id=self.owner_id
            )
            placement = current["placement"]
            self.page = (placement["position"] - 1) // 10 + 1 if placement else 1
            await interaction.edit_original_response(
                embed=await self.render(),
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @discord.ui.button(label="Refresh", row=3)
    async def refresh(self, interaction, button):
        await self.update(interaction)

    @discord.ui.button(label="How XP works", row=3)
    async def explain(self, interaction, button):
        await interaction.response.send_message(
            "XP reflects eligible activity with cooldowns and caps. Activity boards count "
            "messages, credited voice time and unique non-self reactions separately. "
            "Reactions given and received "
            "are distinct. Rolling periods mean the last 24 hours / 7 days / 31 days. "
            "Historical activity is not invented; stored boards may include former members. "
            "Use /statistics, /xp_needed or /xp-optout for your data.",
            ephemeral=True,
        )

    async def on_timeout(self):
        async with self.lock:
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

    async def on_error(self, interaction, error, item):
        log.error("Leaderboard control failed (%s)", type(error).__name__)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Board update failed. Run /leaderboard xp again.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Board update failed. Run the command again.", ephemeral=True
            )
