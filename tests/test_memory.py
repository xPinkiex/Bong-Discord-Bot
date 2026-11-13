"""Long-term memory: the Chroma store, the pinned embedder, the tools and migration."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

pytest.importorskip("chromadb")

from bong.memory import GENERAL
from bong.memory.chroma_store import ChromaMemoryStore
from bong.memory.embedder import MODEL_NAME, EmbeddingModelError, PinnedMiniLM
from bong.tools.memory import memory_tools

from conftest import make_turn

MODEL_DIR = Path(__file__).resolve().parent.parent / "bong_data" / "embedding_model"
HAVE_MODEL = (MODEL_DIR / MODEL_NAME / "onnx" / "model.onnx").exists()
needs_model = pytest.mark.skipif(not HAVE_MODEL, reason="embedding model not downloaded (run the bot once)")

EVE, RADO, MIA, ADMIN = 1, 2, 3, 9


class WordEmbedder:
    """Tiny deterministic stand-in: hashed bag of words. ``salt`` makes a 'different model'."""

    dimensions = 64

    def __init__(self, name="words-v1", salt=""):
        self.name, self.salt = name, salt

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            for w in t.lower().replace(".", " ").split():
                v[int(hashlib.md5((self.salt + w).encode()).hexdigest(), 16) % self.dimensions] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture(scope="module")
def real_embedder():
    if not HAVE_MODEL:
        pytest.skip("embedding model not downloaded")
    e = PinnedMiniLM(MODEL_DIR)
    e.load()
    return e


@pytest.fixture
async def store(tmp_path, real_embedder):
    s = ChromaMemoryStore(tmp_path / "memory", real_embedder)
    await s.start()
    return s


# --- store --------------------------------------------------------------------

@needs_model
async def test_recall_prefers_the_speaker_and_relevant_facts(store):
    await store.add("Eve loves dubstep and Skrillex", about_user_id=EVE, about_name="Eve", category="preference")
    await store.add("Eve is allergic to peanuts", about_user_id=EVE, about_name="Eve", importance=5)
    await store.add("Rado has a cat named Miso", about_user_id=RADO, about_name="Rado")
    await store.add("The server has a meme channel", category="fact")
    await store.add("Mia hates pineapple pizza", about_user_id=MIA, about_name="Mia")

    got = [m.text for m in await store.recall(make_turn("bong what music should I listen to", author_id=EVE))]
    assert got[0] == "Eve loves dubstep and Skrillex"
    assert "Eve is allergic to peanuts" in got          # important facts about the speaker are always known
    assert "Rado has a cat named Miso" not in got        # unrelated memories about others stay out

    got = [m.text for m in await store.recall(make_turn("bong does rado have any pets?", author_id=EVE))]
    assert "Rado has a cat named Miso" in got            # relevant memories about others come in

    got = [m.text for m in await store.recall(make_turn("bong hi", author_id=RADO))]
    assert got[0] == "Rado has a cat named Miso" and "Eve is allergic to peanuts" not in got


@needs_model
async def test_near_duplicates_are_not_saved_twice(store):
    first, created = await store.add("Eve loves dubstep", about_user_id=EVE, about_name="Eve")
    again, created_again = await store.add("Eve loves dubstep!", about_user_id=EVE, about_name="Eve")
    assert created and not created_again and again.id == first.id
    _, other_person = await store.add("Eve loves dubstep", about_user_id=RADO, about_name="Rado")
    assert other_person  # same text about someone else is a different memory
    assert await store.count() == 2


@needs_model
async def test_update_delete_and_backup_file(store):
    m, _ = await store.add("Eve's favourite colour is blue", about_user_id=EVE, about_name="Eve")
    updated = await store.update("#" + m.id, "Eve's favourite colour is green")
    assert updated.text == "Eve's favourite colour is green" and updated.about_user_id == EVE
    hits = await store.search("favourite colour", about_user_id=EVE)
    assert hits[0][0].text.endswith("green")

    backup = json.loads((store.path / "memories-backup.json").read_text())
    assert [(r["id"], r["text"], r["about_user_id"]) for r in backup] == [(m.id, "Eve's favourite colour is green", EVE)]
    assert await store.delete(m.id) and not await store.delete(m.id)
    assert json.loads((store.path / "memories-backup.json").read_text()) == []


@needs_model
async def test_discord_sized_ids_survive(store):
    big = 273761843544064000
    m, _ = await store.add("Owner fact", about_user_id=big, about_name="Eve")
    assert (await store.get(m.id)).about_user_id == big
    assert [x.text for x in await store.recall(make_turn("hi", author_id=big))] == ["Owner fact"]


# --- pinning ----------------------------------------------------------------------

async def test_model_change_reembeds_everything_from_text(tmp_path):
    path = tmp_path / "memory"
    old = ChromaMemoryStore(path, WordEmbedder("words-v1"))
    await old.start()
    await old.add("Rado has a cat named Miso", about_user_id=RADO, about_name="Rado")
    await old.add("Eve loves dubstep", about_user_id=EVE, about_name="Eve")

    new = ChromaMemoryStore(path, WordEmbedder("words-v2", salt="different"))
    await new.start()

    assert await new.count() == 2
    hits = await new.search("cat named Miso", about_user_id=RADO, min_similarity=0.5)
    assert hits and hits[0][0].text == "Rado has a cat named Miso"   # search works with the new vectors
    assert json.loads((path / "embedding.json").read_text())["model"] == "words-v2"
    assert list(path.glob("memories-backup-before-reembed-*.json"))


async def test_same_model_restart_does_not_reembed(tmp_path):
    path = tmp_path / "memory"
    s = ChromaMemoryStore(path, WordEmbedder())
    await s.start()
    await s.add("a fact", about_user_id=EVE)
    s2 = ChromaMemoryStore(path, WordEmbedder())
    await s2.start()
    assert not list(path.glob("memories-backup-before-reembed-*.json"))


@needs_model
def test_tampered_model_files_are_refused(tmp_path):
    copy = tmp_path / "models"
    shutil.copytree(MODEL_DIR, copy)
    vocab = copy / MODEL_NAME / "onnx" / "vocab.txt"
    vocab.write_text(vocab.read_text() + "extra\n")
    with pytest.raises(EmbeddingModelError, match="pinned version"):
        PinnedMiniLM(copy).load()


# --- tools ---------------------------------------------------------------------------

def tools_for(store, names=None, admins=(ADMIN,)):
    names = names or {"rado": (RADO, "Rado"), "mia": (MIA, "Mia")}
    tools = memory_tools(store, lambda n, t: names.get(n.lower()), lambda uid: uid in admins)
    return {t.name: t.handler for t in tools}


async def test_tools_save_about_people_and_report_unknown_names(tmp_path):
    store = ChromaMemoryStore(tmp_path / "m", WordEmbedder())
    await store.start()
    t = tools_for(store)
    eve = make_turn("bong", author_id=EVE, author_name="Eve")

    out = await t["save_memory"](eve, {"fact": "I prefer to be called a creecher", "category": "preference",
                                        "importance": 3, "about": "me"})
    assert out.startswith("Remembered: #") and "(about Eve)" in out
    out = await t["save_memory"](eve, {"fact": "Rado has a cat", "category": "fact", "importance": 2, "about": "Rado"})
    assert "(about Rado)" in out
    out = await t["save_memory"](eve, {"fact": "Zed likes trains", "category": "nonsense", "importance": 99,
                                        "about": "Zed"})
    assert "Couldn't find anyone called \"Zed\"" in out

    mems = {m.text: m for m in await store.all()}
    assert mems["I prefer to be called a creecher"].about_user_id == EVE
    assert mems["Rado has a cat"].about_user_id == RADO and mems["Rado has a cat"].saved_by == EVE
    zed = mems["Zed likes trains"]
    assert zed.about_user_id == GENERAL and zed.category == "fact" and zed.importance == 5

    out = await t["recall_memories"](eve, {"query": "pets", "about": "rado"})
    assert "Rado has a cat" in out and "#" in out


async def test_only_subject_saver_or_admin_can_change_a_memory(tmp_path):
    store = ChromaMemoryStore(tmp_path / "m", WordEmbedder())
    await store.start()
    t = tools_for(store)
    about_rado, _ = await store.add("Rado has a cat", about_user_id=RADO, about_name="Rado", saved_by=RADO)
    general, _ = await store.add("The server has a meme channel", saved_by=RADO)

    mia = make_turn("bong", author_id=MIA, author_name="Mia")
    assert "can't make you forget" in await t["forget_memory"](mia, {"memory_id": about_rado.id})
    assert "can't change" in await t["update_memory"](mia, {"memory_id": "#" + general.id, "fact": "lies"})
    assert await store.count() == 2

    rado = make_turn("bong", author_id=RADO, author_name="Rado")
    assert (await t["update_memory"](rado, {"memory_id": about_rado.id, "fact": "Rado has two cats"})).startswith("Updated")
    admin = make_turn("bong", author_id=ADMIN, author_name="Boss")
    assert (await t["forget_memory"](admin, {"memory_id": general.id})).startswith("Forgotten")
    assert "No memory with ID" in await t["forget_memory"](admin, {"memory_id": "nope"})
    assert [m.text for m in await store.all()] == ["Rado has two cats"]


# --- migration -----------------------------------------------------------------------

async def test_migration_imports_old_memories_once(tmp_path):
    import chromadb
    from chromadb.config import Settings
    from bong.memory.migrate import migrate

    old_dir = tmp_path / "chroma_db"
    old = chromadb.PersistentClient(path=str(old_dir), settings=Settings(anonymized_telemetry=False))
    col = old.create_collection("bong_memories", embedding_function=None)
    col.add(ids=["o1", "o2", "o3"], embeddings=[[0.1] * 8, [0.2] * 8, [0.3] * 8],
            documents=["Eve loves dubstep", "the server has a meme channel", "Rado has a cat"],
            metadatas=[{"user_id": EVE, "user_name": "Eve", "category": "preference", "importance": 4,
                        "saved_at": 1_700_000_000.0},
                       {"user_name": "", "category": "fact", "importance": 2},
                       {"user_id": RADO, "user_name": "Rado", "category": "weird", "importance": 3}])
    del old, col

    store = ChromaMemoryStore(tmp_path / "memory", WordEmbedder())
    dry = await migrate(old_dir, store, apply=False)
    assert dry["found"] == 3 and dry["imported"] == 0 and await store.count() == 0

    counts = await migrate(old_dir, store, apply=True)
    assert counts["imported"] == 3
    mems = {m.text: m for m in await store.all()}
    assert mems["Eve loves dubstep"].about_user_id == EVE and mems["Eve loves dubstep"].importance == 4
    assert mems["Eve loves dubstep"].created_at == 1_700_000_000.0
    assert mems["the server has a meme channel"].about_user_id == GENERAL
    assert mems["Rado has a cat"].category == "fact"

    again = await migrate(old_dir, store, apply=True)
    assert again["already_imported"] == 3 and await store.count() == 3
