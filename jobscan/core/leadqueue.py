"""data/lead_queue.json: leads found but not yet drafted.

Fed by mail_sync.py and repair_replies.py (an auto-reply saying "I have left, write to X") and
consumed by draft_direct.py --queue. Entries drafted from the queue are always flagged for review:
the unattended run never sends a queued lead without you reading it.
"""
from __future__ import annotations

import datetime as dt

import config
import fileutil


def _path():
    return config.data_path("lead_queue.json")


def load() -> list:
    return fileutil.read_json(_path(), [])


def save(entries: list) -> None:
    fileutil.write_json_atomic(_path(), entries)


def entry_key(entry: dict) -> str:
    return f"{(entry.get('email') or '').lower()}::{(entry.get('role') or '').lower()}"


def add(entries_to_add: list) -> int:
    """Append new entries (the same address and role are not queued twice). Returns how many."""
    queue = load()
    known = {entry_key(e) for e in queue}
    added = 0
    for entry in entries_to_add:
        if entry_key(entry) in known:
            continue
        entry = {**entry, "queued_at": entry.get("queued_at") or dt.date.today().isoformat()}
        queue.append(entry)
        known.add(entry_key(entry))
        added += 1
    if added:
        save(queue)
    return added


def drop_drafted(store: dict) -> int:
    """Remove queue entries whose address is now in the tracker."""
    tracked = {(r.get("email") or "").lower() for r in store.values()}
    queue = load()
    kept = [e for e in queue if (e.get("email") or "").lower() not in tracked]
    if len(kept) != len(queue):
        save(kept)
    return len(queue) - len(kept)
