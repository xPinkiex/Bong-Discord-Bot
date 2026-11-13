"""The discord.py layer. Everything Discord-specific lives in this package.

    client.py    BongBot — creates the services and loads the cogs once at startup
    convert.py   discord.Message → Turn / HistoryEntry
    chat.py      ChatCog — the on_message pipeline
    admin.py     AdminCog — @llm, @tags, @poweroff, @debug
    approval.py  DM access requests sent to the owner with buttons
"""
