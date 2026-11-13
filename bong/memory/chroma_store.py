"""Long-term memory in a local Chroma database.

Each memory is a short fact with metadata: who it's about (a Discord user ID,
or GENERAL), that person's name when it was saved, a category, an importance
1-5, who caused it to be saved, and when. Vectors come from the pinned
embedder; Chroma only stores and searches them.

Files (under bong_data/memory/):
    chroma/              the Chroma database
    embedding.json       which model made the vectors + its probe vector
    memories-backup.json readable export of every memory (text + metadata)

All Chroma and embedding work is blocking, so the async methods run it in a
worker thread, one operation at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
import uuid
from pathlib import Path

from ..conversation.turn import Turn
from . import CATEGORIES, GENERAL, Memory
from .embedder import PROBE_TEXT, Embedder

log = logging.getLogger(__name__)

COLLECTION = "memories"
DUPLICATE_SIMILARITY = 0.90     # saving something this close to an existing memory is a no-op
MIN_SPEAKER_SIMILARITY = 0.20   # memories about the person talking
MIN_GENERAL_SIMILARITY = 0.35   # general facts
MIN_OTHERS_SIMILARITY = 0.45    # memories about other people
ALWAYS_KNOW_ABOUT_SPEAKER = 3   # top memories about the speaker included even if not "relevant"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_KNOWN_FIELDS = {"about_user_id", "about_name", "category", "importance", "created_at", "saved_by"}


def _to_memory(mid: str, text: str, meta: dict) -> Memory:
    return Memory(
        id=mid,
        text=text,
        about_user_id=int(meta.get("about_user_id", GENERAL)),
        about_name=str(meta.get("about_name", "")),
        category=str(meta.get("category", "fact")),
        importance=int(meta.get("importance", 3)),
        created_at=float(meta.get("created_at", 0.0)),
        saved_by=int(meta.get("saved_by", 0)),
        metadata={k: v for k, v in meta.items() if k not in _KNOWN_FIELDS},
    )


def _to_meta(m: Memory) -> dict:
    return {
        "about_user_id": int(m.about_user_id),
        "about_name": m.about_name,
        "category": m.category,
        "importance": int(m.importance),
        "created_at": float(m.created_at),
        "saved_by": int(m.saved_by),
        **{k: v for k, v in m.metadata.items() if isinstance(v, (str, int, float, bool))},
    }


class ChromaMemoryStore:
    def __init__(self, path: Path, embedder: Embedder):
        self.path = Path(path)
        self.embedder = embedder
        self._collection = None
        self._lock = threading.Lock()

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await asyncio.to_thread(self._start)

    def _start(self) -> None:
        import chromadb
        from chromadb.config import Settings

        if hasattr(self.embedder, "load"):
            self.embedder.load()
        self.path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.path / "chroma"),
                                           settings=Settings(anonymized_telemetry=False))
        self._collection = client.get_or_create_collection(
            COLLECTION, embedding_function=None, configuration={"hnsw": {"space": "cosine"}})
        self._check_fingerprint()
        self._export()
        log.info("Memory ready: %d memories in %s", self._collection.count(), self.path)

    def _check_fingerprint(self) -> None:
        """Make sure stored vectors came from the current model; re-embed everything if not."""
        manifest_path = self.path / "embedding.json"
        probe = self.embedder.embed([PROBE_TEXT])[0]
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None

        same = (manifest is not None
                and manifest.get("model") == self.embedder.name
                and len(manifest.get("probe", [])) == len(probe)
                and _cosine(manifest["probe"], probe) > 0.9999)
        if not same and self._collection.count() > 0:
            old = manifest.get("model") if manifest else "unknown"
            log.warning("Embedding model changed (%s -> %s): re-embedding %d memories from their text",
                        old, self.embedder.name, self._collection.count())
            self._export(self.path / f"memories-backup-before-reembed-{int(time.time())}.json")
            self._reembed_all()
        if not same:
            manifest_path.write_text(json.dumps({
                "model": self.embedder.name, "dimensions": len(probe), "probe_text": PROBE_TEXT, "probe": probe,
            }), encoding="utf-8")

    def _reembed_all(self, batch: int = 64) -> None:
        data = self._collection.get(include=["documents", "metadatas"])
        for i in range(0, len(data["ids"]), batch):
            ids, docs = data["ids"][i:i + batch], data["documents"][i:i + batch]
            self._collection.update(ids=ids, embeddings=self.embedder.embed(docs))

    def _export(self, path: Path | None = None) -> None:
        data = self._collection.get(include=["documents", "metadatas"])
        rows = [{"id": i, "text": d, **m} for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])]
        target = path or self.path / "memories-backup.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)

    async def _run(self, fn, *args, **kwargs):
        if self._collection is None:
            raise RuntimeError("Memory store not started")

        def locked():
            with self._lock:
                return fn(*args, **kwargs)
        return await asyncio.to_thread(locked)

    # --- MemoryStore interface ----------------------------------------------

    async def recall(self, turn: Turn, limit: int = 8) -> list[Memory]:
        query = turn.content
        if turn.reply_to:
            query = f"{query}\n{turn.reply_to.content}"
        return await self._run(self._recall, turn.author_id, query, limit)

    async def observe(self, turn: Turn, reply: str) -> None:
        return None  # Bong saves memories herself with tools

    # --- management (used by the memory tools) --------------------------------

    async def add(self, text: str, *, about_user_id: int = GENERAL, about_name: str = "", category: str = "fact",
                  importance: int = 3, saved_by: int = 0, metadata: dict | None = None,
                  created_at: float | None = None) -> tuple[Memory, bool]:
        """Save a memory. Returns (memory, created); created=False means a near-duplicate already existed."""
        memory = Memory(
            id=uuid.uuid4().hex[:8],
            text=" ".join(text.split()),
            about_user_id=int(about_user_id),
            about_name=about_name.strip(),
            category=category if category in CATEGORIES else "fact",
            importance=max(1, min(5, int(importance))),
            created_at=created_at or time.time(),
            saved_by=int(saved_by),
            metadata=metadata or {},
        )
        return await self._run(self._add, memory)

    async def update(self, memory_id: str, text: str) -> Memory | None:
        return await self._run(self._update, memory_id.lstrip("#"), " ".join(text.split()))

    async def delete(self, memory_id: str) -> bool:
        return await self._run(self._delete, memory_id.lstrip("#"))

    async def get(self, memory_id: str) -> Memory | None:
        return await self._run(self._get, memory_id.lstrip("#"))

    async def search(self, query: str, about_user_id: int | None = None, limit: int = 5,
                     min_similarity: float = 0.25) -> list[tuple[Memory, float]]:
        """Most similar memories; about_user_id=None searches everyone and general facts."""
        where = None if about_user_id is None else {"about_user_id": int(about_user_id)}
        return await self._run(self._search, query, where, limit, min_similarity)

    async def count(self) -> int:
        return await self._run(self._collection.count)

    async def all(self) -> list[Memory]:
        return await self._run(self._all)

    # --- blocking internals (run under the lock in a worker thread) -----------

    def _query(self, embedding: list[float], where: dict | None, limit: int) -> list[tuple[Memory, float]]:
        if self._collection.count() == 0:
            return []
        res = self._collection.query(query_embeddings=[embedding], n_results=limit, where=where,
                                     include=["documents", "metadatas", "distances"])
        return [(_to_memory(i, d, m), 1.0 - dist)
                for i, d, m, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])]

    def _search(self, query, where, limit, min_similarity):
        embedding = self.embedder.embed([query])[0]
        return [(m, s) for m, s in self._query(embedding, where, limit) if s >= min_similarity]

    def _recall(self, speaker_id: int, query: str, limit: int) -> list[Memory]:
        embedding = self.embedder.embed([query])[0]
        scored: dict[str, tuple[Memory, float]] = {}

        def take(results, min_sim, bonus=0.0):
            for m, s in results:
                if s >= min_sim and m.id not in scored:
                    scored[m.id] = (m, s + bonus + 0.03 * m.importance)

        take(self._query(embedding, {"about_user_id": speaker_id}, limit), MIN_SPEAKER_SIMILARITY, bonus=0.1)
        take(self._query(embedding, {"about_user_id": GENERAL}, limit), MIN_GENERAL_SIMILARITY)
        take(self._query(embedding, {"about_user_id": {"$nin": [GENERAL, speaker_id]}}, limit), MIN_OTHERS_SIMILARITY)

        # Always know a few things about whoever is talking, even if this message isn't about them.
        about_speaker = [m for m, _ in scored.values() if m.about_user_id == speaker_id]
        if len(about_speaker) < ALWAYS_KNOW_ABOUT_SPEAKER:
            data = self._collection.get(where={"about_user_id": speaker_id}, include=["documents", "metadatas"])
            extra = sorted((_to_memory(i, d, m) for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])),
                           key=lambda m: (m.importance, m.created_at), reverse=True)
            for m in extra:
                if len(about_speaker) >= ALWAYS_KNOW_ABOUT_SPEAKER:
                    break
                if m.id not in scored:
                    scored[m.id] = (m, 0.0)
                    about_speaker.append(m)

        ranked = sorted(scored.values(), key=lambda pair: pair[1], reverse=True)
        return [m for m, _ in ranked[:limit]]

    def _add(self, memory: Memory) -> tuple[Memory, bool]:
        embedding = self.embedder.embed([memory.text])[0]
        for existing, sim in self._query(embedding, {"about_user_id": memory.about_user_id}, 1):
            if sim >= DUPLICATE_SIMILARITY:
                return existing, False
        self._collection.add(ids=[memory.id], embeddings=[embedding], documents=[memory.text],
                             metadatas=[_to_meta(memory)])
        self._export()
        return memory, True

    def _all(self) -> list[Memory]:
        data = self._collection.get(include=["documents", "metadatas"])
        return [_to_memory(i, d, m) for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])]

    def _get(self, memory_id: str) -> Memory | None:
        data = self._collection.get(ids=[memory_id], include=["documents", "metadatas"])
        if not data["ids"]:
            return None
        return _to_memory(data["ids"][0], data["documents"][0], data["metadatas"][0])

    def _update(self, memory_id: str, text: str) -> Memory | None:
        old = self._get(memory_id)
        if old is None:
            return None
        meta = _to_meta(old) | {"updated_at": time.time()}
        self._collection.update(ids=[memory_id], embeddings=self.embedder.embed([text]),
                                documents=[text], metadatas=[meta])
        self._export()
        return self._get(memory_id)

    def _delete(self, memory_id: str) -> bool:
        if self._get(memory_id) is None:
            return False
        self._collection.delete(ids=[memory_id])
        self._export()
        return True
