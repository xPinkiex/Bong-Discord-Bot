"""Queue / loop / shuffle for one server. Pure state, no Discord — the voice
controller asks it what to play and tells it what happened.

Behaviour (same as the old bot, minus its bugs):

- Playing while something is already on adds to the queue.
- Loop has three states:
    off        play the queue once, then stop
    track      repeat the current song
    queue      repeat the whole playlist (current song + queue), in order
  Turning loop on with nothing playing is remembered and binds to the next
  song that starts. Queueing a song while a single track loops turns it into
  a queue loop of both.
- Shuffle picks the next queued song at random; with an empty queue and no
  loop it plays random songs from the library.
- What plays next: queue first, then track loop, then shuffle from library.
- Stop clears everything (queue, loop, shuffle). Clearing the queue keeps the
  current song but ends a queue loop.

Fixed from the old bot: a looped queue now repeats in order (it used to skip
the song that had just played when refilling), and running out of songs no
longer silently resets loop/shuffle settings.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class LoopMode(str, Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


@dataclass
class Player:
    library: Callable[[], list[str]] = list      # every song, for shuffle-from-library
    rng: random.Random = field(default_factory=random.Random)

    current: str | None = None
    queue: list[str] = field(default_factory=list)
    loop: LoopMode = LoopMode.OFF
    loop_pending: bool = False                   # loop asked for while nothing played
    playlist: list[str] = field(default_factory=list)  # what a queue loop repeats
    shuffle: bool = False

    # --- requests ---------------------------------------------------------------

    def request(self, track: str) -> int:
        """Play a track now if idle, else queue it. Returns 0 if it starts now, else its queue position."""
        if self.current is None:
            self._start(track)
            return 0
        self.queue.append(track)
        if self.loop is LoopMode.TRACK:
            self.loop, self.playlist = LoopMode.QUEUE, [self.current, *self.queue]
        elif self.loop is LoopMode.QUEUE:
            self.playlist.append(track)
        return len(self.queue)

    def set_loop(self, on: bool) -> LoopMode | None:
        """Turn loop on/off. Returns the new mode, or None if it will bind to the next song."""
        self.loop_pending = False
        if not on:
            self.loop, self.playlist = LoopMode.OFF, []
            return LoopMode.OFF
        if self.current and self.queue:
            self.loop, self.playlist = LoopMode.QUEUE, [self.current, *self.queue]
        elif self.current:
            self.loop, self.playlist = LoopMode.TRACK, []
        else:
            self.loop, self.loop_pending = LoopMode.OFF, True
            return None
        return self.loop

    def set_shuffle(self, on: bool) -> None:
        self.shuffle = on

    def clear_queue(self) -> int:
        count = len(self.queue)
        self.queue.clear()
        if self.loop is LoopMode.QUEUE:
            self.loop, self.playlist = LoopMode.OFF, []
        return count

    def stop(self) -> None:
        self.current = None
        self.queue.clear()
        self.loop, self.loop_pending, self.playlist = LoopMode.OFF, False, []
        self.shuffle = False

    # --- progression --------------------------------------------------------------

    def next(self) -> str | None:
        """The current song ended or was skipped: pick and start the next one (None = nothing left)."""
        if not self.queue and self.loop is LoopMode.QUEUE and self.playlist:
            self.queue = list(self.playlist)
        if self.queue:
            index = self.rng.randrange(len(self.queue)) if self.shuffle else 0
            self._start(self.queue.pop(index))
            return self.current
        if self.loop is LoopMode.TRACK and self.current:
            return self.current
        if self.shuffle:
            songs = self.library()
            if songs:
                self._start(self.rng.choice(songs))
                return self.current
        self.current = None
        return None

    def _start(self, track: str) -> None:
        self.current = track
        if self.loop_pending:
            self.loop_pending = False
            self.loop = LoopMode.TRACK

    # --- display -------------------------------------------------------------------

    @property
    def loop_label(self) -> str:
        if self.loop_pending:
            return "loop: starts with the next song"
        return {LoopMode.OFF: "", LoopMode.TRACK: "loop: track",
                LoopMode.QUEUE: f"loop: queue ({len(self.playlist)} songs)"}[self.loop]
