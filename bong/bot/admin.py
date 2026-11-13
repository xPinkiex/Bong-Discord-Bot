"""Admin-only commands: @llm, @tags, @poweroff, @debug."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..users import TAGS

if TYPE_CHECKING:
    from .client import BongBot

log = logging.getLogger(__name__)


class AdminCog(commands.Cog, name="Admin"):
    def __init__(self, bot: "BongBot"):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return self.bot.user_store.is_admin(ctx.author.id)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("Only admins can use this command.")
        elif isinstance(error, (commands.UserInputError,)):
            await ctx.send(f"{error}\nUsage: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        else:
            log.exception("Command %s failed", ctx.command, exc_info=error)
            await ctx.send("That command failed. Check the logs.")

    @commands.command(name="llm", help="Toggle Bong in this channel")
    async def llm(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("Bong is always on in DMs for approved users.")
            return
        if self.bot.active_channels.toggle(ctx.channel.id):
            chat = self.bot.get_cog("ChatCog")
            if chat is not None:
                await chat.load_history(ctx.channel, before=ctx.message)
            await ctx.send(f"Bong is now active in this channel! (ID: {ctx.channel.id})")
        else:
            await ctx.send(f"No more Bonging in this channel! (ID: {ctx.channel.id})")

    @commands.group(name="tags", help="Manage permission tags", invoke_without_command=True)
    async def tags(self, ctx: commands.Context):
        p = ctx.prefix
        await ctx.send(f"Usage: `{p}tags list <user>` | `{p}tags add <user> <tag>` | `{p}tags remove <user> <tag>`\n"
                       f"Tags: {', '.join(TAGS)}")

    @tags.command(name="list")
    async def tags_list(self, ctx: commands.Context, user: discord.User):
        tags = self.bot.user_store.tags(user.id)
        await ctx.send(f"{user.display_name} (`{user.id}`): {', '.join(tags) if tags else 'no tags'}")

    @tags.command(name="add")
    async def tags_add(self, ctx: commands.Context, user: discord.User, tag: str):
        tag = tag.lower()
        if tag not in TAGS:
            await ctx.send(f"Unknown tag `{tag}`. Valid tags: {', '.join(TAGS)}")
            return
        added = self.bot.user_store.add_tag(user.id, tag)
        current = ", ".join(self.bot.user_store.tags(user.id))
        await ctx.send(f"{'Added' if added else 'Already had'} `{tag}` for {user.display_name}. Tags: {current}")

    @tags.command(name="remove")
    async def tags_remove(self, ctx: commands.Context, user: discord.User, tag: str):
        tag = tag.lower()
        if self.bot.user_store.remove_tag(user.id, tag):
            current = ", ".join(self.bot.user_store.tags(user.id)) or "no tags"
            await ctx.send(f"Removed `{tag}` from {user.display_name}. Tags: {current}")
        else:
            await ctx.send(f"{user.display_name} doesn't have `{tag}` (or it can't be removed).")

    @commands.command(name="poweroff", help="Shut the bot down")
    async def poweroff(self, ctx: commands.Context):
        await ctx.send("Onoffing...")
        await self.bot.close()

    @commands.command(name="debug", help="Toggle debug logging")
    async def debug(self, ctx: commands.Context, enabled: bool | None = None):
        logger = logging.getLogger("bong")
        on = (logger.level != logging.DEBUG) if enabled is None else enabled
        logger.setLevel(logging.DEBUG if on else logging.INFO)
        await ctx.send(f"Debug logging {'enabled' if on else 'disabled'}.")
