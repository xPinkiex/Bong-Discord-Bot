"""Timezones and natural-language times, for current_time and reminders.

Timezones: a city or IANA zone ("Warsaw", "Europe/Warsaw") follows daylight
saving time; abbreviations and offsets ("CET", "UTC+2", "-5", "+5:30") are
fixed offsets, like the old bot stored them.

Times (ported from the old bot, with two fixes: a bare time like "at 3pm" no
longer crashes, and results are real UTC instead of the server's local time):
    "tomorrow at 3pm", "friday at 12:00", "next monday at 9am", "today 17:00",
    "june 5 at 3pm", "31.5 at 10:22" (day.month), "31.5.2026", "2026-06-05 15:00",
    "6/5/2026" (month/day), "at 8am" (today, or tomorrow if already past)
Durations: "30 minutes", "2 hours", "1h 30m", "1 day", "2 weeks".
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --- timezones -----------------------------------------------------------------------------

FIXED_ZONES: dict[str, float] = {
    "utc": 0, "gmt": 0, "est": -5, "edt": -4, "cst": -6, "cdt": -5,
    "mst": -7, "mdt": -6, "pst": -8, "pdt": -7,
    "cet": 1, "cest": 2, "eet": 2, "eest": 3, "bst": 1, "wet": 0, "west": 1,
    "aest": 10, "acst": 9.5, "awst": 8, "nzst": 12, "nzdt": 13,
    "ist": 5.5, "jst": 9, "kst": 9, "hkt": 8, "sgt": 8,
}

CITY_ZONES: dict[str, str] = {
    "poland": "Europe/Warsaw", "warsaw": "Europe/Warsaw", "krakow": "Europe/Warsaw", "kraków": "Europe/Warsaw",
    "wroclaw": "Europe/Warsaw", "gdansk": "Europe/Warsaw", "poznan": "Europe/Warsaw", "katowice": "Europe/Warsaw",
    "gliwice": "Europe/Warsaw", "rybnik": "Europe/Warsaw", "lodz": "Europe/Warsaw",
    "london": "Europe/London", "uk": "Europe/London", "dublin": "Europe/Dublin", "lisbon": "Europe/Lisbon",
    "paris": "Europe/Paris", "berlin": "Europe/Berlin", "germany": "Europe/Berlin", "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid", "rome": "Europe/Rome", "prague": "Europe/Prague", "vienna": "Europe/Vienna",
    "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo", "helsinki": "Europe/Helsinki", "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv", "athens": "Europe/Athens", "istanbul": "Europe/Istanbul", "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai", "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "india": "Asia/Kolkata", "bangkok": "Asia/Bangkok", "jakarta": "Asia/Jakarta", "singapore": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong", "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai", "china": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne", "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth", "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
    "new york": "America/New_York", "nyc": "America/New_York", "boston": "America/New_York",
    "toronto": "America/Toronto", "chicago": "America/Chicago", "texas": "America/Chicago",
    "denver": "America/Denver", "calgary": "America/Edmonton", "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles", "california": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "vancouver": "America/Vancouver", "mexico city": "America/Mexico_City", "sao paulo": "America/Sao_Paulo",
    "são paulo": "America/Sao_Paulo", "buenos aires": "America/Argentina/Buenos_Aires",
    "anchorage": "America/Anchorage", "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
}

_OFFSET = re.compile(r"^\s*(?:utc|gmt)?\s*([+-]?)\s*(?:(\d{1,2})(?::(\d{2}))?|(\d{1,2}\.\d+))\s*$", re.I)

TimezoneSpec = "str | float"  # IANA zone name, or a fixed UTC offset in hours


def parse_timezone(text: str) -> str | float | None:
    """'Warsaw' → 'Europe/Warsaw'; 'CET' → 1.0; 'UTC+5:30' → 5.5; nonsense → None."""
    key = " ".join(text.strip().lower().split())
    if key in CITY_ZONES:
        return CITY_ZONES[key]
    if key in FIXED_ZONES:
        return float(FIXED_ZONES[key])
    if "/" in text.strip():
        try:
            ZoneInfo(text.strip())
            return text.strip()
        except (ZoneInfoNotFoundError, ValueError):
            return None
    m = _OFFSET.match(key)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    hours = float(m.group(4)) if m.group(4) else int(m.group(2)) + (int(m.group(3) or 0) / 60)
    offset = sign * hours
    return offset if -12 <= offset <= 14 else None


def offset_hours(spec: str | float, at: datetime | None = None) -> float:
    """UTC offset in hours for a timezone spec at a given moment (DST-aware for zone names)."""
    if isinstance(spec, (int, float)):
        return float(spec)
    at = at or datetime.now(timezone.utc)
    return ZoneInfo(spec).utcoffset(at.astimezone(ZoneInfo(spec)).replace(tzinfo=None)).total_seconds() / 3600


def format_offset(offset: float) -> str:
    sign = "+" if offset >= 0 else "-"
    hours, minutes = int(abs(offset)), round((abs(offset) % 1) * 60)
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def describe_timezone(spec: str | float, at: datetime | None = None) -> str:
    offset = format_offset(offset_hours(spec, at))
    return f"{spec} ({offset})" if isinstance(spec, str) else offset


# --- durations -------------------------------------------------------------------------------

_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400, "d": 86400,
    "week": 604800, "weeks": 604800, "w": 604800,
}
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b")


def parse_duration(text: str) -> float | None:
    """'1 hour 30 minutes' → 5400.0 seconds; None if nothing recognisable."""
    total = sum(float(v) * _UNITS[u] for v, u in _DURATION.findall(text.lower()))
    return total if total > 0 else None


def format_duration(seconds: float) -> str:
    d = timedelta(seconds=round(seconds))
    hours, rest = divmod(d.seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{n} {unit}{'s' if n != 1 else ''}"
             for n, unit in ((d.days, "day"), (hours, "hour"), (minutes, "minute")) if n]
    return ", ".join(parts) or f"{secs} second{'s' if secs != 1 else ''}"


# --- absolute times ---------------------------------------------------------------------------

_DAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
         "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
         "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
           "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
           "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
           "december": 12, "dec": 12}


class PastDateError(ValueError):
    pass


def _clock(text: str) -> tuple[int, int] | None:
    if re.search(r"\bnoon\b|\bmidday\b", text):
        return 12, 0
    if re.search(r"\bmidnight\b", text):
        return 0, 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if m.group(3) == "pm" else 0)
        return hour, minute
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\bat\s+(\d{1,2})(?![\d.:/])\b", text)   # "at 12", "at 18" (24-hour)
    if m and int(m.group(1)) < 24:
        return int(m.group(1)), 0
    return None


def _day(text: str, today: date) -> tuple[date | None, str]:
    """The day mentioned, and what kind of mention it was: "fixed", "weekday" or "no_year"
    (the latter two can roll forward a week / a year if they turn out to be past)."""
    if re.search(r"\btoday\b", text):
        return today, "fixed"
    if re.search(r"\btomorrow\b", text):
        return today + timedelta(days=1), "fixed"
    for name, weekday in _DAYS.items():
        m = re.search(rf"\b(next\s+)?{name}\b", text)
        if m:
            ahead = (weekday - today.weekday()) % 7
            if ahead == 0 and m.group(1):
                ahead = 7
            return today + timedelta(days=ahead), "weekday"
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?\b", text)
    if m:
        return _make_date(int(m.group(3) or today.year), _MONTHS[m.group(1)], int(m.group(2))), \
            "fixed" if m.group(3) else "no_year"
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?\b", text)
    if m:
        return _make_date(int(m.group(3) or today.year), _MONTHS[m.group(2)], int(m.group(1))), \
            "fixed" if m.group(3) else "no_year"
    m = re.search(r"\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b", text)
    if m:
        return _make_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "fixed"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        month, day = (b, a) if a > 12 else (a, b)  # month/day unless that can't be right
        return _make_date(int(m.group(3)), month, day), "fixed"
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text)
    if m:
        return _make_date(int(m.group(3)), int(m.group(2)), int(m.group(1))), "fixed"
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\b(?!\.\d)", text)
    if m:
        return _make_date(today.year, int(m.group(2)), int(m.group(1))), "no_year"
    return None, "none"


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_time(text: str, tz: str | float = 0.0, now: datetime | None = None) -> float | None:
    """Parse a date/time in the given timezone into a UTC timestamp.

    Returns None if it can't be understood; raises PastDateError if it's in the past.
    A day with no time means 09:00; a time with no day means the next time it comes round.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_now = (now + timedelta(hours=offset_hours(tz, now))).replace(tzinfo=None)
    text = " ".join(text.lower().strip().rstrip(".").split())

    clock = _clock(text)
    day, kind = _day(text, local_now.date())
    if day is None and (clock is None or kind != "none"):
        return None  # nothing recognisable, or a date that doesn't exist (e.g. 31.2)
    hour, minute = clock if clock else (9, 0)
    if day is None:
        day, kind = local_now.date(), "time_only"

    def to_utc(d: date) -> datetime:
        local = datetime(d.year, d.month, d.day, hour, minute)
        if isinstance(tz, str):
            return local.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
        return (local - timedelta(hours=tz)).replace(tzinfo=timezone.utc)

    utc = to_utc(day)
    if utc <= now:  # roll forward when the wording allows it
        if kind == "time_only":
            day += timedelta(days=1)
        elif kind == "weekday":
            day += timedelta(days=7)
        elif kind == "no_year":
            day = _make_date(day.year + 1, day.month, day.day) or day
        utc = to_utc(day)
    if utc <= now:
        raise PastDateError(f"That time ({day:%Y-%m-%d} {hour:02d}:{minute:02d}) is in the past.")
    return utc.timestamp()
