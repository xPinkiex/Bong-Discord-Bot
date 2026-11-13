"""bot_stats and shutdown."""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from ..conversation.turn import Turn
from . import Tool


def stats_tool(collect: Callable[[], Awaitable[dict]]) -> Tool:
    """collect() returns the numbers; see BongBot.collect_stats."""

    async def handler(turn: Turn, args: dict) -> str:
        s = await collect()
        up = int(time.time() - s["started_at"])
        days, rest = divmod(up, 86400)
        hours, rest = divmod(rest, 3600)
        lines = [f"Uptime: {days}d {hours}h {rest // 60}m",
                 f"Memories: {s['memories'] if s['memories'] is not None else 'memory is off'}",
                 f"Known users: {s['known_users']}",
                 f"Pending reminders: {s['reminders']}",
                 f"Songs in library: {s['songs']}",
                 f"Total song plays: {s['total_plays']}"]
        if s["top_songs"]:
            lines.append("Top songs:\n" + "\n".join(f"  {i}. {n} ({c} plays)" for i, (n, c) in enumerate(s["top_songs"], 1)))
        lines.append(f"Total tokens used: {s['total_tokens']:,}")
        if s["top_users"]:
            lines.append("Top users:\n" + "\n".join(f"  {i}. {n} ({c:,} tokens)" for i, (n, c) in enumerate(s["top_users"], 1)))
        return "\n".join(lines)

    return Tool("bot_stats", "Bot statistics: uptime, memories, users, reminders, top songs, and top users by tokens.",
                handler)


def shutdown_tool(is_admin: Callable[[int], bool]) -> Tool:
    async def handler(turn: Turn, args: dict) -> str:
        if not is_admin(turn.author_id):
            return f"Can't shut down: {turn.author_name} isn't an admin."
        turn.outbox.shutdown = True
        return "Shutting down after this reply."

    return Tool("shutdown", "Shut the bot down. Only admins can do this.", handler)
