"""Bong's prompt files. Edit the .md files to change how she talks; no code changes needed."""

from __future__ import annotations

import re
from pathlib import Path

PERSONA_DIR = Path(__file__).resolve().parent

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load(name: str) -> str:
    return (PERSONA_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def fill(template: str, **values: str) -> str:
    """Fill {placeholders} in one pass.

    Unlike chained str.replace, text inserted for one placeholder is never
    re-scanned, so a user typing "{history}" can't pull other fields in.
    Unknown placeholders are left as-is.
    """
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)
