"""Voice connections and playback, one Player per server.

Tools call these methods directly and get back a sentence describing what
happened — there are no "pending" flags to dispatch later. When a song ends,
discord.py calls back from its audio thread; we hop back onto the event loop
before touching any state, and a per-server generation number makes callbacks
from songs we stopped or skipped ourselves harmless.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from ..music.library import MusicLibrary
from ..music.player import LoopMode, Player
from ..music.stats import SongStats

if TYPE_CHECKING:
    from .client import BongBot

log = logging.getLogger(__name__)

VOLUME = 0.3


@dataclass
class GuildAudio:
    player: Player
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0


def song_name(track: str | None) -> str:
    return Path(track).stem if track else ""


class VoiceController:
    def __init__(self, bot: "BongBot", library: MusicLibrary, stats: SongStats):
        self.bot = bot
        self.library = library
        self.stats = stats
        self._guilds: dict[int, GuildAudio] = {}

    # --- lookups --------------------------------------------------------------------

    def audio(self, guild_id: int) -> GuildAudio:
        if guild_id not in self._guilds:
            self._guilds[guild_id] = GuildAudio(Player(library=self.library.paths))
        return self._guilds[guild_id]

    def voice_client(self, guild_id: int) -> discord.VoiceClient | None:
        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None
        return vc if vc and vc.is_connected() else None

    def member_channel(self, guild_id: int, user_id: int):
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        return member.voice.channel if member and member.voice else None

    def check_caller(self, guild_id: int, user_id: int) -> str | None:
        """Playback controls need the person in voice — in Bong's channel if she's connected."""
        channel = self.member_channel(guild_id, user_id)
        if channel is None:
            return ("They need to be in a voice channel to control music. "
                    "(This might be someone trolling from outside the voice channel.)")
        vc = self.voice_client(guild_id)
        if vc and vc.channel.id != channel.id:
            return f"They're in a different voice channel than you (you're in '{vc.channel.name}')."
        return None

    # --- connecting -------------------------------------------------------------------

    async def join(self, guild_id: int, user_id: int) -> str:
        channel = self.member_channel(guild_id, user_id)
        if channel is None:
            return "They aren't in a voice channel, so there's nothing to join."
        vc = self.voice_client(guild_id)
        try:
            if vc and vc.channel.id == channel.id:
                return f"Already in '{channel.name}'."
            if vc:
                await vc.move_to(channel)
            else:
                await channel.connect(self_deaf=True)
        except Exception as e:
            log.exception("Could not join voice channel %s", channel)
            return f"Couldn't join '{channel.name}': {e}"
        await self._update_status(guild_id)
        return f"Joined '{channel.name}'."

    async def leave(self, guild_id: int) -> str:
        vc = self.voice_client(guild_id)
        if vc is None:
            return "Not in a voice channel."
        audio = self.audio(guild_id)
        async with audio.lock:
            audio.player.stop()
            audio.generation += 1
            await self._set_status(vc, None)
            name = vc.channel.name
            await vc.disconnect()
        return f"Left '{name}'."

    # --- playback ----------------------------------------------------------------------

    async def play(self, guild_id: int, user_id: int, track: Path) -> str:
        if self.voice_client(guild_id) is None:
            joined = await self.join(guild_id, user_id)
            if not joined.startswith("Joined"):
                return joined
        audio = self.audio(guild_id)
        async with audio.lock:
            position = audio.player.request(str(track))
            if position == 0:
                self._start(guild_id, audio)
                note = f" ({audio.player.loop_label})" if audio.player.loop is not LoopMode.OFF else ""
                return f"Playing '{track.stem}'{note}."
            note = ""
            if audio.player.loop is LoopMode.QUEUE:
                note = f" Queue loop is on ({len(audio.player.playlist)} songs)."
            return f"Added '{track.stem}' to the queue (position {position}).{note}"

    async def pause(self, guild_id: int) -> str:
        vc = self.voice_client(guild_id)
        if vc and vc.is_playing():
            vc.pause()
            return f"Paused '{song_name(self.audio(guild_id).player.current)}'."
        return "Nothing is playing to pause."

    async def resume(self, guild_id: int) -> str:
        vc = self.voice_client(guild_id)
        if vc and vc.is_paused():
            vc.resume()
            return f"Resumed '{song_name(self.audio(guild_id).player.current)}'."
        return "Nothing is paused."

    async def skip(self, guild_id: int) -> str:
        vc = self.voice_client(guild_id)
        audio = self.audio(guild_id)
        async with audio.lock:
            if vc is None or audio.player.current is None:
                return "Nothing is playing to skip."
            skipped = song_name(audio.player.current)
            if audio.player.loop is LoopMode.TRACK:
                audio.player.set_loop(False)  # skipping a looped song means "move on"
            nxt = audio.player.next()
            if nxt is None:
                audio.generation += 1
                vc.stop()
                await self._update_status(guild_id)
                return f"Skipped '{skipped}'. The queue is empty, so nothing else is playing."
            self._start(guild_id, audio)
            return f"Skipped '{skipped}'. Now playing '{song_name(nxt)}'."

    async def stop(self, guild_id: int) -> str:
        vc = self.voice_client(guild_id)
        audio = self.audio(guild_id)
        async with audio.lock:
            was = audio.player.current
            audio.player.stop()
            audio.generation += 1
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
            await self._update_status(guild_id)
        if not was:
            return "Nothing was playing. Cleared the queue, loop and shuffle anyway."
        return f"Stopped '{song_name(was)}' and cleared the queue, loop and shuffle."

    async def set_loop(self, guild_id: int, on: bool) -> str:
        audio = self.audio(guild_id)
        async with audio.lock:
            mode = audio.player.set_loop(on)
        await self._update_status(guild_id)
        if mode is None:
            return "Loop is on. It will start with the next song that plays."
        if mode is LoopMode.OFF:
            return "Loop disabled."
        if mode is LoopMode.TRACK:
            return f"Looping '{song_name(audio.player.current)}'."
        return f"Looping the queue ({len(audio.player.playlist)} songs)."

    async def set_shuffle(self, guild_id: int, on: bool) -> str:
        audio = self.audio(guild_id)
        audio.player.set_shuffle(on)
        await self._update_status(guild_id)
        return f"Shuffle {'enabled' if on else 'disabled'}."

    async def clear_queue(self, guild_id: int) -> str:
        audio = self.audio(guild_id)
        async with audio.lock:
            count = audio.player.clear_queue()
        await self._update_status(guild_id)
        return f"Cleared {count} song(s) from the queue." if count else "The queue is already empty."

    def describe_queue(self, guild_id: int) -> str:
        p = self.audio(guild_id).player
        vc = self.voice_client(guild_id)
        lines = []
        if p.current:
            paused = " (paused)" if vc and vc.is_paused() else ""
            lines.append(f"Now playing: {song_name(p.current)}{paused}")
        else:
            lines.append("Nothing is currently playing.")
        lines += [f"  {i}. {song_name(t)}" for i, t in enumerate(p.queue, 1)] or ["Queue is empty."]
        flags = [x for x in (p.loop_label, "shuffle on" if p.shuffle else "") if x]
        if flags:
            lines.append(f"({', '.join(flags)})")
        return "\n".join(lines)

    def status_for_prompt(self, guild_id: int | None) -> str:
        """The {voice_status} line in Bong's prompt (same wording as the old bot)."""
        if guild_id is None:
            return "DM — no voice channels available."
        vc = self.voice_client(guild_id)
        if vc is None:
            return "Not in any voice channel."
        text = f"Connected to voice channel '{vc.channel.name}'. Do NOT use join_voice — you are already in voice."
        p = self.audio(guild_id).player
        if p.current:
            text += f"\nCurrently playing '{song_name(p.current)}'."
            if p.queue:
                text += f" {len(p.queue)} song(s) queued."
        return text

    # --- internals ---------------------------------------------------------------------

    def _start(self, guild_id: int, audio: GuildAudio) -> None:
        """Start audio.player.current on the voice client. Call with audio.lock held."""
        vc = self.voice_client(guild_id)
        track = audio.player.current
        if vc is None or track is None:
            return
        audio.generation += 1
        generation = audio.generation
        if vc.is_playing() or vc.is_paused():
            vc.stop()  # its callback carries an old generation, so it's ignored
        loop = asyncio.get_running_loop()

        def after(error: Exception | None) -> None:  # runs on discord.py's audio thread
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._finished(guild_id, generation, error)))

        try:
            vc.play(discord.FFmpegPCMAudio(track, options=f"-filter:a volume={VOLUME}"), after=after)
        except Exception:
            log.exception("Could not start %s", track)
            return
        self.stats.played(song_name(track))
        asyncio.ensure_future(self._update_status(guild_id))

    async def _finished(self, guild_id: int, generation: int, error: Exception | None) -> None:
        if error:
            log.warning("Playback error in guild %s: %s", guild_id, error)
        audio = self.audio(guild_id)
        async with audio.lock:
            if generation != audio.generation:
                return  # we stopped or replaced this song ourselves
            if audio.player.next() is not None:
                self._start(guild_id, audio)
                return
        await self._update_status(guild_id)

    async def _update_status(self, guild_id: int) -> None:
        vc = self.voice_client(guild_id)
        if vc is None:
            return
        p = self.audio(guild_id).player
        status = None
        if p.current:
            status = f"🎵 {song_name(p.current)}"
            if p.loop is LoopMode.QUEUE:
                status += " 🔁Q"
            elif p.loop is LoopMode.TRACK or p.loop_pending:
                status += " 🔁"
            if p.shuffle:
                status += " 🔀"
        await self._set_status(vc, status)

    @staticmethod
    async def _set_status(vc, status: str | None) -> None:
        try:
            await vc.channel.edit(status=status)
        except discord.Forbidden:
            pass
        except Exception as e:
            log.debug("Could not set voice status: %s", e)

    async def on_voice_state_update(self, member, before, after) -> None:
        """If Bong gets disconnected (kicked, channel deleted), reset that server's player."""
        if self.bot.user and member.id == self.bot.user.id and before.channel and after.channel is None:
            audio = self.audio(member.guild.id)
            audio.player.stop()
            audio.generation += 1

    async def shutdown(self) -> None:
        for guild in self.bot.guilds:
            vc = guild.voice_client
            if vc:
                await self._set_status(vc, None)
                await vc.disconnect(force=True)
