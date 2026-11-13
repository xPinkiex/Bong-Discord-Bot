"""Background jobs: delivering reminders."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from .client import BongBot

log = logging.getLogger(__name__)

LATE_AFTER = 120  # seconds; mention lateness beyond this (e.g. Bong was offline)


class BackgroundCog(commands.Cog):
    def __init__(self, bot: "BongBot"):
        self.bot = bot
        self.deliver_reminders.start()

    async def cog_unload(self) -> None:
        self.deliver_reminders.cancel()

    @tasks.loop(seconds=30)
    async def deliver_reminders(self) -> None:
        now = time.time()
        for r in self.bot.reminders.pop_due(now):
            text = f"⏰ **Reminder**: {r['message']}"
            if now - r["due_at"] > LATE_AFTER:
                text += f" (late — this was due {datetime.fromtimestamp(r['due_at']):%Y-%m-%d %H:%M}, sorry!)"
            try:
                user = self.bot.get_user(r["user_id"]) or await self.bot.fetch_user(r["user_id"])
                await user.send(text)
            except discord.HTTPException as e:
                log.warning("Couldn't deliver reminder to %s: %s", r["user_id"], e)

    @deliver_reminders.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()
