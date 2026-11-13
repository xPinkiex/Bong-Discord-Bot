"""current_time — the time for whoever is asking.

Uses the person's saved UTC offset (the "timezone" field the old bot stored in
users.json) when there is one, otherwise the time where Bong is running.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from ..conversation.turn import Turn
from . import Tool

TimezoneLookup = Callable[[int], "float | None"]

FORMAT = "%H:%M on %A, %d %B %Y"


def _format_offset(offset: float) -> str:
    sign = "+" if offset >= 0 else "-"
    hours, minutes = int(abs(offset)), round((abs(offset) % 1) * 60)
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def current_time_tool(timezone_of: TimezoneLookup, now: Callable[[], datetime] | None = None) -> Tool:
    def _now() -> datetime:
        return now() if now else datetime.now(timezone.utc)

    async def handler(turn: Turn, args: dict) -> str:
        utc = _now().astimezone(timezone.utc)
        offset = timezone_of(turn.author_id)
        if offset is not None:
            local = utc + timedelta(hours=offset)
            return f"Current time for {turn.author_name} ({_format_offset(offset)}): {local.strftime(FORMAT)}"
        here = utc.astimezone()
        return (f"Current time where Bong is: {here.strftime(FORMAT)} "
                f"({_format_offset(here.utcoffset().total_seconds() / 3600)}). "
                f"{turn.author_name} hasn't saved a timezone, so their local time may differ.")

    return Tool(
        name="current_time",
        description="Get the current time and date for the person you're talking to. "
                    "Use this whenever someone asks what time or day it is.",
        handler=handler,
    )
