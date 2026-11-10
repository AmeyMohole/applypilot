"""Receives LinkedIn captures from the browser extension in the background, with no Save dialog.

The extension used to save each capture through Chrome's download manager. With "Ask where to save
each file" turned on, that opens a Save As dialog per file and a run waits for a click (this morning's
captures sat for 50 minutes). Instead the extension POSTs each capture here, as JSON, to
http://127.0.0.1:8765/save, and this writes it into data/captures/, overwriting.

Overwrite, not accumulate: a capture is stored under a fixed slot name, so each run replaces the last:

    <stamp>-<n>-<keywords>   ->  q<n>-<keywords>.txt     (an older file for the same n is removed first)
    <stamp>-done             ->  run-done.txt            (the end-of-run marker run_daily waits for)
    <stamp>-page-<keywords>  ->  page-<keywords>.txt     ("This page only")

data/captures/manifest.json is rewritten after every save (what was saved, how many posts, when): a
live progress file that other tools can read.

Safety: it listens on 127.0.0.1 only, accepts POSTs only from a browser extension (an Origin of
chrome-extension://...), accepts only the file names above, caps a capture at 8 MB, and only runs while
a capture is in progress (run_daily starts it, `python tools/receiver.py` starts it by hand).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fileutil

PORT = 8765
MAX_BYTES = 8 * 1024 * 1024
NAME_RE = re.compile(r"^(?P<stamp>\d{12})-(?:(?P<n>\d{1,2})-(?P<slug>[a-z0-9-]{0,40})|(?P<done>done)|"
                     r"page-(?P<page>[a-z0-9-]{0,40}))$")


def slot_for(name: str) -> tuple[str, str] | None:
    """(slot file stem, kind) for an extension file name, or None when the name is not one of ours."""
    m = NAME_RE.match(name or "")
    if not m:
        return None
    if m.group("done"):
        return "run-done", "done"
    if m.group("page") is not None:
        return f"page-{m.group('page')}".rstrip("-"), "page"
    return f"q{int(m.group('n'))}-{m.group('slug')}".rstrip("-"), "query"


def save_capture(directory: Path, name: str, text: str) -> Path | None:
    """Write one capture into `directory` under its slot name, replacing the previous run's file."""
    slot = slot_for(name)
    if slot is None:
        return None
    stem, kind = slot
    directory.mkdir(parents=True, exist_ok=True)
    if kind == "query":
        number = stem.split("-", 1)[0]                     # q3-... : drop an older q3-<other keywords> file
        for old in directory.glob(f"{number}-*.txt"):
            old.unlink(missing_ok=True)
    path = directory / f"{stem}.txt"
    fileutil.write_text_atomic(path, text)
    update_manifest(directory, stem, text, name)
    return path


def update_manifest(directory: Path, stem: str, text: str, name: str) -> None:
    manifest = directory / "manifest.json"
    data = fileutil.read_json(manifest, {})
    data["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    data["run"] = name.split("-", 1)[0]
    files = {k: v for k, v in data.get("files", {}).items() if k != stem}
    files[stem] = {"posts": text.count("Feed post"), "bytes": len(text.encode("utf-8")),
                   "saved_at": data["updated"]}
    data["files"] = files
    fileutil.write_json_atomic(manifest, data)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False     # on Windows, reuse lets two programs listen on one port: refuse that
    daemon_threads = True


class CaptureServer:
    """Start with start(), stop with stop(). start() returns False (never raises) if the port is busy."""

    def __init__(self, directory: Path, port: int = PORT):
        self.directory = Path(directory)
        self.port = port
        self.saved: list[Path] = []
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):            # silence the default per-request stderr line
                pass

            def _reply(self, code: int, body: dict) -> None:
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):                         # the extension (and doctor) ask whether it is listening
                self._reply(200, {"ok": True, "service": "jobscan-receiver"}) if self.path == "/ping" \
                    else self._reply(404, {"ok": False})

            def do_POST(self):
                if self.path != "/save":
                    return self._reply(404, {"ok": False, "error": "not found"})
                if not (self.headers.get("Origin") or "").startswith("chrome-extension://"):
                    return self._reply(403, {"ok": False, "error": "only the browser extension may post here"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if not 0 < length <= MAX_BYTES:
                    return self._reply(413, {"ok": False, "error": "empty or too large"})
                try:
                    msg = json.loads(self.rfile.read(length).decode("utf-8"))
                    name, text = str(msg["name"]), str(msg["text"])
                except (ValueError, KeyError):
                    return self._reply(400, {"ok": False, "error": "bad json"})
                try:
                    path = save_capture(owner.directory, name, text)
                except OSError as exc:
                    return self._reply(500, {"ok": False, "error": str(exc)})
                if path is None:
                    return self._reply(400, {"ok": False, "error": "unexpected file name"})
                owner.saved.append(path)
                self._reply(200, {"ok": True, "file": path.name})

        try:
            self._server = _Server(("127.0.0.1", self.port), Handler)
        except OSError:
            return False
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
