#!/usr/bin/env python3
# TODO: Add manual merge
import argparse
from datetime import datetime
from pathlib import Path

from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

CSI = "\033["
RESET = f"{CSI}0m"
COLOR_EVEN = f"{CSI}36m"
COLOR_ODD = f"{CSI}33m"

DB_DIR = Path(__file__).resolve().parent.parent / "chroma_db"

parser = argparse.ArgumentParser(description="Manage Bong's long-term memory")
parser.add_argument("-w", "--what", help="Search memories by query")
parser.add_argument("-k", type=int, default=10, help="Number of search results (default: 10)")
parser.add_argument("-a", "--add", nargs="?", const="", help="Add a memory. Provide text or leave blank to be prompted")
parser.add_argument("-l", "--list", action="store_true", help="List all memories")
parser.add_argument("-d", "--delete", nargs="?", const="", help="Delete memories. Optionally provide a search query to filter, or leave blank to list all")
parser.add_argument("-e", "--edit", nargs="?", const="", help="Edit a memory. Optionally provide a search query to filter, or leave blank to list all")
args = parser.parse_args()

db = Chroma(
    collection_name="bong_memories",
    embedding_function=OllamaEmbeddings(model="nomic-embed-text"),
    persist_directory=str(DB_DIR),
)

collection = db._collection

def format_saved(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    return str(ts)

if args.add is not None:
    text = args.add
    if not text:
        text = input("Enter memory to add: ").strip()
        if not text:
            print("Cancelled.")
            exit()
    db.add_texts(
        texts=[text],
        metadatas=[{"saved_at": datetime.now().timestamp()}],
    )
    print(f"Added: {text}")

elif args.delete is not None:
    total = collection.count()
    if total == 0:
        print("No memories saved yet.")
        exit()

    if args.delete:
        results = db.similarity_search_with_relevance_scores(args.delete, k=10)
        indexed = []
        for i, (doc, score) in enumerate(results, 1):
            ts = doc.metadata.get('saved_at')
            c = COLOR_EVEN if i % 2 == 0 else COLOR_ODD
            print(f"  {c}{i}. [{score:.2f}] {doc.page_content}{RESET}")
            print(f"  {c}   saved: {format_saved(ts)}{RESET}")
            indexed.append((doc, score))
    else:
        all_data = collection.get(include=["documents", "metadatas"])
        if not all_data["ids"]:
            print("No memories saved yet.")
            exit()
        indexed = []
        for i, (doc_id, text, meta) in enumerate(zip(all_data["ids"], all_data["documents"], all_data["metadatas"]), 1):
            class _Doc:
                pass
            doc = _Doc()
            doc.id = doc_id
            doc.page_content = text
            doc.metadata = meta
            ts = meta.get('saved_at')
            c = COLOR_EVEN if i % 2 == 0 else COLOR_ODD
            print(f"  {c}{i}. {doc.page_content}{RESET}")
            print(f"  {c}   saved: {format_saved(ts)}{RESET}")
            indexed.append((doc, None))

    if not indexed:
        print("No results found.")
        exit()

    print()
    choice = input("Enter index numbers to delete (comma-separated), or press Enter to cancel: ").strip()
    if not choice:
        print("Cancelled.")
        exit()

    to_delete = []
    for num in choice.split(","):
        num = num.strip()
        if not num.isdigit():
            print(f"  Skipping invalid index: {num}")
            continue
        idx = int(num) - 1
        if 0 <= idx < len(indexed):
            doc, score = indexed[idx]
            to_delete.append(doc.id if hasattr(doc, 'id') else doc.metadata.get("id"))
            print(f"  Marked for deletion: {doc.page_content[:60]}...")
        else:
            print(f"  Index {num} out of range, skipping.")

    if not to_delete:
        print("Nothing to delete.")
        exit()

    confirm = input(f"\nDelete {len(to_delete)} memory(s)? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        exit()

    collection.delete(ids=to_delete)
    print(f"Deleted {len(to_delete)} memory(s).")

elif args.what:
    total = collection.count()
    print(f"Total memories stored: {total}\n")

    if total == 0:
        print("No memories saved yet.")
        exit()

    results = db.similarity_search_with_relevance_scores(args.what, k=args.k)

    if not results:
        print("No results found.")
    else:
        for i, (doc, score) in enumerate(results, 1):
            c = COLOR_EVEN if i % 2 == 0 else COLOR_ODD
            print(f"{c}[{score:.2f}] {doc.page_content}{RESET}")
            if doc.metadata:
                ts = doc.metadata.get('saved_at')
                print(f"{c}    saved: {format_saved(ts)}{RESET}")
            print()

elif args.edit is not None:
    total = collection.count()
    if total == 0:
        print("No memories saved yet.")
        exit()

    if args.edit:
        results = db.similarity_search_with_relevance_scores(args.edit, k=10)
        indexed = []
        for i, (doc, score) in enumerate(results, 1):
            ts = doc.metadata.get('saved_at')
            c = COLOR_EVEN if i % 2 == 0 else COLOR_ODD
            print(f"  {c}{i}. [{score:.2f}] {doc.page_content}{RESET}")
            print(f"  {c}   saved: {format_saved(ts)}{RESET}")
            indexed.append((doc, score))
    else:
        all_data = collection.get(include=["documents", "metadatas"])
        if not all_data["ids"]:
            print("No memories saved yet.")
            exit()
        indexed = []
        for i, (doc_id, text, meta) in enumerate(zip(all_data["ids"], all_data["documents"], all_data["metadatas"]), 1):
            class _Doc:
                pass
            doc = _Doc()
            doc.id = doc_id
            doc.page_content = text
            doc.metadata = meta
            ts = meta.get('saved_at')
            c = COLOR_EVEN if i % 2 == 0 else COLOR_ODD
            print(f"  {c}{i}. {doc.page_content}{RESET}")
            print(f"  {c}   saved: {format_saved(ts)}{RESET}")
            indexed.append((doc, None))

    if not indexed:
        print("No results found.")
        exit()

    print()
    choice = input("Enter the index number of the memory to edit, or press Enter to cancel: ").strip()
    if not choice or not choice.isdigit():
        print("Cancelled.")
        exit()

    idx = int(choice) - 1
    if idx < 0 or idx >= len(indexed):
        print(f"Index {choice} out of range.")
        exit()

    doc, score = indexed[idx]
    doc_id = doc.id if hasattr(doc, 'id') else doc.metadata.get("id")
    print(f"\nEditing: {doc.page_content}\n")
    new_text = input("New text (or Enter to cancel): ").strip()
    if not new_text:
        print("Cancelled.")
        exit()

    orig_meta = doc.metadata.copy() if doc.metadata else {}
    collection.delete(ids=[doc_id])
    db.add_texts(
        texts=[new_text],
        metadatas=[orig_meta],
    )
    print(f"Updated memory: {new_text}")

elif args.list:
    total = collection.count()
    print(f"Total memories stored: {total}\n")

    if total == 0:
        print("No memories saved yet.")
        exit()

    all_data = collection.get(include=["documents", "metadatas"])
    for i, (text, meta) in enumerate(zip(all_data["documents"], all_data["metadatas"]), 1):
        ts = meta.get('saved_at')
        c = COLOR_EVEN if i % 2 == 0 else COLOR_ODD
        print(f"  {c}{i}. {text}{RESET}")
        print(f"  {c}   saved: {format_saved(ts)}{RESET}")

else:
    parser.print_help()