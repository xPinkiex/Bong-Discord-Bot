#!/usr/bin/env python3
# TODO: Add manual merge (let user pick any entries to merge, not just detected duplicates)
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BONG_DATA = PROJECT_ROOT / "bong_data"

from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings
from langchain_ollama.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

DB_DIR = BONG_DATA / "chroma_db"

parser = argparse.ArgumentParser(description="Deduplicate Bong's long-term memory by merging duplicates")
parser.add_argument("-t", "--threshold", type=float, default=0.7, help="Similarity threshold for dedup (default: 0.7)")
parser.add_argument("-d", "--dry-run", action="store_true", help="Show what would be merged without making changes")
parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
args = parser.parse_args()

embeddings = OllamaEmbeddings(model="nomic-embed-text")
db = Chroma(
    collection_name="bong_memories",
    embedding_function=embeddings,
    persist_directory=str(DB_DIR),
)

llm = ChatOllama(model="glm-5.1:cloud", temperature=0.3)

SUMMARIZE_PROMPT = """You are merging duplicate memory entries for a Discord bot's long-term memory.
Given multiple similar memory entries, combine them into ONE concise sentence that preserves ALL unique information.
Be brief. Do not add new information. Just merge what's there.

Entries to merge:
{entries}

Merged result:"""

collection = db._collection
all_data = collection.get(include=["documents", "metadatas", "embeddings"])
all_ids = all_data["ids"]
all_texts = all_data["documents"]

if not all_ids:
    print("No memories stored yet.")
    sys.exit()

print(f"Checking {len(all_ids)} memories for duplicates (threshold: {args.threshold})...\n")

merge_groups = []
seen = set()

for i, (doc_id, text) in enumerate(zip(all_ids, all_texts)):
    if doc_id in seen:
        continue
    results = db.similarity_search_with_relevance_scores(text, k=5)
    dupes = []
    for match_doc, score in results:
        if score >= args.threshold and match_doc.page_content != text:
            for j, (sid, stxt) in enumerate(zip(all_ids, all_texts)):
                if stxt == match_doc.page_content and sid not in seen and sid != doc_id:
                    dupes.append((sid, match_doc.page_content, score))
                    break

    if not dupes:
        continue

    dupe_ids = [d[0] for d in dupes]
    dupe_texts = [d[1] for d in dupes]
    best_score = max(d[2] for d in dupes)

    merge_groups.append((doc_id, text, dupe_ids, dupe_texts, best_score))
    seen.update(dupe_ids)
    seen.add(doc_id)

if not merge_groups:
    print("No duplicates found.")
    sys.exit()

print(f"Found {len(merge_groups)} group(s) to merge:\n")

# Each final group: list of (id, text) to merge, list of (id, text) to delete as exclusions
final_groups = []

for i, (keep_id, keep_text, dupe_ids, dupe_texts, score) in enumerate(merge_groups, 1):
    # Number all entries: 0 = keep, 1..n = dupes
    all_entries = [(keep_id, keep_text)] + list(zip(dupe_ids, dupe_texts))
    print(f"  Group {i} [{score:.2f}]:")
    for j, (_, txt) in enumerate(all_entries):
        print(f"    {j}. {txt}")
    print()

    excluded_indices = set()
    if not args.yes:
        exclude_input = input(f"  Exclude entries from group {i}? (comma-separated numbers, or Enter for none): ").strip()
        if exclude_input:
            for num in exclude_input.split(","):
                num = num.strip()
                if num.isdigit() and 0 <= int(num) < len(all_entries):
                    excluded_indices.add(int(num))
                    print(f"    Excluding #{int(num)}: {all_entries[int(num)][1][:60]}...")

        choice = input(f"  Merge group {i}? [y/n/a(ll)/q(uit)] ").strip().lower()
        if choice == "q":
            print("Cancelled.")
            sys.exit()
        elif choice == "a":
            # Auto-accept all remaining groups
            final_groups.append((all_entries, excluded_indices))
            for g in merge_groups[i:]:
                g_entries = [(g[0], g[1])] + list(zip(g[2], g[3]))
                final_groups.append((g_entries, set()))
            break
        elif choice != "y":
            print(f"  Skipping group {i}.\n")
            continue
    else:
        excluded_indices = set()

    final_groups.append((all_entries, excluded_indices))

if not final_groups:
    print("Nothing to merge.")
    sys.exit()

if args.dry_run:
    print("Dry run — no changes made.")
    sys.exit()

for all_entries, excluded_indices in final_groups:
    to_merge = [(eid, txt) for j, (eid, txt) in enumerate(all_entries) if j not in excluded_indices]

    if len(to_merge) < 2:
        # Only one entry left, nothing to merge
        if to_merge:
            print(f"  Only one entry remains, keeping as-is: {to_merge[0][1][:60]}...")
        continue

    entries_text = "\n".join(f"- {txt}" for _, txt in to_merge)
    prompt = SUMMARIZE_PROMPT.format(entries=entries_text)
    response = llm.invoke([SystemMessage(content="You are a memory consolidation assistant."), HumanMessage(content=prompt)])
    merged = response.content.strip()

    # Get metadata from first entry
    orig = collection.get(ids=[to_merge[0][0]], include=["metadatas"])
    orig_meta = orig["metadatas"][0] if orig["metadatas"] else {}

    # Delete all entries in the merge group
    collection.delete(ids=[eid for eid, _ in to_merge])

    # Re-add the merged entry
    db.add_texts(
        texts=[merged],
        metadatas=[orig_meta],
    )

    print(f"  Merged into: {merged}")

print(f"\nDone. Processed {len(final_groups)} group(s).")