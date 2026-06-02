import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BONG_DATA = PROJECT_ROOT / "bong_data"
BONG_USER_DATA = PROJECT_ROOT / "bong_user_data"

from langchain_core.tools import tool
from ddgs import DDGS
from yt_dlp import YoutubeDL
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings
from langchain_ollama.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

import bong_tools
import debug
import reminders
import user_data

DOWNLOAD_DIR = BONG_DATA / "saved_sounds"
DOWNLOAD_DIR.mkdir(exist_ok=True)
IMAGE_DIR = BONG_DATA / "saved_images"
IMAGE_DIR.mkdir(exist_ok=True)
TEXT_DIR = BONG_DATA / "saved_texts"
TEXT_DIR.mkdir(exist_ok=True)
SONG_STATS_FILE = BONG_DATA / "song_stats.json"
_song_stats: dict[str, int] = {}
_song_stats_dirty = False

DB_DIR = BONG_DATA / "chroma_db"
_embeddings = OllamaEmbeddings(model="nomic-embed-text", keep_alive=-1)
_vector_db = Chroma(
    collection_name="bong_memories",
    embedding_function=_embeddings,
    persist_directory=str(DB_DIR),
)

_BOILERPLATE = re.compile(r"\bbong\b['']?s?\b", re.IGNORECASE)
_USERID_TAG = re.compile(r"\s*\(userID:?\s*\d+\)", re.IGNORECASE)
USER_MEMORY_SCORE_BOOST = 0.25
MEMORY_EXPIRY_DAYS = 180
CONTRADICTION_THRESHOLD = 0.75
_contradiction_model = ChatOllama(model="gemma3:12b-cloud", temperature=0.0, num_predict=5, keep_alive=-1)

_SUMMARIZE_PROMPT = (
    "Summarize the following web page in 2-3 short sentences. "
    "Be concise and focus on the key point. "
    "If the content is too brief or empty to summarize, say so.\n\n"
    "{content}"
)
_SUMMARIZE_MODEL = ChatOllama(model="gemma3:12b-cloud", temperature=0.3, num_predict=256, keep_alive=-1)

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
pending_skip_info = ""
pending_send_image = None
pending_send_text = None
pending_start_listening = None
pending_stop_listening = False

voice_connected = False
caller_in_voice = False
current_user_id = None
current_channel_id = None
authorized = False
current_username = ""
start_time = None
shuffle_enabled = False
loop_enabled = False
loop_track = None
current_track = None
song_queue: list[str] = []

image_library = []
text_library = []
music_library = []

from bong_music import tools as _music_tools
from bong_memory import tools as _memory_tools
from bong_web import tools as _web_tools
from bong_state import tools as _state_tools
tools = _music_tools + _memory_tools + _web_tools + _state_tools
tool_map = {t.name: t for t in tools}


def load_song_stats():
    global _song_stats
    try:
        if SONG_STATS_FILE.exists():
            with open(SONG_STATS_FILE, "r") as f:
                _song_stats = json.load(f)
    except Exception:
        _song_stats = {}


def _save_song_stats():
    try:
        with open(SONG_STATS_FILE, "w") as f:
            json.dump(bong_tools._song_stats, f, indent=2)
        bong_tools._song_stats_dirty = False
    except Exception:
        pass


def _increment_song(title: str):
    _song_stats[title] = _song_stats.get(title, 0) + 1
    bong_tools._song_stats_dirty = True


def _get_top_songs(n: int = 3) -> list[tuple[str, int]]:
    sorted_songs = sorted(bong_tools._song_stats.items(), key=lambda x: x[1], reverse=True)
    return sorted_songs[:n]


def _get_total_plays() -> int:
    return sum(bong_tools._song_stats.values())


def _clean_for_embedding(text: str) -> str:
    text = _BOILERPLATE.sub("", text)
    text = _USERID_TAG.sub("", text)
    return text.strip()


def _apply_recency_boost(score: float, saved_at: float, halflife_days: float = 60.0) -> float:
    if not saved_at:
        return score
    age_days = (datetime.now().timestamp() - saved_at) / 86400.0
    if age_days < 0:
        age_days = 0
    recency_boost = 0.15 * (0.5 ** (age_days / halflife_days))
    return score + recency_boost


def _batch_increment_access_counts(doc_ids: list):
    valid_ids = [did for did in doc_ids if did is not None]
    if not valid_ids:
        return
    try:
        collection = bong_tools._vector_db._collection
        result = collection.get(ids=valid_ids, include=["metadatas"])
        if not result["metadatas"]:
            return
        updated_ids = []
        updated_metas = []
        for i, meta in enumerate(result["metadatas"]):
            new_meta = dict(meta)
            raw_count = new_meta.get("access_count", 0)
            new_meta["access_count"] = (int(raw_count) + 1) if isinstance(raw_count, (int, float)) else 1
            updated_ids.append(result["ids"][i])
            updated_metas.append(new_meta)
        collection.update(ids=updated_ids, metadatas=updated_metas)
    except Exception:
        pass


def retrieve_memories(query: str, username: str = "", user_id: int | None = None, k: int = 10) -> str:
    try:
        seen_ids = set()
        all_results = []
        cleaned_query = bong_tools._clean_for_embedding(query)
        cleaned_name = bong_tools._clean_for_embedding(username) if username else ""

        searches = []
        is_user_search = []
        if user_id:
            searches.append(bong_tools._vector_db.similarity_search_with_relevance_scores(
                cleaned_query, k=k, filter={"user_id": user_id}
            ))
            is_user_search.append(True)
        searches.append(bong_tools._vector_db.similarity_search_with_relevance_scores(cleaned_query, k=k))
        is_user_search.append(False)
        if cleaned_name:
            searches.append(bong_tools._vector_db.similarity_search_with_relevance_scores(cleaned_name, k=k))
            is_user_search.append(False)

        for search_docs, from_user_search in zip(searches, is_user_search):
            for doc, score in search_docs:
                if score < 0.5:
                    continue
                doc_id = doc.id if hasattr(doc, 'id') else doc.metadata.get("id")
                norm = doc.page_content.strip().lower()
                dedup_key = doc_id if doc_id is not None else norm
                if dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)
                adjusted_score = score * (1.0 + bong_tools.USER_MEMORY_SCORE_BOOST) if from_user_search else score
                saved_at = doc.metadata.get("saved_at")
                if saved_at:
                    adjusted_score = bong_tools._apply_recency_boost(adjusted_score, saved_at)
                access_count = doc.metadata.get("access_count", 0)
                if access_count:
                    adjusted_score += min(0.05 * access_count, 0.25)
                all_results.append((doc, doc_id, adjusted_score))

        if not all_results:
            debug.log("Memory", "No relevant memories found")
            return ""
        bong_tools._batch_increment_access_counts([doc_id for _, doc_id, _ in all_results])
        debug.log("Memory", f"Retrieved {len(all_results)} memories for query")
        formatted = []
        for doc, _, s in sorted(all_results, key=lambda x: x[2], reverse=True):
            meta_parts = []
            saved_at = doc.metadata.get("saved_at")
            if saved_at:
                try:
                    meta_parts.append(f"saved {datetime.fromtimestamp(saved_at).strftime('%Y-%m-%d')}")
                except Exception:
                    pass
            uname = doc.metadata.get("username")
            if uname:
                meta_parts.append(f"about {uname}")
            meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
            formatted.append(f"- {doc.page_content}{meta_str}")
        return "\n".join(formatted)
    except Exception as e:
        debug.log("Memory", f"Retrieval error: {e}")
        return ""


def _extract_response_text(response) -> str:
    content = response.content
    if isinstance(content, list):
        return "".join(chunk.text if hasattr(chunk, "text") else str(chunk) for chunk in content)
    return str(content or "")


def _is_contradiction(new_fact: str, existing_fact: str) -> bool:
    try:
        prompt = (
            f"Are these two facts contradictory (i.e. they cannot both be true)? "
            f"Answer ONLY 'YES' or 'NO'.\n\n"
            f"Fact A: {existing_fact}\n"
            f"Fact B: {new_fact}"
        )
        response = bong_tools._contradiction_model.invoke([
            SystemMessage(content="You are a precise logic checker. Answer only YES or NO."),
            HumanMessage(content=prompt),
        ])
        answer = bong_tools._extract_response_text(response).upper()
        return "YES" in answer
    except Exception as e:
        debug.log("Memory", f"Contradiction check failed: {e}")
        return False


def _find_contradiction(fact: str, user_id: int | None) -> str | None:
    try:
        candidates = []
        if user_id:
            similar = bong_tools._vector_db.similarity_search_with_relevance_scores(
                fact, k=5, filter={"user_id": user_id}
            )
            for doc, score in similar:
                if score >= bong_tools.CONTRADICTION_THRESHOLD:
                    candidates.append(doc)
        if not candidates:
            similar_general = bong_tools._vector_db.similarity_search_with_relevance_scores(fact, k=5)
            for doc, score in similar_general:
                if score >= bong_tools.CONTRADICTION_THRESHOLD:
                    if not user_id or doc.metadata.get("user_id") == user_id:
                        candidates.append(doc)
        for doc in candidates:
            if bong_tools._is_contradiction(fact, doc.page_content):
                return doc.id if hasattr(doc, 'id') else doc.metadata.get("id")
        return None
    except Exception:
        return None


def _expire_old_memories(days: int = MEMORY_EXPIRY_DAYS):
    try:
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()
        collection = bong_tools._vector_db._collection
        result = collection.get(where={"saved_at": {"$lt": cutoff}})
        if result["ids"]:
            collection.delete(ids=result["ids"])
            debug.log("Memory", f"Expired {len(result['ids'])} old memories")
    except Exception as e:
        debug.log("Memory", f"Expiry cleanup failed: {e}")


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
    bong_tools.pending_skip_info = ""
    bong_tools.pending_send_image = None
    bong_tools.pending_send_text = None
    bong_tools.pending_start_listening = None
    bong_tools.pending_stop_listening = False


def refresh_image_library():
    bong_tools.image_library = sorted(
        p for p in bong_tools.IMAGE_DIR.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
    )


def refresh_text_library():
    bong_tools.text_library = sorted(
        p for p in bong_tools.TEXT_DIR.iterdir()
        if p.suffix.lower() in (".txt", ".md", ".py", ".json", ".csv", ".xml", ".yaml", ".yml", ".cfg", ".ini", ".log", ".toml", ".rs", ".js", ".ts", ".html", ".css", ".sh", ".bat")
    )


def refresh_music_library():
    bong_tools.music_library = sorted(bong_tools.DOWNLOAD_DIR.glob("*.mp3"))


refresh_music_library()
refresh_image_library()
refresh_text_library()