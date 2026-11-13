"""Async chat client for Ollama's /api/chat, plus the small interface the rest of Bong uses.

Anything with an ``async chat(...) -> ChatResult`` method can stand in for
``OllamaClient`` (tests use a fake).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

import aiohttp

log = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(TypedDict, total=False):
    role: Role
    content: str
    tool_calls: list[dict]   # assistant messages that call tools
    tool_name: str           # tool result messages
    images: list[str]        # base64 images (vision models)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    text: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    tool_calls: tuple[ToolCall, ...] = ()
    raw_message: dict | None = None   # the assistant message as returned, for tool loops

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


class LLM(Protocol):
    async def chat(self, model: str, messages: list[ChatMessage], tools: list[dict] | None = None,
                   **options) -> ChatResult: ...


class LLMError(RuntimeError):
    pass


class OllamaClient:
    """Talks to Ollama over HTTP. Retries transient network errors with backoff."""

    RETRY_DELAYS = (2.0, 4.0)

    def __init__(self, host: str, api_key: str = "", timeout: float = 120.0):
        """``api_key`` is only needed for ollama.com's direct API; local servers don't use one."""
        self.host = host.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._keep_alive = not api_key  # only meaningful for a local server
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)
        return self._session

    async def list_models(self) -> list[str] | None:
        """Model names the server offers, or None if it can't be reached."""
        try:
            session = await self._get_session()
            async with session.get(f"{self.host}/api/tags", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            return [m.get("name", "") for m in data.get("models", [])]
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def chat(self, model: str, messages: list[ChatMessage], tools: list[dict] | None = None,
                   **options) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if tools:
            payload["tools"] = tools
        if self._keep_alive:
            payload["keep_alive"] = -1
        attempts = len(self.RETRY_DELAYS) + 1
        for attempt in range(attempts):
            try:
                session = await self._get_session()
                async with session.post(f"{self.host}/api/chat", json=payload) as resp:
                    if resp.status in (401, 403):
                        raise LLMError(f"Ollama rejected the API key ({resp.status}) — check OLLAMA_API_KEY")
                    if resp.status >= 400:
                        body = await resp.text()
                        raise LLMError(f"Ollama returned {resp.status} for {model}: {body[:300]}")
                    data = await resp.json()
                message = data.get("message") or {}
                return ChatResult(
                    text=message.get("content", "") or "",
                    prompt_tokens=data.get("prompt_eval_count", 0) or 0,
                    output_tokens=data.get("eval_count", 0) or 0,
                    tool_calls=_parse_tool_calls(message),
                    raw_message=message,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == attempts - 1:
                    raise LLMError(f"Could not reach Ollama at {self.host}: {e}") from e
                delay = self.RETRY_DELAYS[attempt]
                log.warning("Ollama request failed (attempt %d/%d), retrying in %.0fs: %s",
                            attempt + 1, attempts, delay, e)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")


def _parse_tool_calls(message: dict) -> tuple[ToolCall, ...]:
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):  # some models send a JSON string
            import json
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if fn.get("name"):
            calls.append(ToolCall(fn["name"], args if isinstance(args, dict) else {}))
    return tuple(calls)
