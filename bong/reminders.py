"""Reminders, stored in bong_user_data/reminders.json (same format as the old bot):
    [{"user_id": 1, "username": "Eve", "message": "feed the cat", "due_at": 1789999999.0}, ...]

Bong DMs people when their reminders come due. Unlike the old bot, reminders
that came due while she was offline are delivered late instead of dropped.
"""

from __future__ import annotations

import time
from pathlib import Path

from .storage import JsonFile


class ReminderStore:
    def __init__(self, path: Path):
        self._file = JsonFile(path, default=[])

    @property
    def _items(self) -> list[dict]:
        return self._file.data

    def load(self) -> None:
        data = self._file.load()
        self._file.data = sorted((r for r in data if isinstance(r, dict) and "due_at" in r and "user_id" in r),
                                 key=lambda r: r["due_at"]) if isinstance(data, list) else []

    def add(self, user_id: int, username: str, message: str, due_at: float) -> dict:
        reminder = {"user_id": int(user_id), "username": username, "message": message.strip(), "due_at": float(due_at)}
        self._items.append(reminder)
        self._items.sort(key=lambda r: r["due_at"])
        self._file.save()
        return reminder

    def for_user(self, user_id: int) -> list[dict]:
        return [r for r in self._items if r["user_id"] == user_id]

    def cancel(self, user_id: int, query: str = "") -> dict | None:
        """Cancel the user's reminder matching the query (the latest one if no query). Returns it, or None."""
        mine = self.for_user(user_id)
        if query.strip():
            q = query.strip().lower()
            mine = [r for r in mine if q in r["message"].lower()]
        if not mine:
            return None
        reminder = mine[-1]
        self._items.remove(reminder)
        self._file.save()
        return reminder

    def pop_due(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        due = [r for r in self._items if r["due_at"] <= now]
        if due:
            self._file.data = [r for r in self._items if r["due_at"] > now]
            self._file.save()
        return due

    def count(self) -> int:
        return len(self._items)
