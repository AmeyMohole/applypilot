"""Draft threaded follow-ups for applications with no reply. Never sends (send.py does, gated).

    python followup.py                       list who is due for the 1st nudge (no Gmail needed)
    python followup.py --drafts              check for replies, then draft the nudges
    python followup.py --second --drafts     2nd round: short, no recap, says it is the last message
    python followup.py --final --drafts      closing "is the role still open?" (by hand, not scheduled)
    python followup.py --all-stages          what each round has due

Waits are calendar days in India time, counted from the previous mail on the thread: 3 after the
application, 4 after the 1st nudge, 4 after the 2nd (config.FOLLOWUP_AFTER_DAYS; --after-days
overrides). The 2nd and final rounds are only ever threaded replies: if the original thread can't
be found the record is skipped rather than mailed fresh.

Before every draft the inbox is checked for a reply, widely: the exact address, anyone on the same
company domain writing about this role, and any non-self message in the original thread. Only a
person answering counts (replies.classify): receipts, out-of-office mail and robots do not stop the
follow-ups. A record with hold_followups set is left alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import datetime as dt
import sys
from email.utils import parseaddr

import config
import contacts
import replies
import runlock
import store as st
from compose import (build_followup, build_second_followup, build_status_check,  # noqa: F401
                     role_focus)

# One row per round. "after" is the field the wait counts from; "prefix" names the fields that mean
# the round already happened (<prefix>_drafted_at / _draft_id / _sent_at).
STAGES = {
    1: {"name": "follow-up", "after": "sent_at", "prefix": "followup", "build": build_followup,
        "since": "applied", "flag": ""},
    2: {"name": "second follow-up", "after": "followup_sent_at", "prefix": "followup2",
        "build": build_second_followup, "since": "first follow-up", "flag": "--second "},
    3: {"name": "status check", "after": "followup2_sent_at", "prefix": "followup3",
        "build": build_status_check, "since": "second follow-up", "flag": "--final "},
}


# --------------------------------------------------------------------------- who is due

def closed_by_conversation(record: dict) -> bool:
    """True when the thread already has a human outcome that a status request would talk over."""
    return bool(record.get("outcome") or record.get("last_reply_outcome"))


def who(record: dict) -> str:
    company = record.get("company")
    return company if company and company != "-" else record.get("email", "?")


def eligible(record: dict, stage: int) -> bool:
    """Waiting for this round: sent, unanswered, open, previous round sent, this one not started."""
    cfg = STAGES[stage]
    if record.get("status") != "sent" or record.get("replied") or not record.get("email"):
        return False
    if st.is_closed(record) or record.get("hold_followups"):
        return False
    if not record.get(cfg["after"]):
        return False
    if record.get(f"{cfg['prefix']}_sent_at") or record.get(f"{cfg['prefix']}_drafted_at"):
        return False
    return not (stage == 3 and closed_by_conversation(record))


def due(store: dict, stage: int, after_days: float) -> tuple[list, list]:
    """([(key, record, age_in_days)] due now, oldest first; [(who, days_left)] still waiting)."""
    cfg = STAGES[stage]
    ready, waiting = [], []
    for key, record in store.items():
        if not eligible(record, stage):
            continue
        age = st.days_since(record[cfg["after"]])
        if age >= after_days:
            ready.append((key, record, age))
        else:
            waiting.append((who(record), after_days - age))
    return sorted(ready, key=lambda c: -c[2]), waiting


def candidates(store: dict, days: float) -> list[tuple[str, dict]]:
    """First-round candidates as (key, record)."""
    return [(key, record) for key, record, _ in due(store, 1, days)[0]]


def next_due_note(waiting: list) -> str:
    soonest = min(days for _, days in waiting)
    names = [name for name, days in waiting if days == soonest]
    when = "tomorrow" if soonest <= 1 else f"in {soonest:g} days"
    return f"Next due {when}: {len(names)} ({', '.join(names[:6])}{', ...' if len(names) > 6 else ''})."


def hold_reason(record: dict) -> str:
    if record.get("hold_followups"):
        return f"hold_followups ({record.get('hold_followups')})"
    return ""


def nudge(rec: dict) -> tuple[str, str]:
    """Round-1 text and HTML (kept for callers that want just the body)."""
    mail = build_followup(rec)
    return mail["text"], mail["html"]


# --------------------------------------------------------------------------- reply checks

def _after(record: dict) -> float | None:
    """Epoch seconds of a hand reset: mail older than that was already judged, don't count it."""
    reset = record.get("replied_reset_at")
    return dt.datetime.fromisoformat(reset).timestamp() if reset else None


def check_replies(svc, store: dict) -> list[dict]:
    """Mark answered applications (and keep what they said), and flag undeliverable ones.
    Returns the newly detected replies. Each change is patched into leads.json on its own."""
    from gmail_draft import has_bounced, latest_reply
    new = []
    for key, r in store.items():
        if r.get("status") != "sent" or not r.get("email"):
            continue
        after = _after(r)
        rep = None if (r.get("replied") and r.get("reply_snippet")) else (
            latest_reply(svc, r["email"], after=after) if after else latest_reply(svc, r["email"]))
        if rep:
            fresh = not r.get("replied")
            fields = {"replied": True, "reply_snippet": rep["snippet"], "reply_subject": rep["subject"],
                      "reply_at": rep["date"]}
            if rep.get("outcome"):
                fields["last_reply_outcome"] = rep["outcome"]
            if fresh:
                fields["replied_detected_at"] = st.now()
                fields["replied_evidence"] = f"mail from {r['email']}"
            r.update(fields)
            if fresh:
                new.append(r)
            st.update_record(key, fields)
        elif (not r.get("replied") and not r.get("bounced")
              and (st._days_since(r.get("sent_at")) or 99) <= 14
              and has_bounced(svc, r["email"])):
            fields = {"bounced": True, "do_not_mail": True, "do_not_mail_reason": "bounced"}
            r.update(fields)
            st.update_record(key, fields)
    return new


def reply_evidence(svc, record: dict, my_address: str = "", colleagues: set | None = None) -> str | None:
    """Why we think they already answered, or None.

    A company inbox (careers@) is often answered by a named recruiter on the same domain, or inside
    the original thread by a colleague. A nudge to someone who did reply is the one mistake worth
    checking hard for. Colleagues with their own applications answer their own threads, so their
    mail counts only when it is about this role."""
    import gmail_draft
    colleagues = colleagues or set()
    address = record["email"].lower()
    after = _after(record)
    since = f" after:{int(after)}" if after else ""

    rep = (gmail_draft.latest_reply(svc, address, after=after) if after
           else gmail_draft.latest_reply(svc, address))
    if rep:
        return f"mail from {address}"

    domain = contacts.domain_of(address)
    if domain and domain not in contacts.FREE_MAIL:
        for m in gmail_draft.recent_messages(svc, f"from:{domain} newer_than:60d{since}", 10):
            sender = m["sender"]
            if sender in (address, my_address) or sender in colleagues or replies.is_automated(sender):
                continue
            if (replies.classify(sender, m["subject"], m["snippet"], my_address).is_reply
                    and replies.mentions_role(m["subject"], record.get("role", ""))):
                return f"mail from {sender} (same company, about this role)"

    thread_id = record.get("thread_id") or (gmail_draft.find_thread(svc, address) or {}).get("thread_id")
    if thread_id:
        thread = svc.users().threads().get(userId="me", id=thread_id, format="metadata",
                                           metadataHeaders=["From", "Subject"]).execute()
        for message in thread.get("messages", []):
            headers = {h["name"].lower(): h["value"] for h in message["payload"]["headers"]}
            sender = parseaddr(headers.get("from", ""))[1].lower()
            if not sender or sender == my_address or replies.is_automated(sender):
                continue
            if after and int(message.get("internalDate", "0")) / 1000 <= after:
                continue
            if replies.classify(sender, headers.get("subject", ""), message.get("snippet", ""),
                                my_address).is_reply:
                return f"reply in the application thread from {sender}"
    return None


# --------------------------------------------------------------------------- one round

def run_stage(stage: int, after_days: float, drafts: bool, store: dict, svc=None) -> int:
    cfg = STAGES[stage]
    candidates_, waiting = due(store, stage, after_days)
    print(f"{len(candidates_)} {cfg['name']}(s) due (at least {after_days:g} days since {cfg['since']}).")
    if not candidates_:
        if waiting:
            print(next_due_note(waiting))
        return 0
    if not drafts:
        for key, r, age in candidates_:
            print(f"  {r['email']:<34} {r.get('role', '')}  ({cfg['since']} {age} days ago)")
        print(f"Dry run. Re-run with {cfg['flag']}--drafts to create the drafts.")
        return 0

    import gmail_draft
    svc = svc or gmail_draft.get_service()
    me = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    colleagues = {(r.get("email") or "").lower() for r in store.values()}
    made = replied = 0
    for key, r, age in candidates_:
        label = f"{r.get('role') or 'role'} - {who(r)}"
        evidence = reply_evidence(svc, r, me, colleagues - {r["email"].lower()})
        if evidence:
            fields = {"replied": True, "replied_evidence": evidence, "replied_detected_at": st.now()}
            r.update(fields)
            st.update_record(key, fields)
            print(f"  skipped, already replied: {label} ({evidence})")
            replied += 1
            continue
        # Reply inside the original application thread so the recruiter sees the first mail above.
        thread = gmail_draft.find_thread(svc, r["email"])
        if not thread:
            print(f"  no sent thread found for {r['email']}, skipping (won't start a fresh email)")
            continue
        mail = cfg["build"](r)
        subj = thread["subject"]
        subj = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        draft_id = gmail_draft.create_draft(svc, r["email"], subj, mail["text"], mail["html"],
                                            thread_id=thread["thread_id"], in_reply_to=thread["message_id"])
        fields = {f"{cfg['prefix']}_drafted_at": st.now(), f"{cfg['prefix']}_draft_id": draft_id,
                  "thread_id": thread["thread_id"]}
        r.update(fields)
        st.update_record(key, fields)   # after every draft: a killed run must not re-draft tomorrow
        print(f"  {cfg['name']} drafted -> {r['email']}")
        made += 1
    print(f"{made} drafted, {replied} skipped because they replied.")
    return made


def main() -> int:
    config.setup_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", "--after-days", dest="days", type=float, default=None,
                    help="calendar days to wait since the previous mail (default 3 / 4 / 4)")
    ap.add_argument("--drafts", action="store_true", help="check for replies, then create the drafts")
    ap.add_argument("--second", action="store_true", help="second (last) follow-up")
    ap.add_argument("--final", action="store_true", help="closing status request")
    ap.add_argument("--all-stages", action="store_true", help="run all three rounds")
    args = ap.parse_args()

    if args.drafts and runlock.refuse_if_sending("followup"):
        return 1
    store = st.load()
    svc = None
    if args.drafts:
        from gmail_draft import get_service
        svc = get_service()
        found = check_replies(svc, store)       # first: who has already answered?
        for r in found:
            print(f"  REPLY from {r['email']}  ({r['role']} @ {r.get('company') or '?'})")
        print(f"{len(found)} new reply(ies) detected.")

    stages = (1, 2, 3) if args.all_stages else (3,) if args.final else (2,) if args.second else (1,)
    for stage in stages:
        if len(stages) > 1:
            print(f"\n===== {STAGES[stage]['name']} =====")
        run_stage(stage, args.days if args.days is not None else config.FOLLOWUP_AFTER_DAYS[stage],
                  args.drafts, store, svc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
