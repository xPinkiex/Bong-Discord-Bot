"""Reminder tools: set_reminder, list_reminders_tool, cancel_reminder (the old bot's names)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..conversation.turn import Turn
from ..reminders import ReminderStore
from ..timeparse import PastDateError, describe_timezone, format_duration, offset_hours, parse_duration, parse_time
from . import Tool

MAX_AHEAD = 5 * 365 * 86400   # five years
MAX_PER_USER = 50


def _when(due_at: float, spec) -> str:
    now = time.time()
    if spec is None:
        return f"in {format_duration(due_at - now)}"
    utc = datetime.fromtimestamp(due_at, timezone.utc)
    local = (utc + timedelta(hours=offset_hours(spec, utc))).replace(tzinfo=None)
    return f"at {local:%H:%M on %A, %d %B %Y} (in {format_duration(due_at - now)})"


def reminder_tools(store: ReminderStore, timezone_of: Callable[[int], "str | float | None"]) -> list[Tool]:

    async def set_reminder(turn: Turn, args: dict) -> str:
        message = str(args.get("message", "")).strip()
        when, delta = str(args.get("time", "") or "").strip(), str(args.get("time_delta", "") or "").strip()
        if not message:
            return "What should the reminder say?"
        if len(store.for_user(turn.author_id)) >= MAX_PER_USER:
            return f"{turn.author_name} already has {MAX_PER_USER} reminders. Cancel some first."
        spec = timezone_of(turn.author_id)
        if when:
            if spec is None:
                return ("They haven't set their timezone yet. Ask for it and use set_timezone before setting "
                        "reminders for a specific time. Relative ones ('in 2 hours') work without it via time_delta.")
            try:
                due = parse_time(when, spec)
            except PastDateError as e:
                return f"{e} Please choose a future time."
            if due is None:
                return (f"Couldn't understand the time '{when}'. Try 'tomorrow at 15:00', 'friday at noon', "
                        f"'31.5 at 10:22' or 'next monday at 9am'.")
        elif delta:
            seconds = parse_duration(delta)
            if seconds is None:
                return f"Couldn't understand '{delta}'. Use things like '30 minutes', '2 hours', '1 day'."
            due = time.time() + seconds
        else:
            return "Give either 'time' (e.g. 'tomorrow at 15:00') or 'time_delta' (e.g. '2 hours')."
        if due - time.time() > MAX_AHEAD:
            return "That's more than five years away. Pick something sooner."
        store.add(turn.author_id, turn.author_name, message, due)
        tz = f" ({describe_timezone(spec)})" if spec is not None else ""
        return f"Reminder set for {turn.author_name}: '{message}' {_when(due, spec)}{tz}. It'll come as a DM."

    async def list_reminders(turn: Turn, args: dict) -> str:
        mine = store.for_user(turn.author_id)
        if not mine:
            return f"{turn.author_name} has no pending reminders."
        spec = timezone_of(turn.author_id)
        return "\n".join(f"{i}. {r['message']} — {_when(r['due_at'], spec)}" for i, r in enumerate(mine, 1))

    async def cancel_reminder(turn: Turn, args: dict) -> str:
        query = str(args.get("query", "") or "")
        removed = store.cancel(turn.author_id, query)
        if removed is None:
            return f"No reminders matching '{query}'." if query.strip() else "No reminders to cancel."
        return f"Cancelled: '{removed['message']}'."

    return [
        Tool("set_reminder",
             "Set a reminder for the person you're talking to; you'll DM them when it's due. ALWAYS use 'time' "
             "when they name a specific date or time (\"tomorrow at 3pm\", \"Friday at noon\", \"31.5 at 10:22\") "
             "and pass their words as-is — don't calculate durations yourself; timezones are handled for you. "
             "ONLY use 'time_delta' for plain durations like \"in 30 minutes\". 'time' needs their timezone set.",
             set_reminder,
             {"type": "object", "required": ["message"], "properties": {
                 "message": {"type": "string", "description": "What to remind them about, e.g. \"feed the cat\"."},
                 "time": {"type": "string", "description": "A date/time in their own words. Dates with dots are "
                          "day.month (31.5 = May 31st); with slashes month/day."},
                 "time_delta": {"type": "string", "description": "A duration from now, e.g. \"30 minutes\", \"2 hours\"."},
             }}),
        Tool("list_reminders_tool", "List the pending reminders of the person you're talking to.", list_reminders),
        Tool("cancel_reminder",
             "Cancel one of their reminders: the one matching the query, or the most recent one if no query.",
             cancel_reminder,
             {"type": "object", "properties": {
                 "query": {"type": "string", "description": "Part of the reminder's text, e.g. \"cat\"."}}}),
    ]
