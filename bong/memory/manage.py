"""Look at and edit Bong's memories from the command line (stop the bot first).

    python -m bong.memory.manage list [--about USER_ID]
    python -m bong.memory.manage search "music taste" [--about USER_ID]
    python -m bong.memory.manage add "Eve loves drum and bass" --about 273761843544064000 --name Eve
    python -m bong.memory.manage edit 3f9a12bc "Eve loves drum and bass and jungle"
    python -m bong.memory.manage delete 3f9a12bc
    python -m bong.memory.manage forget-user 273761843544064000 [--yes]
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT
from . import CATEGORIES, GENERAL, Memory
from .chroma_store import ChromaMemoryStore
from .embedder import PinnedMiniLM


def show(m: Memory, score: float | None = None) -> str:
    who = f"{m.about_name or m.about_user_id}" if m.about_user_id != GENERAL else "general"
    when = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d") if m.created_at else "?"
    sc = f" {score:.2f}" if score is not None else ""
    return f"#{m.id}{sc} [{who}] ({m.category}, {m.importance}, {when}) {m.text}"


async def run(args, store: ChromaMemoryStore) -> int:
    await store.start()
    if args.cmd == "list":
        items = await store.all()
        if args.about is not None:
            items = [m for m in items if m.about_user_id == args.about]
        for m in sorted(items, key=lambda m: (m.about_name.lower(), -m.importance, m.created_at)):
            print(show(m))
        print(f"{len(items)} memories")
    elif args.cmd == "search":
        for m, score in await store.search(args.query, about_user_id=args.about, limit=args.limit, min_similarity=0.0):
            print(show(m, score))
    elif args.cmd == "add":
        m, created = await store.add(args.text, about_user_id=args.about or GENERAL, about_name=args.name,
                                     category=args.category, importance=args.importance)
        print(("Added " if created else "Already have something very similar: ") + show(m))
    elif args.cmd == "edit":
        m = await store.update(args.id, args.text)
        print(f"Updated {show(m)}" if m else f"No memory #{args.id}")
        return 0 if m else 1
    elif args.cmd == "delete":
        ok = await store.delete(args.id)
        print(f"Deleted #{args.id}" if ok else f"No memory #{args.id}")
        return 0 if ok else 1
    elif args.cmd == "forget-user":
        items = [m for m in await store.all() if m.about_user_id == args.user_id]
        if not items:
            print("No memories about that user.")
            return 0
        for m in items:
            print(show(m))
        if not args.yes and input(f"Delete these {len(items)} memories? [y/N] ").strip().lower() != "y":
            print("Nothing deleted.")
            return 1
        for m in items:
            await store.delete(m.id)
        print(f"Deleted {len(items)} memories.")
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m bong.memory.manage", description="Manage Bong's memories.")
    p.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "bong_data")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="list memories")
    ls.add_argument("--about", type=int, help="only memories about this user ID (0 = general)")
    se = sub.add_parser("search", help="search by meaning")
    se.add_argument("query")
    se.add_argument("--about", type=int)
    se.add_argument("--limit", type=int, default=10)
    ad = sub.add_parser("add", help="add a memory")
    ad.add_argument("text")
    ad.add_argument("--about", type=int, help="user ID it's about (default: general)")
    ad.add_argument("--name", default="", help="that person's name")
    ad.add_argument("--category", default="fact", choices=CATEGORIES)
    ad.add_argument("--importance", type=int, default=3, choices=range(1, 6))
    ed = sub.add_parser("edit", help="change a memory's text")
    ed.add_argument("id")
    ed.add_argument("text")
    de = sub.add_parser("delete", help="delete a memory")
    de.add_argument("id")
    fu = sub.add_parser("forget-user", help="delete every memory about a user")
    fu.add_argument("user_id", type=int)
    fu.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = p.parse_args(argv)

    store = ChromaMemoryStore(args.data_dir / "memory", PinnedMiniLM(args.data_dir / "embedding_model"))
    raise SystemExit(asyncio.run(run(args, store)))


if __name__ == "__main__":
    main()
