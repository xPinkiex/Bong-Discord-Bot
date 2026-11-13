"""Small JSON files on disk, written atomically with a .bak of the previous version."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JsonFile:
    """Load once, mutate ``data`` in memory, call ``save()`` after each change.

    Writes go to a temp file that replaces the real one, so a crash mid-write
    can never leave a half-written file. The previous version is kept as
    ``<name>.bak``.
    """

    def __init__(self, path: Path, default: Any):
        self.path = Path(path)
        self._default = default
        self.data: Any = copy.deepcopy(default)

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def load(self) -> Any:
        for candidate in (self.path, self.backup_path):
            if not candidate.exists():
                continue
            try:
                self.data = json.loads(candidate.read_text(encoding="utf-8"))
                if candidate is self.backup_path:
                    log.warning("Loaded %s from backup", self.path.name)
                return self.data
            except (OSError, ValueError) as e:
                log.error("Could not read %s: %s", candidate, e)
        self.data = copy.deepcopy(self._default)
        return self.data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.backup_path)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
