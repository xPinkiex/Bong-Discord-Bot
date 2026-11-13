"""Turn discord.py objects into the plain data the conversation core works with."""

from __future__ import annotations

import discord

from ..conversation.history import HistoryEntry
from ..conversation.turn import ReplyInfo, Turn


# Automated messages the bot sends (including the old bot's features). They aren't
# conversation, so they're left out when history is loaded from Discord.
NOTIFICATION_PREFIXES = (
    "New e621 post matching",
    "\u23f0 **Reminder**",
    "\U0001f512 **DM Access Request**",
    "Approved **",
    "Denied **",
    "Slow down! Try again in",
    "Eve has approved you",
    "Eve has denied your request",
    "I've sent your request to Eve",
    "Your approval request has timed out",
    "Your request is still pending",
    "You've been approved to talk with me",
    "Your request to talk with me was denied",
    "Your access request timed out",
    "I've asked ",
)


def is_notification(message: discord.Message, me: discord.abc.User) -> bool:
    return message.author.id == me.id and message.content.startswith(NOTIFICATION_PREFIXES)


def _local(dt):
    return dt.astimezone().replace(tzinfo=None)


def to_turn(message: discord.Message, me: discord.abc.User,
            replied: discord.Message | None) -> Turn:
    reply_info = None
    if replied is not None:
        reply_info = ReplyInfo(
            author_name=replied.author.display_name,
            author_id=replied.author.id,
            content=replied.clean_content,
            is_bong=replied.author.id == me.id,
        )
    mentions_bong = any(u.id == me.id for u in message.mentions) or bool(reply_info and reply_info.is_bong)
    return Turn(
        author_id=message.author.id,
        author_name=message.author.display_name,
        channel_id=message.channel.id,
        content=message.clean_content,
        guild_id=message.guild.id if message.guild else None,
        attachments=tuple(a.filename for a in message.attachments),
        reply_to=reply_info,
        mentions_bong=mentions_bong,
        sent_at=_local(message.created_at),
    )


def to_history_entry(message: discord.Message, me: discord.abc.User) -> HistoryEntry:
    text = " ".join(message.clean_content.split())
    if message.attachments:
        text = f"{text} [attached: {', '.join(a.filename for a in message.attachments)}]".strip()
    return HistoryEntry(
        author_name=message.author.display_name,
        text=text,
        at=_local(message.created_at),
        is_bong=message.author.id == me.id,
    )
