"""set_alias — let people choose what Bong calls them.

The alias replaces the Discord display name everywhere Bong sees it: the
current message, chat history, replies and memories. Anyone can set or clear
their own alias; only admins can set someone else's.
"""

from __future__ import annotations

from typing import Callable

from ..conversation.turn import Turn
from ..users import UserStore
from . import Tool

NameResolver = Callable[[str, Turn], "tuple[int, str] | None"]

SELF_WORDS = {"", "me", "myself", "i", "my", "mine"}


def set_alias_tool(users: UserStore, resolver: NameResolver) -> Tool:
    async def handler(turn: Turn, args: dict) -> str:
        alias = " ".join(str(args.get("alias", "") or "").split())
        who = str(args.get("about", "") or "").strip().lstrip("@")

        if who.lower() in SELF_WORDS or who.lower() == turn.author_name.lower():
            target_id, target_name = turn.author_id, turn.author_name
        else:
            found = resolver(who, turn)
            if not found:
                return f'Couldn\'t find anyone called "{who}".'
            target_id, target_name = found
            if target_id != turn.author_id and not users.is_admin(turn.author_id):
                return (f"{turn.author_name} can't choose what {target_name} is called — "
                        f"people set their own name, or an admin can.")

        if len(alias) > 32:
            return "That name is too long (32 characters max)."
        users.set_alias(target_id, alias or None)
        if not alias:
            return f"Cleared the alias for {target_name}; you'll use their Discord name again."
        return f"From now on, call {target_name} \"{alias}\"."

    return Tool(
        name="set_alias",
        description="Set the name you call someone, instead of their Discord name. Use this when someone tells "
                    "you what they want to be called (e.g. \"call me Eve\"). An empty alias goes back to their "
                    "Discord name. People can only set their own name unless an admin asks.",
        handler=handler,
        parameters={"type": "object", "required": ["alias"], "properties": {
            "alias": {"type": "string", "description": "The name to use, e.g. \"Eve\". Empty to clear."},
            "about": {"type": "string",
                      "description": "Who it's for. Leave empty for the person talking to you."},
        }},
    )
