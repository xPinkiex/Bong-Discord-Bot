"""Rolling per-channel chat history, with older messages folded into short summaries.

Each channel keeps up to ``max_entries`` lines (oldest first). When it fills up,
the oldest ``chunk`` lines are summarized by the helper model in the background
and removed. Only the newest ``max_summaries`` summaries are kept. All of this
lives in memory; it is rebuilt from Discord's message history on startup.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

Summarizer = Callable[[list[str]], Awaitable[str]]


@dataclass
class HistoryEntry:
    author_name: str
    text: str
    at: datetime
    is_bong: bool = False

    def render(self) -> str:
        who = "Bong" if self.is_bong else self.author_name
        return f"{who} at {self.at.strftime('%H:%M')}: {self.text}"


@dataclass
class ChannelHistory:
    entries: list[HistoryEntry] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    summarizing: bool = False

    def recent(self, n: int) -> list[HistoryEntry]:
        return self.entries[-n:] if n > 0 else []


class HistoryStore:
    def __init__(self, summarize: Summarizer, max_entries: int = 30, chunk: int = 10,
                 max_summaries: int = 5):
        self._summarize = summarize
        self.max_entries = max_entries
        self.chunk = chunk
        self.max_summaries = max_summaries
        self._channels: dict[int, ChannelHistory] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, channel_id: int) -> ChannelHistory:
        return self._channels.setdefault(channel_id, ChannelHistory())

    def has(self, channel_id: int) -> bool:
        return channel_id in self._channels

    def seed(self, channel_id: int, entries: list[HistoryEntry]) -> None:
        """Replace a channel's history (used when loading from Discord). Oldest first."""
        ch = self.get(channel_id)
        ch.entries = list(entries)[-self.max_entries:]

    def add(self, channel_id: int, entry: HistoryEntry) -> None:
        ch = self.get(channel_id)
        ch.entries.append(entry)
        self._maybe_summarize(channel_id, ch)

    def _maybe_summarize(self, channel_id: int, ch: ChannelHistory) -> None:
        if ch.summarizing or len(ch.entries) < self.max_entries:
            return
        ch.summarizing = True
        batch = ch.entries[: self.chunk]
        task = asyncio.create_task(self._fold(channel_id, ch, batch))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fold(self, channel_id: int, ch: ChannelHistory, batch: list[HistoryEntry]) -> None:
        try:
            summary = (await self._summarize([e.render() for e in batch])).strip()
            if summary:
                ch.summaries.append(summary)
                del ch.summaries[: -self.max_summaries]
            log.debug("Summarized %d messages in channel %s", len(batch), channel_id)
        except Exception as e:
            log.warning("History summary failed for channel %s: %s", channel_id, e)
        finally:
            # Entries are only ever appended at the end, and only one fold runs per
            # channel, so the batch is still at the front — drop exactly those.
            if ch.entries[: len(batch)] == batch:
                del ch.entries[: len(batch)]
            ch.summarizing = False

    async def wait_idle(self) -> None:
        """Wait for background summaries to finish (used by tests and shutdown)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
