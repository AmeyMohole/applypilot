"""Capture -> parse -> shortlist -> dedupe -> (drafts).

    python scan.py                       preview the newest capture (nothing is saved)
    python scan.py --show-skipped        ...and why each post was rejected
    python scan.py --drafts              create the Gmail drafts
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

import config
import contacts
import parser as jp
import runlock
import store as st
from compose import compose


def recent_captures(hours: float, directory: Path | None = None) -> list[Path]:
    """Every capture saved in the last `hours` (the extension writes one file per query)."""
    dirs = [directory] if directory else [config.CAPTURES, Path.home() / "Downloads" / "jobscan"]
    files = [f for d in dirs if d.exists() for f in d.iterdir()
             if f.suffix.lower() in (".txt", ".html", ".htm") and "-done" not in f.name]
    if not files:
        raise SystemExit(f"No capture found. Run the extension, or paste a search page into a "
                         f".txt file in {config.CAPTURES}")
    cutoff = time.time() - hours * 3600
    fresh = sorted((f for f in files if f.stat().st_mtime >= cutoff), key=lambda f: f.stat().st_mtime)
    if not fresh:  # nothing recent: fall back to the single newest file rather than failing
        fresh = [max(files, key=lambda f: f.stat().st_mtime)]
    return fresh


def dedupe(keep: list[jp.Lead], store: dict) -> tuple[list[jp.Lead], list[tuple[jp.Lead, str]]]:
    """Layer 1: lead key. Layer 2: address anywhere in the store. Layer 3: address earlier this run."""
    new, dropped = [], []
    known = st.known_emails(store)
    seen: set[str] = set()
    for lead in keep:
        if lead.key in store:
            continue  # already handled, silent
        if lead.email in known:
            dropped.append((lead, "already contacted, skipping"))
        elif lead.email in seen:
            dropped.append((lead, "same address earlier in this capture"))
        else:
            new.append(lead)
            seen.add(lead.email)
    return new, dropped


def screen(new: list[jp.Lead], store: dict, check_mx: bool = True
           ) -> tuple[list[jp.Lead], list[tuple[jp.Lead, str]], list[tuple[jp.Lead, str]]]:
    """Checks before a draft exists. Returns (ok, blocked with reason, warnings).

    Blocked: do-not-contact or earlier bounce, a domain that cannot receive mail, and a company where
    someone already answered (a second recruiter there would be the same conversation). Warnings:
    other applications at the same company, printed for you but not blocking."""
    ok, blocked, warned = [], [], []
    for lead in new:
        company = lead.company if lead.company_confident else ""
        issues = contacts.blockers(store, lead.email, company, lead.role, check_mx)
        hard = [msg for level, msg in issues if level == "block"]
        talking = [r for _, r in contacts.same_company(store, lead.email, company)
                   if contacts.open_conversation(r)]
        if hard:
            blocked.append((lead, hard[0]))
        elif talking:
            blocked.append((lead, f"a conversation is already open at this company: {contacts.describe(talking[0])}"))
        else:
            ok.append(lead)
            warned += [(lead, msg) for level, msg in issues if msg.startswith("same company")]
    return ok, blocked, warned


TRACK_RANK = {"cloud": 0, "devops": 1, "fullstack": 1, "sre": 2, "devsecops": 2}


def priority(lead: jp.Lead) -> tuple:
    """Best first: cloud before devops / full-stack before sre / devsecops, and Nashik > Pune > Mumbai >
    remote > the rest. The daily send cap means the order decides who is mailed today."""
    return (TRACK_RANK.get(lead.track, 9), jp.city_rank(lead.locations))


def auto_ok(lead: jp.Lead) -> tuple[bool, str]:
    """Stricter than the shortlist: may this lead be sent with nobody reading it first?
    Failing leads still get a Gmail draft, flagged for review, and are never auto-sent."""
    if not (lead.role and lead.track and lead.email):
        return False, "role, track or email missing"
    if lead.truncated and lead.min_years is None:
        return False, "post was cut off and the years required are unknown"
    return True, ""


def describe(lead: jp.Lead) -> str:
    yrs = "yrs ?" if lead.min_years is None else f"{lead.min_years:g}+ yrs"
    co = lead.company if lead.company_confident else f"{lead.company or '?'} (guess)"
    extra = f" | {lead.stipend} {lead.duration}".rstrip() if lead.is_intern else ""
    return f"[{lead.track:<9}] {lead.role} @ {co} | {yrs} | {lead.email} | {lead.poster}{extra}"


def run(argv=None, interns: str = "exclude") -> None:
    config.setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", nargs="?", type=Path)
    ap.add_argument("--hours", type=float, default=12,
                    help="when no file is given, read every capture saved in the last N hours")
    ap.add_argument("--drafts", action="store_true", help="create Gmail drafts")
    ap.add_argument("--show-skipped", action="store_true")
    ap.add_argument("--full", action="store_true", help="print each email in the preview")
    ap.add_argument("--max-years", type=float, default=config.DEFAULT_MAX_YEARS)
    ap.add_argument("--min-stipend", type=float, default=config.DEFAULT_MIN_STIPEND)
    ap.add_argument("--locations", help="comma list, e.g. pune,mumbai (default: everywhere)")
    args = ap.parse_args(argv)

    paths = [args.capture] if args.capture else recent_captures(args.hours)
    print(f"Captures ({len(paths)}): " + ", ".join(p.name for p in paths))
    leads = jp.parse_many(p.read_text(encoding="utf-8", errors="replace") for p in paths)
    cut_off = [lead for lead in leads if lead.truncated and not lead.email]
    if cut_off:
        print(f"! {len(cut_off)} of {len(leads)} posts were cut off by '...more' and show no email; "
              f"expand them and recapture.")
    wanted = {x.strip().lower() for x in args.locations.split(",")} if args.locations else None
    keep, skipped = jp.shortlist(leads, args.max_years, wanted, interns, args.min_stipend)
    store = st.load()
    new, dropped = dedupe(keep, store)
    new, blocked, warned = screen(new, store)
    new.sort(key=priority)
    print(f"{len(leads)} posts -> {len(keep)} match -> {len(new)} new\n")

    if args.show_skipped:
        for lead, reason in skipped:
            print(f"  skip: {lead.role or '(no title)':<40} {reason}")
        print()
    else:
        counts = Counter(r.split(" (")[0] for _, r in skipped)
        for reason, n in counts.most_common():
            print(f"  {n:>3} skipped: {reason}")
        print()
    for lead, reason in dropped:
        print(f"  {lead.email}: {reason}")
    for lead, reason in blocked:
        print(f"  {lead.email}: not drafting, {reason}")
    for lead, reason in warned:
        print(f"  {lead.email}: note, {reason}")

    for lead in new:
        mail = compose(lead)
        print(describe(lead))
        print(f"    subject: {mail['subject']}  | greeting: {mail['text'].splitlines()[0]}")
        if args.full:
            print("\n" + mail["text"] + "\n" + "-" * 60)

    if args.drafts and new and runlock.refuse_if_sending("scan"):
        return
    if args.drafts and new:
        from gmail_draft import create_draft, get_service
        svc = get_service()
        for lead in new:
            mail = compose(lead)
            draft_id = create_draft(svc, lead.email, mail["subject"], mail["text"], mail["html"],
                                    [config.RESUME])
            store[lead.key] = st.make_record(lead, "drafted", draft_id)
            store[lead.key]["auto_interest"] = mail["auto_interest"]
            ok, why = auto_ok(lead)
            store[lead.key]["needs_review"] = not ok
            store[lead.key]["review_reason"] = why
            st.save(store)  # after every draft, so a killed run never orphans a Gmail draft
            print(f"  drafted -> {lead.email}")
        weak = [lead for lead in new if compose(lead)["auto_interest"]]
        if weak:
            print("\nWorth replacing with a specific line before you send (auto-written interest):")
            for lead in weak:
                print(f"  {lead.email}  {lead.role}")
        print("\nNow read every draft in Gmail, then: python send.py")
    elif new and not args.drafts:
        print("\nPreview only. Add --drafts to create the Gmail drafts.")

    by_hand = [(lead, r) for lead, r in skipped
               if r.startswith("no contact email") or r.startswith("stipend not stated")]
    if by_hand:
        print("\nMatches your profile but needs applying by hand:")
        for lead, reason in by_hand:
            links = ", ".join(lead.links) or "(no link found)"
            print(f"  {lead.role} @ {lead.company or '?'}  [{reason}]  {links}")
        if args.drafts:  # track them on the sheet once each, so you can note the outcome yourself
            added = 0
            for lead, reason in by_hand:
                if not lead.links:
                    continue  # nothing actionable
                key = (f"link::{st.slug(lead.company or 'unknown')}-{st.slug(lead.role)}-"
                       f"{st.slug(lead.links[0])[-14:]}")
                if key not in store:
                    store[key] = {"role": lead.role, "company": lead.company, "poster": lead.poster,
                                  "email": "", "track": lead.track, "status": "by_hand",
                                  "channel": "link", "apply_url": lead.links[0],
                                  "locations": lead.locations, "min_years": lead.min_years,
                                  "drafted_at": st.now()}
                    added += 1
            if added:
                st.save(store)
                print(f"  ({added} added to applications.csv with status by_hand)")


if __name__ == "__main__":
    run(sys.argv[1:])
