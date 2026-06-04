import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BONG_DATA = PROJECT_ROOT / "bong_data"

DOWNLOAD_DIR = BONG_DATA / "saved_sounds"
DOWNLOAD_DIR.mkdir(exist_ok=True)
IMAGE_DIR = BONG_DATA / "saved_images"
IMAGE_DIR.mkdir(exist_ok=True)
TEXT_DIR = BONG_DATA / "saved_texts"
TEXT_DIR.mkdir(exist_ok=True)

BOT_USER_ID = "698627881760456724"

pending_reactions = []
pending_join_voice = None
pending_leave_voice = None
pending_shutdown = False
pending_play_audio = None
pending_pause = False
pending_resume = False
pending_stop = False
pending_skip = False
pending_skip_target = None
pending_send_image = None
pending_send_text = None
pending_start_listening = None
pending_stop_listening = False

voice_connected = False
caller_in_voice = False
current_user_id = None
current_channel_id = None
current_username = ""
start_time = None
shuffle_enabled = False
loop_enabled = False
loop_track = None
queue_snapshot: list[str] = []
current_track = None
song_queue: list[str] = []

image_library = []
text_library = []
music_library = []

import bong_tools

from bong_music import tools as _music_tools
from bong_memory import tools as _memory_tools
from bong_web import tools as _web_tools
from bong_state import tools as _state_tools
from bong_e621 import tools as _e621_tools
tools = _music_tools + _memory_tools + _web_tools + _state_tools + _e621_tools
tool_map = {t.name: t for t in tools}


def advance_queue():
    """Pick the next track from the queue following the loop/shuffle rules.

    Returns (track_path, description) or (None, None) if nothing to play.
    Shared logic used by both skip_audio (sync tool) and after_play (async callback).
    """
    bt = bong_tools
    # 1) Queue-loop refill: if queue is empty but we have a snapshot, repopulate
    if not bt.song_queue and bt.loop_enabled and bt.queue_snapshot:
        bt.song_queue = [s for s in bt.queue_snapshot if s != bt.current_track]
        if not bt.song_queue:
            bt.song_queue = list(bt.queue_snapshot)
    # 2) Queue: pop next song (random if shuffle, otherwise FIFO)
    if bt.song_queue:
        if bt.shuffle_enabled:
            idx = random.randrange(len(bt.song_queue))
            next_track = bt.song_queue.pop(idx)
        else:
            next_track = bt.song_queue.pop(0)
        bt.current_track = next_track
        return next_track, f"Playing '{Path(next_track).stem}' (from queue)."
    # 3) Track-loop: replay the loop track
    if bt.loop_enabled and bt.loop_track:
        bt.current_track = bt.loop_track
        return bt.loop_track, f"Looping '{Path(bt.loop_track).stem}'."
    # 4) Shuffle mode (no queue, no loop): pick a random track from library
    if bt.shuffle_enabled:
        bt.refresh_music_library()
        files = bt.music_library
        if files:
            next_track = random.choice(files)
            bt.current_track = str(next_track)
            return str(next_track), f"Playing random '{next_track.stem}'."
    # Nothing to continue
    bt.current_track = None
    bt.loop_enabled = False
    bt.loop_track = None
    bt.queue_snapshot = []
    bt.shuffle_enabled = False
    return None, None


def reset_pending():
    bong_tools.pending_reactions.clear()
    bong_tools.pending_join_voice = None
    bong_tools.pending_leave_voice = None
    bong_tools.pending_shutdown = False
    bong_tools.pending_play_audio = None
    bong_tools.pending_pause = False
    bong_tools.pending_resume = False
    bong_tools.pending_stop = False
    bong_tools.pending_skip = False
    bong_tools.pending_skip_target = None
    bong_tools.pending_send_image = None
    bong_tools.pending_send_text = None
    bong_tools.pending_start_listening = None
    bong_tools.pending_stop_listening = False


def refresh_image_library():
    bong_tools.image_library = sorted(
        (p for p in bong_tools.IMAGE_DIR.iterdir()
         if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")),
        key=lambda p: p.name,
    )


def refresh_text_library():
    bong_tools.text_library = sorted(
        (p for p in bong_tools.TEXT_DIR.iterdir()
         if p.suffix.lower() in (".txt", ".md", ".py", ".json", ".csv", ".xml", ".yaml", ".yml", ".cfg", ".ini", ".log", ".toml", ".rs", ".js", ".ts", ".html", ".css", ".sh", ".bat")),
        key=lambda p: p.name,
    )


def refresh_music_library():
    bong_tools.music_library = sorted(bong_tools.DOWNLOAD_DIR.glob("*.mp3"), key=lambda p: p.name)


refresh_music_library()
refresh_image_library()
refresh_text_library()