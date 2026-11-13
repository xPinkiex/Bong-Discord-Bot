"""Decides whether a message in an active channel is meant for Bong.

Fast path: DMs, pings, replies to Bong, or the word "bong" → yes, no LLM call.
Slow path: ask the helper model with the classifier prompt and recent context.
"""

from __future__ import annotations

import logging

from .. import persona
from ..llm import LLM
from .history import HistoryEntry
from .turn import Turn

log = logging.getLogger(__name__)


def obviously_for_bong(turn: Turn) -> bool:
    return turn.is_dm or turn.mentions_bong or "bong" in turn.content.lower()


class Detector:
    def __init__(self, llm: LLM, model: str, context_size: int = 7):
        self.llm = llm
        self.model = model
        self.context_size = context_size
        self.template = persona.load("classifier")

    async def is_for_bong(self, turn: Turn, recent: list[HistoryEntry]) -> bool:
        if obviously_for_bong(turn):
            return True

        context = "\n".join(e.render() for e in recent[-self.context_size:]) or "No recent messages"
        reply = "None"
        if turn.reply_to:
            who = "Bong" if turn.reply_to.is_bong else turn.reply_to.author_name
            reply = f"{who}: {' '.join(turn.reply_to.content.split())}"

        prompt = persona.fill(
            self.template,
            recent_context=context,
            message_content=f"{turn.author_name}: {turn.display_text()}",
            reply_context=reply,
        )
        try:
            result = await self.llm.chat(self.model, [{"role": "user", "content": prompt}],
                                         temperature=0.1, num_predict=50)
        except Exception as e:
            log.warning("Classifier failed, assuming not for Bong: %s", e)
            return False
        return "YES" in result.text.upper()
