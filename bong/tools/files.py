"""Images, text files and reactions.

    describe_image   look at an attached image with the vision model (and save it)
    read_text_file   read an attached text file (and save it)
    list_images / send_image   saved images in bong_data/saved_images
    list_texts / send_text     saved texts in bong_data/saved_texts
    react            react to the message with emojis

Files to send and reactions go into the turn's outbox; the Discord layer
delivers them with the reply.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Awaitable, Callable

import aiohttp

from ..conversation.turn import IMAGE_EXTS, TEXT_EXTS, Attachment, Turn
from ..llm import LLM
from ..music.library import MusicLibrary
from . import Tool

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
TEXT_LIMIT = 8000          # characters of a text file shown to Bong
LIST_LIMIT = 50

Fetch = Callable[[Attachment, int], Awaitable[bytes]]

LABEL_PROMPT = ("Describe this image in exactly 5 words as a short label suitable for a filename. Use only "
                "letters, numbers, and underscores instead of spaces. Examples: Dog_wearing_small_hat_meme, "
                "Orange_cat_sleeping_on_couch, Beautiful_sunset_over_the_ocean. Reply with ONLY the label, nothing else.")


async def fetch_attachment(attachment: Attachment, limit: int) -> bytes:
    """Download an attachment from Discord's CDN, refusing anything over `limit` bytes."""
    if attachment.size and attachment.size > limit:
        raise ValueError(f"'{attachment.filename}' is too big ({attachment.size // 1024} KB).")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(attachment.url) as resp:
            resp.raise_for_status()
            data = await resp.content.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"'{attachment.filename}' is too big.")
    return data


def _safe_stem(text: str, fallback: str) -> str:
    return re.sub(r"[^\w]", "_", text).strip("_")[:50] or fallback


def _unique(library: MusicLibrary, stem: str, suffix: str):
    library.folder.mkdir(parents=True, exist_ok=True)
    path, n = library.folder / f"{stem}{suffix}", 2
    while path.exists():
        path, n = library.folder / f"{stem}_{n}{suffix}", n + 1
    return path


def file_tools(llm: LLM, vision_model: str, images: MusicLibrary, texts: MusicLibrary,
               fetch: Fetch = fetch_attachment) -> list[Tool]:

    def pick(turn: Turn, kind: str, index) -> Attachment | str:
        found = turn.attachments_of(kind)
        label = "image" if kind == "image" else "text file"
        if not found:
            return f"There's no {label} attached to this message."
        try:
            i = int(index or 0)
        except (TypeError, ValueError):
            i = 0
        if not 0 <= i < len(found):
            return f"{label.capitalize()} {i} doesn't exist; there are {len(found)} (0-{len(found) - 1})."
        return found[i]

    async def describe_image(turn: Turn, args: dict) -> str:
        att = pick(turn, "image", args.get("index", 0))
        if isinstance(att, str):
            return att
        question = str(args.get("question") or "Briefly describe this image in 1-2 sentences. Be concise.")
        try:
            data = await fetch(att, MAX_IMAGE_BYTES)
        except Exception as e:
            return f"Couldn't download '{att.filename}': {e}"
        b64 = base64.b64encode(data).decode()
        try:
            result = await llm.chat(vision_model, [{"role": "user", "content": question, "images": [b64]}],
                                    temperature=0.3, num_predict=800)
            label = await llm.chat(vision_model, [{"role": "user", "content": LABEL_PROMPT, "images": [b64]}],
                                   temperature=0.3, num_predict=30)
        except Exception as e:
            log.warning("Vision model failed: %s", e)
            return f"The vision model couldn't look at '{att.filename}': {e}"
        suffix = "." + att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else ".png"
        if suffix not in IMAGE_EXTS:
            suffix = ".png"
        path = _unique(images, _safe_stem(label.text.strip().replace(" ", "_"), "image"), suffix)
        path.write_bytes(data)
        return f"Description of '{att.filename}': {result.text.strip() or 'Could not describe image'}"

    async def read_text_file(turn: Turn, args: dict) -> str:
        att = pick(turn, "text", args.get("index", 0))
        if isinstance(att, str):
            return att
        try:
            data = await fetch(att, MAX_TEXT_BYTES)
        except Exception as e:
            return f"Couldn't download '{att.filename}': {e}"
        text = data.decode("utf-8", errors="replace")
        stem, _, ext = att.filename.rpartition(".")
        suffix = f".{ext.lower()}" if stem and f".{ext.lower()}" in TEXT_EXTS else ".txt"
        _unique(texts, _safe_stem(stem or att.filename, "text_file"), suffix).write_text(text, encoding="utf-8")
        shown = text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + f"\n\n... [truncated, {len(text)} characters total]"
        return f"Contents of '{att.filename}':\n{shown}"

    def lister(library: MusicLibrary, what: str):
        async def handler(turn: Turn, args: dict) -> str:
            items = library.files()
            if not items:
                return f"No saved {what} yet."
            shown = "\n".join(f"- {p.stem}" for p in items[:LIST_LIMIT])
            more = f"\n...and {len(items) - LIST_LIMIT} more." if len(items) > LIST_LIMIT else ""
            return f"{len(items)} saved {what}:\n{shown}{more}"
        return handler

    def sender(library: MusicLibrary, what: str):
        async def handler(turn: Turn, args: dict) -> str:
            name = str(args.get("name", "")).strip()
            path = library.find(name) if name else None
            if path is None:
                return f"No saved {what} matching '{name}'. Use the list tool to see what there is."
            turn.outbox.files.append(path)
            return f"Sending '{path.stem}' with your reply."
        return handler

    async def react(turn: Turn, args: dict) -> str:
        emojis = str(args.get("emojis", "")).split()[:5]
        if not emojis:
            return "Which emoji?"
        turn.outbox.reactions.extend(emojis)
        return f"Reacted with {' '.join(emojis)}"

    name_param = lambda what: {"type": "object", "required": ["name"], "properties": {  # noqa: E731
        "name": {"type": "string", "description": f"The {what}'s name (or part of it) from the list."}}}
    index_param = lambda what: {"type": "integer", "description": f"Which {what}, counting from 0 (default 0)."}  # noqa: E731

    return [
        Tool("describe_image",
             "Look at an image attached to the current message with the vision model: describe it, read text in "
             "it, or answer a question about it. The image is also saved for later.",
             describe_image,
             {"type": "object", "properties": {
                 "index": index_param("image"),
                 "question": {"type": "string", "description": "What to ask about the image. Default: a brief "
                              "description. Use \"Read all the text in this image.\" for text."}}}),
        Tool("read_text_file",
             "Read a text file attached to the current message (it's also saved for later).",
             read_text_file, {"type": "object", "properties": {"index": index_param("text file")}}),
        Tool("list_images", "List saved images you can send.", lister(images, "images")),
        Tool("send_image", "Send a saved image to the chat.", sender(images, "image"), name_param("image")),
        Tool("list_texts", "List saved text files you can send.", lister(texts, "text files")),
        Tool("send_text", "Send a saved text file to the chat.", sender(texts, "text file"), name_param("text file")),
        Tool("react", "React to the user's message with emojis, to show emotion or acknowledge it.", react,
             {"type": "object", "required": ["emojis"], "properties": {
                 "emojis": {"type": "string", "description": "One or more emojis separated by spaces, e.g. \"❤️ 😂\"."}}}),
    ]
