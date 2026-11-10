"""Deletes old LinkedIn capture files. Captures are raw page text with other people's names and email
addresses, and scan.py only reads the last 12 hours, so after a week they are only a liability.

Only files the pipeline itself creates are ever deleted, by name pattern, never anything else that
happens to be in the folder (a capture you pasted in by hand, for example):

    <12-digit stamp>-<n>-<keywords>.txt / <stamp>-done.txt|log   browser downloads (also "... (1).txt")
    q<n>-<keywords>.txt, run-done.txt, page-<keywords>.txt        the background receiver
    linkedin-jobscan-*.txt                                        google_posts.py / make_capture.py output

in data/captures/ and in ~/Downloads/jobscan/ (where downloads land when the receiver is not used).
It is fully automatic: auto() runs from config.setup_console(), i.e. at the start of every command in the
pipeline (scan, follow-up, send, mail sync, the scheduled run...), at most once every 12 hours.
`python tools/cleanup.py` exists only for a manual or dry run.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import config

RETENTION_DAYS = 7
PATTERNS = [
    re.compile(r"^\d{12}-.+\.(?:txt|log)$"),
    re.compile(r"^q\d{1,2}-[a-z0-9-]*\.txt$"),
    re.compile(r"^run-done\.txt$"),
    re.compile(r"^page-[a-z0-9-]*\.txt$"),
    re.compile(r"^linkedin-jobscan-.+\.txt$"),
]


def default_dirs() -> list[Path]:
    return [config.CAPTURES, Path.home() / "Downloads" / "jobscan"]


def is_capture_name(name: str) -> bool:
    return any(p.match(name) for p in PATTERNS)


def purge(days: float = RETENTION_DAYS, dirs: list[Path] | None = None, now: float | None = None,
          dry_run: bool = False) -> list[Path]:
    """Delete (or with dry_run only list) capture files older than `days`. Returns the files affected.
    Never raises: a file in use or a missing folder is skipped."""
    cutoff = (now if now is not None else time.time()) - days * 86400
    removed = []
    for folder in dirs if dirs is not None else default_dirs():
        try:
            entries = list(Path(folder).iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and is_capture_name(path.name) and path.stat().st_mtime < cutoff:
                    if not dry_run:
                        path.unlink()
                    removed.append(path)
            except OSError:
                continue
    return removed


AUTO_EVERY_HOURS = 12


def auto(now: float | None = None) -> list[Path]:
    """Purge old captures unless that already happened in the last 12 hours. Safe to call from anywhere:
    never raises, and does nothing (and writes no stamp) when it is not yet due."""
    now = now if now is not None else time.time()
    try:
        stamp = config.data_path("cleanup_last.txt")
        try:
            if now - float(stamp.read_text().strip()) < AUTO_EVERY_HOURS * 3600:
                return []
        except (OSError, ValueError):
            pass
        removed = purge(now=now)
        stamp.write_text(str(now))
        if removed:
            import logging
            logging.getLogger("jobscan.cleanup").info(
                "deleted %d capture file(s) older than %d days", len(removed), RETENTION_DAYS)
        return removed
    except Exception:    # noqa: BLE001 - housekeeping must never break a command
        return []
