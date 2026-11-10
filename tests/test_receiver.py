"""The background capture receiver: slot names, overwriting, and the real local server."""
import json
import urllib.error
import urllib.request

import pytest

import receiver

EXT = {"Origin": "chrome-extension://abcdefghijklmnop", "Content-Type": "application/json"}


def test_extension_file_names_map_to_fixed_slots():
    assert receiver.slot_for("202611040654-1-cloud-engineer-aws-hiring-india") == ("q1-cloud-engineer-aws-hiring-india", "query")
    assert receiver.slot_for("202611040654-12-sre-hiring-india") == ("q12-sre-hiring-india", "query")
    assert receiver.slot_for("202611040654-done") == ("run-done", "done")
    assert receiver.slot_for("202611040654-page-devops-jobs") == ("page-devops-jobs", "page")
    for bad in ("../evil", "202611040654-1-../../x", "notes", "202611040654-1-UPPER", "", None, "20261104-1-x"):
        assert receiver.slot_for(bad) is None, bad


def test_each_run_overwrites_the_last_and_old_keywords_do_not_pile_up(tmp_path):
    first = receiver.save_capture(tmp_path, "202611040654-3-cloud-engineer", "Feed post\nold run\n")
    second = receiver.save_capture(tmp_path, "202611050654-3-devops-engineer", "Feed post\nFeed post\nnew run\n")
    assert first.name == "q3-cloud-engineer.txt" and second.name == "q3-devops-engineer.txt"
    assert not first.exists() and sorted(p.name for p in tmp_path.glob("q3-*")) == ["q3-devops-engineer.txt"]
    receiver.save_capture(tmp_path, "202611050654-3-devops-engineer", "Feed post\nsame keywords, again\n")
    assert (tmp_path / "q3-devops-engineer.txt").read_text() == "Feed post\nsame keywords, again\n"   # replaced in place
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
    assert receiver.save_capture(tmp_path, "nonsense", "x") is None


def test_manifest_is_a_live_progress_file(tmp_path):
    receiver.save_capture(tmp_path, "202611050654-1-cloud", "Feed post\nFeed post\n")
    receiver.save_capture(tmp_path, "202611050654-2-devops", "Feed post\n")
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["run"] == "202611050654" and data["files"]["q1-cloud"]["posts"] == 2 and data["files"]["q2-devops"]["posts"] == 1
    receiver.save_capture(tmp_path, "202611050654-1-cloud", "Feed post\n")                   # re-saved: replaced, not duplicated
    assert len(json.loads((tmp_path / "manifest.json").read_text())["files"]) == 2


@pytest.fixture
def server(tmp_path):
    srv = receiver.CaptureServer(tmp_path / "captures", port=0)       # port 0: any free port
    assert srv.start()
    yield srv
    srv.stop()


def post(srv, body, headers=EXT, path="/save"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_the_server_saves_what_the_extension_posts_and_rejects_everything_else(server, tmp_path):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/ping", timeout=5) as resp:
        assert json.loads(resp.read())["service"] == "jobscan-receiver"
    status, body = post(server, {"name": "202611050654-1-cloud-engineer", "ext": "txt", "text": "Feed post\nhello\n"})
    assert (status, body) == (200, {"ok": True, "file": "q1-cloud-engineer.txt"})
    assert (tmp_path / "captures" / "q1-cloud-engineer.txt").read_text() == "Feed post\nhello\n"
    assert [p.name for p in server.saved] == ["q1-cloud-engineer.txt"]
    assert post(server, {"name": "202611050654-done", "ext": "log", "text": "status: Done\n"})[0] == 200
    assert (tmp_path / "captures" / "run-done.txt").exists()
    # not from a browser extension: refused (a web page or another local program)
    assert post(server, {"name": "202611050654-1-x", "text": "x"}, headers={"Content-Type": "application/json"})[0] == 403
    assert post(server, {"name": "202611050654-1-x", "text": "x"}, headers={**EXT, "Origin": "https://evil.example"})[0] == 403
    assert post(server, {"name": "../../etc/passwd", "text": "x"})[0] == 400          # file names are whitelisted
    assert post(server, b"not json")[0] == 400 and post(server, {"text": "no name"})[0] == 400
    assert post(server, {"name": "202611050654-1-x", "text": "x"}, path="/elsewhere")[0] == 404
    assert post(server, b"")[0] == 413
    assert sorted(p.name for p in (tmp_path / "captures").glob("q*.txt")) == ["q1-cloud-engineer.txt"]


def test_a_busy_port_is_reported_not_raised(tmp_path):
    first = receiver.CaptureServer(tmp_path, port=0)
    assert first.start()
    try:
        second = receiver.CaptureServer(tmp_path, port=first.port)
        assert second.start() is False
    finally:
        first.stop()


def test_run_daily_watches_the_receivers_folder_and_the_scan_reads_it(tmp_path, monkeypatch):
    import time

    import config
    import run_daily
    import scan
    monkeypatch.setattr(run_daily, "CAPTURE_DIR", tmp_path / "captures")
    t0 = time.time() - 5
    receiver.save_capture(tmp_path / "captures", "202611050654-1-cloud-engineer", "Feed post\nx\n")
    receiver.save_capture(tmp_path / "captures", "202611050654-done", "status: Done\n")
    assert [f.name for f in run_daily.captures_since(t0, {".txt"})] == ["q1-cloud-engineer.txt"]
    assert [f.name for f in run_daily.done_markers_since(t0)] == ["run-done.txt"]
    assert run_daily.captured_recently(1)
    monkeypatch.setattr(config, "CAPTURES", tmp_path / "captures")
    assert [f.name for f in scan.recent_captures(1, tmp_path / "captures")] == ["q1-cloud-engineer.txt"]      # run-done and manifest are not captures
