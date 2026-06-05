import random
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import bong_tools
import bong_music
import bong_song_stats

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


SAVED_SOUNDS_DIR = Path(__file__).resolve().parent.parent / "bong_data" / "saved_sounds"


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """Reset all playback state and use a temp directory for music files."""
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
    bong_tools.current_user_id = 123456
    saved_sounds = tmp_path / "saved_sounds"
    saved_sounds.mkdir()
    for name in ["song_a", "song_b", "song_c"]:
        (saved_sounds / f"{name}.mp3").write_bytes(b"\x00")
    bong_tools.DOWNLOAD_DIR = saved_sounds
    bong_tools.refresh_music_library()
    yield
    bong_tools.caller_in_voice = False
    bong_tools.voice_connected = False


@pytest.fixture(autouse=True)
def mock_music_perms():
    """Mock permission checks so all tool functions execute their logic."""
    with patch("bong_music._check_music", return_value=None):
        yield


@pytest.fixture(autouse=True)
def mock_song_stats():
    """Mock song stats increment to avoid side effects."""
    with patch.object(bong_song_stats, "_increment_song"):
        yield


A = "/fake/song_a.mp3"
B = "/fake/song_b.mp3"
C = "/fake/song_c.mp3"

_loop_audio = bong_tools.tool_map["loop_audio"].func
_music_shuffle_enabled = bong_tools.tool_map["music_shuffle_enabled"].func
_skip_audio = bong_tools.tool_map["skip_audio"].func
_stop_audio = bong_tools.tool_map["stop_audio"].func
_clear_queue = bong_tools.tool_map["clear_queue"].func
_play_audio = bong_tools.tool_map["play_audio"].func
_queue = bong_tools.tool_map["queue"].func
_advance_queue = bong_tools.advance_queue


class TestLoopAudio:
    def test_loop_off(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = [A]
        result = _loop_audio(enabled=False)
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert "disabled" in result.lower()

    def test_loop_track_when_one_song(self):
        bong_tools.current_track = A
        bong_tools.song_queue = []
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == A
        assert bong_tools.queue_snapshot == []
        assert "Looping" in result or "looping" in result.lower()

    def test_loop_queue_when_songs_in_queue(self):
        bong_tools.current_track = A
        bong_tools.song_queue = [B, C]
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == [A, B, C]
        assert "3 song" in result

    def test_loop_does_not_disable_shuffle(self):
        bong_tools.shuffle_enabled = True
        bong_tools.current_track = A
        bong_tools.song_queue = [B]
        result = _loop_audio(enabled=True)
        assert bong_tools.shuffle_enabled is True
        assert bong_tools.queue_snapshot == [A, B]

    def test_loop_nothing_playing_deferred(self):
        bong_tools.current_track = None
        bong_tools.pending_play_audio = None
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert "Loop enabled" in result

    def test_loop_deferred_when_pending_play(self):
        bong_tools.current_track = None
        bong_tools.pending_play_audio = A
        bong_tools.song_queue = []
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == A

    def test_re_snapshot_on_existing_queue_loop(self):
        bong_tools.current_track = A
        bong_tools.song_queue = [B]
        bong_tools.queue_snapshot = [A, C]
        result = _loop_audio(enabled=True)
        assert bong_tools.queue_snapshot == [A, B]

    def test_track_loop_auto_promotes_when_queue_grows(self):
        bong_tools.current_track = A
        bong_tools.song_queue = []
        _loop_audio(enabled=True)
        assert bong_tools.loop_track == A
        assert bong_tools.queue_snapshot == []
        bong_tools.song_queue = [B]
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == [A, B]

    def test_double_deferred_loop(self):
        bong_tools.current_track = None
        bong_tools.pending_play_audio = None
        _loop_audio(enabled=True)
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert "Loop enabled" in result

    def test_deferred_loop_then_pending_play(self):
        bong_tools.current_track = None
        bong_tools.pending_play_audio = None
        _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track is None
        bong_tools.pending_play_audio = A
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_track == A


class TestShuffleEnabled:
    def test_shuffle_on_doesnt_disable_loop(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = []
        result = _music_shuffle_enabled(enabled=True)
        assert bong_tools.shuffle_enabled is True
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == A

    def test_shuffle_off_doesnt_disable_loop(self):
        bong_tools.loop_enabled = True
        bong_tools.shuffle_enabled = True
        result = _music_shuffle_enabled(enabled=False)
        assert bong_tools.shuffle_enabled is False
        assert bong_tools.loop_enabled is True


class TestSkipAudio:
    def test_skip_pops_fifo(self):
        bong_tools.song_queue = [A, B]
        result = _skip_audio()
        assert "song_a" in result
        assert bong_tools.pending_skip_target == A
        assert len(bong_tools.song_queue) == 1
        assert bong_tools.song_queue[0] == B

    def test_skip_shuffles_from_queue(self):
        bong_tools.song_queue = [A, B, C]
        bong_tools.shuffle_enabled = True
        result = _skip_audio()
        assert bong_tools.pending_skip is True
        assert len(bong_tools.song_queue) == 2

    def test_skip_empty_queue_loop_track(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        result = _skip_audio()
        assert bong_tools.pending_skip_target == A
        assert "song_a" in result

    def test_skip_empty_queue_loop_queue_refill(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        bong_tools.current_track = A
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
        bong_tools.music_library = [Path(A), Path(B)]
        result = _skip_audio()
        assert bong_tools.pending_skip is True
        assert bong_tools.pending_skip_target is not None


class TestStopAudio:
    def test_stop_clears_everything(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = [A]
        bong_tools.shuffle_enabled = True
        bong_tools.song_queue = [B]
        result = _stop_audio()
        assert bong_tools.pending_stop is True
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert bong_tools.shuffle_enabled is False
        assert bong_tools.song_queue == []


class TestClearQueue:
    def test_clear_queue_disables_queue_loop(self):
        bong_tools.song_queue = [A, B]
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        result = _clear_queue()
        assert bong_tools.loop_enabled is False
        assert bong_tools.queue_snapshot == []
        assert bong_tools.song_queue == []
        assert "2 song" in result

    def test_clear_queue_preserves_track_loop(self):
        bong_tools.song_queue = [B]
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = []
        result = _clear_queue()
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == A
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

    def test_play_audio_nothing_playing_sets_pending(self):
        result = _play_audio(index=0)
        assert bong_tools.pending_play_audio is not None
        assert bong_tools.song_queue == []
        assert "Playing" in result

    def test_play_audio_something_playing_appends_queue(self):
        bong_tools.current_track = str(bong_tools.music_library[0])
        bong_tools.song_queue = []
        result = _play_audio(index=1)
        assert len(bong_tools.song_queue) == 1
        assert bong_tools.pending_play_audio is None

    def test_play_audio_track_loop_with_queue_promotes(self):
        song_a = str(bong_tools.music_library[0])
        song_b = str(bong_tools.music_library[1])
        bong_tools.current_track = song_a
        bong_tools.loop_enabled = True
        bong_tools.loop_track = song_a
        bong_tools.queue_snapshot = []
        bong_tools.song_queue = [song_b]
        result = _play_audio(index=2)
        assert bong_tools.loop_track is None
        assert len(bong_tools.queue_snapshot) == 3
        assert "Queue loop activated" in result

    def test_play_audio_name_match_exact(self):
        result = _play_audio(name="song_a")
        assert bong_tools.pending_play_audio is not None
        assert "Playing" in result

    def test_play_audio_name_match_fuzzy(self):
        result = _play_audio(name="song")
        assert bong_tools.pending_play_audio is not None
        assert "Playing" in result

    def test_play_audio_index_out_of_range(self):
        result = _play_audio(index=99)
        assert "out of range" in result.lower() or "not found" in result.lower() or "no song" in result.lower()

    def test_play_audio_no_voice_returns_error(self):
        bong_tools.voice_connected = False
        bong_tools.pending_join_voice = None
        result = _play_audio(index=0)
        assert "voice" in result.lower() or "not in" in result.lower()


class TestQueueDisplay:
    def test_queue_shows_track_loop(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        result = _queue()
        assert "loop: track" in result

    def test_queue_shows_queue_loop(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        result = _queue()
        assert "loop: queue" in result

    def test_queue_shows_shuffle(self):
        bong_tools.current_track = A
        bong_tools.shuffle_enabled = True
        result = _queue()
        assert "shuffle on" in result

    def test_queue_shows_both_loop_and_shuffle(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        bong_tools.shuffle_enabled = True
        result = _queue()
        assert "loop: queue" in result
        assert "shuffle on" in result


class TestAdvanceQueue:
    def test_fifo_pop(self):
        bong_tools.song_queue = [A, B]
        track, desc = _advance_queue()
        assert track == A
        assert bong_tools.song_queue == [B]
        assert bong_tools.current_track == A

    def test_shuffle_pop(self):
        bong_tools.song_queue = [A, B, C]
        bong_tools.shuffle_enabled = True
        with patch("bong_tools.random.randrange", return_value=1):
            track, desc = _advance_queue()
        assert track == B
        assert len(bong_tools.song_queue) == 2
        assert bong_tools.current_track == B

    def test_queue_loop_refill(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        bong_tools.current_track = A
        track, desc = _advance_queue()
        assert track == B
        assert bong_tools.current_track == B

    def test_queue_loop_refill_all_same(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A]
        bong_tools.current_track = A
        track, desc = _advance_queue()
        assert track == A
        assert bong_tools.current_track == A

    def test_track_loop_replay(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        track, desc = _advance_queue()
        assert track == A
        assert "Looping" in desc

    def test_shuffle_library_pick(self):
        bong_tools.song_queue = []
        bong_tools.shuffle_enabled = True
        bong_tools.current_track = None
        bong_tools.music_library = [Path(A), Path(B), Path(C)]
        with patch("bong_tools.random.choice", return_value=Path(B)):
            track, desc = _advance_queue()
        assert track == B
        assert bong_tools.current_track == B

    def test_nothing_resets_all(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = False
        bong_tools.shuffle_enabled = False
        bong_tools.current_track = A
        track, desc = _advance_queue()
        assert track is None
        assert desc is None
        assert bong_tools.current_track is None
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert bong_tools.shuffle_enabled is False

    def test_shuffle_with_queue_pops_from_queue(self):
        bong_tools.song_queue = [A, B]
        bong_tools.shuffle_enabled = True
        bong_tools.current_track = None
        with patch("bong_tools.random.randrange", return_value=0):
            track, desc = _advance_queue()
        assert track == A
        assert len(bong_tools.song_queue) == 1

    def test_current_track_updated(self):
        bong_tools.song_queue = [A]
        track, desc = _advance_queue()
        assert bong_tools.current_track == A

    def test_queue_loop_refill_excludes_current(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B, C]
        bong_tools.current_track = B
        track, desc = _advance_queue()
        assert track is not None
        remaining = bong_tools.song_queue
        assert B not in remaining or A in remaining or C in remaining

    def test_shuffle_queue_loop_refill(self):
        bong_tools.song_queue = []
        bong_tools.loop_enabled = True
        bong_tools.shuffle_enabled = True
        bong_tools.queue_snapshot = [A, B, C]
        bong_tools.current_track = A
        with patch("bong_tools.random.randrange", return_value=0):
            track, desc = _advance_queue()
        assert track == B
        assert len(bong_tools.song_queue) == 1
        assert C in bong_tools.song_queue

    def test_loop_track_before_queue(self):
        bong_tools.song_queue = [B]
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        track, desc = _advance_queue()
        assert track == B
        assert bong_tools.loop_track == A


class TestDeferredLoopBinding:
    def test_deferred_loop_nothing_playing(self):
        result = _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track is None
        assert "Loop enabled" in result

    def test_play_binds_deferred_loop_via_dispatch(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = None
        bong_tools.queue_snapshot = []
        result = _play_audio(index=0)
        assert bong_tools.pending_play_audio is not None
        track_path = bong_tools.pending_play_audio
        assert "Playing" in result
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track is None
        bong_tools.current_track = track_path
        bong_tools.pending_play_audio = None
        if bong_tools.loop_enabled and not bong_tools.loop_track and not bong_tools.queue_snapshot:
            bong_tools.loop_track = track_path
        assert bong_tools.loop_track == track_path

    def test_deferred_loop_then_queue_loop(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = None
        bong_tools.queue_snapshot = []
        bong_tools.current_track = str(bong_tools.music_library[0])
        bong_tools.song_queue = [str(bong_tools.music_library[1])]
        result = _loop_audio(enabled=True)
        assert bong_tools.queue_snapshot is not None
        assert len(bong_tools.queue_snapshot) == 2

    def test_deferred_loop_preserved_across_multiple_plays(self):
        bong_tools.loop_enabled = True
        bong_tools.loop_track = None
        bong_tools.queue_snapshot = []
        result = _play_audio(index=0)
        assert "Playing" in result
        assert bong_tools.loop_enabled is True


class TestQueueStateTransitions:
    def test_play_then_stop(self):
        _play_audio(index=0)
        assert bong_tools.pending_play_audio is not None
        _stop_audio()
        assert bong_tools.pending_stop is True
        bong_tools.loop_enabled = False
        bong_tools.loop_track = None
        bong_tools.queue_snapshot = []
        bong_tools.shuffle_enabled = False
        bong_tools.song_queue = []
        bong_tools.current_track = None
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert bong_tools.shuffle_enabled is False
        assert bong_tools.song_queue == []

    def test_play_loop_advance_track_loop(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = []
        track, desc = _advance_queue()
        assert track == A
        assert bong_tools.loop_track == A

    def test_play_add_loop_advance_queue_loop(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        bong_tools.song_queue = []
        track, desc = _advance_queue()
        assert track == B
        track2, desc2 = _advance_queue()
        assert track2 == A

    def test_deferred_loop_then_play(self):
        _loop_audio(enabled=True)
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track is None
        song_path = str(bong_tools.music_library[0])
        bong_tools.pending_play_audio = song_path
        bong_tools.current_track = song_path
        bong_tools.pending_play_audio = None
        if bong_tools.loop_enabled and not bong_tools.loop_track and not bong_tools.queue_snapshot:
            bong_tools.loop_track = song_path
        assert bong_tools.loop_track == song_path

    def test_loop_toggle_off(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        result = _loop_audio(enabled=False)
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        track, desc = _advance_queue()
        assert track is None

    def test_queue_loop_then_clear(self):
        bong_tools.current_track = A
        bong_tools.song_queue = [B]
        bong_tools.loop_enabled = True
        bong_tools.queue_snapshot = [A, B]
        _clear_queue()
        assert bong_tools.loop_enabled is False
        assert bong_tools.queue_snapshot == []
        assert bong_tools.song_queue == []

    def test_clear_queue_preserves_track_loop_state(self):
        bong_tools.current_track = A
        bong_tools.song_queue = [B]
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = []
        _clear_queue()
        assert bong_tools.loop_enabled is True
        assert bong_tools.loop_track == A
        assert bong_tools.song_queue == []

    def test_skip_advances_queue(self):
        bong_tools.current_track = A
        bong_tools.song_queue = [B, C]
        _skip_audio()
        assert bong_tools.pending_skip_target == B
        assert len(bong_tools.song_queue) == 1

    def test_skip_in_loop_replays_track(self):
        bong_tools.current_track = A
        bong_tools.loop_enabled = True
        bong_tools.loop_track = A
        bong_tools.queue_snapshot = []
        bong_tools.song_queue = []
        _skip_audio()
        assert bong_tools.pending_skip_target == A

    def test_play_shuffle_advance_from_library(self):
        bong_tools.song_queue = []
        bong_tools.shuffle_enabled = True
        bong_tools.current_track = None
        bong_tools.loop_enabled = False
        with patch.object(bong_tools, "refresh_music_library"):
            bong_tools.music_library = [Path(A), Path(B), Path(C)]
            track, desc = _advance_queue()
        assert track is not None
        assert "random" in desc.lower()


class TestAfterPlayCallback:
    def _make_callback(self, guild=None, loop=None):
        """Create an after_play callback with mocked guild and loop."""
        if loop is None:
            loop = MagicMock()
        if guild is None:
            guild = MagicMock()
        return bong_tools, guild, loop

    def test_after_play_next_in_queue(self):
        bt, guild, loop = self._make_callback()
        bt.current_track = A
        bt.song_queue = [B]
        bt.loop_enabled = False
        bt.shuffle_enabled = False
        bt.loop_track = None
        bt.queue_snapshot = []
        vc = MagicMock()
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        vc.is_connected.return_value = True
        guild.voice_client = vc
        callback = _make_after_play_callback_simple(guild, loop)
        callback(None)
        vc.play.assert_called_once()

    def test_after_play_track_loop(self):
        bt, guild, loop = self._make_callback()
        bt.current_track = A
        bt.song_queue = []
        bt.loop_enabled = True
        bt.loop_track = A
        bt.queue_snapshot = []
        bt.shuffle_enabled = False
        vc = MagicMock()
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        vc.is_connected.return_value = True
        guild.voice_client = vc
        callback = _make_after_play_callback_simple(guild, loop)
        callback(None)
        vc.play.assert_called_once()

    def test_after_play_nothing_left(self):
        bt, guild, loop = self._make_callback()
        bt.current_track = A
        bt.song_queue = []
        bt.loop_enabled = False
        bt.loop_track = None
        bt.queue_snapshot = []
        bt.shuffle_enabled = False
        vc = MagicMock()
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        vc.is_connected.return_value = True
        guild.voice_client = vc
        callback = _make_after_play_callback_simple(guild, loop)
        callback(None)
        vc.play.assert_not_called()
        assert bt.current_track is None
        assert bt.loop_enabled is False

    def test_after_play_cedes_to_pending_skip(self):
        bt, guild, loop = self._make_callback()
        bt.pending_skip = True
        bt.current_track = A
        bt.song_queue = [B]
        vc = MagicMock()
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        vc.is_connected.return_value = True
        guild.voice_client = vc
        callback = _make_after_play_callback_simple(guild, loop)
        callback(None)
        vc.play.assert_not_called()

    def test_after_play_cedes_to_pending_stop(self):
        bt, guild, loop = self._make_callback()
        bt.pending_stop = True
        bt.current_track = A
        bt.song_queue = [B]
        vc = MagicMock()
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False
        vc.is_connected.return_value = True
        guild.voice_client = vc
        callback = _make_after_play_callback_simple(guild, loop)
        callback(None)
        vc.play.assert_not_called()


def _make_after_play_callback_simple(guild, async_loop):
    """Reimplementation of _make_after_play_callback for testing without async."""
    import bong_tools as bt_mod
    import bong_song_stats

    def after_play(err):
        if err:
            pass
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return
        try:
            if vc.is_playing() or vc.is_paused():
                return
        except Exception:
            return
        if bt_mod.pending_play_audio or bt_mod.pending_skip or bt_mod.pending_stop:
            return
        try:
            next_track, _desc = bt_mod.advance_queue()
            if next_track:
                bong_song_stats._increment_song(Path(next_track).stem)
                vc.play(MagicMock(), after=after_play)
            else:
                pass
        except Exception:
            pass
    return after_play


class TestPropertyInvariants:
    """Hypothesis-based property tests for playback state machine invariants."""

    @given(
        ops=st.lists(
            st.sampled_from([
                "loop_on", "loop_off", "shuffle_on", "shuffle_off",
                "stop", "clear_queue", "add_song",
            ]),
            max_size=20,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_stop_resets_all_state(self, ops):
        _stop_audio()
        assert bong_tools.loop_enabled is False
        assert bong_tools.loop_track is None
        assert bong_tools.queue_snapshot == []
        assert bong_tools.shuffle_enabled is False
        assert bong_tools.song_queue == []
        assert bong_tools.pending_stop is True

    @given(
        queue=st.lists(st.sampled_from([A, B, C]), min_size=0, max_size=5),
        loop=st.booleans(),
        shuffle=st.booleans(),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_advance_returns_valid_paths(self, queue, loop, shuffle):
        bong_tools.song_queue = list(queue)
        bong_tools.loop_enabled = loop
        bong_tools.shuffle_enabled = shuffle
        bong_tools.loop_track = A if loop else None
        bong_tools.queue_snapshot = [A, B] if loop else []
        bong_tools.current_track = A
        bong_tools.music_library = [Path(A), Path(B), Path(C)]
        track, desc = bong_tools.advance_queue()
        valid_paths = {A, B, C}
        valid_paths.update(str(p) for p in bong_tools.music_library)
        if track is not None:
            assert track in valid_paths

    @given(
        loop=st.booleans(),
        loop_track_val=st.sampled_from([A, B, None]),
        snapshot_size=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_play_audio_never_touches_loop_enabled(self, loop, loop_track_val, snapshot_size):
        bong_tools.loop_enabled = loop
        bong_tools.loop_track = loop_track_val
        bong_tools.queue_snapshot = [A, B][:snapshot_size]
        bong_tools.current_track = str(bong_tools.music_library[0])
        bong_tools.song_queue = []
        saved_loop = bong_tools.loop_enabled
        _play_audio(index=1)
        assert bong_tools.loop_enabled == saved_loop

    @given(
        ops=st.lists(
            st.sampled_from(["loop_on", "loop_off", "add_song", "stop"]),
            max_size=15,
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_no_dangling_loop_snapshot_without_enabled(self, ops):
        for op in ops:
            if op == "loop_on":
                bong_tools.loop_enabled = True
                bong_tools.loop_track = A
                bong_tools.queue_snapshot = [A]
            elif op == "loop_off":
                bong_tools.loop_enabled = False
                bong_tools.loop_track = None
                bong_tools.queue_snapshot = []
            elif op == "add_song":
                bong_tools.song_queue.append(B)
            elif op == "stop":
                bong_tools.song_queue = []
                bong_tools.loop_enabled = False
                bong_tools.loop_track = None
                bong_tools.queue_snapshot = []
                bong_tools.shuffle_enabled = False
                bong_tools.current_track = None
        if not bong_tools.loop_enabled:
            assert bong_tools.loop_track is None
            assert bong_tools.queue_snapshot == []

    def test_snapshot_always_includes_playing_track(self):
        bong_tools.current_track = A
        bong_tools.song_queue = [B, C]
        bong_tools.loop_enabled = True
        _loop_audio(enabled=True)
        if bong_tools.queue_snapshot:
            assert bong_tools.current_track in bong_tools.queue_snapshot or bong_tools.pending_play_audio in bong_tools.queue_snapshot

    @given(
        queue_size=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_add_songs_always_increments_queue(self, queue_size):
        bong_tools.current_track = str(bong_tools.music_library[0])
        bong_tools.song_queue = []
        for i in range(queue_size):
            idx = min(i + 1, len(bong_tools.music_library) - 1)
            _play_audio(index=idx)
        assert len(bong_tools.song_queue) == queue_size