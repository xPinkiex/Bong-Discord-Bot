"""Play counts per song, in bong_data/song_stats.json ({"song name": count}, same as the old bot)."""

from __future__ import annotations

from pathlib import Path

from ..storage import JsonFile


class SongStats:
    def __init__(self, path: Path):
        self._file = JsonFile(path, default={})

    def load(self) -> None:
        data = self._file.load()
        self._file.data = {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def played(self, song: str) -> None:
        self._file.data[song] = self._file.data.get(song, 0) + 1
        self._file.save()

    def top(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self._file.data.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def total(self) -> int:
        return sum(self._file.data.values())
