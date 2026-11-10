"""Delete capture files older than a week (they contain other people's names and email addresses).

    python tools/cleanup.py              delete captures older than 7 days
    python tools/cleanup.py --days 3     a shorter retention
    python tools/cleanup.py --dry-run    only list what would be deleted

Only files the pipeline creates are touched (see core/cleanup.py). The scheduled run does this
automatically at the start of every run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse

import cleanup
import config


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--days", type=float, default=cleanup.RETENTION_DAYS)
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()
    files = cleanup.purge(args.days, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    for f in files:
        print(f"  {verb} {f}")
    print(f"{len(files)} capture file(s) older than {args.days:g} days {verb.replace('would delete', 'would be deleted')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
