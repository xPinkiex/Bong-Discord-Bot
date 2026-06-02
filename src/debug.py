# debug.py — Debug logging utilities for Bong, hot-reloadable via @reload
#
# Provides two logging functions:
#   - log(tag, *args): prints to console if debug mode is on
#   - log_to_file(tag, *args): always writes to a timestamped log file
#
# Debug mode defaults to off unless the bot is started with -d/--debug.
# It can also be toggled at runtime with the @debug bot command,
# which calls toggle_debug(). State is stored in a module-level _PERSIST
# dict so it survives hot reloads.

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BONG_DATA = PROJECT_ROOT / "bong_data"

_start = time.monotonic()
_log_dir = BONG_DATA / "logs"

def _get_state():
    """Return the persistent debug state dict (survives module reloads).

    On first call (or after a reload that wiped it), creates a new dict.
    debug_mode defaults to False unless -d/--debug was passed.
    """
    import debug as _self
    if not hasattr(_self, "_PERSIST"):
        _self._PERSIST = {
            "debug_mode": False,
            "log_file": None,
        }
    # Lazily create the log file only when first written to
    return _self._PERSIST

def _ensure_log_file():
    """Create the log file on first write (avoids empty files from import)."""
    state = _get_state()
    if state["log_file"] is None:
        _log_dir.mkdir(parents=True, exist_ok=True)
        state["log_file"] = _log_dir / f"{datetime.now().strftime('%Y%m%d_%H.%M.%S')}-bong.log"

def _elapsed_str():
    """Return elapsed time since bot start as HH:MM:SS string."""
    elapsed = time.monotonic() - _start
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    s = int(elapsed % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def log(tag, *args):
    """Print a tagged log message to console. Only prints if debug mode is enabled."""
    if _get_state()["debug_mode"]:
        print(f"[{_elapsed_str()}] <{tag}>", *args)

def log_to_file(tag, *args):
    """Append a tagged log message to the current log file. Only writes if debug mode is enabled."""
    if not _get_state()["debug_mode"]:
        return
    _ensure_log_file()
    ts = _elapsed_str()
    line = f"[{ts}] <{tag}> " + " ".join(str(a) for a in args) + "\n"
    with _get_state()["log_file"].open("a", encoding="utf-8") as f:
        f.write(line)

def error(tag, *args):
    """Log an error message — always printed and written to file regardless of debug mode."""
    ts = _elapsed_str()
    msg = f"[{ts}] <{tag}> " + " ".join(str(a) for a in args)
    print(msg, file=sys.stderr)
    _ensure_log_file()
    with _get_state()["log_file"].open("a", encoding="utf-8") as f:
        f.write(msg + "\n")

def toggle_debug(enabled: bool | None = None) -> bool:
    """Toggle or set debug mode. Returns the new state.

    With no argument, toggles. With True/False, sets explicitly.
    """
    state = _get_state()
    if enabled is not None:
        state["debug_mode"] = enabled
    else:
        state["debug_mode"] = not state["debug_mode"]
    return state["debug_mode"]

def is_debug() -> bool:
    """Return whether debug mode is currently enabled."""
    return _get_state()["debug_mode"]