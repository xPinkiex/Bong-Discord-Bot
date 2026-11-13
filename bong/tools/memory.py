"""Tools Bong uses to manage her long-term memory: save, recall, update, forget.

Who can change what: a memory can be updated or forgotten by the person it's
about, by whoever's message saved it, or by an admin. So nobody can rewrite
or delete what Bong knows about someone else.
"""

from __future__ import annotations

from typing import Callable

from ..conversation.turn import Turn
from ..memory import CATEGORIES, GENERAL, Memory
from ..memory.chroma_store import ChromaMemoryStore
from . import Tool

# Turns a display name into (user_id, display name), or None if nobody matches.
NameResolver = Callable[[str, Turn], "tuple[int, str] | None"]

SELF_WORDS = {"me", "myself", "i", "my", "mine"}


def _resolve(name: str, turn: Turn, resolver: NameResolver) -> tuple[int, str] | None:
    name = name.strip().lstrip("@")
    if not name:
        return None
    if name.lower() in SELF_WORDS or name.lower() == turn.author_name.lower():
        return turn.author_id, turn.author_name
    return resolver(name, turn)


def can_edit(memory: Memory, turn: Turn, is_admin: Callable[[int], bool]) -> bool:
    return (is_admin(turn.author_id)
            or memory.about_user_id == turn.author_id
            or (memory.saved_by == turn.author_id and memory.saved_by != 0))


def memory_tools(store: ChromaMemoryStore, resolver: NameResolver, is_admin: Callable[[int], bool]) -> list[Tool]:

    async def save_memory(turn: Turn, args: dict) -> str:
        fact = str(args.get("fact", "")).strip()
        if not fact:
            return "Nothing to save: 'fact' was empty."
        category = str(args.get("category", "fact")).lower().strip()
        try:
            importance = int(args.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3
        about = str(args.get("about", "") or "")
        warning = ""
        who = _resolve(about, turn, resolver)
        if who:
            about_id, about_name = who
        else:
            about_id, about_name = GENERAL, ""
            if about.strip():
                warning = (f' Couldn\'t find anyone called "{about.strip()}", so it was saved as a general fact. '
                           f"Ask for their exact display name if it should be about them.")
        memory, created = await store.add(fact, about_user_id=about_id, about_name=about_name, category=category,
                                          importance=importance, saved_by=turn.author_id)
        if not created:
            return f"Already remembered something very similar: {memory.for_prompt()}{warning}"
        return f"Remembered: {memory.for_prompt()}{warning}"

    async def recall_memories(turn: Turn, args: dict) -> str:
        query = str(args.get("query", "")).strip() or turn.content
        about = str(args.get("about", "") or "")
        if about.strip():
            who = _resolve(about, turn, resolver)
            if not who:
                return f'Couldn\'t find anyone called "{about.strip()}".'
            results = await store.search(query, about_user_id=who[0], limit=8, min_similarity=0.0)
            if not results:
                return f"No memories about {who[1]} yet."
        else:
            results = await store.search(query, limit=8, min_similarity=0.25)
            if not results:
                return "No relevant memories found."
        return "\n".join(f"- {m.for_prompt()}" for m, _ in results)

    async def update_memory(turn: Turn, args: dict) -> str:
        memory_id, fact = str(args.get("memory_id", "")).lstrip("#").strip(), str(args.get("fact", "")).strip()
        if not memory_id or not fact:
            return "Need both memory_id and the corrected fact."
        memory = await store.get(memory_id)
        if memory is None:
            return f"No memory with ID #{memory_id}."
        if not can_edit(memory, turn, is_admin):
            return (f"{turn.author_name} can't change that memory — only the person it's about, "
                    f"whoever saved it, or an admin can.")
        updated = await store.update(memory_id, fact)
        return f"Updated: {memory.text} -> {updated.text}"

    async def forget_memory(turn: Turn, args: dict) -> str:
        memory_id = str(args.get("memory_id", "")).lstrip("#").strip()
        if not memory_id:
            return "Need the memory_id to forget (find it with recall_memories)."
        memory = await store.get(memory_id)
        if memory is None:
            return f"No memory with ID #{memory_id}."
        if not can_edit(memory, turn, is_admin):
            return (f"{turn.author_name} can't make you forget that — only the person it's about, "
                    f"whoever saved it, or an admin can.")
        await store.delete(memory_id)
        return f"Forgotten: {memory.text}"

    about_param = {"type": "string",
                   "description": "Display name of WHO the fact is about. Leave empty for general facts "
                                  "not about anyone. Use display names, never Discord IDs or <@ID> mentions."}
    return [
        Tool(
            name="save_memory",
            description="Save an important fact to long-term memory: things about people, preferences, inside "
                        "jokes, or anything worth recalling later. Be selective. Write facts normally, not in "
                        "third person, and never refer to yourself as Bong in them (write \"Eve loves dubstep\", "
                        "not \"Bong remembers that Eve loves dubstep\").",
            handler=save_memory,
            parameters={"type": "object", "required": ["fact", "category", "importance"], "properties": {
                "fact": {"type": "string", "description": "A short fact using display names, e.g. \"Eve loves dubstep\"."},
                "category": {"type": "string", "enum": list(CATEGORIES),
                             "description": "preference (likes/dislikes), fact, relationship, inside_joke, "
                                            "or instruction (things to always remember or do)."},
                "importance": {"type": "integer", "minimum": 1, "maximum": 5,
                               "description": "1 = trivial, 3 = normal, 5 = critical (allergies, core identity)."},
                "about": about_param,
            }},
        ),
        Tool(
            name="recall_memories",
            description="Search long-term memory. Pass a display name in 'about' to look up a specific person, "
                        "or leave it empty for a general search. Results include each memory's ID.",
            handler=recall_memories,
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "What to look for, e.g. \"music taste\"."},
                "about": about_param,
            }},
        ),
        Tool(
            name="update_memory",
            description="Correct or update a saved memory, by its ID (like #3f9a12bc). Use this when someone "
                        "corrects something you remembered or a fact has changed.",
            handler=update_memory,
            parameters={"type": "object", "required": ["memory_id", "fact"], "properties": {
                "memory_id": {"type": "string", "description": "The memory's ID, e.g. 3f9a12bc."},
                "fact": {"type": "string", "description": "The corrected fact."},
            }},
        ),
        Tool(
            name="forget_memory",
            description="Delete a memory by its ID, when someone asks you to forget something or it's wrong. "
                        "Use recall_memories first if you don't see the ID.",
            handler=forget_memory,
            parameters={"type": "object", "required": ["memory_id"], "properties": {
                "memory_id": {"type": "string", "description": "The memory's ID, e.g. 3f9a12bc."},
            }},
        ),
    ]
