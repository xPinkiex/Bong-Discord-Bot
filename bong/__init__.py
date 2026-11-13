"""Bong — a text-chat Discord bot with a protogen personality.

Layout:
    bong.config        Settings loaded from the environment / .env
    bong.storage       Atomic JSON files with .bak backups
    bong.users         Per-user permission tags and stats (users.json)
    bong.channels      Channels where Bong is active
    bong.llm           Async Ollama chat client
    bong.persona       Persona + classifier prompt files
    bong.conversation  History, "is this for Bong?" detection, prompt building, replying
    bong.memory        Memory interface (a no-op store for now)
    bong.discord       The discord.py layer: bot, cogs, DM approval

Everything outside bong.discord is plain Python with no discord.py imports,
so it can be unit-tested without a bot connection.
"""

__version__ = "0.2.0"
