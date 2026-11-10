"""Keep the tracker in step with the mailbox. Read-only on Gmail; writes leads.json.

    python mail_sync.py            scan the last 3 days of new mail and update the tracker
    python mail_sync.py --days 21  wider backfill
    python mail_sync.py --dry-run  show what would change, change nothing

Applications get submitted from several places (email, LinkedIn Easy Apply, company portals, by
hand) and the tracker silently drifts; the follow-up engine then nudges people who already answered
and misses applications it never heard of. Each new inbox message (handled once; ids are kept in
data/mail_sync_state.json) is first sorted by replies.classify():

- a person answering -> replied=True, the reply text kept, plus an outcome guess (rejected /
  assessment / interview / info requested / acknowledged). A colleague on the same company domain
  answers only the application whose role the subject names (or the only one there); otherwise it
  is listed under ACTION NEEDED instead of marking every application at that company as answered.
- an automated receipt, out-of-office or mass broadcast -> noted in auto_reply, NOT a reply, so the
  follow-ups still go out
- "I have left, write to X" / an unmonitored inbox -> do_not_mail on that record, and the new
  address is queued in data/lead_queue.json for a draft (draft_direct.py --queue)
- a bounce (mailer-daemon, postmaster) naming an address in the tracker -> bounced + do_not_mail
- an ATS / job-board confirmation ("Thanks for applying to X") -> if no record for that company
  exists, one is added as applied
- a rejection for a portal application -> status rejected
- anything asking for action (assessment, test link, interview, "share the details") -> listed under
  ACTION NEEDED, and kept in data/mail_actions.json

A record whose replied flag was reset by hand (replied_reset_at, see repair_replies.py) is not
re-flagged by mail older than the reset. It never sends or deletes mail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import base64
import copy
import datetime as dt
import email.utils
import html
import re
import sys

import config
import contacts
import fileutil
import leadqueue
import replies
import store as st

FREE_MAIL = contacts.FREE_MAIL
# Senders that are platforms, not employers: never treat their domain as "the company replied".
PLATFORM_DOMAINS = ("linkedin.com", "naukri.com", "indeed.com", "glassdoor.com", "internshala.com",
                    "workablemail.com", "smartrecruiters.com", "ashbyhq.com", "greenhouse-mail.io",
                    "kekamail.com", "bamboohr.com", "myworkday.com", "successfactors.com", "hirist.tech",
                    "ziprecruiter.in", "joinhandshake.com", "jobs2web.com", "zohorecruit",
                    "google.com", "googlemail.com")

CONFIRM_PATTERNS = [  # (regex on subject, group index for company, group index for role or None)
    (r"^Thanks for applying to (.+)$", 1, None),                                   # Workable
    (r"^Thank you for applying (?:to|at) (.+)$", 1, None),                         # SmartRecruiters, BambooHR
    (r"your application was sent to (.+)$", 1, None),                              # LinkedIn Easy Apply
    (r"^Your application to (.+?) at (.+)$", 2, 1),                                # LinkedIn
    (r"Thank you for your application for (.+?) position at (.+)$", 2, 1),         # SuccessFactors
    (r"^Your application for the job: (.+?) at (.+)$", 2, 1),                      # SmartRecruiters
    (r"^Application for (.+?) received", None, 1),                                 # Keka (company = sender name)
    (r"^Application received for (.+)$", None, 1),
    (r"^We have received your application", None, None),
    (r"Application Received", None, None),
]
BOARD_NOISE_RE = re.compile(r"(job alert|jobs? for you|recommended|similar jobs|new jobs|jobs matching|"
                            r"reacted to|commented on|people also|walk-in invite from recruiter)", re.I)
QUOTE_START_RE = re.compile(r"(\nOn .{0,120}wrote:|\nFrom: |-{3,}\s*Original Message|\n>)", re.I)

norm_company = contacts.norm_company


def _state_path():
    return config.data_path("mail_sync_state.json")


def _actions_path():
    return config.data_path("mail_actions.json")


load_json = fileutil.read_json


def base_address(address: str) -> str:
    """careers+noreply@acme.com -> careers@acme.com."""
    local, _, dom = address.partition("@")
    return f"{local.split('+')[0]}@{dom}" if dom else address


def build_index(store: dict) -> dict:
    idx = {"email": {}, "domain": {}, "company": {}, "thread": {}}
    for key, r in store.items():
        if r.get("thread_id"):
            idx["thread"].setdefault(r["thread_id"], []).append(key)
        addr = (r.get("email") or "").lower()
        if addr:
            idx["email"].setdefault(addr, []).append(key)
            dom = addr.rsplit("@", 1)[-1]
            if dom not in FREE_MAIL:
                idx["domain"].setdefault(dom, []).append(key)
        nc = norm_company(r.get("company") or "")
        if nc:
            idx["company"].setdefault(nc, []).append(key)
    return idx


def after_reset(record: dict, when: str) -> bool:
    """False for mail no newer than the moment this record's reply flag was reset by hand."""
    reset = record.get("replied_reset_at")
    if not reset:
        return True
    return dt.datetime.fromisoformat(when) > dt.datetime.fromisoformat(reset)


def attribute(store: dict, idx: dict, sender: str, subject: str, is_platform: bool,
              thread: str | None = None) -> tuple:
    """(keys this message is about, how it was matched).

    The application thread first (send.py records thread_id), so a colleague answering in the thread
    is matched exactly. Then the exact address (plus-addressing folded). Otherwise the company
    domain, narrowed to the application whose role the subject names; with several open applications
    there and no role in the subject the answer is "unclear" rather than "all of them"."""
    if thread and idx["thread"].get(thread):
        return idx["thread"][thread], "thread"
    keys = idx["email"].get(sender) or idx["email"].get(base_address(sender))
    if keys:
        return keys, "address"
    if is_platform:
        return [], ""
    domain_keys = idx["domain"].get(sender.rsplit("@", 1)[-1], [])
    if not domain_keys:
        return [], ""
    by_role = [k for k in domain_keys if replies.mentions_role(subject, store[k].get("role"))]
    if by_role:
        return by_role, "domain+role"
    open_keys = [k for k in domain_keys if not st.is_closed(store[k])]
    if len(domain_keys) == 1 or len(open_keys) == 1:
        return (open_keys or domain_keys)[:1], "domain"
    return domain_keys, "unclear"


def handle_message(store: dict, idx: dict, msg: dict, me: str, now: str) -> tuple:
    """Apply one inbound message to the tracker.

    msg = {id, sender, display, subject, snippet, when, from, thread}. Returns
    (changes, actions, queued) for the run summary. Mutates store."""
    changes, actions, queued = [], [], []
    sender, subject, snippet, when = msg["sender"], msg["subject"], msg["snippet"], msg["when"]
    display = msg.get("display", "")
    text = f"{subject} {snippet}"
    sdom = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    verdict = replies.classify(sender, subject, snippet, me)

    def action(kind):
        actions.append({"date": when[:10], "type": kind, "from": msg.get("from", sender),
                        "subject": subject, "snippet": snippet[:200], "id": msg["id"]})

    # 1. bounces
    if verdict.kind == "bounce":
        for addr in verdict.redirect_to:
            for key in idx["email"].get(addr, []):
                if not store[key].get("bounced"):
                    store[key].update(bounced=True, do_not_mail=True, do_not_mail_reason="bounced",
                                      bounce_note=f"{when[:10]}: {subject[:80]}")
                    changes.append(f"BOUNCED   {store[key].get('company') or addr}  <{addr}>")
        return changes, actions, queued

    is_platform = any(p in sdom for p in PLATFORM_DOMAINS)

    # 2. mail from someone we emailed (exact address, or their company domain)
    matched, how = attribute(store, idx, sender, subject, is_platform, msg.get("thread"))
    keys = [k for k in matched if after_reset(store[k], when)]
    if matched and not keys:
        return changes, actions, queued   # mail already judged when the flag was reset
    if keys:
        label = store[keys[0]].get("company") or sender
        if verdict.kind in ("receipt", "ooo", "broadcast"):
            for key in keys if how != "unclear" else []:
                store[key]["auto_reply"] = f"{when[:10]} {verdict.kind}: {subject[:70]}"
            changes.append(f"AUTO      {label}: {verdict.kind}, follow-ups continue  ({subject[:50]})")
            if verdict.kind == "ooo" and verdict.redirect_to:
                action(f"ooo - alternate contact {', '.join(verdict.redirect_to)}")
            return changes, actions, queued
        if verdict.kind == "redirect":
            for key in keys if how != "unclear" else []:
                r = store[key]
                if not r.get("do_not_mail"):
                    r["do_not_mail"] = True
                    r["do_not_mail_reason"] = f"{when[:10]} auto-reply: address no longer read"
                    r["redirected_to"] = verdict.redirect_to
                    changes.append(f"REDIRECT  {label}: {sender} -> "
                                   f"{', '.join(verdict.redirect_to) or '(no new address)'}")
                for new in verdict.redirect_to:
                    if new not in idx["email"]:
                        queued.append({"email": new, "company": r.get("company") or "-",
                                       "role": r.get("role") or "", "years": r.get("min_years"),
                                       "track": r.get("track") or "devops", "source": "redirect", "ref": key,
                                       "note": f"{sender} auto-replied that it is no longer read ({when[:10]})"})
            action("redirect - draft to the new contact" if verdict.redirect_to else "redirect - inbox dead")
            return changes, actions, queued
        if how == "unclear":
            # Several applications at this company and nothing says which one.
            action(f"reply from {sdom}, matches {len(keys)} applications - mark the right one by hand")
            changes.append(f"UNCLEAR   {sender}: {len(keys)} applications at {sdom}  ({subject[:50]})")
            return changes, actions, queued
        outcome = verdict.outcome
        for key in keys:
            r = store[key]
            if not r.get("replied"):
                r.update(replied=True, replied_detected_at=now, replied_evidence=f"mail from {sender} {when[:10]}")
                changes.append(f"REPLIED   {r.get('company') or sender}: {outcome}  ({subject[:60]})")
            r.update(last_reply_outcome=outcome, reply_snippet=snippet[:300], reply_subject=subject, reply_at=when)
            if outcome == "rejected":
                r["outcome"] = "rejected"
        if outcome in ("assessment", "interview", "info requested"):
            action(outcome)
        if verdict.caution:
            action("caution - fee / ID documents / chat app requested; check before answering")
        return changes, actions, queued

    # 3. action items from platforms (assessments, recruiter messages, portal tests)
    if not BOARD_NOISE_RE.search(text) and (replies.ASSESS_RE.search(text)
                                            or re.search(r"interview invite", text, re.I)):
        action("assessment/interview")

    # 4. confirmations -> make sure the application is tracked
    for pattern, cgrp, rgrp in CONFIRM_PATTERNS:
        mt = re.search(pattern, subject, re.I)
        if not mt:
            continue
        company = (mt.group(cgrp) if cgrp else re.sub(r"(?i)\s*(hiring team|careers?|recruiting|people services)\s*$",
                                                       "", display or "")).strip(' "')
        company = company.split(" | ")[0].strip().rstrip("!.,:;- ").strip()
        role = (mt.group(rgrp).strip() if rgrp else "")
        nc = norm_company(company)
        if not nc or nc in ("linkedin", "indeed", "naukri", "workable", "internshala"):
            break
        match = idx["company"].get(nc) or [k for c, ks in idx["company"].items()
                                            if (nc in c or c in nc) and len(c) > 3 for k in ks]
        if match:
            for key in match:
                store[key].setdefault("confirmed_by_mail", when[:10])
        else:
            key = f"portal::{nc}::{re.sub(r'[^a-z0-9]+', '-', role.lower()).strip('-') or 'application'}"
            if key not in store:
                store[key] = {"company": company, "role": role or "(role not in confirmation mail)",
                              "status": "applied", "channel": "portal", "drafted_at": now, "sent_at": when,
                              "email": "", "company_confident": True, "locations": [], "track": "",
                              "note": f"auto-added by mail_sync from confirmation: {subject[:90]}"}
                idx["company"].setdefault(nc, []).append(key)
                changes.append(f"ADDED     {company} - {role or '?'}  (confirmation {when[:10]})")
        break

    # 5. portal rejections (no email on record, matched by company name)
    if replies.REJECT_RE.search(text):
        cands = [norm_company(display)] + [norm_company(x) for x in re.findall(r"with ([A-Z][\w&. ]{2,40})", snippet)]
        for nc in filter(None, cands):
            for key in idx["company"].get(nc, []):
                if store[key].get("status") in ("applied", "sent") and store[key].get("outcome") != "rejected":
                    store[key]["outcome"] = "rejected"
                    store[key]["status"] = "rejected"
                    changes.append(f"REJECTED  {store[key].get('company')}  ({subject[:60]})")
    return changes, actions, queued


def fetch_ids(svc, days: int) -> list:
    q = f"in:inbox newer_than:{days}d -category:promotions -category:social"
    ids, token = [], None
    while True:
        res = svc.users().messages().list(userId="me", q=q, maxResults=100, pageToken=token).execute()
        ids += [m["id"] for m in res.get("messages", [])]
        token = res.get("nextPageToken")
        if not token or len(ids) >= 500:
            break
    return ids


def body_text(payload: dict, limit: int = 1500) -> str:
    """The reply's own words, without the quoted application under it.

    Gmail's snippet stops at ~200 characters, and a polite rejection spends those on "Thank you for
    your interest..."; the "we will not be moving forward" comes after the cut and reads as a receipt."""
    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in ("text/plain", "text/html"):
            raw = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
            if mime == "text/html":
                raw = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", raw)))
            return mime, raw
        found = [f for f in (walk(p) for p in part.get("parts", []) or []) if f]
        # Prefer the plain-text alternative when both exist.
        return next((f for f in found if f[0] == "text/plain"), found[0] if found else None)

    hit = walk(payload)
    if not hit:
        return ""
    text = hit[1]
    cut = QUOTE_START_RE.search(text)
    text = text[:cut.start()] if cut else text
    return re.sub(r"\s+", " ", text).strip()[:limit]


def read_message(svc, mid: str, now: str) -> dict:
    m = svc.users().messages().get(userId="me", id=mid, format="full").execute()
    h = {x["name"].lower(): x["value"] for x in m["payload"]["headers"]}
    display, sender = email.utils.parseaddr(h.get("from", ""))
    try:
        when = email.utils.parsedate_to_datetime(h.get("date")).astimezone(dt.timezone.utc).isoformat()
    except Exception:
        when = now
    return {"id": mid, "thread": m.get("threadId"), "from": h.get("from", ""), "display": display,
            "sender": sender.lower(), "subject": h.get("subject", "").strip(),
            "snippet": body_text(m["payload"]) or html.unescape(m.get("snippet", "")), "when": when}


def sync(svc, days: int = 3, dry_run: bool = False) -> tuple[list, list, list]:
    """Read new inbox mail and apply it to the tracker. Returns (changes, new actions, queued)."""
    me = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    store = st.load()
    original = copy.deepcopy(store)   # to save only what this run changes
    state = load_json(_state_path(), {"seen": []})
    seen = set(state.get("seen", []))
    actions = load_json(_actions_path(), [])
    idx = build_index(store)

    ids = fetch_ids(svc, days)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    changes, new_actions, queued = [], [], []
    for mid in reversed(ids):          # oldest first
        if mid in seen:
            continue
        msg = read_message(svc, mid, now)
        seen.add(mid)
        if msg["sender"] == me:
            continue
        c, a, q = handle_message(store, idx, msg, me, now)
        changes += c
        new_actions += a
        queued += q

    known_ids = {(a.get("id"), a.get("type")) for a in actions}
    fresh = [a for a in new_actions if (a["id"], a["type"]) not in known_ids]
    print(f"mail_sync {now[:16]}: {len(ids)} messages in window, {len(changes)} tracker changes")
    for c in changes:
        print("  " + c)
    if fresh:
        print("\nACTION NEEDED:")
        for a in fresh:
            print(f"  [{a['type']}] {a['date']}  {a['from'][:45]}  |  {a['subject'][:80]}")
    if queued:
        print(f"\nNew contacts from redirects ({len(queued)}), queued in data/lead_queue.json:")
        for q in queued:
            print(f"  {q['email']}  ({q['company']} - {q['role']})")
    if dry_run:
        print("\n(dry run: nothing saved)")
        return changes, fresh, queued
    # Never write back the whole snapshot: send.py or follow-up drafting may have saved meanwhile.
    st.save_changes(original, store)
    leadqueue.add(queued)
    state["seen"] = sorted(seen)[-5000:]
    fileutil.write_json_atomic(_state_path(), state, indent=None)
    fileutil.write_json_atomic(_actions_path(), (actions + fresh)[-300:])
    return changes, fresh, queued


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--days", type=int, default=3)
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()
    import gmail_draft
    sync(gmail_draft.get_service(), args.days, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
