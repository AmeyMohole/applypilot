"""Find applications whose follow-ups stopped on a reply that was not a reply.

    python repair_replies.py                          list the suspects, change nothing
    python repair_replies.py --apply --emails a@x.com b@y.com
    python repair_replies.py --apply --all            reset every suspect listed

A record with replied=True never gets another follow-up. Replies recorded before replies.py existed
(or by a hand-set flag) may be an automated receipt, an out-of-office, a broadcast, a "no longer at
this company" notice, or domain-level evidence (a colleague's mail about another role). The stored
reply text is re-classified here; whatever it no longer reads as a person answering is listed.

Resetting sets replied=False and replied_reset_at=now, so neither mail_sync nor followup counts mail
older than the reset again; the previous evidence is kept in replied_reset_note. A "left the company"
notice instead becomes do_not_mail plus a queued lead for the new contact, since resetting would send
the follow-ups to a dead inbox. Read the list first: a domain-level match can be a real answer from a
recruiter you did not write to.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import copy
import datetime as dt
import re
import sys

import config
import leadqueue
import replies
import store as st

REAL_OUTCOMES = ("interview", "assessment", "info requested", "rejected")


def _stored_verdict(record: dict):
    return replies.classify(record.get("email", ""), record.get("reply_subject", ""),
                            record.get("reply_snippet", ""), config.GMAIL_ACCOUNT)


def suspicion(record: dict) -> str:
    """Why this replied flag looks wrong, or ''."""
    if not record.get("replied") or record.get("status") != "sent":
        return ""
    if record.get("last_reply_outcome") in REAL_OUTCOMES:
        return ""
    evidence = record.get("replied_evidence") or ""
    sender = (re.findall(r"[\w.+-]+@[\w.-]+", evidence) or [""])[0]
    if sender and replies.is_automated(sender):
        return f"automated sender in evidence: {evidence[:80]}"
    if record.get("reply_subject") or record.get("reply_snippet"):
        verdict = _stored_verdict(record)
        if verdict.kind in ("receipt", "ooo", "broadcast", "redirect"):
            return f"stored reply reads as {verdict.kind}: {(record.get('reply_snippet') or record.get('reply_subject'))[:70]}"
    if "someone @" in evidence:
        return f"domain-level evidence only: {evidence[:80]}"
    if record.get("auto_reply") and not record.get("last_reply_outcome") and not record.get("reply_snippet"):
        return f"only auto-mail on record: {record['auto_reply'][:80]}"
    return ""


def suspects(store: dict) -> list:
    return [(key, record, why) for key, record in store.items() for why in [suspicion(record)] if why]


def dead_address(record: dict) -> list | None:
    """New contacts named in a "left the company / unmonitored" reply, or None if the inbox is alive."""
    if not record.get("reply_snippet") and not record.get("reply_subject"):
        return None
    verdict = _stored_verdict(record)
    if verdict.kind != "redirect":
        return None
    return verdict.redirect_to


def reset_fields(record: dict, now: str) -> dict:
    previous = record.get("replied_evidence") or record.get("last_reply_outcome") or "replied"
    new = dead_address(record)
    if new is not None:
        return {"do_not_mail": True, "do_not_mail_reason": "auto-reply: address no longer read",
                "redirected_to": new}
    return {"replied": False, "replied_reset_at": now, "last_reply_outcome": None, "replied_evidence": None,
            "reply_snippet": None, "reply_subject": None, "reply_at": None,
            "replied_reset_note": f"{now[:10]}: reset by repair_replies (was: {previous})"}


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--apply", action="store_true", help="reset the chosen records")
    cli.add_argument("--emails", nargs="*", default=[], help="reset only these addresses")
    cli.add_argument("--all", action="store_true", help="with --apply: reset every suspect")
    args = cli.parse_args()

    store = st.load()
    found = suspects(store)
    if not found:
        print("No suspect reply flags.")
        return 0
    print(f"{len(found)} application(s) stopped on a doubtful reply:")
    for key, record, why in found:
        dead = dead_address(record)
        fix = "reset" if dead is None else f"do_not_mail, queue {', '.join(dead) or '(no new address)'}"
        print(f"  {record.get('email'):42s} {(record.get('role') or '')[:34]:34s} [{fix}] {why}")
    if not args.apply:
        print("\nNothing changed. Reset with --apply --emails <addr ...> (or --apply --all).")
        return 0
    chosen = {e.lower() for e in args.emails}
    if not chosen and not args.all:
        print("\n--apply needs --emails or --all.")
        return 1
    original = copy.deepcopy(store)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    done, queued = 0, []
    for key, record, why in found:
        if args.all or (record.get("email") or "").lower() in chosen:
            fields = reset_fields(record, now)
            for field, value in fields.items():
                if value is None:
                    record.pop(field, None)
                else:
                    record[field] = value
            for new in fields.get("redirected_to") or []:
                queued.append({"email": new, "company": record.get("company") or "-",
                               "role": record.get("role") or "", "years": record.get("min_years"),
                               "track": record.get("track") or "devops", "source": "redirect", "ref": key})
            done += 1
    st.save_changes(original, store)
    added = leadqueue.add(queued)
    print(f"\n{done} repaired; reset ones re-enter the follow-up rounds on the next run"
          + (f"; {added} new contact(s) queued in data/lead_queue.json." if added else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
