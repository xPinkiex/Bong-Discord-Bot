"""Bong's brain: decides whether to answer, builds the prompt, and produces replies.

This class knows nothing about discord.py. The Discord layer turns messages
into ``Turn`` objects and calls:

    await bong.is_for_bong(turn)   # should she answer?
    bong.record(turn)              # no → just remember it as context
    await bong.respond(turn)       # yes → get a Reply (also records both sides)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

from .. import persona
from ..llm import LLM
from ..memory import MemoryStore, NullMemoryStore
from .detector import Detector
from .history import HistoryEntry, HistoryStore
from .prompt import PromptBuilder, PromptContext, PromptSection, default_sections
from .turn import Turn

log = logging.getLogger(__name__)

# The model sometimes imitates the history format ("Bong at 14:02: ...").
_SPEAKER_PREFIX = re.compile(r"^\s*bong(?: at \d{1,2}:\d{2})?\s*:\s*", re.IGNORECASE)

CHAT_OPTIONS = {"temperature": 0.5, "num_predict": 2000, "repeat_penalty": 1.6}
SUMMARY_OPTIONS = {"temperature": 0.3, "num_predict": 300}

SUMMARY_PROMPT = (
    "Summarize this conversation in 2-3 short sentences. "
    "Preserve key facts, names, decisions, and any important context. "
    "Be concise and factual.\n\n{conversation}"
)


@dataclass(frozen=True)
class Reply:
    text: str
    tokens: int = 0


class Cooldown:
    """Per-user rate limit. ``hit`` returns 0 if allowed (and starts the timer), else seconds left."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last: dict[int, float] = {}

    def hit(self, user_id: int, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        last = self._last.get(user_id)
        if last is not None and now - last < self.seconds:
            return self.seconds - (now - last)
        self._last[user_id] = now
        if len(self._last) > 1000:  # forget users whose cooldown is long over
            self._last = {u: t for u, t in self._last.items() if now - t < self.seconds}
        return 0.0


def clean_reply(text: str) -> str:
    return _SPEAKER_PREFIX.sub("", text.strip(), count=1).strip()


class Bong:
    def __init__(
        self,
        llm: LLM,
        chat_model: str,
        helper_model: str,
        memory: MemoryStore | None = None,
        sections: list[PromptSection] | None = None,
        history_size: int = 30,
        summarize_chunk: int = 10,
        max_summaries: int = 5,
        classifier_context: int = 7,
    ):
        self.llm = llm
        self.chat_model = chat_model
        self.helper_model = helper_model
        self.memory = memory or NullMemoryStore()
        self.history = HistoryStore(self._summarize, history_size, summarize_chunk, max_summaries)
        self.detector = Detector(llm, helper_model, classifier_context)
        self.prompt = PromptBuilder(persona.load("persona"),
                                    sections if sections is not None else default_sections(self.memory))
        self._locks: dict[int, asyncio.Lock] = {}

    # --- public API --------------------------------------------------------

    async def is_for_bong(self, turn: Turn) -> bool:
        return await self.detector.is_for_bong(turn, self.history.get(turn.channel_id).entries)

    def record(self, turn: Turn) -> None:
        """Remember a message Bong isn't answering, as context for later."""
        self.history.add(turn.channel_id, self._entry(turn))

    def record_own(self, channel_id: int, text: str) -> None:
        self.history.add(channel_id, HistoryEntry("Bong", " ".join(text.split()), datetime.now(), is_bong=True))

    async def respond(self, turn: Turn) -> Reply:
        # One reply at a time per channel keeps the history in order.
        lock = self._locks.setdefault(turn.channel_id, asyncio.Lock())
        async with lock:
            history = self.history.get(turn.channel_id)
            messages = await self.prompt.build(PromptContext(turn, history))
            log.debug("Prompt for %s:\n%s", turn.author_name, messages[0]["content"])

            result = await self.llm.chat(self.chat_model, messages, **CHAT_OPTIONS)
            tokens = result.total_tokens
            text = clean_reply(result.text)
            if not text:
                log.info("Empty reply from %s, retrying once", self.chat_model)
                result = await self.llm.chat(self.chat_model, messages, **CHAT_OPTIONS)
                tokens += result.total_tokens
                text = clean_reply(result.text) or "*tilts head* :3"

            self.history.add(turn.channel_id, self._entry(turn))
            self.record_own(turn.channel_id, text)

        try:
            await self.memory.observe(turn, text)
        except Exception as e:
            log.warning("Memory store failed to observe turn: %s", e)
        return Reply(text, tokens)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _entry(turn: Turn) -> HistoryEntry:
        return HistoryEntry(turn.author_name, turn.display_text(), turn.sent_at)

    async def _summarize(self, lines: list[str]) -> str:
        prompt = persona.fill(SUMMARY_PROMPT, conversation="\n".join(lines))
        result = await self.llm.chat(self.helper_model, [{"role": "user", "content": prompt}],
                                     **SUMMARY_OPTIONS)
        return result.text

