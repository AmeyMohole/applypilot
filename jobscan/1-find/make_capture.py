"""Turn browser page-text dumps into one capture file the parser can read.

    python make_capture.py <dump-dir> [out-file]

A browser-automation tool saves an evaluate() result as JSON, so a page's text arrives as one quoted
string with escaped newlines. The parser splits posts on a real "Feed post" line, so the text has to
be decoded first. Each dump starts with the site's own chrome (navigation, the search box, the query
itself); concatenated as-is, that header would land inside the previous file's last post and the
parser would read the search query as a job title, so each dump is trimmed to its first post.

Dumps are the files named scan-*.txt in <dump-dir>.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import datetime as dt
import glob
import json
import os
import re

import config

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")


def decode(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        raw = handle.read().strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return raw


def merge(texts: list[str]) -> str:
    """Join dumps, each trimmed to its first post (a dump with no posts is dropped)."""
    parts = []
    for text in texts:
        first = text.find("\nFeed post")
        if first != -1:
            parts.append(text[first:])
    return "\n".join(parts)


def main() -> int:
    config.setup_console()
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return 0
    dump_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.expanduser("~"), "Downloads", "jobscan", f"linkedin-jobscan-{stamp}.txt")
    files = sorted(glob.glob(os.path.join(dump_dir, "scan-*.txt")))
    if not files:
        print(f"no scan-*.txt files in {dump_dir}")
        return 1
    text = merge([decode(p) for p in files])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"{len(files)} dumps -> {out}")
    print(f"{text.count(chr(10) + 'Feed post')} posts, {len(set(EMAIL_RE.findall(text)))} distinct emails")
    return 0


if __name__ == "__main__":
    sys.exit(main())
