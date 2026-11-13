"""Tools Bong can call while answering.

A tool is a name, a description and JSON-schema parameters (what the model
sees), plus an async handler that gets the current ``Turn`` and the
arguments. To add one, write a ``Tool`` and include it in the list passed to
``Bong(tools=...)`` in ``bong/bot/client.py``. Memory tools (save/recall)
will fit here later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..conversation.turn import Turn

log = logging.getLogger(__name__)

Handler = Callable[[Turn, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Handler
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


class ToolBox:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools = {t.name: t for t in tools or []}

    def __bool__(self) -> bool:
        return bool(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    async def run(self, name: str, turn: Turn, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: there is no tool called {name!r}. Available tools: {', '.join(self._tools) or 'none'}."
        try:
            return await tool.handler(turn, arguments)
        except Exception as e:
            log.exception("Tool %s failed", name)
            return f"Error running {name}: {e}"


__all__ = ["Tool", "ToolBox"]
