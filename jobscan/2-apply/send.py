"""Send reviewed drafts. Dry run unless --go (or --auto). Only touches drafts this tool created.

    python send.py                       dry run
    python send.py --go                  send everything listed
    python send.py --go --skip a@b.com   hold specific addresses
    python send.py --go --only a@b.com   send only these
    python send.py --auto                unattended: send only drafts the gate cleared, capped per day

Never sent, whatever the flags (each is listed with the reason):
- a draft to a record that is bounced, withdrawn, rejected, discarded, or marked do_not_mail
  (left the company, inbox dead, never-again note);
- a follow-up whose record has had a reply since it was drafted (checked against the tracker and
  again against Gmail right before sending): a nudge on top of an answer is the one mistake
  follow-ups must not make;
- a first application to a domain with no mail server.
--auto additionally leaves for you: drafts flagged needs_review (cut-off posts, queued leads) and the
2nd / final follow-up rounds (only first applications and 1st nudges are unattended). It stops at the
daily cap, and does nothing while jobscan/AUTO_OFF exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import sys
import time
from datetime import datetime

import config
import contacts
import runlock
import store as st
from gmail_draft import delete_draft, get_service, has_replied, list_drafts, send_draft

# draft-id field -> the field recorded when that draft goes out
FOLLOWUP_FIELDS = {"followup_draft_id": "followup_sent_at", "followup2_draft_id": "followup2_sent_at",
                   "followup3_draft_id": "followup3_sent_at"}
SENT_FIELDS = ("sent_at", *FOLLOWUP_FIELDS.values())


def match(draft: dict, store: dict):
    """(key, "first" | "followup") for a draft this tool created, else (None, "")."""
    for key, rec in store.items():
        for id_field, sent_field in FOLLOWUP_FIELDS.items():
            if rec.get(id_field) == draft["id"] and not rec.get(sent_field):
                return key, "followup"
    for key, rec in store.items():
        if rec.get("draft_id") == draft["id"] and rec.get("status") in ("drafted", "held"):
            return key, "first"
    for key, rec in store.items():  # fallback: recipient, for records without a draft id
        if (rec.get("email", "").lower() == draft["to"] and not rec.get("draft_id")
                and rec.get("status") in ("drafted", "held")):
            return key, "first"
    return None, ""


def refusal(rec: dict, kind: str, check_mx: bool = True) -> str:
    """Why this draft must not go out now, or ''."""
    if rec.get("do_not_mail"):
        return f"do_not_mail ({rec.get('do_not_mail_reason') or 'set on the record'})"
    if rec.get("bounced"):
        return "the address bounced"
    if rec.get("status") in st.CLOSED_STATUSES:
        return f"status {rec['status']}"
    if kind == "followup":
        return "they replied after this follow-up was drafted" if rec.get("replied") else ""
    if check_mx and contacts.mx_status(rec.get("email", "")) in ("no-mx", "nxdomain"):
        return f"no mail server for {contacts.domain_of(rec.get('email', ''))}"
    return ""


def followup_round(rec: dict, draft_id: str) -> int:
    """1, 2 or 3 for a follow-up draft of this record."""
    for n, id_field in enumerate(FOLLOWUP_FIELDS, 1):
        if rec.get(id_field) == draft_id:
            return n
    return 1


def sent_fields(rec: dict, draft_id: str, now: str) -> dict:
    """Tracker fields for one sent draft. A follow-up must never overwrite sent_at, or the
    follow-up engine treats it as a new application and nudges again."""
    for id_field, sent_field in FOLLOWUP_FIELDS.items():
        if draft_id == rec.get(id_field):
            return {sent_field: now}
    return {"status": "sent", "sent_at": now}


def sent_today(store: dict) -> int:
    today = datetime.now().astimezone().date()
    n = 0
    for rec in store.values():
        for field in SENT_FIELDS:
            ts = rec.get(field)
            if ts and datetime.fromisoformat(ts).astimezone().date() == today:
                n += 1
    return n


def main() -> int:
    config.setup_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-mx-check", action="store_true", help="do not look up the recipient's mail server")
    args = ap.parse_args()
    # "--only" followed by nothing (an address list that came out empty) must not mean "no filter":
    # it once released every tracked draft.
    if args.only is not None and not [a for a in args.only if a.strip()]:
        print("--only was given but names no address; nothing sent. Drop --only to send every tracked draft.")
        return 2
    skip = {a.lower() for a in args.skip}
    only = {a.lower() for a in (args.only or [])}

    store = st.load()
    svc = get_service()
    drafts = list_drafts(svc)
    plan, refused = [], []
    for d in drafts:
        key, kind = match(d, store)
        if key is None:
            print(f"  ignoring draft not created here: {d['to']}  {d['subject']}")
            continue
        why = refusal(store[key], kind, check_mx=not args.no_mx_check)
        if why:
            refused.append((d, why))
            continue
        plan.append((d, key, kind))
    for d, why in refused:
        print(f"  NOT SENDING {d['to']}  {d['subject'][:50]}: {why}")

    live_ids = {d["id"] for d in drafts}
    stale = [k for k, r in store.items() if r.get("status") == "drafted"
             and r.get("draft_id") and r["draft_id"] not in live_ids]
    for k in stale:
        print(f"  draft deleted in Gmail, will mark discarded: {store[k]['email']}")

    print(f"\n{len(plan)} draft(s):")
    for d, key, kind in plan:
        flag = "  <- needs your review" if store[key].get("needs_review") and kind == "first" else ""
        print(f"  [{kind:<8}] {d['to']:<34} {d['subject']}{flag}")

    go = args.go or args.auto
    if args.auto and config.AUTO_OFF_FILE.exists():
        print("\nAUTO_OFF file present: nothing is sent. Drafts stay in Gmail for you.")
        go = False
    if not go:
        print("\nDry run. Add --go to send.")
        return 0

    for k in stale:
        st.update_record(k, {"status": "discarded"})
    cap = config.MAX_AUTO_SENDS_PER_DAY
    budget = cap - sent_today(store) if args.auto and cap is not None else 10 ** 6
    sent = 0
    try:
        with runlock.sending(f"send.py {'--auto' if args.auto else '--go'} ({len(plan)} drafts)"):
            for d, key, kind in plan:
                addr = d["to"]
                rec = st.load().get(key, store[key])   # another process may have changed it since start
                if (only and addr not in only) or addr in skip:
                    if kind == "first":  # a skipped follow-up belongs to an already-sent record: leave it
                        st.update_record(key, {"status": "held"})
                    print(f"  held    {addr}")
                    continue
                why = refusal(rec, kind, check_mx=False)   # the mail server was checked while planning
                if why:
                    print(f"  NOT SENDING {addr}: {why}")
                    continue
                if args.auto:
                    if kind == "first" and rec.get("needs_review"):
                        print(f"  review  {addr}: {rec.get('review_reason')} (left as a draft)")
                        continue
                    if kind == "followup" and followup_round(rec, d["id"]) > 1:
                        print(f"  review  {addr}: round {followup_round(rec, d['id'])} follow-up "
                              f"(left as a draft; send.py --go sends it)")
                        continue
                    if budget <= 0:
                        print(f"  cap     {addr}: {config.MAX_AUTO_SENDS_PER_DAY} sends today already; "
                              f"will go tomorrow")
                        continue
                if kind == "followup" and has_replied(svc, addr):
                    # They answered between drafting and now: never nudge someone who replied.
                    st.update_record(key, {"replied": True, "replied_detected_at": st.now(),
                                           "replied_evidence": f"mail from {addr} before sending"})
                    delete_draft(svc, d["id"])
                    print(f"  replied {addr}: follow-up cancelled")
                    continue
                result = send_draft(svc, d["id"])
                fields = sent_fields(rec, d["id"], st.now())
                if "sent_at" in fields and isinstance(result, dict) and result.get("threadId"):
                    fields["thread_id"] = result["threadId"]   # mail_sync matches replies to it exactly
                st.update_record(key, fields)
                budget -= 1
                sent += 1
                print(f"  sent    {addr}")
                time.sleep(1)
    except RuntimeError as exc:
        print(exc)
        return 1
    print(f"\n{sent} sent. Tracker updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
