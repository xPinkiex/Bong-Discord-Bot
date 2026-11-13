"""Long-term memory interface.

Bong's reply pipeline asks a ``MemoryStore`` for memories relevant to each
message and puts them in the prompt (see ``conversation.prompt.MemorySection``),
then tells the store about the finished exchange via ``observe``. Right now the
store is ``NullMemoryStore``, which remembers nothing.

To add per-user memories later, write a class with these methods (e.g. a
ChromaDB- or JSON-backed store) and pass it to ``Bong(...)`` in
``bong/discord/bot.py``. Nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..conversation.turn import Turn


@dataclass(frozen=True)
class Memory:
    text: str
    about_user_id: int | None = None      # None = general fact, not about anyone
    about_name: str = ""
    category: str = "fact"
    importance: int = 3                    # 1 (trivial) .. 5 (critical)
    created_at: float = 0.0
    metadata: dict = field(default_factory=dict)


class MemoryStore(Protocol):
    async def recall(self, turn: Turn, limit: int = 8) -> list[Memory]:
        """Memories worth showing Bong for this message (about the speaker first)."""
        ...

    async def observe(self, turn: Turn, reply: str) -> None:
        """Called after Bong replies, so a store can extract and save new facts."""
        ...


class NullMemoryStore:
    """Remembers nothing. The default until a real store is plugged in."""

    async def recall(self, turn: Turn, limit: int = 8) -> list[Memory]:
        return []

    async def observe(self, turn: Turn, reply: str) -> None:
        return None


__all__ = ["Memory", "MemoryStore", "NullMemoryStore"]
