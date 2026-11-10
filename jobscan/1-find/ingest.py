"""Turn found leads into a ranked, deduped queue of drafts.

    python ingest.py found.json               rank and check, change nothing
    python ingest.py found.json --queue       also add the good ones to data/lead_queue.json
    python ingest.py triage.txt --queue       same, from a pasted triage printout
    python ingest.py --show-queue             what is already queued
    python draft_direct.py --queue            then draft them (always flagged for review)

Input is either
- JSON: a list of {"emails" | "email", "text" (the post or page), and optionally "role", "company",
  "years", "location" | "locations", "url", "age", "source", "poster"} (careers_emails.py writes
  this shape), or
- text: "== a@x.com, b@y.com | yrs=1,2 | loc=ok | age=2d" followed by the post body.

Every lead is parsed with the same parser as scan.py, checked against the tracker and the
do-not-contact rules (contacts.blockers), and scored: cloud over devops / full-stack over sre,
Nashik > Pune > Mumbai > Bengaluru > Hyderabad > remote, fewer years asked, a named recruiter over a
generic inbox, fresh posts over old ones, and a penalty for a mandatory skill you don't have.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import json
import re

import config
import contacts
import leadqueue
import parser as jp
import replies
import store as st

TRIAGE_HEAD_RE = re.compile(r"^== (?P<emails>[^|]+?)(?: \| (?P<rest>.*))?$")

HARD_NO_RE = re.compile(r"\b(unpaid|registration fee|training fee|walk-?in only|offline interviews? only|"
                        r"commission (?:based|only))\b", re.I)
# A skill you don't have, stated as mandatory: Azure / GCP-only shops, Java / .NET stacks, etc.
_LACKED = "|".join(re.escape(s) for s in [*config.MATCH_ONLY_SKILLS, "java", "spring boot", ".net", "c#",
                                          "angular", "golang", "scala", "django", "mongodb", "typescript"])
MUST_HAVE_RE = re.compile(rf"\b({_LACKED})\b[^.\n]{{0,40}}\b(mandatory|must|required|compulsory)|"
                          rf"\b(mandatory|must have|required)\b[^.\n]{{0,30}}\b({_LACKED})\b", re.I)
GOOD_FIT_RE = re.compile(r"\b(terraform|aws|kubernetes|eks|docker|ci/cd|jenkins|fastapi|react|python|"
                         r"node\.?js|postgres(?:ql)?|prometheus|grafana)\b", re.I)
CITY_SCORE = {"nashik": 3.0, "pune": 2.5, "mumbai": 1.5, "bengaluru": 1.0, "hyderabad": 0.5}
TRACK_SCORE = {"cloud": 1.5, "devops": 1.0, "fullstack": 1.0, "sre": 0.5, "devsecops": 0.5}


def parse_triage_text(text: str) -> list:
    """A pasted triage printout -> [{emails, text, age, ...}]."""
    leads, current = [], None
    for line in text.splitlines():
        head = TRIAGE_HEAD_RE.match(line.strip())
        if head:
            current = {"emails": [e.strip() for e in head.group("emails").split(",") if e.strip()], "text": ""}
            for part in (head.group("rest") or "").split("|"):
                key, _, value = part.strip().partition("=")
                if key in ("age", "loc", "yrs") and value:
                    current[key] = value
            leads.append(current)
        elif current is not None:
            current["text"] += line.strip().replace(" | ", "\n") + "\n"   # the scripts join lines with " | "
    return leads


def load_leads(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    except ValueError:
        return parse_triage_text(raw)


def pick_address(emails: list, company: str) -> str:
    """The address to write to. Robots and image names are dropped; a company domain beats a
    free-mail one, which is often a candidate's in a repost."""
    usable = [e.lower().rstrip(".") for e in emails
              if not replies.is_automated(e) and not re.search(r"\.(png|jpe?g|gif|svg|webp)$", e, re.I)]
    usable = [e for e in usable if e.split("@")[0] not in ("sales", "support", "billing", "press", "media")]
    if not usable:
        return ""
    company_n = contacts.norm_company(company)
    for address in usable:
        if company_n and company_n[:5] in address.split("@")[-1].replace("-", ""):
            return address
    business = [e for e in usable if not contacts.is_free_mail(e)]
    return (business or usable)[0]


def score(lead: dict) -> tuple:
    """(points, reasons). Higher is better; below 0 is not worth a draft."""
    points, reasons = 0.0, []
    locs = lead["locations"]
    city = jp.best_city(locs)
    if city:
        points += CITY_SCORE[city]
        reasons.append(city)
    elif "remote" in locs:
        points += 1.0
        reasons.append("remote")
    elif locs:
        points -= 3.0
        reasons.append("other city")
    years = lead["years"]
    if years is None:
        points += 0.5
    elif years <= 1:
        points += 2.0
        reasons.append(f"{years:g}y")
    elif years <= 2:
        points += 1.5
        reasons.append(f"{years:g}y")
    elif years <= 3:
        points += 0.5
        reasons.append(f"{years:g}y stretch")
    else:
        points -= 2.0
        reasons.append(f"{years:g}y+ (most replies to such posts are 'needs N years')")
    if lead["track"]:
        points += TRACK_SCORE.get(lead["track"], 0)
        reasons.append(lead["track"])
    else:
        points -= 1.0
        reasons.append("not clearly your stack")
    if GOOD_FIT_RE.search(lead["text"]) or GOOD_FIT_RE.search(lead["role"]):
        points += 0.5
    if lead["inbox"] == "person":
        points += 1.0
        reasons.append("named recruiter")
    age = (lead.get("age") or "").strip()
    if re.fullmatch(r"\d+[mh]|[1-3]d", age):
        points += 1.0
        reasons.append(f"fresh ({age})")
    elif re.fullmatch(r"\d+w|\d+mo", age):
        points -= 1.0
        reasons.append(f"old ({age})")
    if MUST_HAVE_RE.search(lead["text"]):
        points -= 2.5
        reasons.append("mandatory skill you don't list")
    return round(points, 1), reasons


def assess(raw: dict, store: dict, check_mx: bool = True) -> dict:
    """One found lead -> {verdict, score, reasons, spec, ...}."""
    text = raw.get("text") or raw.get("body") or raw.get("post") or ""
    emails = raw.get("emails") or ([raw["email"]] if raw.get("email") else jp.extract_emails(text))
    if raw.get("company"):
        company, confident = raw["company"], True
    else:
        company, confident = jp.extract_company(text, emails[0] if emails else "")
    role = raw.get("role") or jp.extract_role(text.split("\n")) or ""
    years = raw.get("years")
    if years is None:
        years = jp.extract_years(text)
    locations = raw.get("locations") or ([raw["location"]] if raw.get("location") else jp.extract_locations(text))
    track = raw.get("track") or jp.classify(role, text[:700])
    address = pick_address(emails, company or "")
    lead = {"email": address, "company": company if confident else "", "role": role, "years": years,
            "locations": [loc.lower() for loc in locations], "track": track, "text": text,
            "age": raw.get("age", ""), "inbox": contacts.inbox_kind(address, raw.get("poster", "")) if address else "",
            "url": raw.get("url", ""), "source": raw.get("source") or "linkedin-post"}
    out = {**lead, "verdict": "new", "notes": []}

    if not address:
        out["verdict"] = "no usable address"
        return out
    for check, label in ((jp.is_job_seeker, "a job seeker's own post"), (jp.is_vendor, "a staffing vendor's pitch"),
                         (jp.is_course, "a course / interview-prep pitch")):
        if text and check(text):
            out["verdict"] = f"skip: {label}"
            return out
    if HARD_NO_RE.search(text):
        out["verdict"] = f"skip: {HARD_NO_RE.search(text).group(0)}"
        return out
    if not role:
        out["notes"].append("no role title found - set it by hand")
    for level, message in contacts.blockers(store, address, company or "", role, check_mx=check_mx):
        if level == "block":
            out["verdict"] = f"blocked: {message}"
            return out
        if message.startswith("already written to"):
            out["verdict"] = f"already mailed: {message.split(': ', 1)[1]}"
            return out
        out["notes"].append(message)
    if any(n.startswith("same company") for n in out["notes"]):
        out["verdict"] = "new (company already contacted)"
    out["score"], out["reasons"] = score(lead)
    location = jp.best_city(lead["locations"]) or ("remote" if "remote" in lead["locations"] else "")
    out["spec"] = {k: v for k, v in {
        "email": address, "company": lead["company"] or "-", "role": role or "", "years": years,
        "location": location, "track": track or "devops", "source": lead["source"], "post_url": lead["url"],
    }.items() if v not in (None, "")}
    return out


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("path", nargs="?", help="found JSON or triage text")
    cli.add_argument("--queue", action="store_true", help="add the new, scoring leads to data/lead_queue.json")
    cli.add_argument("--min-score", type=float, default=2.0, help="drop leads scoring below this")
    cli.add_argument("--no-mx", action="store_true", help="skip mail-server lookups")
    cli.add_argument("--show-queue", action="store_true", help="list data/lead_queue.json and exit")
    args = cli.parse_args()

    store = st.load()
    if args.show_queue:
        dropped = leadqueue.drop_drafted(store)
        queue = leadqueue.load()
        print(f"{len(queue)} queued" + (f" ({dropped} already drafted, removed)" if dropped else ""))
        for e in sorted(queue, key=lambda e: -(e.get("score") or 0)):
            print(f"  {e.get('score', ''):>4}  {e['email']:38s} {e.get('company', '-')[:22]:22s} "
                  f"{e.get('role', '')[:40]}  [{e.get('source', '')}]")
        return 0
    if not args.path:
        cli.error("give a found/triage file, or --show-queue")

    results = [assess(raw, store, check_mx=not args.no_mx) for raw in load_leads(args.path)]
    seen, unique = set(), []
    for r in results:                  # one post lists several addresses; searches overlap: first sighting wins
        if r["email"] and r["email"] in seen:
            continue
        seen.add(r["email"])
        unique.append(r)
    good = sorted((r for r in unique if r["verdict"].startswith("new") and r.get("score", -9) >= args.min_score),
                  key=lambda r: -r["score"])
    rest = [r for r in unique if r not in good]

    print(f"{len(unique)} leads: {len(good)} worth drafting, {len(rest)} not\n")
    for r in good:
        print(f"{r['score']:>5}  {r['email']:38s} {(r['company'] or '-')[:22]:22s} {r['role'][:44]}")
        print(f"       {', '.join(r['reasons'])}" + (f" | {' | '.join(r['notes'])}" if r["notes"] else ""))
    if rest:
        print("\nNot drafting:")
        for r in rest:
            why = (f"score {r['score']} < {args.min_score:g}: {', '.join(r['reasons'])}"
                   if r["verdict"].startswith("new") else r["verdict"])
            print(f"  {r['email'] or '(none)':38s} {r['role'][:36]:36s} {why[:100]}")

    if args.queue and good:
        added = leadqueue.add([{**r["spec"], "score": r["score"], "reasons": r["reasons"], "notes": r["notes"],
                                "text": r["text"][:1500]} for r in good])
        print(f"\n{added} added to data/lead_queue.json; next: python draft_direct.py --queue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
