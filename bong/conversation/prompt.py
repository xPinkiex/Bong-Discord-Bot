"""Builds the message sent to the chat model.

Like the original bot, the whole prompt — persona, context and the user's
message — is one *user* message made by filling ``persona/persona.md``.

Each placeholder in that file ({memories}, {history}, ...) is filled by a
*section*. A section returns a dict of placeholder → text. To give Bong a new
kind of context, write a section, add its placeholder to persona.md, and add
it to ``default_sections``. ``MemorySection`` is how memories get in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from .. import persona
from ..llm import ChatMessage
from ..memory import MemoryStore
from .history import ChannelHistory
from .turn import Turn


@dataclass(frozen=True)
class PromptContext:
    turn: Turn
    history: ChannelHistory


class PromptSection(Protocol):
    async def render(self, ctx: PromptContext) -> dict[str, str]: ...


def _one_line(text: str) -> str:
    return text.replace("\n", ". ")


class MemorySection:
    """{memories} — long-term memories from the memory store (empty for now)."""

    def __init__(self, store: MemoryStore, limit: int = 8):
        self.store = store
        self.limit = limit

    async def render(self, ctx: PromptContext) -> dict[str, str]:
        memories = await self.store.recall(ctx.turn, self.limit)
        return {"memories": "\n".join(f"- {m.for_prompt()}" for m in memories)}


class MessageSection:
    """{username} {userID} {message} {attachments} {reply_context} — the current message."""

    async def render(self, ctx: PromptContext) -> dict[str, str]:
        t = ctx.turn
        values = {
            "username": t.author_name,
            "userID": str(t.author_id),
            "message": _one_line(t.content),
            "attachments": "",
            "reply_context": "",
        }
        if t.attachments:
            values["attachments"] = (f"\nAttachments included with this message: {', '.join(t.attachments)} "
                                     f"(you can't open attachments right now)\n")
        if t.reply_to:
            r = t.reply_to
            values["reply_context"] = (
                "\nThe user is replying to a message. Take into account what they're replying to when responding.\n\n"
                f"Replied-to user ID: {r.author_id}\n"
                f"The user replied to: {r.author_name} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: "
                f"{_one_line(r.content)}\n"
            )
        return values


class HistorySection:
    """{history} {summaries} — recent chat (newest first, like the original) and older summaries."""

    async def render(self, ctx: PromptContext) -> dict[str, str]:
        history = "\n".join(e.render() for e in reversed(ctx.history.entries))
        summaries = ""
        if ctx.history.summaries:
            summaries = ("\n\nThe older conversation above has been summarized for context. These are compressed "
                         "versions of what was discussed before the chat history you can see:\n"
                         + "\n".join(f"- {s}" for s in ctx.history.summaries))
        return {"history": history, "summaries": summaries}


class VoiceStatusSection:
    """{voice_status} — whether Bong is in voice and what's playing (filled by the Discord layer)."""

    def __init__(self, status_for: "Callable[[Turn], str]"):
        self.status_for = status_for

    async def render(self, ctx: PromptContext) -> dict[str, str]:
        return {"voice_status": self.status_for(ctx.turn)}


def default_sections(memory: MemoryStore) -> list[PromptSection]:
    return [MemorySection(memory), MessageSection(), HistorySection()]


class PromptBuilder:
    def __init__(self, template: str, sections: list[PromptSection]):
        self.template = template
        self.sections = sections

    async def build(self, ctx: PromptContext) -> list[ChatMessage]:
        values: dict[str, str] = {"voice_status": "Not in any voice channel."}
        for section in self.sections:
            values.update(await section.render(ctx))
        return [{"role": "user", "content": persona.fill(self.template, **values)}]
