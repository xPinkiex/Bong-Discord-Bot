"""Bong's music tools. They need the `music` tag, a server (not DMs), and — for
anything that changes playback — the person asking to be in voice with Bong.

Names match the old bot's tools so her original prompt still fits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

from ..conversation.turn import Turn
from ..music import downloader
from ..music.library import MusicLibrary
from . import Tool

if TYPE_CHECKING:
    from ..bot.voice import VoiceController

NO_PARAMS = {"type": "object", "properties": {}}
LIST_LIMIT = 50


def music_tools(voice: "VoiceController", library: MusicLibrary, has_music: Callable[[int], bool]) -> list[Tool]:

    def gate(handler, needs_voice: bool = False):
        async def wrapped(turn: Turn, args: dict) -> str:
            if not has_music(turn.author_id):
                return (f"{turn.author_name} doesn't have permission to use music. "
                        f"An admin can give them the music tag.")
            if turn.guild_id is None:
                return "Music only works in servers, not DMs."
            if needs_voice:
                problem = voice.check_caller(turn.guild_id, turn.author_id)
                if problem:
                    return problem
            return await handler(turn, args)
        return wrapped

    async def join_voice(turn, args):
        return await voice.join(turn.guild_id, turn.author_id)

    async def leave_voice(turn, args):
        return await voice.leave(turn.guild_id)

    async def download_music(turn, args):
        query = str(args.get("query", "")).strip()
        if not query:
            return "Need a song name or YouTube link."
        if not downloader.is_youtube_url(query):
            have = library.find(query)
            if have:
                return f"'{have.stem}' is already in the library. Use play_audio to play it."
        try:
            got = await asyncio.to_thread(downloader.download, query, library)
        except downloader.DownloadError as e:
            return f"Download failed: {e}"
        if got.already_had:
            return f"'{got.title}' is already in the library. Use play_audio to play it."
        return f"Downloaded '{got.title}'. Use play_audio to play it."

    async def list_music(turn, args):
        songs = library.songs()
        if not songs:
            return "The music library is empty. Use download_music to get songs."
        shown = "\n".join(f"- {p.stem}" for p in songs[:LIST_LIMIT])
        more = f"\n...and {len(songs) - LIST_LIMIT} more (use search_music)." if len(songs) > LIST_LIMIT else ""
        return f"{len(songs)} songs:\n{shown}{more}"

    async def search_music(turn, args):
        query = str(args.get("query", "")).strip()
        hits = library.search(query)
        if not hits:
            return f"No songs matching '{query}'. Use download_music to get it."
        return "Matches:\n" + "\n".join(f"- {p.stem}" for p in hits)

    async def play_audio(turn, args):
        name = str(args.get("name", "")).strip()
        if not name:
            return "Which song? Pass its name (use search_music to find it)."
        track = library.find(name)
        if track is None:
            return f"No song matching '{name}' in the library. Use download_music to get it, then play_audio."
        return await voice.play(turn.guild_id, turn.author_id, track)

    async def pause_audio(turn, args):
        return await voice.pause(turn.guild_id)

    async def resume_audio(turn, args):
        return await voice.resume(turn.guild_id)

    async def skip_audio(turn, args):
        return await voice.skip(turn.guild_id)

    async def stop_audio(turn, args):
        return await voice.stop(turn.guild_id)

    async def loop_audio(turn, args):
        return await voice.set_loop(turn.guild_id, bool(args.get("enabled", True)))

    async def music_shuffle_enabled(turn, args):
        return await voice.set_shuffle(turn.guild_id, bool(args.get("enabled", True)))

    async def queue(turn, args):
        return voice.describe_queue(turn.guild_id)

    async def clear_queue(turn, args):
        return await voice.clear_queue(turn.guild_id)

    def enabled_param(what):
        return {"type": "object", "required": ["enabled"], "properties": {
            "enabled": {"type": "boolean", "description": f"true to turn {what} on, false to turn it off."}}}

    return [
        Tool("join_voice", "Join the voice channel the user is in.", gate(join_voice)),
        Tool("leave_voice", "Leave the voice channel (stops music and clears the queue).", gate(leave_voice, True)),
        Tool("download_music",
             "Download a song as mp3 from YouTube, from a YouTube link or a song name (searches YouTube). "
             "Check the library with search_music first; if the song is already there, use play_audio instead.",
             gate(download_music),
             {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "A YouTube URL or a song name, e.g. \"Jersey by Mayday Parade\"."}}}),
        Tool("list_music", "List the downloaded songs.", gate(list_music)),
        Tool("search_music", "Search the downloaded songs by name. Use this before play_audio when given a song name.",
             gate(search_music),
             {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": "Part of the song name, e.g. \"jersey\"."}}}),
        Tool("play_audio",
             "Play a downloaded song in the user's voice channel (joins it if needed). If something is already "
             "playing, the song is added to the queue.",
             gate(play_audio, True),
             {"type": "object", "required": ["name"], "properties": {
                 "name": {"type": "string", "description": "The song's name as listed by search_music."}}}),
        Tool("pause_audio", "Pause the music.", gate(pause_audio, True)),
        Tool("resume_audio", "Resume paused music.", gate(resume_audio, True)),
        Tool("skip_audio", "Skip the current song and play the next one.", gate(skip_audio, True)),
        Tool("stop_audio", "Stop the music entirely and clear the queue, loop and shuffle.", gate(stop_audio, True)),
        Tool("loop_audio",
             "Turn loop on or off. With one song it repeats that song; with a queue it repeats the whole queue. "
             "If nothing is playing yet, loop starts with the next song.",
             gate(loop_audio, True), enabled_param("loop")),
        Tool("music_shuffle_enabled",
             "Turn shuffle on or off: play queued songs in random order, or random library songs when the queue is empty.",
             gate(music_shuffle_enabled, True), enabled_param("shuffle")),
        Tool("queue", "Show what's playing, what's queued, and loop/shuffle state.", gate(queue)),
        Tool("clear_queue", "Remove all queued songs without stopping the current one.", gate(clear_queue, True)),
    ]
