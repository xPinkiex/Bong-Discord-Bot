"""The set of guild channels Bong is active in (toggled with @llm), persisted to disk."""

from __future__ import annotations

from pathlib import Path

from .storage import JsonFile


class ActiveChannels:
    def __init__(self, path: Path):
        self._file = JsonFile(path, default=[])
        self._ids: set[int] = set()

    def load(self) -> None:
        self._ids = {int(c) for c in self._file.load()}

    def __contains__(self, channel_id: int) -> bool:
        return channel_id in self._ids

    def __iter__(self):
        return iter(sorted(self._ids))

    def toggle(self, channel_id: int) -> bool:
        """Flip the channel's state. Returns True if Bong is now active there."""
        if channel_id in self._ids:
            self._ids.discard(channel_id)
            active = False
        else:
            self._ids.add(channel_id)
            active = True
        self._file.data = sorted(self._ids)
        self._file.save()
        return active
