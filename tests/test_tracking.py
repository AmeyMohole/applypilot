"""Tracking: store patching, the three follow-up rounds, send refusals, mail sync, repair, health."""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

import compose
import config
import contacts
import followup
import health
import mail_sync
import repair_replies
import send
import store as st
from fakes import ME, FakeGmail


def ago(days, hours=0):
    return (datetime.now().astimezone() - timedelta(days=days, hours=hours)).isoformat()


def rec(email="careers@acme.com", **kw):
    return {"email": email, "role": "DevOps Engineer", "company": "Acme", "company_confident": True,
            "poster": "", "track": "devops", "status": "sent", "sent_at": ago(5),
            "drafted_at": ago(5), **kw}


# ---- store

def test_days_since_counts_calendar_days_in_india_time():
    late_yesterday = (datetime.now(st.IST).replace(hour=23, minute=59) - timedelta(days=1)).isoformat()
    assert st.days_since(late_yesterday) == 1            # a few hours ago can already be "1 day"
    assert st.days_since(datetime.now(st.IST).isoformat()) == 0
    assert st.days_since(None) is None


def test_update_record_patches_on_top_of_the_file_and_none_removes_a_field():
    st.save({"a": {"email": "a@x.com", "status": "drafted", "note": "keep"}})
    st.update_record("a", {"status": "sent", "note": None})
    st.update_record("b", {"email": "b@x.com"})
    assert st.load() == {"a": {"email": "a@x.com", "status": "sent"}, "b": {"email": "b@x.com"}}


def test_save_changes_does_not_wipe_what_another_process_wrote():
    st.save({"a": {"email": "a@x.com", "status": "sent"}, "b": {"email": "b@x.com", "status": "sent"}})
    original = st.load()
    edited = st.load()
    edited["a"]["replied"] = True                       # this run's work (e.g. mail_sync)
    st.update_record("b", {"followup_drafted_at": "x"})  # meanwhile: follow-up drafting saved
    assert st.save_changes(original, edited) == 1
    saved = st.load()
    assert saved["a"]["replied"] is True and saved["b"]["followup_drafted_at"] == "x"
    assert st.save_changes(saved, saved) == 0


def test_is_closed():
    assert not st.is_closed({"status": "sent"}) and not st.is_closed({"status": "held"})
    assert all(st.is_closed({"status": s}) for s in ("rejected", "withdrawn", "discarded"))
    assert st.is_closed({"status": "sent", "do_not_mail": True}) and st.is_closed({"status": "sent", "bounced": True})


# ---- who is due, per round

def test_each_round_waits_for_the_previous_one_and_skips_closed_records():
    store = {
        "new": rec(sent_at=ago(5)),
        "second": rec("b@acme.com", followup_sent_at=ago(5)),
        "second_not_yet": rec("c@acme.com", followup_sent_at=ago(1)),
        "only_drafted": rec("d@acme.com", followup_drafted_at=ago(5)),     # 1st never went out: no 2nd yet
        "final": rec("e@acme.com", followup_sent_at=ago(9), followup2_sent_at=ago(5)),
        "final_but_talking": rec("f@acme.com", followup_sent_at=ago(9), followup2_sent_at=ago(5),
                                 last_reply_outcome="rejected"),
        "left": rec("g@acme.com", do_not_mail=True), "bounced": rec("h@acme.com", bounced=True),
        "on_hold": rec("i@acme.com", hold_followups="asked to wait"), "replied": rec("j@acme.com", replied=True),
    }
    keys = lambda stage, days: [k for k, _, _ in followup.due(store, stage, days)[0]]
    assert keys(1, 3) == ["new"]
    assert keys(2, 4) == ["second"]
    assert keys(3, 4) == ["final"]
    waiting = followup.due(store, 2, 4)[1]
    assert waiting == [("Acme", 3)] and "Next due in 3 days" in followup.next_due_note(waiting)


# ---- the rounds, against a fake Gmail

@pytest.fixture
def gmail(monkeypatch):
    g = FakeGmail()
    monkeypatch.setattr(config, "AUTO_OFF_FILE", config.LEADS.parent / "AUTO_OFF")
    return g


def applied(g, email, thread="T1", subject="Application for DevOps Engineer - Amey Mohole"):
    """The application as it sits in Sent mail."""
    g.add(ME, subject, to=email, thread=thread, sent=True, date=1_750_000_000)


def run(stage, store, g, days=3):
    st.save(store)
    return followup.run_stage(stage, days, True, store, svc=g)


def test_first_nudge_is_a_threaded_reply_and_is_saved_per_draft(gmail):
    applied(gmail, "careers@acme.com")
    run(1, {"k": rec()}, gmail)
    (did,) = gmail.created
    assert gmail.drafts[did]["thread"] == "T1" and gmail.drafts[did]["subject"].startswith("Re: Application")
    saved = st.load()["k"]
    assert saved["followup_draft_id"] == did and saved["followup_drafted_at"] and saved["thread_id"] == "T1"


def test_later_rounds_never_start_a_fresh_email(gmail):
    run(2, {"k": rec(followup_sent_at=ago(5))}, gmail, days=4)         # no sent thread to reply into
    assert gmail.created == []


def test_second_and_final_rounds_record_their_own_fields(gmail):
    applied(gmail, "careers@acme.com")
    store = {"k": rec(followup_sent_at=ago(9), followup2_sent_at=ago(5))}
    run(3, store, gmail, days=4)
    assert st.load()["k"]["followup3_draft_id"] and "followup2_draft_id" not in st.load()["k"]


def test_a_receipt_or_out_of_office_does_not_stop_the_nudge(gmail):
    applied(gmail, "careers@acme.com")
    gmail.add("careers+noreply@acme.com", "Re: Application for DevOps Engineer",
              "Thank you for applying. We have received your application and our team will review it.")
    gmail.add("careers@acme.com", "Automatic reply", "I am out of the office and will be back on 20 Nov.")
    run(1, {"k": rec()}, gmail)
    assert len(gmail.created) == 1 and "replied" not in st.load()["k"]


def test_a_person_answering_stops_the_nudge_and_is_recorded(gmail):
    applied(gmail, "careers@acme.com")
    gmail.add("careers@acme.com", "Re: Application for DevOps Engineer", "Please share notice period: ")
    run(1, {"k": rec()}, gmail)
    saved = st.load()["k"]
    assert gmail.created == [] and saved["replied"] and saved["replied_evidence"] == "mail from careers@acme.com"


def test_a_colleague_counts_only_for_the_role_the_subject_names(gmail):
    applied(gmail, "hr@acme.com")
    gmail.add("priya.n@acme.com", "Re: Application for Security Engineer", "Thanks, reviewing your profile.")
    run(1, {"k": rec("hr@acme.com")}, gmail)
    assert len(gmail.created) == 1                                    # about another role: not an answer
    gmail.add("priya.n@acme.com", "Re: Application for DevOps Engineer", "Thanks, reviewing your profile.")
    run(1, {"k": rec("hr@acme.com", followup_drafted_at=None)}, gmail)
    assert st.load()["k"]["replied_evidence"] == "mail from priya.n@acme.com (same company, about this role)"


def test_a_colleague_with_their_own_application_does_not_count(gmail):
    applied(gmail, "hr@acme.com")
    gmail.add("priya.n@acme.com", "Re: Application for DevOps Engineer", "Thanks, reviewing your profile.")
    store = {"k": rec("hr@acme.com"), "other": rec("priya.n@acme.com", sent_at=ago(1))}
    run(1, store, gmail)
    assert len(gmail.created) == 1                                    # priya answers her own thread


def test_an_answer_inside_the_application_thread_counts(gmail):
    applied(gmail, "hr@acme.com", thread="T7")
    gmail.add("someone@elsewhere.com", "Re: Application for DevOps Engineer", "Forwarded to the panel.", thread="T7")
    run(1, {"k": rec("hr@acme.com", thread_id="T7")}, gmail)
    assert gmail.created == [] and "application thread" in st.load()["k"]["replied_evidence"]


def test_mail_older_than_a_hand_reset_is_not_counted_again(gmail):
    applied(gmail, "hr@acme.com")
    gmail.add("hr@acme.com", "Re: Application", "Please share notice period: ", date=1_760_000_000)
    run(1, {"k": rec("hr@acme.com", replied_reset_at=datetime.now().astimezone().isoformat())}, gmail)
    assert len(gmail.created) == 1


def test_dry_run_lists_without_touching_gmail(capsys):
    st.save({"k": rec()})
    followup.run_stage(1, 3, False, st.load())          # no svc and no --drafts: must not need Gmail
    assert "careers@acme.com" in capsys.readouterr().out


# ---- the mails themselves

def test_followup_rounds_say_the_right_things_and_claim_nothing_unverified():
    r = rec("priya.sharma@acme.com", poster="Priya Sharma", sent_at="2026-11-01T10:00:00+05:30")
    first, second, final = (f(r) for f in (compose.build_followup, compose.build_second_followup,
                                           compose.build_status_check))
    assert "Hello Priya," in first["text"] and "1 November" in first["text"] and "recap" in first["text"]
    assert "last message" in second["text"] and "recap" not in second["text"]
    assert "still open" in final["text"] and "recap" not in final["text"]
    for mail in (first, second, final):
        low = mail["text"].lower()
        assert not any(word in low for word in config.MATCH_ONLY_SKILLS), "claimed a skill not on the resume"
        assert "<p>" in mail["html"] and "ameymohole@gmail.com" in mail["text"]
    assert "Hello," in compose.build_followup({**r, "greet_name": ""})["text"]
    assert "Hello Asha," in compose.build_followup({**r, "greet_name": "Asha"})["text"]


# ---- send refusals

@pytest.fixture
def outbox(gmail, monkeypatch):
    monkeypatch.setattr(send, "get_service", lambda: gmail)
    monkeypatch.setattr(send.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["send.py", "--go"])
    return gmail


def drafted(g, store, key, email, **kw):
    did = g.add_draft(email, "Application for DevOps Engineer", thread="T9")
    store[key] = {"email": email, "role": "DevOps Engineer", "status": "drafted", "draft_id": did,
                  "drafted_at": ago(0), **kw}
    return did


def test_a_first_application_goes_out_and_records_its_thread(outbox):
    store = {}
    did = drafted(outbox, store, "k", "hr@acme.com")
    st.save(store)
    assert send.main() == 0
    saved = st.load()["k"]
    assert outbox.sent == [did] and saved["status"] == "sent" and saved["sent_at"] and saved["thread_id"] == "T9"


def test_a_second_follow_up_never_overwrites_the_original_send_date(outbox):
    original = ago(10)
    did = outbox.add_draft("hr@acme.com", "Re: Application", thread="T9")
    st.save({"k": rec("hr@acme.com", sent_at=original, followup_sent_at=ago(6), followup2_draft_id=did)})
    send.main()
    saved = st.load()["k"]
    assert outbox.sent == [did] and saved["followup2_sent_at"] and saved["sent_at"] == original


@pytest.mark.parametrize("fields, why", [
    ({"do_not_mail": True, "do_not_mail_reason": "left"}, "do_not_mail"),
    ({"bounced": True}, "the address bounced"),
    ({"status": "rejected"}, "status rejected"),
    ({"status": "withdrawn"}, "status withdrawn"),
])
def test_closed_records_are_never_sent_to(outbox, capsys, fields, why):
    did = outbox.add_draft("hr@acme.com", "Re: Application", thread="T9")
    st.save({"k": rec("hr@acme.com", followup_draft_id=did, **fields)})
    send.main()
    assert outbox.sent == [] and why in capsys.readouterr().out


def test_a_first_draft_on_a_closed_record_is_left_alone(outbox):
    store = {}
    drafted(outbox, store, "k", "hr@acme.com", status="rejected")      # no longer a draft this tool owns
    st.save(store)
    send.main()
    assert outbox.sent == []


def test_a_first_draft_to_a_dead_address_is_refused(outbox, capsys):
    store = {}
    drafted(outbox, store, "k", "hr@acme.com", do_not_mail=True, do_not_mail_reason="auto-reply: no longer read")
    st.save(store)
    send.main()
    assert outbox.sent == [] and "NOT SENDING hr@acme.com" in capsys.readouterr().out


def test_unattended_mode_sends_first_nudges_but_leaves_later_rounds_for_you(outbox, monkeypatch, capsys):
    one = outbox.add_draft("a@acme.com", "Re: Application", thread="T1")
    two = outbox.add_draft("b@acme.com", "Re: Application", thread="T2")
    three = outbox.add_draft("c@acme.com", "Re: Application", thread="T3")
    st.save({"a": rec("a@acme.com", followup_draft_id=one),
             "b": rec("b@acme.com", followup_sent_at=ago(6), followup2_draft_id=two),
             "c": rec("c@acme.com", followup_sent_at=ago(9), followup2_sent_at=ago(5), followup3_draft_id=three)})
    monkeypatch.setattr(sys, "argv", ["send.py", "--auto"])
    send.main()
    assert outbox.sent == [one] and capsys.readouterr().out.count("round") == 2
    monkeypatch.setattr(sys, "argv", ["send.py", "--go"])                  # by hand, the later rounds go
    send.main()
    assert sorted(outbox.sent) == sorted([one, two, three])


def test_a_follow_up_is_refused_when_they_replied_after_it_was_drafted(outbox, capsys):
    did = outbox.add_draft("hr@acme.com", "Re: Application", thread="T9")
    st.save({"k": rec("hr@acme.com", followup_draft_id=did, replied=True)})
    send.main()
    assert outbox.sent == [] and "they replied" in capsys.readouterr().out


def test_a_follow_up_is_cancelled_by_a_reply_that_only_gmail_knows_about(outbox):
    did = outbox.add_draft("hr@acme.com", "Re: Application", thread="T9")
    st.save({"k": rec("hr@acme.com", followup_draft_id=did)})
    outbox.add("hr@acme.com", "Re: Application", "Please share notice period: ")
    send.main()
    assert outbox.sent == [] and did not in outbox.drafts and st.load()["k"]["replied"]


def test_a_domain_with_no_mail_server_is_refused_for_first_applications(outbox, monkeypatch, capsys):
    monkeypatch.setattr(contacts, "_lookup_mx", lambda d: "nxdomain")
    store = {}
    drafted(outbox, store, "k", "hr@nowhere.example")
    st.save(store)
    send.main()
    assert outbox.sent == [] and "no mail server" in capsys.readouterr().out


def test_nothing_is_sent_while_another_send_holds_the_lock(outbox, capsys):
    store = {}
    drafted(outbox, store, "k", "hr@acme.com")
    st.save(store)
    config.data_path("send.lock").write_text(json.dumps({"pid": os.getpid(), "what": "other", "since": "10:00:00"}))
    assert send.main() == 1 and outbox.sent == [] and "another send is running" in capsys.readouterr().out


def test_an_empty_only_list_sends_nothing(outbox, monkeypatch):
    store = {}
    drafted(outbox, store, "k", "hr@acme.com")
    st.save(store)
    monkeypatch.setattr(sys, "argv", ["send.py", "--go", "--only"])
    assert send.main() == 2 and outbox.sent == []


# ---- mail sync

def sync_one(store, sender, subject, snippet="", when="2026-11-05T10:00:00+00:00", **kw):
    msg = {"id": "m1", "sender": sender, "subject": subject, "snippet": snippet, "when": when,
           "from": sender, "display": "", **kw}
    return mail_sync.handle_message(store, mail_sync.build_index(store), msg, ME, "2026-11-05T12:00:00+00:00")


def test_a_bounce_marks_the_address_dead():
    store = {"a": rec("dead.end@acme.com")}
    changes, _, _ = sync_one(store, "mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)",
                             "Your message wasn't delivered to dead.end@acme.com")
    assert store["a"]["bounced"] and store["a"]["do_not_mail"] and changes[0].startswith("BOUNCED")


def test_a_receipt_is_noted_but_is_not_a_reply():
    store = {"a": rec("careers@acme.com")}
    changes, _, _ = sync_one(store, "careers+noreply@acme.com", "Re: Application", "We have received your application.")
    assert not store["a"].get("replied") and "receipt" in store["a"]["auto_reply"] and changes[0].startswith("AUTO")


def test_a_person_answering_marks_the_reply_and_keeps_what_they_said():
    store = {"a": rec("neha.p@acme.com")}
    changes, actions, _ = sync_one(store, "neha.p@acme.com", "Re: Application for DevOps Engineer",
                                   "Can we talk tomorrow? Interview invite: meet.google.com/abc-defg")
    a = store["a"]
    assert a["replied"] and a["last_reply_outcome"] == "interview" and "meet.google.com" in a["reply_snippet"]
    assert a["reply_subject"].startswith("Re:") and a["replied_evidence"].startswith("mail from neha.p@acme.com")
    assert [x["type"] for x in actions] == ["interview"]


def test_a_rejection_closes_the_outcome():
    store = {"a": rec("hr@acme.com")}
    sync_one(store, "hr@acme.com", "Re: Application", "We regret to inform you that we will not be moving forward.")
    assert store["a"]["outcome"] == "rejected" and store["a"]["last_reply_outcome"] == "rejected"


def test_left_the_company_stops_mail_and_queues_the_new_contact():
    store = {"a": rec("ravi.k@acme.com", locations=["pune"], min_years=2)}
    changes, actions, queued = sync_one(store, "ravi.k@acme.com", "Automatic reply: Application",
                                        "I've left Acme. Please write to meera.s@acme.com for hiring queries.")
    assert store["a"]["do_not_mail"] and store["a"]["redirected_to"] == ["meera.s@acme.com"]
    assert [q["email"] for q in queued] == ["meera.s@acme.com"] and queued[0]["role"] == "DevOps Engineer"
    assert not store["a"].get("replied") and actions[0]["type"].startswith("redirect")


def test_a_colleague_answers_only_the_application_their_subject_names():
    store = {"a": rec("a@acme.com", role="Cloud Engineer"), "b": rec("b@acme.com", role="SRE")}
    sync_one(store, "c@acme.com", "Re: Application for SRE", "Thanks, reviewing your profile.")
    assert store["b"].get("replied") and not store["a"].get("replied")


def test_a_colleague_with_several_open_applications_and_no_role_is_listed_not_guessed():
    store = {"a": rec("a@acme.com", role="Cloud Engineer"), "b": rec("b@acme.com", role="SRE")}
    changes, actions, _ = sync_one(store, "c@acme.com", "Hello", "Thanks, reviewing your profile.")
    assert not store["a"].get("replied") and not store["b"].get("replied")
    assert changes[0].startswith("UNCLEAR") and "2 applications" in actions[0]["type"]


def test_the_application_thread_matches_exactly():
    store = {"a": rec("hr@acme.com", thread_id="T1"), "b": rec("x@other.com", thread_id="T2")}
    sync_one(store, "someone@elsewhere.com", "Re: Application", "Thanks, reviewing your profile.", thread="T1")
    assert store["a"]["replied"] and not store["b"].get("replied")


def test_mail_no_newer_than_a_hand_reset_is_ignored():
    store = {"a": rec("hr@acme.com", replied_reset_at="2026-11-06T00:00:00+00:00")}
    sync_one(store, "hr@acme.com", "Re: Application", "Please share notice period: ", when="2026-11-05T10:00:00+00:00")
    assert not store["a"].get("replied")


def test_an_ats_confirmation_adds_a_missing_application_once():
    store = {}
    idx = mail_sync.build_index(store)
    msg = {"id": "m1", "sender": "no-reply@myworkday.com", "display": "Globex", "when": "2026-11-05T10:00:00+00:00",
           "subject": "Thank you for applying at Globex!", "snippet": "", "from": "no-reply@myworkday.com"}
    changes, _, _ = mail_sync.handle_message(store, idx, msg, ME, "2026-11-05T12:00:00+00:00")
    (key,) = store
    assert key.startswith("portal::globex") and store[key]["status"] == "applied" and changes[0].startswith("ADDED")
    assert store[key]["sent_at"] and store[key]["drafted_at"]                     # sorts and shows in the sheet
    mail_sync.handle_message(store, idx, msg, ME, "2026-11-05T12:00:00+00:00")
    assert len(store) == 1 and store[key]["confirmed_by_mail"] == "2026-11-05"


def test_a_portal_rejection_closes_the_record():
    store = {"portal::globex::sre": {"company": "Globex", "role": "SRE", "status": "applied", "channel": "portal"}}
    sync_one(store, "careers@globex-jobs.com", "Update on your application",
             "Unfortunately we will not be moving forward with Globex", display="Globex")
    msg = {"id": "m2", "sender": "careers@globex-jobs.com", "display": "Globex", "when": "2026-11-05T10:00:00+00:00",
           "subject": "Update on your application", "from": "x",
           "snippet": "Unfortunately we will not be moving forward with your application"}
    mail_sync.handle_message(store, mail_sync.build_index(store), msg, ME, "2026-11-05T12:00:00+00:00")
    assert store["portal::globex::sre"]["status"] == "rejected"


def test_sync_end_to_end_handles_each_message_once_and_saves_only_its_changes(gmail):
    st.save({"a": rec("neha.p@acme.com", thread_id="T1")})
    gmail.add("neha.p@acme.com", "Re: Application for DevOps Engineer", "Interview invite: meet.google.com/x", date=1_760_000_000)
    changes, actions, _ = mail_sync.sync(gmail, 3)
    assert changes[0].startswith("REPLIED") and st.load()["a"]["last_reply_outcome"] == "interview"
    assert [a["type"] for a in actions] == ["interview"]
    changes, actions, _ = mail_sync.sync(gmail, 3)                 # same inbox again: nothing new
    assert changes == [] and actions == []
    st.update_record("a", {"note": "edited meanwhile"})
    mail_sync.sync(gmail, 3, dry_run=True)
    assert st.load()["a"]["note"] == "edited meanwhile"


# ---- repair_replies

def test_stored_receipts_and_autoreplies_are_suspect_real_answers_are_not():
    receipt = rec(replied=True, reply_subject="Re: Application", reply_snippet="We have received your application.")
    ooo = rec(replied=True, reply_subject="Automatic reply", reply_snippet="I am out of the office, back on 3 Dec.")
    real = rec(replied=True, last_reply_outcome="interview", reply_snippet="Interview invite")
    domain = rec(replied=True, replied_evidence="mail from someone @acme.com")
    robot = rec(replied=True, replied_evidence="mail from careers+noreply@acme.com 2026-11-02")
    assert "receipt" in repair_replies.suspicion(receipt) and "ooo" in repair_replies.suspicion(ooo)
    assert repair_replies.suspicion(real) == "" and repair_replies.suspicion(rec()) == ""
    assert "domain-level" in repair_replies.suspicion(domain) and "automated sender" in repair_replies.suspicion(robot)


def test_repair_resets_the_flag_or_retires_a_dead_address(monkeypatch, capsys):
    st.save({"receipt": rec("a@acme.com", replied=True, reply_subject="Re: Application",
                            reply_snippet="We have received your application."),
             "left": rec("b@acme.com", replied=True, reply_subject="Auto",
                         reply_snippet="I've left Acme. Please write to meera.s@acme.com."),
             "real": rec("c@acme.com", replied=True, last_reply_outcome="interview")})
    monkeypatch.setattr(sys, "argv", ["repair_replies.py"])
    repair_replies.main()
    assert st.load()["receipt"]["replied"]                                    # listing changes nothing
    monkeypatch.setattr(sys, "argv", ["repair_replies.py", "--apply", "--all"])
    repair_replies.main()
    saved = st.load()
    assert saved["receipt"]["replied"] is False and saved["receipt"]["replied_reset_at"]
    assert "reply_snippet" not in saved["receipt"] and "was:" in saved["receipt"]["replied_reset_note"]
    assert saved["left"]["do_not_mail"] and saved["left"]["replied"]          # dead inbox: not reset, retired
    assert saved["real"]["replied"]
    from leadqueue import load as queue
    assert [q["email"] for q in queue()] == ["meera.s@acme.com"]


# ---- health

def test_health_report_counts_replies_rounds_and_problems():
    store = {"a": rec("neha.p@acme.com", poster="Neha P", replied=True, last_reply_outcome="interview",
                      reply_snippet="Interview invite", replied_detected_at=ago(1)),
             "b": rec("careers@acme.com"),
             "c": rec("x@acme.com", replied=True, reply_subject="Auto", reply_snippet="I am out of the office."),
             "d": {"email": "hr@acme.com", "status": "drafted", "draft_id": "d1", "needs_review": True},
             "p": {"email": "", "channel": "portal", "status": "applied", "sent_at": ago(3)}}
    s = health.summarise(store)
    assert (s["email"], s["replies"], s["interviews"]) == (3, 1, 1)         # the OOO "reply" is not genuine
    assert s["due"] == {1: 1, 2: 0, 3: 0} and len(s["suspects"]) == 1 and s["needs_review"] == 1
    text = health.render(s, [{"type": "interview", "date": "2026-11-05", "from": "neha.p", "subject": "Invite"}])
    assert "EMAIL APPLICATIONS  3 sent | 1 replied (33.3%)" in text and "repair_replies.py" in text
    assert "1st   due now:   1" in text and "CONVERSATIONS IN PROGRESS (1)" in text and "[interview]" in text
