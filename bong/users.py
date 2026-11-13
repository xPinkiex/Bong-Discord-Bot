"""Per-user permission tags and stats, stored in bong_user_data/users.json.

Format (compatible with the old bot — unknown fields and tags are preserved):
    {"273761843544064000": {"allowed": ["admin"], "display_name": "Eve", "tokens": 1234}, ...}

Tags that matter to the text bot:
    llm       talk to Bong
    llm_fast  talk to Bong without the cooldown (implies llm)
    admin     everything, plus admin commands
The owner is always admin.
"""

from __future__ import annotations

from pathlib import Path

from .storage import JsonFile

TAGS = ("llm", "llm_fast", "admin")


class UserStore:
    def __init__(self, path: Path, owner_id: int):
        self.owner_id = owner_id
        self._file = JsonFile(path, default={})
        self._users: dict[int, dict] = {}

    # --- persistence -------------------------------------------------------

    def load(self) -> None:
        raw = self._file.load()
        users: dict[int, dict] = {}
        for uid, entry in (raw.items() if isinstance(raw, dict) else []):
            users[int(uid)] = dict(entry) if isinstance(entry, dict) else {"allowed": []}
        users.setdefault(self.owner_id, {})["allowed"] = ["admin"]
        self._users = users

    def save(self) -> None:
        self._file.data = {str(uid): entry for uid, entry in self._users.items()}
        self._file.save()

    # --- permissions -------------------------------------------------------

    def tags(self, user_id: int) -> list[str]:
        return list(self._users.get(user_id, {}).get("allowed", []))

    def has(self, user_id: int, tag: str) -> bool:
        if user_id == self.owner_id:
            return True
        allowed = self._users.get(user_id, {}).get("allowed", [])
        if "admin" in allowed:
            return True
        if tag == "llm" and "llm_fast" in allowed:
            return True
        return tag in allowed

    def is_admin(self, user_id: int) -> bool:
        return self.has(user_id, "admin")

    def add_tag(self, user_id: int, tag: str) -> bool:
        if tag not in TAGS:
            raise ValueError(f"unknown tag {tag!r}")
        entry = self._users.setdefault(user_id, {})
        allowed = entry.setdefault("allowed", [])
        if tag in allowed:
            return False
        allowed.append(tag)
        self.save()
        return True

    def remove_tag(self, user_id: int, tag: str) -> bool:
        if user_id == self.owner_id and tag == "admin":
            return False
        allowed = self._users.get(user_id, {}).get("allowed", [])
        if tag not in allowed:
            return False
        allowed.remove(tag)
        self.save()
        return True

    def grant(self, user_id: int, tags: list[str], display_name: str = "") -> None:
        entry = self._users.setdefault(user_id, {})
        entry["allowed"] = [t for t in tags if t in TAGS]
        if display_name:
            entry["display_name"] = display_name
        self.save()

    # --- settings ----------------------------------------------------------

    def timezone(self, user_id: int) -> float | None:
        """UTC offset in hours saved for this user (by the old bot), or None."""
        value = self._users.get(user_id, {}).get("timezone")
        return float(value) if isinstance(value, (int, float)) else None

    def known_names(self) -> dict[int, str]:
        """Display names seen for users (saved when they were approved or chatted)."""
        return {uid: e["display_name"] for uid, e in self._users.items() if e.get("display_name")}

    # --- stats -------------------------------------------------------------

    def add_tokens(self, user_id: int, count: int, display_name: str = "") -> None:
        if count <= 0:
            return
        entry = self._users.setdefault(user_id, {})
        entry["tokens"] = entry.get("tokens", 0) + count
        if display_name:
            entry["display_name"] = display_name
        self.save()
