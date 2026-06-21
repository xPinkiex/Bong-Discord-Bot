#!/usr/bin/env python3
import requests

API_BASE = "http://127.0.0.1:11434"
PROTECTED = {"nomic-embed-text"}


def main():
    resp = requests.get(f"{API_BASE}/api/tags")
    resp.raise_for_status()
    models = [m["name"] for m in resp.json().get("models", [])]

    targets = [
        name for name in models
        if ":cloud" not in name and not any(name.startswith(p) for p in PROTECTED)
    ]

    if not targets:
        print("No models to prune.")
        return

    print(f"Found {len(targets)} model(s) to review:\n")
    deleted = []
    kept = []

    for name in targets:
        answer = input(f"  Delete {name}? [y/N] ").strip().lower()
        if answer == "y":
            r = requests.delete(f"{API_BASE}/api/delete", json={"name": name})
            if r.ok:
                print(f"    Deleted {name}")
                deleted.append(name)
            else:
                print(f"    Failed to delete {name}: {r.text}")
                kept.append(name)
        else:
            kept.append(name)

    print(f"\nDone. Deleted {len(deleted)}, kept {len(kept)}.")
    if kept:
        print("Kept:", ", ".join(kept))


if __name__ == "__main__":
    main()