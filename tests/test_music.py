"""Music: the queue/loop/shuffle player, library, voice controller (fake voice client) and tools."""

from __future__ import annotations

import asyncio
import random
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from bong.bot.voice import VoiceController
from bong.music import downloader
from bong.music.library import MusicLibrary, clean_title
from bong.music.player import LoopMode, Player
from bong.music.stats import SongStats
from bong.tools.music import music_tools

from conftest import make_turn


# --- player state machine ---------------------------------------------------------------

def drain(p: Player, n: int) -> list[str | None]:
    return [p.next() for _ in range(n)]


def test_play_when_idle_then_queue_in_order():
    p = Player()
    assert p.request("A") == 0 and p.current == "A"
    assert p.request("B") == 1 and p.request("C") == 2
    assert drain(p, 3) == ["B", "C", None]
    assert p.current is None


def test_track_loop_repeats_until_turned_off():
    p = Player()
    p.request("A")
    assert p.set_loop(True) is LoopMode.TRACK
    assert drain(p, 3) == ["A", "A", "A"]
    p.set_loop(False)
    assert p.next() is None


def test_queueing_during_track_loop_becomes_queue_loop():
    p = Player()
    p.request("A")
    p.set_loop(True)
    p.request("B")
    assert p.loop is LoopMode.QUEUE and p.playlist == ["A", "B"]
    assert drain(p, 4) == ["B", "A", "B", "A"]


def test_queue_loop_repeats_in_order():
    # The old bot played A B C A B A C ... (it skipped the last song when refilling).
    p = Player()
    for t in "ABC":
        p.request(t)
    assert p.set_loop(True) is LoopMode.QUEUE
    assert drain(p, 8) == ["B", "C", "A", "B", "C", "A", "B", "C"]


def test_adding_to_a_looping_queue_joins_the_loop():
    p = Player()
    p.request("A"); p.request("B")
    p.set_loop(True)
    p.request("C")
    assert p.playlist == ["A", "B", "C"]
    assert drain(p, 5) == ["B", "C", "A", "B", "C"]


def test_loop_before_anything_plays_binds_to_next_song():
    p = Player()
    assert p.set_loop(True) is None and p.loop_pending
    p.request("A")
    assert p.loop is LoopMode.TRACK and not p.loop_pending
    assert drain(p, 2) == ["A", "A"]


class PickLast:
    """Deterministic 'random': always the last option."""
    def randrange(self, n):
        return n - 1

    def choice(self, seq):
        return seq[-1]


def test_shuffle_picks_from_queue_then_library():
    p = Player(library=lambda: ["X", "Y", "Z"], rng=PickLast())
    p.request("A")
    for t in "BCD":
        p.request(t)
    p.set_shuffle(True)
    assert drain(p, 3) == ["D", "C", "B"]            # random picks from the queue, not FIFO
    assert drain(p, 2) == ["Z", "Z"]                 # empty queue: random library songs

    real = Player(library=lambda: ["X", "Y", "Z"], rng=random.Random(7))
    real.request("A"); real.set_shuffle(True)
    assert all(t in "XYZ" for t in drain(real, 20))


def test_shuffle_with_empty_library_stops_but_keeps_settings():
    p = Player(library=list)
    p.request("A")
    p.set_shuffle(True)
    assert p.next() is None
    assert p.shuffle  # the old bot reset loop/shuffle here


def test_stop_and_clear_queue():
    p = Player()
    p.request("A"); p.request("B")
    p.set_loop(True)
    assert p.clear_queue() == 1 and p.loop is LoopMode.OFF and p.current == "A"
    p.request("C"); p.set_shuffle(True)
    p.stop()
    assert (p.current, p.queue, p.loop, p.shuffle, p.loop_pending) == (None, [], LoopMode.OFF, False, False)


# --- library ------------------------------------------------------------------------------

def make_library(tmp_path, names):
    for n in names:
        (tmp_path / f"{n}.mp3").write_bytes(b"")
    return MusicLibrary(tmp_path)


def test_library_search_and_find(tmp_path):
    lib = make_library(tmp_path, ["Mayday Parade Jersey", "Mayday Parade Miserable at Best", "Skrillex Bangarang"])
    assert [p.stem for p in lib.search("mayday")] == ["Mayday Parade Jersey", "Mayday Parade Miserable at Best"]
    assert lib.find("jersey mayday").stem == "Mayday Parade Jersey"
    assert lib.find("bangarag").stem == "Skrillex Bangarang"     # typo → fuzzy
    assert lib.find("nothing like this") is None


def test_clean_title_matches_old_bot():
    assert clean_title("Mayday Parade - Jersey (Official Video) | HD") == "Mayday Parade Jersey (Official Video) HD"
    assert "/" not in clean_title("../../etc/passwd")


def test_strip_playlist():
    url = "https://www.youtube.com/watch?v=abc&list=PL123&index=4"
    assert downloader.strip_playlist(url) == "https://www.youtube.com/watch?v=abc"
    assert downloader.is_youtube_url("https://youtu.be/abc") and not downloader.is_youtube_url("jersey")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_mp3_decodes_for_discord(tmp_path):
    song = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(song)], check=True)
    source = discord.FFmpegPCMAudio(str(song), options="-filter:a volume=0.3")
    try:
        frame = source.read()
        assert len(frame) == discord.opus.Encoder.FRAME_SIZE   # 20 ms of 48 kHz stereo PCM
    finally:
        source.cleanup()


# --- voice controller with a fake voice client ---------------------------------------------

class FakeVoiceClient:
    """Mimics discord.VoiceClient: `after` fires from another thread when a song ends or is stopped."""

    def __init__(self, channel):
        self.channel = channel
        self.playing = None
        self.paused = False
        self._after = None
        self.started = []

    def is_connected(self):
        return True

    def is_playing(self):
        return self.playing is not None and not self.paused

    def is_paused(self):
        return self.playing is not None and self.paused

    def play(self, source, after):
        assert self.playing is None, "play() while already playing"
        self.playing, self._after, self.paused = source[1], after, False
        self.started.append(Path(source[1]).stem)

    def _end(self):
        after, self.playing, self._after = self._after, None, None
        threading.Thread(target=after, args=(None,)).start()

    def stop(self):
        if self.playing is not None:
            self._end()

    def finish_song(self):  # the song reaches its end naturally
        self._end()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    async def disconnect(self, force=False):
        self.connected = False


async def settle():
    for _ in range(5):
        await asyncio.sleep(0.02)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(discord, "FFmpegPCMAudio", lambda path, options=None: ("source", path))
    statuses = []

    async def edit(status=None):
        statuses.append(status)

    channel = SimpleNamespace(id=50, name="Chill VC", edit=edit)
    vc = FakeVoiceClient(channel)
    member_in_vc = SimpleNamespace(voice=SimpleNamespace(channel=channel))
    members = {1: member_in_vc, 2: SimpleNamespace(voice=None)}
    guild = SimpleNamespace(id=5, voice_client=vc, get_member=members.get)
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 5 else None, user=SimpleNamespace(id=99), guilds=[guild])
    lib = make_library(tmp_path, ["A", "B", "C"])
    stats = SongStats(tmp_path / "song_stats.json")
    stats.load()
    return VoiceController(bot, lib, stats), vc, lib, stats, statuses


async def test_songs_auto_advance_and_stats_count(rig):
    ctrl, vc, lib, stats, statuses = rig
    assert await ctrl.play(5, 1, lib.find("A")) == "Playing 'A'."
    assert (await ctrl.play(5, 1, lib.find("B"))).startswith("Added 'B' to the queue (position 1)")
    await settle()
    assert statuses[-1] == "🎵 A"
    vc.finish_song(); await settle()
    assert vc.started == ["A", "B"] and statuses[-1] == "🎵 B"
    vc.finish_song(); await settle()
    assert vc.playing is None and statuses[-1] is None
    assert stats.top() == [("A", 1), ("B", 1)]


async def test_skip_and_stop_do_not_double_advance(rig):
    ctrl, vc, lib, *_ = rig
    for t in "ABC":
        await ctrl.play(5, 1, lib.find(t))
    assert await ctrl.skip(5) == "Skipped 'A'. Now playing 'B'."
    await settle()                                 # the stopped song's callback must be ignored
    assert vc.started == ["A", "B"] and vc.playing.endswith("B.mp3")
    assert (await ctrl.stop(5)).startswith("Stopped 'B'")
    await settle()
    assert vc.started == ["A", "B"] and vc.playing is None
    assert ctrl.describe_queue(5) == "Nothing is currently playing.\nQueue is empty."


async def test_queue_loop_keeps_going_through_real_callbacks(rig):
    ctrl, vc, lib, _, statuses = rig
    for t in "AB":
        await ctrl.play(5, 1, lib.find(t))
    assert await ctrl.set_loop(5, True) == "Looping the queue (2 songs)."
    for _ in range(4):
        vc.finish_song(); await settle()
    assert vc.started == ["A", "B", "A", "B", "A"]
    assert statuses[-1] == "🎵 A 🔁Q"


async def test_skip_on_a_looped_song_moves_on(rig):
    ctrl, vc, lib, *_ = rig
    await ctrl.play(5, 1, lib.find("A"))
    await ctrl.set_loop(5, True)
    assert (await ctrl.skip(5)).startswith("Skipped 'A'. The queue is empty")


async def test_pause_resume_and_prompt_status(rig):
    ctrl, vc, lib, *_ = rig
    assert ctrl.status_for_prompt(None) == "DM — no voice channels available."
    await ctrl.play(5, 1, lib.find("A"))
    assert await ctrl.pause(5) == "Paused 'A'." and vc.paused
    assert "(paused)" in ctrl.describe_queue(5)
    assert await ctrl.resume(5) == "Resumed 'A'."
    assert ctrl.status_for_prompt(5) == ("Connected to voice channel 'Chill VC'. Do NOT use join_voice — you are "
                                         "already in voice.\nCurrently playing 'A'.")


async def test_caller_must_be_in_voice(rig):
    ctrl, *_ = rig
    assert ctrl.check_caller(5, 1) is None
    assert "need to be in a voice channel" in ctrl.check_caller(5, 2)


# --- tools ------------------------------------------------------------------------------------

async def test_music_tools_gate_and_play(rig, monkeypatch):
    ctrl, vc, lib, *_ = rig
    allowed = {1}
    tools = {t.name: t.handler for t in music_tools(ctrl, lib, lambda uid: uid in allowed)}
    member = make_turn("bong play A", author_id=1, guild_id=5)

    assert "doesn't have permission" in await tools["play_audio"](make_turn(author_id=3, guild_id=5), {"name": "A"})
    assert "only works in servers" in await tools["play_audio"](make_turn(author_id=1, guild_id=None), {"name": "A"})
    allowed.add(2)
    assert "need to be in a voice channel" in await tools["skip_audio"](make_turn(author_id=2, guild_id=5), {})
    assert "No song matching 'zzz'" in await tools["play_audio"](member, {"name": "zzz"})

    assert await tools["play_audio"](member, {"name": "a"}) == "Playing 'A'."
    assert "Added 'B'" in await tools["play_audio"](member, {"name": "B"})
    assert "Looping the queue" in await tools["loop_audio"](member, {"enabled": True})
    assert (await tools["queue"](member, {})).splitlines() == ["Now playing: A", "  1. B", "(loop: queue (2 songs))"]
    assert "3 songs" in await tools["list_music"](member, {})


async def test_download_tool_checks_library_first(rig, monkeypatch):
    ctrl, vc, lib, *_ = rig
    calls = []

    def fake_download(query, library):
        calls.append(query)
        path = library.folder / "Jersey.mp3"
        path.write_bytes(b"")
        return downloader.Downloaded(path, "Jersey")

    monkeypatch.setattr(downloader, "download", fake_download)
    tools = {t.name: t.handler for t in music_tools(ctrl, lib, lambda uid: True)}
    turn = make_turn(author_id=1, guild_id=5)
    assert "already in the library" in await tools["download_music"](turn, {"query": "A"})
    assert await tools["download_music"](turn, {"query": "Jersey by Mayday Parade"}) == \
        "Downloaded 'Jersey'. Use play_audio to play it."
    assert calls == ["Jersey by Mayday Parade"]

    def failing(query, library):
        raise downloader.DownloadError("Sign in to confirm you're not a bot")
    monkeypatch.setattr(downloader, "download", failing)
    assert "Download failed: Sign in" in await tools["download_music"](turn, {"query": "something new"})
