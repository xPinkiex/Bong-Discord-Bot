from __future__ import annotations

from datetime import datetime

import pytest

from bong.conversation.turn import Turn
from bong.llm import ChatResult


class FakeLLM:
    """Records calls; replies come from a per-model queue or a default."""

    def __init__(self, replies: dict[str, list[str]] | None = None, default: str = "*boops* :3"):
        self.replies = {k: list(v) for k, v in (replies or {}).items()}
        self.default = default
        self.calls: list[tuple[str, list, dict]] = []

    async def chat(self, model, messages, **options):
        self.calls.append((model, messages, options))
        queue = self.replies.get(model)
        text = queue.pop(0) if queue else self.default
        return ChatResult(text=text, prompt_tokens=10, output_tokens=5)

    def calls_to(self, model):
        return [c for c in self.calls if c[0] == model]


def make_turn(content="hi", author_id=1, author_name="Eve", channel_id=100, guild_id=5, **kw) -> Turn:
    return Turn(author_id=author_id, author_name=author_name, channel_id=channel_id,
                content=content, guild_id=guild_id, sent_at=datetime(2026, 11, 13, 14, 30), **kw)


@pytest.fixture
def fake_llm():
    return FakeLLM()
