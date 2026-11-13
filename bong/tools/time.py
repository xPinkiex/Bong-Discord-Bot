"""Time tools: current_time, set_timezone, get_timezone.

Timezones are kept per user in users.json ("timezone": a zone name like
"Europe/Warsaw", or a fixed UTC offset like the old bot stored).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from ..conversation.turn import Turn
from ..timeparse import describe_timezone, offset_hours, parse_timezone
from . import Tool

FORMAT = "%H:%M on %A, %d %B %Y"

TimezoneLookup = Callable[[int], "str | float | None"]


def local_time(spec: "str | float", now: datetime) -> datetime:
    return (now + timedelta(hours=offset_hours(spec, now))).replace(tzinfo=None)


def current_time_tool(timezone_of: TimezoneLookup, now: Callable[[], datetime] | None = None) -> Tool:
    def _now() -> datetime:
        return (now() if now else datetime.now(timezone.utc)).astimezone(timezone.utc)

    async def handler(turn: Turn, args: dict) -> str:
        utc = _now()
        spec = timezone_of(turn.author_id)
        if spec is not None:
            return (f"Current time for {turn.author_name} ({describe_timezone(spec, utc)}): "
                    f"{local_time(spec, utc).strftime(FORMAT)}")
        here = utc.astimezone()
        return (f"Current time where Bong is: {here.strftime(FORMAT)} "
                f"({describe_timezone(here.utcoffset().total_seconds() / 3600)}). "
                f"{turn.author_name} hasn't set a timezone, so their local time may differ — "
                f"use set_timezone if they mention it.")

    return Tool(
        name="current_time",
        description="Get the current time and date for the person you're talking to (in their timezone if set). "
                    "Use this whenever someone asks what time or day it is.",
        handler=handler,
    )


def timezone_tools(timezone_of: TimezoneLookup, set_timezone: Callable[[int, "str | float"], None]) -> list[Tool]:
    async def set_tz(turn: Turn, args: dict) -> str:
        text = str(args.get("timezone", "")).strip()
        spec = parse_timezone(text)
        if spec is None:
            return (f"Couldn't understand the timezone '{text}'. Try a city (\"Warsaw\", \"New York\"), "
                    f"a zone (\"Europe/Warsaw\"), or an offset (\"UTC+2\", \"EST\").")
        set_timezone(turn.author_id, spec)
        return f"Timezone for {turn.author_name} set to {describe_timezone(spec)}."

    async def get_tz(turn: Turn, args: dict) -> str:
        spec = timezone_of(turn.author_id)
        if spec is None:
            return f"{turn.author_name} hasn't set a timezone. Ask them and use set_timezone."
        return f"{turn.author_name}'s timezone: {describe_timezone(spec)}"

    return [
        Tool("set_timezone",
             "Save the timezone of the person you're talking to, when they mention it or you need it "
             "(e.g. for reminders). Cities and zone names follow daylight saving time.",
             set_tz,
             {"type": "object", "required": ["timezone"], "properties": {
                 "timezone": {"type": "string",
                              "description": "A city (\"Warsaw\"), zone (\"Europe/Warsaw\"), abbreviation (\"CET\") "
                                             "or offset (\"UTC+2\", \"-5\", \"+5:30\")."}}}),
        Tool("get_timezone", "Look up the timezone of the person you're talking to.", get_tz),
    ]
