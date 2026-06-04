import sys
from pathlib import Path
import tempfile
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
import bong_tools
import bong_song_stats


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """Reset all music state between tests and use a temp directory for music files."""
    bong_tools.loop_enabled = False
    bong_tools.loop_track = None
    bong_tools.queue_snapshot = []
    bong_tools.shuffle_enabled = False
    bong_tools.current_track = None
    bong_tools.song_queue = []
    bong_tools.pending_skip = False
    bong_tools.pending_skip_target = None
    bong_tools.pending_stop = False
    bong_tools.pending_play_audio = None
    bong_tools.pending_pause = False
    bong_tools.pending_resume = False
    bong_tools.caller_in_voice = True
    bong_tools.voice_connected = True
    # Create real mp3 files in a temp dir so refresh_music_library finds them
    saved_sounds = tmp_path / "saved_sounds"
    saved_sounds.mkdir()
    for name in ["song_a", "song_b", "song_c"]:
        (saved_sounds / f"{name}.mp3").write_bytes(b"\x00")
    bong_tools.DOWNLOAD_DIR = saved_sounds
    bong_tools.refresh_music_library()
    yield
    bong_tools.caller_in_voice = False
    bong_tools.voice_connected = False


# Access underlying functions from the @tool-decorated StructuredTool objects
_loop_audio = bong_tools.tool_map["loop_audio"].func
_music_shuffle_enabled = bong_tools.tool_map["music_shuffle_enabled"].func
_skip_audio = bong_tools.tool_map["skip_audio"].func
_stop_audio = bong_tools.tool_map["stop_audio"].func
_clear_queue = bong_tools.tool_map["clear_queue"].func
_play_audio = bong_tools.tool_map["play_audio"].func
_queue = bong_tools.tool_map["queue"].func


class TestLoopAudio:
    def test_loop_off(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = "/fake/song_a.mp3"
        bong_tools.queue_snapshot = ["/fake/song_a.mp3"]
        result = _loop_audio(enabled=False)
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert "disabled" in result.lower()

    def test_loop_track_when_one_song(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.song_queue = []
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == "/fake/song_a.mp3"
        assert bong_tools.queue_snapshot == []
        assert "Looping" in result or "looping" in result.lower()

    def test_loop_queue_when_songs_in_queue(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.song_queue = ["/fake/song_b.mp3", "/fake/song_c.mp3"]
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == ["/fake/song_a.mp3", "/fake/song_b.mp3", "/fake/song_c.mp3"]
        assert "3 song" in result

    def test_loop_does_not_disable_shuffle(self):
        bong_tools.shuffle_enabled = True
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.song_queue = ["/fake/song_b.mp3"]
        result = _loop_audio(enabled=True)
        assert bong_tools.shuffle_enabled is True
        assert bong_tools.queue_snapshot == ["/fake/song_a.mp3", "/fake/song_b.mp3"]

    def test_loop_nothing_playing(self):
        bong_tools.current_track = None
        bong_tools.pending_play_audio = None
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is False
        assert "Nothing" in result

    def test_loop_deferred_when_pending_play(self):
        bong_tools.current_track = None
        bong_tools.pending_play_audio = "/fake/song_a.mp3"
        bong_tools.song_queue = []
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == "/fake/song_a.mp3"

    def test_re_snapshot_on_existing_queue_loop(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.song_queue = ["/fake/song_b.mp3"]
        bong_tools.queue_snapshot = ["/fake/song_a.mp3", "/fake/song_c.mp3"]
        result = _loop_audio(enabled=True)
        assert bong_tools.queue_snapshot == ["/fake/song_a.mp3", "/fake/song_b.mp3"]

    def test_track_loop_auto_promotes_when_queue_grows(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.song_queue = []
        _loop_audio(enabled=True)
        assert bong_tools.loop_track == "/fake/song_a.mp3"
        assert bong_tools.queue_snapshot == []
        bong_tools.song_queue = ["/fake/song_b.mp3"]
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == ["/fake/song_a.mp3", "/fake/song_b.mp3"]


class TestShuffleEnabled:
    def test_shuffle_on_doesnt_disable_loop(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = "/fake/song_a.mp3"
        bong_tools.queue_snapshot = []
        result = _music_shuffle_enabled(enabled=True)
        assert bong_tools.shuffle_enabled is True
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == "/fake/song_a.mp3"

    def test_shuffle_off_doesnt_disable_loop(self):
        bong_tools.loop_enabled = True
        bong_tools.shuffle_enabled = True
        result = _music_shuffle_enabled(enabled=False)
        assert bong_tools.shuffle_enabled is False
        assert bong_tools.loop_enabled is True


class TestSkipAudio:
    def test_skip_pops_fifo(self):
        bong_tools.song_queue = ["/fake/song_a.mp3", "/fake/song_b.mp3"]
        result = _skip_audio()
        assert "song_a" in result
        assert bong_tools.pending_skip_target == "/fake/song_a.mp3"
        assert len(bong_tools.song_queue) == 1
        assert bong_tools.song_queue[0] == "/fake/song_b.mp3"

    def test_skip_shuffles_from_queue(self):
        bong_tools.song_queue = ["/fake/song_a.mp3", "/fake/song_b.mp3", "/fake/song_c.mp3"]
        bong_tools.shuffle_enabled = True
        result = _skip_audio()
        assert bong_tools.pending_skip is True
        assert len(bong_tools.song_queue) == 2

    def test_skip_empty_queue_loop_track(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.loop_track = "/fake/song_a.mp3"
        result = _skip_audio()
        assert bong_tools.pending_skip_target == "/fake/song_a.mp3"
        assert "song_a" in result

    def test_skip_empty_queue_loop_queue_refill(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = ["/fake/song_a.mp3", "/fake/song_b.mp3"]
        bong_tools.current_track = "/fake/song_a.mp3"
        result = _skip_audio()
        assert bong_tools.pending_skip is True
        assert bong_tools.pending_skip_target is not None
        assert "song_b" in result

    def test_skip_empty_queue_nothing(self):
        bong_tools.song_queue = []
        result = _skip_audio()
        assert bong_tools.pending_skip_target is None
        assert "empty" in result.lower() or "add" in result.lower()

    def test_skip_empty_queue_shuffle_picks_from_folder(self):
        bong_tools.song_queue = []
        bong_tools.shuffle_enabled = True
        bong_tools.music_library = [Path("/fake/song_a.mp3"), Path("/fake/song_b.mp3")]
        result = _skip_audio()
        assert bong_tools.pending_skip is True
        assert bong_tools.pending_skip_target is not None


class TestStopAudio:
    def test_stop_clears_everything(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = "/fake/song_a.mp3"
        bong_tools.queue_snapshot = ["/fake/song_a.mp3"]
        bong_tools.shuffle_enabled = True
        bong_tools.song_queue = ["/fake/song_b.mp3"]
        result = _stop_audio()
        assert bong_tools.pending_stop is True
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert bong_tools.shuffle_enabled is False
        assert bong_tools.song_queue == []


class TestClearQueue:
    def test_clear_queue_disables_queue_loop(self):
        bong_tools.song_queue = ["/fake/song_a.mp3", "/fake/song_b.mp3"]
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = ["/fake/song_a.mp3", "/fake/song_b.mp3"]
        result = _clear_queue()
        assert bong_tools.loop_enabled is False
        assert bong_tools.queue_snapshot == []
        assert bong_tools.song_queue == []
        assert "2 song" in result

    def test_clear_queue_preserves_track_loop(self):
        bong_tools.song_queue = ["/fake/song_b.mp3"]
        bong_tools.loop_enabled = True
        bong_tools.loop_track = "/fake/song_a.mp3"
        bong_tools.queue_snapshot = []
        result = _clear_queue()
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == "/fake/song_a.mp3"
        assert bong_tools.song_queue == []


class TestPlayAudio:
    def test_play_audio_auto_promotes_track_loop_to_queue_loop(self):
        song_a = str(bong_tools.music_library[0])
        song_b = str(bong_tools.music_library[1])
        bong_tools.current_track = song_a
        bong_tools.loop_enabled = True
        bong_tools.loop_track = song_a
        bong_tools.queue_snapshot = []
        bong_tools.song_queue = [song_b]
        result = _play_audio(index=2)
        assert "Queue loop activated" in result
        assert bong_tools.loop_track is None
        assert len(bong_tools.queue_snapshot) == 3

    def test_play_audio_appends_to_queue_loop_snapshot(self):
        song_a = str(bong_tools.music_library[0])
        song_b = str(bong_tools.music_library[1])
        bong_tools.current_track = song_a
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [song_a, song_b]
        bong_tools.song_queue = [song_b]
        result = _play_audio(index=2)
        assert "loop has" in result
        assert len(bong_tools.queue_snapshot) == 3

    def test_play_audio_normal_queue_when_no_loop(self):
        song_a = str(bong_tools.music_library[0])
        song_b = str(bong_tools.music_library[1])
        bong_tools.current_track = song_a
        bong_tools.song_queue = [song_b]
        result = _play_audio(index=2)
        assert "position 2" in result


class TestQueueDisplay:
    def test_queue_shows_track_loop(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.loop_enabled = True
        bong_tools.loop_track = "/fake/song_a.mp3"
        result = _queue()
        assert "loop: track" in result

    def test_queue_shows_queue_loop(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = ["/fake/song_a.mp3", "/fake/song_b.mp3"]
        result = _queue()
        assert "loop: queue" in result

    def test_queue_shows_shuffle(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.shuffle_enabled = True
        result = _queue()
        assert "shuffle on" in result

    def test_queue_shows_both_loop_and_shuffle(self):
        bong_tools.current_track = "/fake/song_a.mp3"
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = ["/fake/song_a.mp3", "/fake/song_b.mp3"]
        bong_tools.shuffle_enabled = True
        result = _queue()
        assert "loop: queue" in result
        assert "shuffle on" in result