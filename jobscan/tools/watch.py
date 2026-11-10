"""Follow the pipeline's log live, like `tail -f`. Works for the hidden scheduled runs too.

    python tools/watch.py            show the last 25 lines, then follow new ones (Ctrl+C to stop)
    python tools/watch.py --last 80  start from the last 80 lines
    python tools/watch.py --detail   follow the detailed log (data/logs/jobscan.log) instead

The run log is data/run_daily.log: one line per step and per capture-progress update. The same
progress is also in the panel at the bottom right of the LinkedIn page while the extension searches.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def tail(path: Path, last: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-last:]
    except OSError:
        return []


def follow(path: Path, last: int = 25, poll: float = 1.0, stop=lambda: False):
    """Yield the last `last` lines, then each new line as it is written."""
    yield from tail(path, last)
    position = path.stat().st_size if path.exists() else 0
    while not stop():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size < position:            # rotated or truncated
            position = 0
        if size > position:
            with path.open(encoding="utf-8", errors="replace") as fh:
                fh.seek(position)
                chunk = fh.read()
                position = fh.tell()
            yield from chunk.splitlines()
        else:
            time.sleep(poll)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:    # noqa: BLE001
            pass
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--last", type=int, default=25)
    cli.add_argument("--detail", action="store_true")
    args = cli.parse_args()
    path = DATA / "logs" / "jobscan.log" if args.detail else DATA / "run_daily.log"
    print(f"Following {path}  (Ctrl+C to stop)\n", flush=True)
    try:
        for line in follow(path, args.last):
            print(line, flush=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
