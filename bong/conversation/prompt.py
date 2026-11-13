"""Builds the messages sent to the chat model.

The system prompt is the persona followed by a list of *sections*. Each section
looks at the current turn and returns a block of text (or None to skip). To
give Bong a new kind of context, write a section and add it to the list in
``default_sections`` — e.g. ``MemorySection`` is how memories get in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..llm import ChatMessage
from ..memory import Memory, MemoryStore
from .history import ChannelHistory
from .turn import Turn


@dataclass(frozen=True)
class PromptContext:
    turn: Turn
    history: ChannelHistory


class PromptSection(Protocol):
    async def render(self, ctx: PromptContext) -> str | None: ...


class MemorySection:
    """Long-term memories from the memory store, if it has any."""

    def __init__(self, store: MemoryStore, limit: int = 8):
        self.store = store
        self.limit = limit

    async def render(self, ctx: PromptContext) -> str | None:
        memories = await self.store.recall(ctx.turn, self.limit)
        if not memories:
            return None
        return "Long-term memories (facts you've learned about people and things):\n" + "\n".join(
            f"- {_format_memory(m)}" for m in memories
        )


def _format_memory(m: Memory) -> str:
    return f"[about {m.about_name}] {m.text}" if m.about_name else m.text


class SummarySection:
    async def render(self, ctx: PromptContext) -> str | None:
        if not ctx.history.summaries:
            return None
        return ("Summary of the older conversation in this channel:\n"
                + "\n".join(f"- {s}" for s in ctx.history.summaries))


class HistorySection:
    async def render(self, ctx: PromptContext) -> str | None:
        if not ctx.history.entries:
            return None
        return ("Recent chat history (read-only context, oldest first):\n"
                + "\n".join(e.render() for e in ctx.history.entries))


class SpeakerSection:
    async def render(self, ctx: PromptContext) -> str | None:
        t = ctx.turn
        lines = [
            f"Current time (background info — only mention it if someone asks or it matters): "
            f"{t.sent_at.strftime('%H:%M on %A, %Y-%m-%d')}.",
            f"You are talking to {t.author_name}. Just call them by name — don't ping them; "
            f"you're already replying to them. (Their ping, only if you ever need it: <@{t.author_id}>)",
            "This is a private DM." if t.is_dm else "This is a server channel with other people in it.",
        ]
        if t.reply_to:
            who = "you (Bong)" if t.reply_to.is_bong else t.reply_to.author_name
            lines.append(f"They are replying to this message from {who}: "
                         f"{' '.join(t.reply_to.content.split())}")
        return "\n".join(lines)


def default_sections(memory: MemoryStore) -> list[PromptSection]:
    return [MemorySection(memory), SummarySection(), HistorySection(), SpeakerSection()]


class PromptBuilder:
    def __init__(self, persona_text: str, sections: list[PromptSection]):
        self.persona_text = persona_text
        self.sections = sections

    async def build(self, ctx: PromptContext) -> list[ChatMessage]:
        blocks = [self.persona_text]
        for section in self.sections:
            block = await section.render(ctx)
            if block:
                blocks.append(block)
        user_line = f"{ctx.turn.author_name}: {ctx.turn.display_text()}"
        return [
            {"role": "system", "content": "\n\n".join(blocks)},
            {"role": "user", "content": user_line},
        ]
