# debug.py — Debug utilities for Bong, hot-reloadable via @reload

import time
from datetime import datetime
from pathlib import Path

_start = time.monotonic()
_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(exist_ok=True)

def _get_state():
    """Return the persistent debug state dict (survives module reloads)."""
    import debug as _self
    if not hasattr(_self, "_PERSIST"):
        _log_file = _log_dir / f"{datetime.now().strftime('%Y%m%d_%H.%M.%S')}-bong.log"
        _log_file.touch(exist_ok=True)
        _self._PERSIST = {
            "debug_mode": True,
            "log_file": _log_file,
        }
    return _self._PERSIST

def _elapsed_str():
    elapsed = time.monotonic() - _start
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    s = int(elapsed % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def log(tag, *args):
    if _get_state()["debug_mode"]:
        print(f"[{_elapsed_str()}] <{tag}>", *args)

def log_to_file(tag, *args):
    ts = _elapsed_str()
    line = f"[{ts}] <{tag}> " + " ".join(str(a) for a in args) + "\n"
    with _get_state()["log_file"].open("a", encoding="utf-8") as f:
        f.write(line)

def toggle_debug(enabled: bool = None) -> bool:
    state = _get_state()
    if enabled is not None:
        state["debug_mode"] = enabled
    else:
        state["debug_mode"] = not state["debug_mode"]
    return state["debug_mode"]