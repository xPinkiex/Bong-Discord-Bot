"""A single incoming message, described without any discord.py objects.

Everything about "who is speaking, where" travels in a ``Turn`` that is passed
explicitly through the pipeline — there is no shared global "current user", so
two people talking to Bong at the same time can't get mixed up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ReplyInfo:
    author_name: str
    author_id: int
    content: str
    is_bong: bool = False


@dataclass(frozen=True)
class Turn:
    author_id: int
    author_name: str
    channel_id: int
    content: str                       # mentions already resolved to readable names
    guild_id: int | None = None        # None in DMs
    attachments: tuple[str, ...] = ()  # filenames only (text-only bot)
    reply_to: ReplyInfo | None = None
    mentions_bong: bool = False        # pinged or replied to Bong directly
    sent_at: datetime = field(default_factory=datetime.now)

    @property
    def is_dm(self) -> bool:
        return self.guild_id is None

    def display_text(self) -> str:
        """Message content on one line, with attachment names appended."""
        text = " ".join(self.content.split("\n")).strip()
        if self.attachments:
            files = ", ".join(self.attachments)
            text = f"{text} [attached: {files}]".strip()
        return text
