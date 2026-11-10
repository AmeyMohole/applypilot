"""Draft an application to an address that did not come from a LinkedIn post (job board, careers
page, a redirect) or that needs hand-written paragraphs for one job description.

    python draft_direct.py hr@acme.com Acme "Cloud Engineer" --years 2 --track cloud
    python draft_direct.py hr@acme.com - "DevOps Engineer" --preview        print it, no Gmail, no tracker
    python draft_direct.py --queue                   draft every lead waiting in data/lead_queue.json
    python draft_direct.py --spec specs.json         one tailored draft per spec (see below)
    python draft_direct.py ... --force               draft even though a check blocks it

Checks first (contacts.blockers): do-not-contact list and earlier bounces, a domain with no mail
server, other applications at the same company; and compose.fact_warnings() on every sentence you
wrote by hand (a skill your resume doesn't list is blocked unless the sentence states the gap). A
block needs --force; a warning is printed and the draft goes ahead.

Direct drafts are always flagged for review, so the unattended run never sends them: read the draft
in Gmail, then python send.py --go.

A spec is the JSON form of the flags (same names, underscores):

    {"email": "hr@acme.com", "company": "Acme", "role": "Full Stack Developer", "years": 2,
     "location": "pune", "track": "fullstack", "from_post": false, "greet": "Asha",
     "subject": null,                                  # optional override
     "interest": "one paragraph written against the JD",
     "blocks": [["p", "..."], ["ul", ["...", "..."]]], # replaces opener/stack/project/interest
     "fu_focus": "one JD sentence for the 1st follow-up",
     "fu1_paras": ["..."], "fu2_paras": ["..."],       # full per-JD follow-up paragraphs
     "source": "careers-page", "post_url": "https://..."}
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import json

import compose
import config
import contacts
import leadqueue
import parser as jp
import runlock
import store as st

TRACKS = ["cloud", "devops", "fullstack", "sre", "devsecops"]
LOCATIONS = ["nashik", "pune", "mumbai", "bengaluru", "hyderabad", "remote", "none"]
DEFAULTS = {"company": "-", "years": None, "location": "none", "track": "devops", "from_post": False,
            "interest": "", "greet": None, "blocks": None, "subject": None, "fu_focus": "", "fu1_paras": None,
            "fu2_paras": None, "source": "", "post_url": ""}


class Blocked(Exception):
    pass


def lead_for(spec: dict) -> jp.Lead:
    # A dash means the advert never named the employer: the email then avoids naming one.
    company = "" if (spec.get("company") or "").strip() in ("-", "") else spec["company"]
    loc = (spec.get("location") or "none").lower()
    return jp.Lead(role=spec["role"], email=spec["email"], emails=[spec["email"]], company=company,
                   company_confident=bool(company), min_years=spec.get("years"),
                   locations=[] if loc == "none" else [loc], track=spec["track"],
                   is_intern=jp.has_term(spec["role"], jp.INTERN_TERMS))


def tailored_texts(spec: dict) -> list[str]:
    """Every sentence in the spec that was written by hand for the JD."""
    texts = [spec.get("interest") or "", spec.get("fu_focus") or ""]
    texts += compose.blocks_text(spec.get("blocks") or [])
    texts += list(spec.get("fu1_paras") or []) + list(spec.get("fu2_paras") or [])
    return [t for t in texts if t]


def prepare(spec: dict, store: dict, force: bool = False, check_mx: bool = True) -> dict:
    """Everything up to the Gmail call: checks, the email, the tracker record. Pure apart from the
    MX lookup. Returns {key, mail, record, warnings}; raises Blocked."""
    spec = {**DEFAULTS, **{k: v for k, v in spec.items() if v is not None or k in ("years", "greet", "subject")}}
    spec["email"] = spec["email"].strip().lower()
    spec["location"] = (spec["location"] or "none").lower()
    if spec["location"] not in LOCATIONS:
        raise Blocked(f"location must be one of {LOCATIONS}, not {spec['location']!r}")
    if spec["track"] not in TRACKS:
        raise Blocked(f"track must be one of {TRACKS}, not {spec['track']!r}")
    lead = lead_for(spec)
    if lead.key in store:
        raise Blocked(f"already in tracker: {lead.key} ({store[lead.key].get('status')})")
    warnings, blocks = [], []
    for level, message in contacts.blockers(store, spec["email"], lead.company, spec["role"], check_mx=check_mx):
        (blocks if level == "block" else warnings).append(message)
    texts = tailored_texts(spec)
    blocks += [f"not on your resume - {p}" for t in texts for p in compose.fact_warnings(t)]
    warnings += [f"states a gap: {s}" for t in texts for s in compose.negative_sentences(t)]
    if blocks and not force:
        raise Blocked("; ".join(blocks))
    warnings += [f"(forced) {b}" for b in blocks]

    greet = spec["greet"]
    if greet is None and not compose.greeting_name("", spec["email"]):
        greet = ""
    mail = compose.compose(lead, source="post" if spec["from_post"] else "direct", greet=greet,
                           interest=spec["interest"] or None, blocks=spec["blocks"], subject=spec["subject"])
    record = {**_record(lead, spec, mail), "needs_review": True,
              "review_reason": "drafted directly: read before sending"}
    return {"key": lead.key, "mail": mail, "record": record, "warnings": warnings}


def _record(lead: jp.Lead, spec: dict, mail: dict) -> dict:
    record = st.make_record(lead, "drafted", "")
    record.update(source=spec["source"] or ("linkedin-post" if spec["from_post"] else "direct"),
                  inbox=contacts.inbox_kind(spec["email"]), subject=mail["subject"],
                  greet_name=spec["greet"] if spec["greet"] is not None else compose.greeting_name("", spec["email"]))
    if spec["interest"] or spec["blocks"]:
        record["tailored"] = True
    for field in ("fu_focus", "fu1_paras", "fu2_paras", "post_url"):
        if spec.get(field):
            record[field] = spec[field]
    return record


def commit(prepared: dict, svc) -> str:
    """Create the Gmail draft and record it on top of the current file."""
    import gmail_draft
    mail, record = prepared["mail"], prepared["record"]
    draft_id = gmail_draft.create_draft(svc, record["email"], mail["subject"], mail["text"], mail["html"],
                                        [config.RESUME])
    record["draft_id"] = draft_id
    st.update_record(prepared["key"], record)
    return draft_id


def spec_from_args(args) -> dict:
    return {"email": args.email, "company": args.company, "role": args.role, "years": args.years,
            "location": args.location, "track": args.track, "from_post": args.from_post, "greet": args.greet,
            "subject": args.subject, "interest": args.interest, "fu_focus": args.fu_focus}


def spec_from_queue(entry: dict) -> dict:
    return {"email": entry["email"], "company": entry.get("company") or "-", "role": entry["role"],
            "years": entry.get("years"), "location": entry.get("location") or "none",
            "track": entry.get("track") or "devops", "source": entry.get("source") or "queue",
            "from_post": entry.get("source") == "linkedin-post", "post_url": entry.get("post_url") or ""}


def build_cli() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("email", nargs="?")
    cli.add_argument("company", nargs="?", help="company name, or '-' when the advert does not name one")
    cli.add_argument("role", nargs="?")
    cli.add_argument("--years", type=float)
    cli.add_argument("--track", default="devops", choices=TRACKS)
    cli.add_argument("--location", default="none", type=str.lower, choices=LOCATIONS,
                     help="the job's city (preference: Nashik > Pune > Mumbai > Bengaluru > Hyderabad)")
    cli.add_argument("--from-post", action="store_true", help="the lead came from a LinkedIn post")
    cli.add_argument("--greet", default=None, help="first name to greet ('' for a plain Hello,)")
    cli.add_argument("--subject", default=None)
    cli.add_argument("--interest", default="", help="a paragraph written against the JD")
    cli.add_argument("--fu-focus", default="", help="one JD sentence for the first follow-up")
    cli.add_argument("--queue", action="store_true", help="draft the queued leads")
    cli.add_argument("--spec", help="JSON file: one spec or a list (see above)")
    cli.add_argument("--preview", action="store_true", help="print each email and the checks; change nothing")
    cli.add_argument("--force", action="store_true", help="draft even when a check blocks it")
    cli.add_argument("--no-mx", action="store_true", help="skip the mail-server lookup (offline)")
    return cli


def main() -> int:
    config.setup_console()
    cli = build_cli()
    args = cli.parse_args()
    if args.spec:
        specs = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        specs = specs if isinstance(specs, list) else [specs]
    elif args.queue:
        specs = []
        for entry in leadqueue.load():
            if entry.get("role"):
                specs.append(spec_from_queue(entry))
            else:
                print(f"  skipped {entry.get('email')}: no role recorded, use draft_direct.py by hand")
    elif args.email and args.company is not None and args.role:
        specs = [spec_from_args(args)]
    else:
        cli.error("give EMAIL COMPANY ROLE, --queue, or --spec FILE")
    if not args.preview and runlock.refuse_if_sending("draft_direct"):
        return 1

    store = st.load()
    svc = None
    made = failed = 0
    for spec in specs:
        label = f"{spec.get('email')} - {spec.get('role')}"
        try:
            prepared = prepare(spec, store, force=args.force, check_mx=not args.no_mx)
        except Blocked as why:
            print(f"NOT DRAFTED {label}: {why}")
            failed += 1
            continue
        except KeyError as missing:
            print(f"NOT DRAFTED {label}: spec is missing {missing}")
            failed += 1
            continue
        for warning in prepared["warnings"]:
            print(f"  warning ({spec['email']}): {warning}")
        mail = prepared["mail"]
        if args.preview:
            print(f"--- preview -> {spec['email']}\nsubject: {mail['subject']}\n\n{mail['text']}\n")
            continue
        if svc is None:
            import gmail_draft
            svc = gmail_draft.get_service()
        draft_id = commit(prepared, svc)
        store[prepared["key"]] = prepared["record"]      # the next spec in the batch sees this one
        made += 1
        print(f"  drafted ({draft_id}) -> {spec['email']}")
    if args.queue and not args.preview:
        print(f"{leadqueue.drop_drafted(st.load())} removed from the queue.")
    if len(specs) > 1 or failed:
        print(f"\n{made} drafted, {failed} not drafted" + (" (preview only)" if args.preview else ""))
    if made:
        print("Read the drafts in Gmail, then: python 2-apply/send.py --go")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
