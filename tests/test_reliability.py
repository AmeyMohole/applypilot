"""Production behaviour: crash-safe writes, backups, a corrupt tracker, Gmail retries, doctor, alerts, logs."""
import json
import logging
import subprocess
import sys

import pytest

import config
import doctor
import fileutil
import gmail_draft
import log
import run_daily
import watch
import store as st

# ---- crash-safe files

def test_atomic_write_leaves_no_temp_file_and_unreadable_files_fall_back(tmp_path):
    path = tmp_path / "sub" / "state.json"
    fileutil.write_json_atomic(path, {"a": 1})
    fileutil.write_json_atomic(path, {"a": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2} and [p.name for p in path.parent.iterdir()] == ["state.json"]
    path.write_text("{truncated", encoding="utf-8")
    assert fileutil.read_json(path, "default") == "default" and fileutil.read_json(tmp_path / "none", []) == []


# ---- tracker backups and corruption

def test_one_backup_per_day_taken_before_the_save_and_only_14_kept():
    st.save({"a": {"email": "a@x.com"}})                       # nothing to back up yet
    assert st.latest_backup() is None
    st.save({"a": {"email": "a@x.com"}, "b": {"email": "b@x.com"}})
    first = st.latest_backup()
    assert first is not None and json.loads(first.read_text(encoding="utf-8")) == {"a": {"email": "a@x.com"}}
    st.save({"c": {}})                                          # same day: the backup is not overwritten
    assert json.loads(first.read_text(encoding="utf-8")) == {"a": {"email": "a@x.com"}}
    folder = st.backups_dir(config.LEADS)
    for day in range(1, 21):
        (folder / f"leads-2020-01-{day:02d}.json").write_text("{}")
    st.backup(config.LEADS)                                     # today's exists: no-op, nothing pruned yet
    (folder / first.name).unlink()
    st.backup(config.LEADS)                                     # a new day's backup triggers pruning
    assert len(list(folder.glob("leads-*.json"))) == st.BACKUPS_KEPT


def test_a_corrupt_tracker_is_never_overwritten_and_points_at_the_backup():
    st.save({"a": {"email": "a@x.com"}})
    st.save({"a": {"email": "a@x.com"}, "b": {}})              # makes today's backup
    config.LEADS.write_text('{"a": {"email": "a@x.', encoding="utf-8")     # killed mid-write by something else
    with pytest.raises(st.StoreCorrupt, match="not valid JSON.*backup"):
        st.load()
    with pytest.raises(st.StoreCorrupt):
        st.update_record("c", {"x": 1})                         # must refuse, not start again from {}
    assert config.LEADS.read_text(encoding="utf-8").startswith('{"a": {"email": "a@x.')


# ---- Gmail retries

def test_gmail_requests_retry_by_default(monkeypatch):
    from googleapiclient.http import HttpRequest
    seen = []
    monkeypatch.setattr(HttpRequest, "execute", lambda self, http=None, num_retries=0: seen.append(num_retries) or "ok")
    request = object.__new__(gmail_draft._retrying_request())
    assert request.execute() == "ok" and request.execute(num_retries=2) == "ok"
    assert seen == [gmail_draft.API_RETRIES, 2]


# ---- doctor

def test_doctor_reports_each_problem_and_fails_only_on_real_ones(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RESUME", tmp_path / "missing.pdf")
    monkeypatch.setattr(config, "CREDENTIALS", tmp_path / "credentials.json")
    monkeypatch.setattr(config, "TOKEN", tmp_path / "token.json")
    results = {name: (status, detail) for name, status, detail in doctor.run_checks()}
    assert results["resume"][0] == doctor.FAIL and results["credentials"][0] == doctor.FAIL
    assert results["tracker"][0] == doctor.OK and results["data dir"][0] == doctor.OK
    assert results["queries"][0] == doctor.OK and results["kill switch"][0] == doctor.OK
    monkeypatch.setattr(sys, "argv", ["doctor.py"])
    assert doctor.main() == 1 and "problem(s)" in capsys.readouterr().out


def test_doctor_checks_token_scopes_and_the_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CREDENTIALS", tmp_path / "credentials.json")
    monkeypatch.setattr(config, "TOKEN", tmp_path / "token.json")
    config.CREDENTIALS.write_text("{}")
    assert doctor.check_credentials()[0] == doctor.WARN                       # no token yet
    config.TOKEN.write_text(json.dumps({"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}))
    status, detail = doctor.check_credentials()
    assert status == doctor.FAIL and "gmail.compose" in detail
    config.TOKEN.write_text(json.dumps({"scopes": sorted(doctor.NEEDED_SCOPES)}))
    assert doctor.check_credentials()[0] == doctor.OK
    monkeypatch.setattr(config, "AUTO_OFF_FILE", tmp_path / "AUTO_OFF")
    (tmp_path / "AUTO_OFF").write_text("")
    assert doctor.check_kill_switch()[0] == doctor.WARN


def test_doctor_flags_a_corrupt_tracker_and_a_stale_last_run():
    config.LEADS.write_text("{oops")
    assert doctor.check_tracker()[0] == doctor.FAIL
    config.data_path("last_run.txt").write_text(json.dumps({"runs": ["2020-01-01T10:00:00"]}))
    assert doctor.check_last_run()[0] == doctor.WARN


def test_doctor_warns_when_the_extension_queries_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "HERE", tmp_path)
    (tmp_path / "extension").mkdir()
    (tmp_path / "extension" / "content.js").write_text('const DEFAULT_QUERIES = [\n "something else",\n];')
    assert doctor.check_extension()[0] == doctor.WARN
    (tmp_path / "extension" / "content.js").write_text("nothing here")
    assert doctor.check_extension()[0] == doctor.FAIL


# ---- unattended-run alerting

def test_a_failed_step_is_recorded_for_the_alert(monkeypatch):
    run_daily.failed_steps.clear()
    monkeypatch.setattr(run_daily, "LOG", config.data_path("run_daily.log"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "boom", "trace"))
    out = run_daily.step("Inbox -> tracker", "3-track/mail_sync.py")
    assert "boom" in out and run_daily.failed_steps == ["Inbox -> tracker"]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "fine", ""))
    run_daily.step("Send", "2-apply/send.py")
    assert run_daily.failed_steps == ["Inbox -> tracker"]
    run_daily.failed_steps.clear()


# ---- logging

def test_logging_writes_a_rotating_file_once(monkeypatch):
    monkeypatch.setattr(log, "_configured", False)
    logger = logging.getLogger("jobscan")
    before = list(logger.handlers)
    try:
        log.setup("DEBUG")
        log.setup("DEBUG")                                       # idempotent: no duplicate handlers
        added = [h for h in logger.handlers if h not in before]
        assert len(added) == 2
        logging.getLogger("jobscan.test").info("hello from a test")
        for handler in added:
            handler.flush()
        text = (config.DATA / "logs" / "jobscan.log").read_text(encoding="utf-8")
        assert "hello from a test" in text and "jobscan.test" in text
    finally:
        for handler in logger.handlers[:]:
            if handler not in before:
                handler.close()
                logger.removeHandler(handler)


# ---- live view

def test_watch_follows_new_lines_and_survives_rotation(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    lines, seen = [], iter(range(3))

    def stop():
        n = next(seen, None)
        if n == 0:
            with path.open("a", encoding="utf-8") as fh:
                fh.write("four\n")
        if n == 1:
            path.write_text("fresh\n", encoding="utf-8")        # rotated: shorter than before
        return n is None
    for line in watch.follow(path, last=2, poll=0, stop=stop):
        lines.append(line)
    assert lines == ["two", "three", "four", "fresh"]
    assert watch.tail(tmp_path / "missing.log", 5) == []


def test_force_recaptures_even_if_a_capture_finished_recently(monkeypatch):
    monkeypatch.setattr(run_daily, "captured_recently", lambda hours=3: True)
    monkeypatch.setattr(run_daily, "CHROME_PATHS", [])           # no Chrome: capture stops right after the guard
    monkeypatch.setattr(run_daily, "LOG", config.data_path("run_daily.log"))
    run_daily.out_lines.clear()
    run_daily.capture(force=False)
    run_daily.capture(force=True)
    assert "skipping LinkedIn" in run_daily.out_lines[0] and run_daily.out_lines[1] == "Chrome not found; skipping capture."


# ---- capture cleanup (delete after a week)

def test_old_captures_are_deleted_after_a_week_and_nothing_else_is(tmp_path):
    import os
    import time

    import cleanup
    now = time.time()
    old, recent = now - 8 * 86400, now - 2 * 86400
    names_old = ["202611010433-1-devops-engineer-hiring-india.txt", "202611010433-done.log",
                 "202611010433-2-sre-hiring-india (1).txt", "q3-cloud-engineer.txt", "run-done.txt",
                 "page-devops-jobs.txt", "linkedin-jobscan-google-2026-11-01T10-00-00.txt"]
    keep_old = ["devops1.txt", "my-notes.txt", "leads.json", "manifest.json", "resume.pdf", "q3-cloud.csv"]
    for name in names_old + keep_old:
        (tmp_path / name).write_text("x")
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "q4-fresh.txt").write_text("x")
    (tmp_path / "202611040705-1-cloud-engineer.txt").write_text("x")
    os.utime(tmp_path / "q4-fresh.txt", (recent, recent))
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "q9-nested.txt").write_text("x")
    os.utime(tmp_path / "subdir" / "q9-nested.txt", (old, old))
    listed = cleanup.purge(dirs=[tmp_path], now=now, dry_run=True)
    assert sorted(p.name for p in listed) == sorted(names_old) and all(p.exists() for p in listed)   # dry run: nothing deleted
    deleted = cleanup.purge(dirs=[tmp_path, tmp_path / "missing"], now=now)
    assert sorted(p.name for p in deleted) == sorted(names_old)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == sorted(keep_old + ["q4-fresh.txt", "202611040705-1-cloud-engineer.txt", "subdir"])
    assert (tmp_path / "subdir" / "q9-nested.txt").exists()                                      # not recursive
    assert cleanup.purge(days=0.0001, dirs=[tmp_path], now=now + 86400, dry_run=True)             # a shorter retention applies


def test_run_daily_cleans_up_at_the_start_of_a_run(tmp_path, monkeypatch):
    import os
    import time

    old = time.time() - 9 * 86400
    folder = tmp_path / "captures"
    folder.mkdir()
    (folder / "q1-cloud.txt").write_text("x")
    os.utime(folder / "q1-cloud.txt", (old, old))
    config.data_path("cleanup_last.txt").write_text(str(time.time()))   # the automatic pass already ran: test run_daily's own
    monkeypatch.setattr(run_daily, "CAPTURE_DIR", folder)
    monkeypatch.setattr(run_daily, "FALLBACK_DIR", tmp_path / "nowhere")
    monkeypatch.setattr(run_daily, "LOG", config.data_path("run_daily.log"))
    monkeypatch.setattr(run_daily, "STAMP", config.data_path("last_run.txt"))
    monkeypatch.setattr(run_daily, "LOCK", config.data_path("run.lock"))
    monkeypatch.setattr(run_daily, "REPORT", config.data_path("report.txt"))
    monkeypatch.setattr(run_daily, "step", lambda *a, **k: "")
    monkeypatch.setattr(run_daily, "notify", lambda text: None)
    monkeypatch.setattr(config, "RESUME", tmp_path / "resume.pdf")
    (tmp_path / "resume.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(sys, "argv", ["run_daily.py", "--no-capture", "--force"])
    run_daily.out_lines.clear()
    run_daily.main()
    assert not (folder / "q1-cloud.txt").exists() and any("Deleted 1 capture file" in x for x in run_daily.out_lines)


def test_cleanup_runs_by_itself_from_every_command_at_most_every_12_hours(tmp_path):
    import os
    import time

    import cleanup
    folder = tmp_path / "captures"
    folder.mkdir()
    old = time.time() - 9 * 86400

    def make(name):
        (folder / name).write_text("x")
        os.utime(folder / name, (old, old))
    make("q1-cloud.txt")
    config.setup_console()                                   # what every command calls first
    assert not (folder / "q1-cloud.txt").exists()            # deleted with nobody running cleanup
    assert config.data_path("cleanup_last.txt").exists()
    make("q2-devops.txt")
    assert cleanup.auto() == [] and (folder / "q2-devops.txt").exists()         # ran < 12 h ago: not due yet
    later = time.time() + 13 * 3600
    assert [p.name for p in cleanup.auto(now=later)] == ["q2-devops.txt"]        # due again
    config.data_path("cleanup_last.txt").write_text("garbage")                   # a damaged stamp just means "due"
    make("q3-sre.txt")
    assert [p.name for p in cleanup.auto()] == ["q3-sre.txt"]
