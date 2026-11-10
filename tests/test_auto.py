"""Unattended behaviour: what may be auto-sent, caps, reply re-checks, the kill switch."""
import sys
from datetime import datetime, timedelta

import pytest

import config
import followup
import gmail_draft
import parser as jp
import scan
import send
import store as st


def lead(**kw):
    base = dict(role="DevOps Engineer", email="x@acme.com", track="devops", min_years=2.0, truncated=False)
    return jp.Lead(**{**base, **kw})


def test_gate_blocks_cut_off_posts_with_unknown_years_only():
    assert scan.auto_ok(lead())[0]
    assert scan.auto_ok(lead(truncated=True, min_years=2.0))[0]          # cut off, but years known
    assert scan.auto_ok(lead(truncated=False, min_years=None))[0]        # complete post, years unstated
    ok, why = scan.auto_ok(lead(truncated=True, min_years=None))
    assert not ok and "cut off" in why
    assert not scan.auto_ok(lead(email=""))[0] and not scan.auto_ok(lead(track=""))[0]


class FakeGmail:
    def __init__(self, drafts, replied=()):
        self.drafts, self.replied = drafts, set(replied)
        self.sent, self.deleted = [], []


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEADS", tmp_path / "leads.json")
    monkeypatch.setattr(config, "XLSX", tmp_path / "a.xlsx")
    monkeypatch.setattr(config, "AUTO_OFF_FILE", tmp_path / "AUTO_OFF")
    g = FakeGmail([])
    monkeypatch.setattr(send, "get_service", lambda: object())
    monkeypatch.setattr(send, "list_drafts", lambda svc: g.drafts)
    monkeypatch.setattr(send, "send_draft", lambda svc, i: g.sent.append(i))
    monkeypatch.setattr(send, "delete_draft", lambda svc, i: g.deleted.append(i))
    monkeypatch.setattr(send, "has_replied", lambda svc, a: a in g.replied)
    monkeypatch.setattr(send.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["send.py", "--auto"])
    return g


def rec(email, **kw):
    return {"email": email, "role": "SRE", "status": "drafted", "draft_id": f"d-{email}",
            "drafted_at": st.now(), **kw}


def test_auto_send_skips_review_cap_and_respects_kill_switch(world, tmp_path):
    store = {f"k{i}": rec(f"a{i}@x.com") for i in range(3)}
    store["rev"] = rec("rev@x.com", needs_review=True, review_reason="cut off")
    st.save(store)
    world.drafts = [{"id": r["draft_id"], "to": r["email"], "subject": "s"} for r in store.values()]

    (tmp_path / "AUTO_OFF").write_text("")                 # kill switch: nothing leaves
    send.main()
    assert world.sent == []
    (tmp_path / "AUTO_OFF").unlink()

    send.main()
    saved = st.load()
    assert sorted(world.sent) == ["d-a0@x.com", "d-a1@x.com", "d-a2@x.com"]   # not the review one
    assert saved["rev"]["status"] == "drafted" and saved["k0"]["status"] == "sent"


def test_daily_cap(world, monkeypatch):
    monkeypatch.setattr(config, "MAX_AUTO_SENDS_PER_DAY", 2)
    store = {f"k{i}": rec(f"a{i}@x.com") for i in range(4)}
    st.save(store)
    world.drafts = [{"id": r["draft_id"], "to": r["email"], "subject": "s"} for r in store.values()]
    send.main()
    assert len(world.sent) == 2
    send.main()                                            # same day: cap already used
    assert len(world.sent) == 2


def test_followup_is_cancelled_if_they_replied_meanwhile(world):
    ago = (datetime.now().astimezone() - timedelta(days=5)).isoformat()
    store = {"k": rec("a@x.com", status="sent", sent_at=ago, followup_draft_id="f1"),
             "j": rec("b@x.com", status="sent", sent_at=ago, followup_draft_id="f2")}
    st.save(store)
    world.drafts = [{"id": "f1", "to": "a@x.com", "subject": "Re: s"},
                    {"id": "f2", "to": "b@x.com", "subject": "Re: s"}]
    world.replied = {"a@x.com"}
    send.main()
    saved = st.load()
    assert world.sent == ["f2"] and world.deleted == ["f1"]
    assert saved["k"]["replied"] and "followup_sent_at" not in saved["k"]
    assert saved["j"]["followup_sent_at"] and saved["j"]["sent_at"] == ago   # original sent_at untouched


def test_check_replies_keeps_the_reply_comment_and_flags_bounces(monkeypatch):
    replies = {"yes@x.com": {"subject": "Re: Application", "date": "Mon, 2 Nov 2026 02:47:50 +0530",
                             "snippet": "Please share your notice period"}}
    monkeypatch.setattr(gmail_draft, "latest_reply", lambda svc, a: replies.get(a))
    monkeypatch.setattr(gmail_draft, "has_bounced", lambda svc, a: a == "bad@x.com")
    now = st.now()
    store = {"1": {"email": "yes@x.com", "status": "sent", "role": "r", "sent_at": now},
             "2": {"email": "no@x.com", "status": "sent", "role": "r", "sent_at": now},
             "3": {"email": "yes@x.com", "status": "drafted", "role": "r"},
             "4": {"email": "", "status": "applied", "role": "r"},
             "5": {"email": "bad@x.com", "status": "sent", "role": "r", "sent_at": now}}
    new = followup.check_replies(object(), store)
    assert [r["email"] for r in new] == ["yes@x.com"]
    assert store["1"]["replied"] and store["1"]["reply_snippet"] == "Please share your notice period"
    assert "replied" not in store["2"] and "replied" not in store["3"]
    assert store["5"]["bounced"] and not store["2"].get("bounced")
    assert followup.candidates(store, 0) == [("2", store["2"])]      # no nudges to replied/bounced
    # a reply detected earlier without its comment gets the comment backfilled
    store["6"] = {"email": "yes@x.com", "status": "sent", "role": "r", "sent_at": now, "replied": True}
    followup.check_replies(object(), store)
    assert store["6"]["reply_snippet"] == "Please share your notice period"


# ---- run limits: three times a day, 4 hours apart

def test_run_limits(tmp_path, monkeypatch):
    import json
    import run_daily
    monkeypatch.setattr(run_daily, "STAMP", tmp_path / "last_run.txt")
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=1, microsecond=0)

    def runs(*times):
        run_daily.STAMP.write_text(json.dumps({"runs": [t.isoformat() for t in times]}))

    assert run_daily.may_run() == ""                                   # no history
    run_daily.STAMP.write_text("2026-11-01")                           # old one-line format is ignored
    assert run_daily.may_run() == ""
    runs(midnight, midnight + timedelta(seconds=5), midnight + timedelta(seconds=9))
    assert "Already ran 3 times" in run_daily.may_run()                # daily limit
    runs(now - timedelta(days=1))
    assert run_daily.may_run() == ""                                   # yesterday does not count
    if now.hour >= 1:
        runs(now - timedelta(minutes=30))
        assert "4 hours" in run_daily.may_run()                        # too soon after the last run
    before = len(run_daily.load_runs())
    run_daily.record_run()
    assert len(run_daily.load_runs()) == before + 1


def test_done_marker_is_found_with_either_extension_and_never_read_as_a_capture(tmp_path, monkeypatch):
    import time as _t
    import run_daily
    monkeypatch.setattr(run_daily, "CAPTURE_DIR", tmp_path)
    (tmp_path / "20261103-1-devops.txt").write_text("x")
    (tmp_path / "20261103-done.txt").write_text("status: Done")          # Chrome saved .log as .txt
    (tmp_path / "20261102-done.log").write_text("status: Done")
    t0 = _t.time() - 60
    assert [f.name for f in run_daily.captures_since(t0, {".txt"})] == ["20261103-1-devops.txt"]
    assert sorted(f.name for f in run_daily.done_markers_since(t0)) == ["20261102-done.log", "20261103-done.txt"]
    monkeypatch.setattr(config, "CAPTURES", tmp_path / "none")
    assert [f.name for f in scan.recent_captures(1, tmp_path)] == ["20261103-1-devops.txt"]
