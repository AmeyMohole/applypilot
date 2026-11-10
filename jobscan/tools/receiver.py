"""Run the capture receiver by hand, for when you press the panel button yourself on LinkedIn.

    python tools/receiver.py             listen on 127.0.0.1:8765 until Ctrl+C

The scheduled run starts the receiver itself, so you only need this for manual captures. While it
runs, the extension saves each capture in the background (data/captures/, overwriting the previous
run's) instead of through Chrome's download manager, so no Save dialog appears.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import time

import config
import receiver


def main() -> int:
    config.setup_console()
    server = receiver.CaptureServer(config.CAPTURES)
    if not server.start():
        print(f"Port {server.port} is already in use (is a run in progress?).")
        return 1
    print(f"Listening on 127.0.0.1:{server.port}; captures go to {config.CAPTURES}. Ctrl+C to stop.")
    seen = 0
    try:
        while True:
            time.sleep(1)
            while seen < len(server.saved):
                print(f"  saved {server.saved[seen].name}", flush=True)
                seen += 1
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
