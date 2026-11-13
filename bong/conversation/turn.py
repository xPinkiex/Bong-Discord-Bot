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


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
TEXT_EXTS = (".txt", ".md", ".py", ".json", ".csv", ".xml", ".yaml", ".yml", ".cfg", ".ini", ".log", ".toml",
             ".rs", ".js", ".ts", ".html", ".css", ".sh", ".bat", ".c", ".h", ".cpp", ".java", ".lua", ".asm")
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".webm", ".mkv")


@dataclass(frozen=True)
class Attachment:
    filename: str
    url: str = ""
    content_type: str = ""
    size: int = 0
    width: int | None = None
    height: int | None = None

    def _is(self, mime: str, exts: tuple[str, ...]) -> bool:
        return self.content_type.startswith(mime) or self.filename.lower().endswith(exts)

    @property
    def kind(self) -> str:
        if self._is("image/", IMAGE_EXTS):
            return "image"
        if self._is("video/", VIDEO_EXTS):
            return "video"
        if self._is("text/", TEXT_EXTS):
            return "text"
        return "file"


@dataclass
class Outbox:
    """Side effects tools ask for, carried out by the Discord layer after Bong replies."""
    files: list = field(default_factory=list)       # Paths to send with the reply
    reactions: list[str] = field(default_factory=list)
    shutdown: bool = False


@dataclass(frozen=True)
class Turn:
    author_id: int
    author_name: str
    channel_id: int
    content: str                       # mentions already resolved to readable names
    guild_id: int | None = None        # None in DMs
    attachments: tuple[Attachment, ...] = ()
    reply_to: ReplyInfo | None = None
    mentions_bong: bool = False        # pinged or replied to Bong directly
    sent_at: datetime = field(default_factory=datetime.now)
    outbox: Outbox = field(default_factory=Outbox, compare=False, repr=False)

    def attachments_of(self, kind: str) -> list[Attachment]:
        return [a for a in self.attachments if a.kind == kind]

    @property
    def is_dm(self) -> bool:
        return self.guild_id is None

    def display_text(self) -> str:
        """Message content on one line, with attachment names appended."""
        text = " ".join(self.content.split("\n")).strip()
        if self.attachments:
            files = ", ".join(a.filename for a in self.attachments)
            text = f"{text} [attached: {files}]".strip()
        return text
