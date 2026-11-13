"""Import memories from the old bot's ChromaDB (bong_data/chroma_db).

    python -m bong.memory.migrate               # dry run: shows what would be imported
    python -m bong.memory.migrate --apply       # import

Only the text and metadata are taken; everything is re-embedded with the
pinned model. The old database is copied to a temp folder first, so it's
never modified. Running it twice is safe: memories already imported are
skipped, and near-duplicates are skipped too.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import tempfile
from pathlib import Path

from ..config import PROJECT_ROOT
from . import CATEGORIES, GENERAL
from .chroma_store import ChromaMemoryStore
from .embedder import PinnedMiniLM

OLD_COLLECTION = "bong_memories"


def read_old(old_db: Path) -> list[dict]:
    import chromadb
    from chromadb.config import Settings

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "chroma_db"
        shutil.copytree(old_db, copy)
        client = chromadb.PersistentClient(path=str(copy), settings=Settings(anonymized_telemetry=False))
        data = client.get_collection(OLD_COLLECTION).get(include=["documents", "metadatas"])
    rows = []
    for old_id, text, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        meta = meta or {}
        try:
            about = int(meta.get("user_id")) if meta.get("user_id") not in (None, "") else GENERAL
        except (TypeError, ValueError):
            about = GENERAL
        category = str(meta.get("category", "fact"))
        try:
            importance = int(meta.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3
        rows.append({
            "old_id": old_id,
            "text": text or "",
            "about_user_id": about,
            "about_name": str(meta.get("user_name", "") or "") if about != GENERAL else "",
            "category": category if category in CATEGORIES else "fact",
            "importance": importance,
            "created_at": float(meta.get("saved_at", 0) or 0),
        })
    return [r for r in rows if r["text"].strip()]


async def migrate(old_db: Path, store: ChromaMemoryStore, apply: bool) -> dict[str, int]:
    rows = read_old(old_db)
    counts = {"found": len(rows), "imported": 0, "already_imported": 0, "duplicates": 0}
    await store.start()
    existing = {m.metadata.get("migrated_from") for m in await store.all()}
    for row in rows:
        if row["old_id"] in existing:
            counts["already_imported"] += 1
            continue
        if not apply:
            about = row["about_name"] or "general"
            print(f"  would import [{about}] ({row['category']}, {row['importance']}) {row['text']}")
            continue
        _, created = await store.add(row["text"], about_user_id=row["about_user_id"], about_name=row["about_name"],
                                     category=row["category"], importance=row["importance"], saved_by=0,
                                     metadata={"migrated_from": row["old_id"]},
                                     created_at=row["created_at"] or None)
        counts["imported" if created else "duplicates"] += 1
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m bong.memory.migrate", description=__doc__.split("\n\n")[0])
    parser.add_argument("old_db", nargs="?", type=Path, default=PROJECT_ROOT / "bong_data" / "chroma_db")
    parser.add_argument("--apply", action="store_true", help="actually import (default is a dry run)")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "bong_data")
    args = parser.parse_args(argv)

    if not args.old_db.exists():
        raise SystemExit(f"No old memory database at {args.old_db}")
    store = ChromaMemoryStore(args.data_dir / "memory", PinnedMiniLM(args.data_dir / "embedding_model"))
    counts = asyncio.run(migrate(args.old_db, store, args.apply))
    print(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items()))
    if not args.apply:
        print("Dry run — run again with --apply to import.")


if __name__ == "__main__":
    main()
