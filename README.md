# Bong

A Discord chat bot with a protogen personality, powered by a local or cloud LLM through [Ollama](https://ollama.com).

This branch is a ground-up rewrite of Bong: chatting, per-user long-term memory, music in voice channels, and tools, with the code split into small modules. Voice commands, e621, reminders and web search from the old bot are not included yet.

## What she does

- **Chats in character.** Her prompt is [`bong/persona/persona.md`](bong/persona/persona.md): the original prompt word for word, minus the lines for features that aren't back yet, plus permission to swear. Like the original, it's sent as one user message with history newest first. Edit that file to change how she talks.
- **Uses tools.** Right now: `current_time`, `set_alias`, her memory tools and music tools (below).
- **Calls people what they want.** Say "call me Eve" and she saves an alias with `set_alias`. From then on the alias replaces the Discord name in messages, history, replies and name lookups. People set their own; admins can set anyone's, also with `@alias <user> <name>`. Aliases live in `users.json`. It uses the timezone saved in `users.json` (from the old bot) or the time where she's running. Tools live in `bong/tools/`, and a new one is a name, a description and an async function.
- **Knows when she's being talked to.** DMs, pings, replies to her, and any message containing "bong" are answered directly. Anything else in an active channel goes to a small classifier model ([`classifier.md`](bong/persona/classifier.md)) that's told to lean towards "yes".
- **Follows the conversation.** Each channel keeps a rolling history of the last 30 messages, including ones not meant for her. When it fills up, the oldest 10 are summarized in the background (up to 5 summaries are kept). On first use, history is loaded from Discord, skipping automated messages like the old bot's e621 alerts and approval notices.
- **Understands replies.** If you reply to a message, she sees what you replied to.
- **Has permissions.** Per-user tags (`llm`, `llm_fast`, `music`, `admin`) are stored in `bong_user_data/users.json`. The file format is the same as the old bot's, so existing files keep working and unknown fields are preserved.
- **Has a DM approval flow.** When someone without access DMs her, the owner gets buttons: Chat / Chat (no cooldown) / Chat+Music / Admin / Deny.
- **Has a cooldown.** Users without `llm_fast` can get one reply per 60s. The cooldown only applies to messages she's actually answering.
- **Can't mass-ping.** `@everyone`, `@here` and role pings are disabled at the API level.

## Commands (admin only, prefix `@`)

| Command | What it does |
|---|---|
| `@llm` | Toggle Bong in the current channel (saved in `bong_data/active_channels.json`) |
| `@tags list <user>` | Show someone's tags (`<user>` = mention or ID) |
| `@tags add <user> <tag>` | Give a tag: `llm`, `llm_fast`, `music`, `admin` |
| `@tags remove <user> <tag>` | Take a tag away |
| `@alias <user> [name]` | Set what Bong calls someone (no name clears it) |
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
  music/               player.py (queue/loop/shuffle), library.py, downloader.py (yt-dlp), stats.py
  memory/              Memory + MemoryStore interface
    chroma_store.py    Chroma-backed store: recall ranking, dedupe, backups, re-embedding
    embedder.py        Pinned all-MiniLM-L6-v2 (hash-checked)
    migrate.py         Import from the old bot's chroma_db
  tools/               Tool + ToolBox; time.py, people.py (set_alias), memory.py, music.py
  bot/                 The discord.py layer
    client.py          BongBot: builds everything once in setup_hook
    chat.py            on_message pipeline
    admin.py           Admin commands
    approval.py        DM approval buttons
    voice.py           VoiceController: voice connections + playback, one Player per server
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

## Music

Bong plays songs from `bong_data/saved_sounds/` (the same folder and file names as the old bot, so the existing library works) in voice channels. Play counts go to `bong_data/song_stats.json`, also in the old format.

| Tool | What it does |
|---|---|
| `join_voice` / `leave_voice` | Join the asker's voice channel / leave (stops music) |
| `download_music` | Download a YouTube link, or the top YouTube result for a song name, as mp3 (max 20 min) |
| `list_music` / `search_music` | Browse the library |
| `play_audio` | Play a song by name; joins voice if needed; queues it if something's playing |
| `pause_audio` / `resume_audio` / `skip_audio` / `stop_audio` | Playback controls |
| `loop_audio` | One song → repeat it; a queue → repeat the whole queue, in order. With nothing playing, it starts with the next song |
| `music_shuffle_enabled` | Random order from the queue; random library songs when the queue is empty |
| `queue` / `clear_queue` | Show / empty the queue |

- **Permissions.** Music needs the `music` tag (or admin). Anything that changes playback needs the asker in voice, in Bong's channel if she's connected, so nobody can mess with it from outside.
- **What plays next:** the queue first, then a looped song, then shuffle from the library. Stop clears the queue, loop and shuffle. Skipping a looped song moves on instead of replaying it.
- **Now playing** is shown as the voice channel status (🎵 song, 🔁 / 🔁Q for loop, 🔀 for shuffle), and in her prompt.
- **One player per server.** Tools act right away rather than through deferred flags, and the "song ended" callback is moved back onto the event loop with a generation counter. That way, songs that are stopped or skipped can't trigger a second advance.
- **Fixed from the old bot:** a looped queue repeats in order (it used to replay as A B C A B A C), and running out of songs no longer resets loop/shuffle.

Needs ffmpeg installed. Keep `yt-dlp` up to date (`pip install -U yt-dlp`): YouTube changes often.

## Memory

Bong remembers things about people with her own tools. She decides what's worth saving, and it all stays on your machine.

| Tool | What it does |
|---|---|
| `save_memory` | Save a fact with a category, importance 1–5, and who it's about (a display name, resolved to the Discord user) |
| `recall_memories` | Search, either about one person or everything |
| `update_memory` | Correct a memory by its ID (`#3f9a12bc`) |
| `forget_memory` | Delete a memory by its ID |

- **Recall before each reply.** The prompt gets memories about the person talking (the most relevant, plus their most important ones regardless), general facts relevant to the message, and memories about other people if the message is about them.
- **Who can change what.** A memory can be updated or forgotten by the person it's about, by whoever saved it, or by an admin. Nobody can rewrite what Bong knows about someone else.
- **Near-duplicates aren't saved twice.**

### Pinned embedding model

Memories are matched by meaning using **all-MiniLM-L6-v2**, a small model that runs on the CPU next to Bong (no Ollama involved), so it works the same in local and direct mode. It's downloaded once (~80 MB) to `bong_data/embedding_model/`. To keep a model change from breaking memory:

- The model files are checked against SHA-256 hashes in `bong/memory/embedder.py` on every start. If they don't match, Bong refuses to use them.
- Bong computes embeddings herself and Chroma only stores them, so upgrading Chroma can't swap the model. `chromadb` is also pinned to an exact version.
- A fixed probe sentence is embedded at startup and compared with the stored result. If the model ever does change, every memory is re-embedded from its saved text (after writing a backup), instead of the old vectors silently going stale.

Files in `bong_data/memory/`: the Chroma database, `embedding.json` (model + probe), and `memories-backup.json`, a readable export of every memory, updated on each change. Set `BONG_MEMORY=off` to run without memory.

### Importing the old bot's memories

```bash
python -m bong.memory.migrate            # dry run: lists what would be imported
python -m bong.memory.migrate --apply    # import from bong_data/chroma_db
python -m bong.memory.migrate --about 273761843544064000 --about 773961674314219530 --apply
                                         # only memories about these Discord users
```

Texts and metadata (who, category, importance, date) are copied and re-embedded with the pinned model. The old database is read from a temporary copy, so it's never modified. Running it again skips memories that were already imported.

### Other context and tools

For new tools, add a `Tool` in `bong/tools/` and include it in `setup_hook` in `bong/bot/client.py`. For other kinds of context, write a `PromptSection` that returns `{"placeholder": text}`, put `{placeholder}` in `persona.md`, and add the section in `conversation/prompt.py:default_sections`.

## Setup

Requires Python 3.10+. On the first run without a `.env`, Bong creates one with placeholders and stops; fill it in and run again.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Ollama: local or direct

Bong can reach the models in two ways, chosen when you start her:

| | Command | Talks to | Needs | Default models (chat / helper) |
|---|---|---|---|---|
| **Local** (default) | `python -m bong` | Your Ollama server at `OLLAMA_HOST` | Ollama running (signed in for cloud models) | `glm-5.2:cloud` / `gemma4:31b-cloud` |
| **Direct** | `python -m bong --direct` | `https://ollama.com` API | `OLLAMA_API_KEY` | `glm-5.2` / `gemma4:31b` |

Both modes use the same models (glm-5.1 and gemma3 were retired by Ollama in 2026). Local Ollama names cloud models with a cloud tag; the direct API uses the plain names. Run `ollama pull glm-5.2:cloud` and `ollama pull gemma4:31b-cloud` once to check the local names. `BONG_CHAT_MODEL` / `BONG_HELPER_MODEL` override the models in either mode. At startup Bong logs which mode and models she's using, and warns (with the available list) if a model isn't offered.

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
