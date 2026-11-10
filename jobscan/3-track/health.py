"""One screen on how the pipeline is doing and what is waiting today.

    python health.py              the report
    python health.py --days 7     "recent" means the last 7 days (default 7)

Reads leads.json, data/lead_queue.json and data/mail_actions.json; never touches Gmail and changes
nothing. Sections:

- what went out and what came back: applications, genuine replies, interviews, by source, inbox
  kind (named recruiter vs careers@) and track: the numbers that show which route works;
- follow-ups due today per round, drafts waiting to be sent;
- conversations in progress (someone answered and nothing is closed);
- data problems that silently cost follow-ups: doubtful reply flags (repair_replies.py), addresses
  marked do_not_mail, the lead queue.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import collections
import json
import sys

import config
import contacts
import followup
import leadqueue
import repair_replies
import store as st


def is_email_app(record: dict) -> bool:
    return bool(record.get("email")) and record.get("channel", "email") == "email" and bool(record.get("sent_at"))


def genuine_reply(record: dict) -> bool:
    return bool(record.get("replied")) and not repair_replies.suspicion(record)


def interviewed(record: dict) -> bool:
    return genuine_reply(record) and record.get("last_reply_outcome") in ("interview", "assessment")


def source_of(record: dict) -> str:
    return record.get("source") or ("linkedin-post" if record.get("poster") else "direct")


def rate_table(records: list, keyf) -> list:
    """[(group, n, replies, interviews)] largest first."""
    groups = collections.defaultdict(list)
    for record in records:
        groups[keyf(record)].append(record)
    rows = [(name, len(rs), sum(genuine_reply(r) for r in rs), sum(interviewed(r) for r in rs))
            for name, rs in groups.items()]
    return sorted(rows, key=lambda row: -row[1])


def summarise(store: dict, recent_days: int = 7) -> dict:
    email = [r for r in store.values() if is_email_app(r)]
    portal = [r for r in store.values() if r.get("channel") == "portal"]
    recent = [r for r in email if (st.days_since(r["sent_at"]) or 0) < recent_days]
    due = {stage: followup.due(store, stage, config.FOLLOWUP_AFTER_DAYS[stage])[0] for stage in (1, 2, 3)}
    waiting_send = [key for key, r in store.items()
                    for field, sent in (("followup_draft_id", "followup_sent_at"),
                                        ("followup2_draft_id", "followup2_sent_at"),
                                        ("followup3_draft_id", "followup3_sent_at"))
                    if r.get(field) and not r.get(sent)]
    drafted = [key for key, r in store.items() if r.get("status") in ("drafted", "held") and r.get("draft_id")]
    open_talks = [r for r in email if genuine_reply(r) and not st.is_closed(r)
                  and r.get("outcome") != "rejected" and r.get("last_reply_outcome") != "rejected"]
    return {
        "recent_days": recent_days, "email": len(email), "portal": len(portal), "recent": len(recent),
        "replies": sum(genuine_reply(r) for r in email), "interviews": sum(interviewed(r) for r in email),
        "portal_replies": sum(bool(r.get("replied")) for r in portal),
        "recent_replies": sum(genuine_reply(r) for r in recent),
        "by_source": rate_table(email, source_of),
        "by_inbox": rate_table(email, lambda r: contacts.inbox_kind(r.get("email", ""), r.get("poster", ""))),
        "by_track": rate_table(email, lambda r: r.get("track") or "-"),
        "due": {stage: len(items) for stage, items in due.items()},
        "due_names": {stage: [followup.who(r) for _, r, _ in items[:8]] for stage, items in due.items()},
        "followups_waiting_send": len(waiting_send), "applications_waiting_send": len(drafted),
        "needs_review": sum(1 for r in store.values() if r.get("status") == "drafted" and r.get("needs_review")),
        "open_talks": sorted(open_talks, key=lambda r: r.get("replied_detected_at") or "", reverse=True),
        "suspects": repair_replies.suspects(store),
        "held": [r for r in store.values() if r.get("status") == "sent" and r.get("hold_followups")],
        "do_not_mail": sum(bool(r.get("do_not_mail")) for r in store.values()),
        "bounced": sum(bool(r.get("bounced")) for r in store.values()),
        "queue": len(leadqueue.load()),
    }


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def render(s: dict, actions: list | None = None) -> str:
    out = []
    add = out.append
    add(f"EMAIL APPLICATIONS  {s['email']} sent | {s['replies']} replied ({pct(s['replies'], s['email'])}) | "
        f"{s['interviews']} interviews/assessments ({pct(s['interviews'], s['email'])})")
    add(f"  last {s['recent_days']} days: {s['recent']} sent, {s['recent_replies']} replied so far")
    add(f"  portals for comparison: {s['portal']} applied, {s['portal_replies']} answered "
        f"({pct(s['portal_replies'], s['portal'])})")
    for title, rows in (("by source", s["by_source"]), ("by inbox", s["by_inbox"]), ("by track", s["by_track"])):
        add(f"  {title}: " + " | ".join(f"{name} {n} -> {pct(rep, n)}" for name, n, rep, _ in rows[:6]))
    add("")
    add("FOLLOW-UPS")
    names = {1: "1st", 2: "2nd", 3: "final"}
    for stage in (1, 2, 3):
        listed = ", ".join(s["due_names"][stage]) + (", ..." if s["due"][stage] > 8 else "")
        add(f"  {names[stage]:5s} due now: {s['due'][stage]:3d}  {listed}")
    if s["due"][3]:
        add("  (the final round is not in the scheduled run: python followup.py --final --drafts)")
    add(f"  drafts waiting for send.py: {s['followups_waiting_send']} follow-ups, "
        f"{s['applications_waiting_send']} applications ({s['needs_review']} need your review)")
    add("")
    add(f"CONVERSATIONS IN PROGRESS ({len(s['open_talks'])})")
    for r in s["open_talks"][:12]:
        since = st.days_since(r.get("replied_detected_at")) if r.get("replied_detected_at") else None
        add(f"  {followup.who(r)[:28]:28s} {(r.get('last_reply_outcome') or 'replied'):15s} "
            f"{'' if since is None else f'{since}d ago':8s} {(r.get('reply_snippet') or '')[:60]}")
    add("")
    add("DATA PROBLEMS")
    add(f"  doubtful reply flags stopping follow-ups: {len(s['suspects'])}"
        + ("  -> python repair_replies.py" if s["suspects"] else ""))
    for key, r, why in s["suspects"][:8]:
        add(f"    {r.get('email')}: {why[:80]}")
    add(f"  follow-ups on hold (hold_followups): {len(s['held'])}")
    add(f"  addresses marked do_not_mail: {s['do_not_mail']} ({s['bounced']} bounced)")
    add(f"  leads queued for drafting (data/lead_queue.json): {s['queue']}"
        + ("  -> python draft_direct.py --queue" if s["queue"] else ""))
    if actions:
        add("")
        add("LATEST ACTION ITEMS (data/mail_actions.json)")
        for a in actions[-10:]:
            add(f"  [{a.get('type')}] {a.get('date')}  {a.get('from', '')[:40]}  |  {a.get('subject', '')[:60]}")
    return "\n".join(out)


def report(recent_days: int = 7) -> str:
    try:
        actions = json.loads(config.data_path("mail_actions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        actions = []
    return render(summarise(st.load(), recent_days), actions)


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--days", type=int, default=7)
    args = cli.parse_args()
    print(report(args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
