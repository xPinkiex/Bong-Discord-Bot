"""The message pipeline: decide whether Bong answers, then answer."""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..conversation.bong import Cooldown
from ..textutil import split_message
from .approval import ApprovalManager
from .convert import to_history_entry, to_turn

if TYPE_CHECKING:
    from .client import BongBot

log = logging.getLogger(__name__)

ERROR_REPLY = "Something went wrong processing that message. Try again?"


class ChatCog(commands.Cog):
    def __init__(self, bot: "BongBot"):
        self.bot = bot
        self.approvals = ApprovalManager(bot)
        self.cooldown = Cooldown(bot.settings.cooldown_seconds)
        self._seen: deque[int] = deque(maxlen=1000)

    # --- helpers -----------------------------------------------------------

    def _already_seen(self, message_id: int) -> bool:
        # Discord can redeliver events after a reconnect.
        if message_id in self._seen:
            return True
        self._seen.append(message_id)
        return False

    async def load_history(self, channel: discord.abc.Messageable, before: discord.Message | None = None) -> None:
        """Seed Bong's history for a channel from Discord's recent messages."""
        me = self.bot.user
        entries = []
        try:
            async for msg in channel.history(limit=self.bot.settings.history_size, before=before):
                if msg.content or msg.attachments:
                    entries.append(to_history_entry(msg, me))
        except discord.HTTPException as e:
            log.warning("Could not load history for channel %s: %s", getattr(channel, "id", "?"), e)
        entries.reverse()  # Discord returns newest first
        self.bot.bong.history.seed(channel.id, entries)

    async def _replied_message(self, message: discord.Message) -> discord.Message | None:
        ref = message.reference
        if ref is None or ref.message_id is None:
            return None
        if isinstance(ref.resolved, discord.Message):
            return ref.resolved
        try:
            return await message.channel.fetch_message(ref.message_id)
        except discord.HTTPException:
            return None  # deleted or not visible

    # --- pipeline ----------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or self._already_seen(message.id):
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:  # a real command; the command framework handles it
            return

        users = self.bot.user_store
        is_dm = message.guild is None
        if not is_dm and message.channel.id not in self.bot.active_channels:
            return

        allowed = users.has(message.author.id, "llm")
        if is_dm and not allowed:
            await self.approvals.request(message)
            return

        bong = self.bot.bong
        if not bong.history.has(message.channel.id):
            await self.load_history(message.channel, before=message)

        turn = to_turn(message, self.bot.user, await self._replied_message(message))

        # People without access still count as conversation context in active channels.
        if not allowed or not await bong.is_for_bong(turn):
            bong.record(turn)
            return

        if not users.has(message.author.id, "llm_fast"):
            wait = self.cooldown.hit(message.author.id)
            if wait:
                bong.record(turn)
                await message.channel.send(f"Slow down! Try again in {int(wait) + 1}s.", delete_after=5)
                return

        async with message.channel.typing():
            try:
                reply = await bong.respond(turn)
            except Exception:
                log.exception("Failed to reply to %s in %s", turn.author_name, turn.channel_id)
                await message.channel.send(ERROR_REPLY)
                return

        for chunk in split_message(reply.text):
            await message.channel.send(chunk)
        users.add_tokens(message.author.id, reply.tokens, message.author.display_name)
