# user_data.py — Per-user settings persisted to users.json
#
# Stores permission tier, timezone, and other per-user data.
# The owner (Eve) is always guaranteed admin even if the file is missing.
#
# users.json format:
#   {"273761843544064000": {"tier": "admin", "timezone": 2}, ...}

import json
from pathlib import Path

_USERS_FILE = Path(__file__).parent / "users.json"

# In-memory data: user_id -> dict of settings
_user_data: dict[int, dict] = {}

# The owner who receives approval requests — always admin
OWNER_ID = 273761843544064000


def load_users():
    """Load user data from disk. Owner is always guaranteed admin."""
    global _user_data
    _user_data = {}
    try:
        if _USERS_FILE.exists():
            with open(_USERS_FILE, "r") as f:
                raw = json.load(f)
            for uid_str, value in raw.items():
                uid = int(uid_str)
                if isinstance(value, str):
                    _user_data[uid] = {"tier": value}
                else:
                    _user_data[uid] = dict(value)
    except Exception:
        _user_data = {}
    _user_data.setdefault(OWNER_ID, {})["tier"] = "admin"


def save_users():
    """Persist all user data to disk."""
    try:
        data = {str(uid): settings for uid, settings in _user_data.items()}
        with open(_USERS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_tier(user_id: int) -> str | None:
    """Get the permission tier for a user, or None if unknown."""
    entry = _user_data.get(user_id)
    if entry is None:
        return None
    return entry.get("tier")


def is_admin(user_id: int) -> bool:
    """Check if a user has admin tier."""
    return get_tier(user_id) == "admin"


def is_authorized(user_id: int) -> bool:
    """Check if a user has admin or authorized tier."""
    return get_tier(user_id) in ("admin", "authorized")


def is_known(user_id: int) -> bool:
    """Check if a user is in any tier."""
    return user_id in _user_data


def set_tier(user_id: int, tier: str):
    """Set the permission tier for a user and persist."""
    _user_data.setdefault(user_id, {})["tier"] = tier
    save_users()


def get_timezone(user_id: int) -> float | None:
    """Get the UTC offset for a user, or None if not set."""
    entry = _user_data.get(user_id)
    if entry is None:
        return None
    return entry.get("timezone")


def set_timezone(user_id: int, offset: float):
    """Set the UTC offset for a user and persist."""
    _user_data.setdefault(user_id, {})["timezone"] = offset
    save_users()


def remove_timezone(user_id: int):
    """Remove the timezone for a user and persist."""
    entry = _user_data.get(user_id)
    if entry and "timezone" in entry:
        del entry["timezone"]
        save_users()


# ---- Timezone name / city lookup ----

_TZ_ALIASES: dict[str, float] = {
    # Common abbreviations
    "utc": 0, "gmt": 0, "est": -5, "edt": -4, "cst": -6, "cdt": -5,
    "mst": -7, "mdt": -6, "pst": -8, "pdt": -7,
    "cet": 1, "cest": 2, "eet": 2, "eest": 3,
    "aest": 10, "acst": 9.5, "awst": 8,
    "nzst": 12, "nzdt": 13,
    "ist": 5.5, "jst": 9, "kst": 9, "cst_china": 8, "hkt": 8, "sgt": 8,
    # Cities
    "new york": -5, "los angeles": -8, "chicago": -6, "denver": -7,
    "london": 0, "paris": 1, "berlin": 1, "amsterdam": 1, "madrid": 1,
    "rome": 1, "moscow": 3, "istanbul": 3, "dubai": 4, "mumbai": 5.5,
    "delhi": 5.5, "kolkata": 5.5, "bangkok": 7, "jakarta": 7,
    "shanghai": 8, "beijing": 8, "singapore": 8, "hong kong": 8,
    "tokyo": 9, "seoul": 9, "sydney": 11, "melbourne": 11,
    "auckland": 13, "honolulu": -10, "anchorage": -9,
    "sao paulo": -3, "buenos aires": -3, "mexico city": -6,
    "toronto": -5, "vancouver": -8, "calgary": -7,
}

_TZ_REGEX = __import__("re").compile(
    r"""
    ^\s*
    (?:UTC|GMT)?                              # optional UTC/GMT prefix
    \s*
    ([+-]?)                                    # optional sign
    \s*
    (?:
        (\d{1,2})                               # hours
        (?::(\d{2}))?                           # optional :minutes
        |
        (\d{1,2}(?:\.\d+)?)                    # decimal hours e.g. 5.5
    )
    \s*$
    """,
    __import__("re").VERBOSE,
)


def parse_timezone(text: str) -> float | None:
    """Parse a timezone string into a UTC offset in hours.

    Accepts:
      - Named zones: 'EST', 'PST', 'CET', 'New York', 'London', etc.
      - Offsets: 'UTC+2', 'GMT-5', '+2', '-7', '+5:30', '5.5'
    Returns the offset as a float (e.g. 2.0, -5.0, 5.5) or None if unparseable.
    """
    import re

    # Try named zone / city lookup first (case-insensitive)
    key = text.strip().lower()
    if key in _TZ_ALIASES:
        return _TZ_ALIASES[key]

    # Try UTC offset parsing
    m = _TZ_REGEX.match(text)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        if m.group(4) is not None:
            # Decimal hours like +5.5
            hours = float(m.group(4))
        elif m.group(2) is not None:
            hours = int(m.group(2))
            minutes = int(m.group(3)) if m.group(3) else 0
            hours += minutes / 60.0
        else:
            return None
        result = sign * hours
        if -12 <= result <= 14:
            return result
        return None

    return None