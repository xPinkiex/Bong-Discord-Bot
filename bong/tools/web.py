"""Web tools: web_search, youtube_search, summarize_url.

summarize_url only fetches public addresses: every hostname (including
redirect targets) is resolved first and refused if it points at a private,
local or otherwise internal IP, so nobody can use Bong to poke around the
network she runs on.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

from ..conversation.turn import Turn
from ..llm import LLM
from . import Tool

log = logging.getLogger(__name__)

MAX_PAGE_BYTES = 2 * 1024 * 1024
PAGE_TEXT_LIMIT = 6000
USER_AGENT = "Mozilla/5.0 (compatible; BongBot/2.0)"
SEARCH_BACKENDS = ("auto", "bing", "brave", "google")

SUMMARIZE_PROMPT = ("Summarize the following web page in 2-3 short sentences. Be concise and focus on the key "
                    "point. If the content is too brief or empty to summarize, say so.\n\n{content}")


class BlockedAddress(Exception):
    pass


def is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


class PublicOnlyResolver(AbstractResolver):
    """Resolves hostnames normally, but refuses any that point at a non-public address."""

    def __init__(self):
        self._inner = DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        results = await self._inner.resolve(host, port, family)
        for r in results:
            if not is_public(r["host"]):
                raise BlockedAddress(f"{host} points to a private or local address")
        return results

    async def close(self) -> None:
        await self._inner.close()


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    body = "\n".join(p.parts)
    return f"Title: {p.title.strip()}\n\n{body}" if p.title.strip() else body


async def fetch_page_text(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("That isn't a valid URL.")
    try:  # a literal IP skips DNS, so check it here
        if not is_public(parsed.hostname):
            raise BlockedAddress(f"{parsed.hostname} is a private or local address")
    except ValueError:
        pass
    connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=20),
                                     headers={"User-Agent": USER_AGENT}) as session:
        async with session.get(url, max_redirects=5) as resp:
            resp.raise_for_status()
            kind = resp.headers.get("Content-Type", "")
            if "text/html" not in kind and "text/plain" not in kind:
                raise ValueError(f"Can only summarize web pages, not {kind or 'unknown content'}.")
            raw = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                raw += chunk
                if len(raw) >= MAX_PAGE_BYTES:  # plenty for a summary; stop reading
                    break
            try:
                encoding = resp.get_encoding()
            except RuntimeError:
                encoding = "utf-8"
            text = bytes(raw).decode(encoding or "utf-8", errors="replace")
    return html_to_text(text) if "text/html" in kind else text


def web_tools(llm: LLM, helper_model: str) -> list[Tool]:

    async def web_search(turn: Turn, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "What should I search for?"

        def search():
            from ddgs import DDGS
            for backend in SEARCH_BACKENDS:  # engines are flaky; try a few before giving up
                try:
                    found = list(DDGS().text(query, max_results=3, backend=backend))
                except Exception as e:
                    log.debug("Search backend %s failed: %s", backend, e)
                    continue
                if found:
                    return found
            return []
        results = await asyncio.to_thread(search)
        if not results:
            return ("The web search didn't return anything right now. Tell the user you couldn't look it up — "
                    "don't guess, especially about recent events.")
        return "\n\n".join(f"{r.get('title', '')}: {r.get('body', '')}\n{r.get('href', '')}" for r in results)

    async def youtube_search(turn: Turn, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "What should I search for?"

        def search():
            from ddgs import DDGS
            return list(DDGS().videos(query, max_results=6))
        try:
            results = await asyncio.to_thread(search)
        except Exception as e:
            return f"YouTube search failed: {e}"
        lines = [f"- {r.get('title', 'Untitled')}: {r.get('content') or r.get('url', '')}" for r in results
                 if "youtu" in (r.get("content") or r.get("url") or "")]
        return "YouTube results:\n" + "\n".join(lines[:3]) if lines else "No YouTube results found."

    async def summarize_url(turn: Turn, args: dict) -> str:
        url = str(args.get("url", "")).strip()
        try:
            text = await fetch_page_text(url)
        except BlockedAddress as e:
            return f"Not fetching that: {e}."
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            return f"Couldn't fetch the page: {e}"
        text = "\n".join(line for line in text.splitlines() if line.strip())[:PAGE_TEXT_LIMIT]
        if len(text) < 30:
            return "The page doesn't have enough text to summarize."
        try:
            result = await llm.chat(helper_model, [{"role": "user", "content": SUMMARIZE_PROMPT.format(content=text)}],
                                    temperature=0.3, num_predict=256)
        except Exception as e:
            return f"Fetched the page but couldn't summarize it: {e}"
        return result.text.strip() or "Couldn't generate a summary."

    query = {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "description": "What to search for."}}}
    return [
        Tool("web_search", "Search the web. Use this when you need facts, news or anything you don't know.",
             web_search, query),
        Tool("youtube_search", "Search YouTube for videos (gives links you can pass to download_music).",
             youtube_search, query),
        Tool("summarize_url", "Fetch a web page and summarize it. Use this when someone shares a link.",
             summarize_url, {"type": "object", "required": ["url"], "properties": {
                 "url": {"type": "string", "description": "The full URL, e.g. https://example.com/article"}}}),
    ]
