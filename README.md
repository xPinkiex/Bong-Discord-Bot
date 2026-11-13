# Bong

A Discord chat bot with a protogen personality, powered by a local or cloud LLM through [Ollama](https://ollama.com).

This branch is a ground-up rewrite of the text side of Bong. It focuses on chatting, keeps the code split into small modules, and has a memory interface ready for per-user memories. Music, voice, e621, reminders and the LLM tool loop from the old bot are not included yet.

## What she does

- **Chats in character.** Her prompt is [`bong/persona/persona.md`](bong/persona/persona.md): the original prompt word for word, minus the lines for features that aren't back yet, plus permission to swear. Like the original, it's sent as one user message with history newest first. Edit that file to change how she talks.
- **Uses tools.** Right now there's one, `current_time`. It uses the timezone saved in `users.json` (from the old bot) or the time where she's running. Tools live in `bong/tools/`, and a new one is a name, a description and an async function.
- **Knows when she's being talked to.** DMs, pings, replies to her, and any message containing "bong" are answered directly. Anything else in an active channel goes to a small classifier model ([`classifier.md`](bong/persona/classifier.md)) that's told to lean towards "yes".
- **Follows the conversation.** Each channel keeps a rolling history of the last 30 messages, including ones not meant for her. When it fills up, the oldest 10 are summarized in the background (up to 5 summaries are kept). On first use, history is loaded from Discord, skipping automated messages like the old bot's e621 alerts and approval notices.
- **Understands replies.** If you reply to a message, she sees what you replied to.
- **Has permissions.** Per-user tags (`llm`, `llm_fast`, `admin`) are stored in `bong_user_data/users.json`. The file format is the same as the old bot's, so existing files keep working and unknown fields are preserved.
- **Has a DM approval flow.** When someone without access DMs her, the owner gets buttons: Chat / Chat (no cooldown) / Admin / Deny.
- **Has a cooldown.** Users without `llm_fast` can get one reply per 60s. The cooldown only applies to messages she's actually answering.
- **Can't mass-ping.** `@everyone`, `@here` and role pings are disabled at the API level.

## Commands (admin only, prefix `@`)

| Command | What it does |
|---|---|
| `@llm` | Toggle Bong in the current channel (saved in `bong_data/active_channels.json`) |
| `@tags list <user>` | Show someone's tags (`<user>` = mention or ID) |
| `@tags add <user> <tag>` | Give a tag: `llm`, `llm_fast`, `admin` |
| `@tags remove <user> <tag>` | Take a tag away |
| `@debug [on/off]` | Toggle debug logging (logs full prompts) |
| `@poweroff` | Shut down |

## Layout

```
bong/
  __main__.py          python -m bong [-d]
  config.py            Settings from .env (token, owner, Ollama mode/host/key, models, limits)
  storage.py           Atomic JSON files with .bak backups
  users.py             Permission tags + token stats (users.json)
  channels.py          Active channel set
  llm.py               Async Ollama client (aiohttp), local or ollama.com with API key
  textutil.py          Splitting replies at Discord's 2000-char limit
  persona/             persona.md, classifier.md, template filling
  conversation/        No discord.py imports — all unit-testable
    turn.py            Turn: one incoming message as plain data
    history.py         Per-channel history + background summaries
    detector.py        "Is this for Bong?"
    prompt.py          Fills persona.md from pluggable sections
    bong.py            Bong: is_for_bong / record / respond (with tool loop), Cooldown
  memory/              Memory + MemoryStore interface, NullMemoryStore
  tools/               Tool + ToolBox; time.py (current_time)
  bot/                 The discord.py layer
    client.py          BongBot: builds everything once in setup_hook
    chat.py            on_message pipeline
    admin.py           Admin commands
    approval.py        DM approval buttons
    convert.py         discord.Message → Turn / HistoryEntry
```

The message flow:

```
on_message (bot/chat.py)
  → ignore bots, duplicate events, real commands, inactive channels
  → unknown user in DM → approval request to owner
  → build a Turn (who, where, text, reply target)
  → bong.is_for_bong(turn)?  no → bong.record(turn) as context
  → cooldown check (unless llm_fast/admin)
  → bong.respond(turn): sections fill persona.md → chat model ⇄ tools (max 5 rounds) → reply
  → send (split at 2000 chars), add token count to the user
```

Who is talking travels in the `Turn` object instead of in global variables. Replies are handled one at a time per channel, so history stays in order, and different channels don't block each other.

## Adding memories later

`bong/memory/__init__.py` defines the interface:

```python
class MemoryStore(Protocol):
    async def recall(self, turn: Turn, limit: int = 8) -> list[Memory]: ...
    async def observe(self, turn: Turn, reply: str) -> None: ...
```

- `recall` runs before every reply. Whatever it returns fills `{memories}` in the prompt. `turn.author_id` tells you whose memories to fetch.
- `observe` runs after every reply, so a store can pull out facts to save.

To turn memories on, write a class with those two methods (backed by JSON, ChromaDB or anything else) and pass it in: `BongBot(settings, memory=MyStore(...))` in `bong/__main__.py`. Nothing else changes. Memory tools (save/recall/forget) can be added as `Tool`s in `bong/tools/`. For other kinds of context, write a `PromptSection` that returns `{"placeholder": text}`, put `{placeholder}` in `persona.md`, and add the section in `conversation/prompt.py:default_sections`.

## Setup

Requires Python 3.10+.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Ollama: local or direct

Bong can reach the models in two ways, chosen when you start her:

| | Command | Talks to | Needs | Default models (chat / helper) |
|---|---|---|---|---|
| **Local** (default) | `python -m bong` | Your Ollama server at `OLLAMA_HOST` | Ollama running (signed in for `:cloud` models) | `glm-5.1:cloud` / `gemma3:12b-cloud` |
| **Direct** | `python -m bong --direct` | `https://ollama.com` API | `OLLAMA_API_KEY` | `glm-5.2` / `gemma4:31b` |

The direct API has its own model list, without the `:cloud` names, which is why the defaults differ. `BONG_CHAT_MODEL` / `BONG_HELPER_MODEL` override the models in either mode. At startup Bong logs which mode and models she's using, and warns (with the available list) if a model isn't offered.

### `.env`

Put this in the project root:

```
DISCORD_TOKEN=...                       # required
OWNER_ID=273761843544064000             # optional, gets approval requests + always admin

# Local mode
OLLAMA_HOST=127.0.0.1:11434             # optional, IP:port of your Ollama server

# Direct mode (--direct)
OLLAMA_API_KEY=...                      # from ollama.com/settings/keys

BONG_CHAT_MODEL=...                     # optional, Bong's replies
BONG_HELPER_MODEL=...                   # optional, classifier + summaries
BONG_COOLDOWN_SECONDS=60                # optional
```

The bot needs the **Message Content** and **Server Members** intents enabled in the Discord developer portal.

```bash
python -m bong                 # local Ollama
python -m bong --direct        # ollama.com with OLLAMA_API_KEY
python -m bong -d              # either, with debug logging
```

Logs go to the console and `bong_data/logs/`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the conversation core, storage and permissions, plus the `on_message` handler driven with mock Discord objects. They don't need Discord or Ollama.
