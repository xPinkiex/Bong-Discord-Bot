# bong_music.py + Playback — Music Queue, Loop & Shuffle

The music system spans three files: `bong_music.py` (LangChain @tool definitions), `bong_tools.py` (shared state & `advance_queue`), and `bong.py` (async dispatch & `after_play` callback). ~700 lines total.

## State Variables

All playback state lives in `bong_tools.py` as module-level globals. The sync tool functions set `pending_*` flags and direct state; the async dispatchers read them and perform Discord API calls.

### Pending Flags (set by tools, consumed by dispatchers)

| Flag | Type | Set By | Cleared By |
|---|---|---|---|
| `pending_join_voice` | `int | None` | `join_voice` | `_dispatch_join_voice` |
| `pending_leave_voice` | `bool` | `leave_voice` | `_dispatch_leave_voice` |
| `pending_play_audio` | `str | None` | `play_audio`, `_dispatch_loop_audio`, `_dispatch_skip_audio` | `_dispatch_play_audio` (finally block) |
| `pending_pause` | `bool` | `pause_audio` | `_dispatch_pause_audio` |
| `pending_resume` | `bool` | `resume_audio` | `_dispatch_resume_audio` |
| `pending_stop` | `bool` | `stop_audio` | `_dispatch_stop_audio` |
| `pending_skip` | `bool` | `skip_audio` | `_dispatch_skip_audio` |
| `pending_skip_target` | `str | None` | `skip_audio` | `_dispatch_skip_audio` |
| `pending_start_listening` | `int | None` | `start_listening` | `_dispatch_start_listening` |
| `pending_stop_listening` | `bool` | `stop_listening` | `_dispatch_stop_listening` |
| `pending_send_image` | `str | None` | `send_image` | `dispatch_voice_actions` |
| `pending_send_text` | `str | None` | `send_text` | `dispatch_voice_actions` |
| `pending_reactions` | `list[str]` | `react` | `apply_reactions` |
| `pending_shutdown` | `bool` | `shutdown` | `on_message` (after dispatch) |

### Playback State (direct, not pending)

| Variable | Type | Purpose |
|---|---|---|
| `current_track` | `str | None` | Path of currently playing audio file |
| `song_queue` | `list[str]` | Ordered list of tracks waiting to play (FIFO) |
| `loop_enabled` | `bool` | Whether loop mode is active |
| `loop_track` | `str | None` | Single-track loop target (mutually exclusive with `queue_snapshot`) |
| `queue_snapshot` | `list[str]` | Queue-loop snapshot — the full playlist that repeats |
| `shuffle_enabled` | `bool` | Whether shuffle mode picks random tracks from the library |
| `voice_connected` | `bool` | Whether Bong is in a voice channel (updated per message) |
| `caller_in_voice` | `bool` | Whether the message author is in a voice channel |
| `music_library` | `list[Path]` | Cached list of `.mp3` files in `bong_data/saved_sounds/` |

## Tool Functions

All tools in `bong_music.py` are synchronous LangChain `@tool` functions. They validate permissions, update state, and return result strings. They do **not** call Discord APIs directly.

### Music Tool Permission

All music tools require the `music` tag. Checked by `_check_music()` which returns a denial string or `None`.

### `_requires_voice` Decorator

Applied to tools that need the user in a voice channel (`play_audio`, `pause_audio`, `resume_audio`, `stop_audio`, `skip_audio`, `loop_audio`, `music_shuffle_enabled`, `clear_queue`). Checks `bong_tools.caller_in_voice`.

## playback Flow

```
User message
  → on_message()
    → update_voice_state()        # Sets voice_connected, caller_in_voice
    → run_tool_loop()             # LLM calls sync tools → pending_* flags set
    → dispatch_voice_actions()    # Reads pending_* flags → Discord API calls
```

### Dispatch Order

`dispatch_voice_actions()` runs dispatchers in priority order. Later dispatchers see state changes from earlier ones.

```
_dispatch_join_voice       # Connect to VC
_dispatch_leave_voice      # Disconnect from VC
_dispatch_stop_audio       # Stop playback, clear all state
_dispatch_skip_audio       # Stop current, queue next
_dispatch_loop_audio       # Start deferred loop track if VC is idle
_dispatch_play_audio       # Start playback of pending_play_audio
_dispatch_pause_audio      # Pause
_dispatch_resume_audio     # Resume
_dispatch_start_listening  # Start wake word listener
_dispatch_stop_listening   # Stop wake word listener
```

### Why This Order Matters

- `stop` and `skip` run before `play` so a skip+play in the same chain works correctly (stop current first, then start new).
- `join` runs before `play` so the voice client exists when playback starts.
- `loop` runs before `play` so a deferred loop track can be picked up by `_dispatch_play_audio`.

## Deferred vs Immediate Tools

Most music tools are **deferred** — they set `pending_*` flags and the async dispatcher handles the actual work. Two tools are **immediate** — they modify playback state directly:

| Tool | Mode | What It Does |
|---|---|---|
| `loop_audio` | **Immediate** | Sets `loop_enabled`, `loop_track`, `queue_snapshot` directly |
| `music_shuffle_enabled` | **Immediate** | Sets `shuffle_enabled` directly |
| `clear_queue` | **Immediate** | Clears `song_queue`, resets `loop_enabled`/`queue_snapshot` |
| `play_audio` | **Deferred** | Sets `pending_play_audio` or appends to `song_queue` |
| All others | **Deferred** | Set `pending_*` flags |

This means `loop_audio` can run before `play_audio` in the same tool loop and still work — `loop_enabled` is set immediately, and `_dispatch_play_audio` binds `loop_track` when playback starts (line 680).

### Deferred Loop Binding

When `loop_audio(enabled=True)` is called with nothing playing, `loop_enabled` is set to `True` but `loop_track` remains `None`. This is a **deferred loop** state. When the next song starts playing via `_dispatch_play_audio`, it checks:

```python
if bong_tools.loop_enabled and not bong_tools.loop_track and not bong_tools.queue_snapshot:
    bong_tools.loop_track = track_path
```

This binds the loop to the new track. The song will repeat until loop is disabled.

## after_play Callback

When a song finishes naturally, discord.py calls the `after` callback registered with `vc.play()`. This is implemented by `_make_after_play_callback()`.

### Threading Model

The `after_play` callback runs in a **separate thread** (discord.py's audio decoder thread), not the asyncio event loop. It:
1. Calls `bong_tools.advance_queue()` (synchronous, thread-safe for reads)
2. Calls `vc.play()` directly (discord.py handles internal locking)
3. Uses `asyncio.run_coroutine_threadsafe()` for voice status updates

**No `pending_*` flags are set by `after_play`** — it plays the next track directly. This avoids race conditions with the main event loop.

### Callback Logic

```python
def after_play(err):
    if err: log and continue
    if not vc or not vc.is_connected(): return
    if vc.is_playing() or vc.is_paused(): return
    if pending_play_audio or pending_skip or pending_stop: return  # defer to dispatch
    next_track, _desc = advance_queue()
    if next_track:
        vc.play(FFmpegPCMAudio(next_track, ...), after=after_play)
        set_voice_status(now_playing)
    else:
        set_voice_status(None)  # Nothing left to play
```

The `pending_*` check prevents `after_play` from fighting with `dispatch_voice_actions`. If the main loop has a skip/stop/play queued, `after_play` cedes control.

## advance_queue() — Queue Progression Logic

This is the core state machine for what plays next. Called by both `after_play` and `skip_audio`.

```python
def advance_queue():
    # 1. Queue-loop refill: if queue is empty but we have a snapshot, repopulate
    if not song_queue and loop_enabled and queue_snapshot:
        song_queue = [s for s in queue_snapshot if s != current_track]
        if not song_queue:  # snapshot was all the same song
            song_queue = list(queue_snapshot)

    # 2. Queue: pop next song (random if shuffle, FIFO otherwise)
    if song_queue:
        if shuffle_enabled:
            next = song_queue.pop(random index)
        else:
            next = song_queue.pop(0)
        current_track = next
        return next

    # 3. Track-loop: replay the loop track
    if loop_enabled and loop_track:
        current_track = loop_track
        return loop_track

    # 4. Shuffle (no queue, no loop): pick random from library
    if shuffle_enabled:
        refresh_music_library()
        if library:
            next = random.choice(library)
            current_track = str(next)
            return next

    # 5. Nothing to continue — reset all state
    current_track = None
    loop_enabled = False
    loop_track = None
    queue_snapshot = []
    shuffle_enabled = False
    return None
```

### Loop Modes

| Mode | `loop_enabled` | `loop_track` | `queue_snapshot` | Behavior |
|---|---|---|---|---|
| No loop | `False` | `None` | `[]` | Play queue once, then stop |
| Track loop | `True` | path | `[]` | Repeat single track forever |
| Queue loop | `True` | `None` | `[paths...]` | Repeat entire queue forever |

**Track-loop → Queue-loop promotion**: When a song is added to the queue while in track-loop mode, `play_audio` promotes to queue-loop mode. The loop track becomes the first item in `queue_snapshot`, and `loop_track` is set to `None`. This ensures all queued songs cycle.

### Shuffle Behavior

Shuffle mode (`shuffle_enabled = True`) has two effects:
1. When picking from the queue (`song_queue`), it pops a random index instead of FIFO
2. When the queue is empty and no loop is active (step 4), it picks a random track from the entire music library

Shuffle is independent of loop — both can be active simultaneously.

## Known Bugs & Edge Cases

### Bug 1 (Fixed): Loop doesn't activate when called before play

When `loop_audio(enabled=True)` was called before `play_audio` in the same tool chain, `loop_audio` saw nothing playing and disabled itself with "Nothing is playing." The fix makes `loop_audio` keep `loop_enabled=True` as a deferred state. When the next song starts, `_dispatch_play_audio` binds `loop_track`. (Fixed 2026-06-06)

### Bug 2 (Wontfix): VC commands restart playback from beginning

When voice commands are active, Bong uses `VoiceRecvClient` for bidirectional audio. Disconnecting and reconnecting causes playback to restart. This is inherent to the voice receive architecture — no fix planned.

### Bug 3 (Investigating): Shuffle/loop may reset when adding songs

Under some conditions, adding a song to the queue while shuffle or loop is active may reset the loop/shuffle state. Root cause not yet confirmed. Possible causes: race condition in `after_play` thread, or state reset in `advance_queue`'s "nothing to continue" path.

### Edge Case: Deferred loop with queue

If `loop_audio(enabled=True)` is called with nothing playing and `song_queue` is non-empty, `loop_audio` enters the queue-loop path (lines 283-292) and sets `queue_snapshot` to `[pending_play_audio] + song_queue`. This is correct — the queue becomes a loop.

### Edge Case: Single-song queue loop

When a single song is in track-loop mode and another song is added to the queue, `play_audio` promotes to queue-loop: `queue_snapshot = [loop_track, new_song]`, `loop_track = None`. The two songs now cycle.

## Key Functions

| Function | File:Line | Purpose |
|---|---|---|
| `download_music(query)` | `bong_music.py:38` | Download MP3 from YouTube URL or search |
| `play_audio(index, name)` | `bong_music.py:155` | Play track or add to queue; handles loop promotion |
| `loop_audio(enabled)` | `bong_music.py:269` | Toggle loop mode; supports deferred binding |
| `skip_audio()` | `bong_music.py:250` | Skip to next track via `advance_queue()` |
| `stop_audio()` | `bong_music.py:233` | Stop playback and clear all state |
| `advance_queue()` | `bong_tools.py:62` | Core state machine for next-track selection |
| `reset_pending()` | `bong_tools.py:104` | Clear all pending flags (error recovery only) |
| `_make_after_play_callback()` | `bong.py:537` | Creates `after_play` closure for auto-continue |
| `_dispatch_play_audio()` | `bong.py:668` | Start playback, bind deferred loop |
| `_dispatch_skip_audio()` | `bong.py:769` | Stop current, start next track from `advance_queue` |
| `dispatch_voice_actions()` | `bong.py:832` | Main async dispatcher for all voice/audio actions |