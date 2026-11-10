"""Crash-safe file writes. A process killed mid-write must never leave a truncated state file: the
tracker and the lead queue hold the only record of who was contacted."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def replace_with_retry(tmp: Path, dest: Path, attempts: int = 20) -> None:
    """os.replace, retrying briefly: Windows refuses the swap while another process is reading dest."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25)


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    replace_with_retry(tmp, path)


def write_json_atomic(path: Path, obj, indent: int | None = 1) -> None:
    write_text_atomic(path, json.dumps(obj, indent=indent, ensure_ascii=False))


def read_json(path: Path, default):
    """The JSON in path, or `default` when it is missing or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
