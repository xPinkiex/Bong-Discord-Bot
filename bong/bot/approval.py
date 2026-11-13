"""DM access requests: when someone without the llm tag DMs Bong, the owner gets buttons.

Pending requests are kept in memory only. If the bot restarts, the buttons stop
working anyway, so the person can simply DM again to send a fresh request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .client import BongBot

log = logging.getLogger(__name__)

# (button label, tags granted, style, emoji)
PRESETS = [
    ("Chat", ["llm"], discord.ButtonStyle.secondary, "\U0001f4ac"),
    ("Chat (no cooldown)", ["llm_fast"], discord.ButtonStyle.primary, "⚡"),
    ("Admin", ["admin"], discord.ButtonStyle.success, "\U0001f451"),
]

REQUEST_TIMEOUT = 300


class ApprovalView(discord.ui.View):
    def __init__(self, manager: "ApprovalManager", requester: discord.abc.User):
        super().__init__(timeout=REQUEST_TIMEOUT)
        self.manager = manager
        self.requester = requester
        for label, tags, style, emoji in PRESETS:
            button = discord.ui.Button(label=label, style=style, emoji=emoji)
            button.callback = self._make_approve(tags)
            self.add_item(button)
        deny = discord.ui.Button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
        deny.callback = self._deny
        self.add_item(deny)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.manager.bot.settings.owner_id:
            await interaction.response.send_message("Only the owner can answer this.", ephemeral=True)
            return False
        return True

    def _make_approve(self, tags: list[str]):
        async def callback(interaction: discord.Interaction):
            who = self.requester
            self.manager.bot.user_store.grant(who.id, tags, display_name=who.display_name)
            await self._finish(interaction, f"Approved **{who.display_name}** (`{who.id}`) with: {', '.join(tags)}.")
            await self.manager.notify(who, f"You've been approved to talk with me! :3 Access: {', '.join(tags)} \U0001f389")
        return callback

    async def _deny(self, interaction: discord.Interaction):
        who = self.requester
        await self._finish(interaction, f"Denied **{who.display_name}** (`{who.id}`).")
        await self.manager.notify(who, "Your request to talk with me was denied. Sorry!")

    async def _finish(self, interaction: discord.Interaction, text: str):
        self.manager.pending.discard(self.requester.id)
        self.stop()
        await interaction.response.edit_message(content=text, view=None)

    async def on_timeout(self):
        self.manager.pending.discard(self.requester.id)
        await self.manager.notify(self.requester, "Your access request timed out. DM me again to send a new one.")


class ApprovalManager:
    def __init__(self, bot: "BongBot"):
        self.bot = bot
        self.pending: set[int] = set()

    async def notify(self, user: discord.abc.User, text: str) -> None:
        try:
            await user.send(text)
        except discord.HTTPException:
            pass

    async def request(self, message: discord.Message) -> None:
        user = message.author
        if user.id in self.pending:
            await self.notify(user, "Your request is still pending — hang tight!")
            return

        try:
            owner = self.bot.get_user(self.bot.settings.owner_id) or await self.bot.fetch_user(self.bot.settings.owner_id)
        except discord.HTTPException as e:
            log.error("Could not fetch owner for approval request: %s", e)
            return

        content = message.clean_content
        preview = content[:100] + "..." if len(content) > 100 else (content or ("(attachment)" if message.attachments else "(empty)"))
        try:
            await owner.send(
                f"\U0001f512 **DM Access Request**\n"
                f"**{user.display_name}** (`{user.id}`) wants to talk to Bong.\n"
                f"First message: \"{preview}\"",
                view=ApprovalView(self, user),
            )
        except discord.HTTPException as e:
            log.error("Could not DM owner an approval request: %s", e)
            return

        self.pending.add(user.id)
        await self.notify(user, f"I've asked {owner.display_name} if we can chat. I'll let you know! \U0001f4ec")
