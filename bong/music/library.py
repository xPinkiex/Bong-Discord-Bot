"""The downloaded songs: mp3 files in bong_data/saved_sounds (same folder as the old bot)."""

from __future__ import annotations

import difflib
import re
from pathlib import Path

# Same cleaning as the old bot, so new downloads match songs already in the library.
_UNSAFE = re.compile(r"[^\w\s\[\]\(\)\{\}]")


def clean_title(title: str) -> str:
    """A safe filename stem from a video title."""
    title = re.sub(r" {2,}", " ", _UNSAFE.sub("", title)).strip()
    return title[:150] or "untitled"


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


class MusicLibrary:
    """A folder of files matched by name. Used for songs (mp3), and for saved images and texts."""

    def __init__(self, folder: Path, suffixes: tuple[str, ...] = (".mp3",)):
        self.folder = Path(folder)
        self.suffixes = suffixes

    def songs(self) -> list[Path]:
        if not self.folder.exists():
            return []
        return sorted((p for p in self.folder.iterdir() if p.is_file() and p.suffix.lower() in self.suffixes),
                      key=lambda p: p.stem.lower())

    files = songs

    def paths(self) -> list[str]:
        return [str(p) for p in self.songs()]

    def path_for(self, title: str) -> Path:
        return self.folder / f"{clean_title(title)}.mp3"

    def search(self, query: str, limit: int = 10) -> list[Path]:
        """Songs whose name contains every word of the query, best first; falls back to fuzzy matching."""
        words = _norm(query).split()
        songs = self.songs()
        if not words:
            return songs[:limit]
        hits = [p for p in songs if all(w in _norm(p.stem) for w in words)]
        if not hits:
            names = {_norm(p.stem): p for p in songs}
            hits = [names[n] for n in difflib.get_close_matches(_norm(query), list(names), n=limit, cutoff=0.5)]
        exact = [p for p in hits if _norm(p.stem) == _norm(query)]
        return (exact + [p for p in hits if p not in exact])[:limit]

    def find(self, query: str) -> Path | None:
        hits = self.search(query, limit=1)
        return hits[0] if hits else None
