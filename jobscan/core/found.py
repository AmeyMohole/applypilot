"""Listings found on job boards and ATS APIs (as opposed to LinkedIn posts with an email).

These carry an apply link, not an address, so they are applied to by hand. The sweeps print them,
write a CSV to data/found/, flag the ones at companies you already applied to (so you don't apply
twice), and with --track add the rest to the tracker as status `by_hand` so you can note the outcome
in applications.csv.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import asdict, dataclass

import config
import contacts
import parser as jp
import store as st

TITLE_STOP = {"and", "the", "for", "of", "in", "at", "with", "naukri", "linkedin", "internshala",
              "pune", "remote", "india", "job", "opportunity", "engineer", "developer"}
TRACK_RANK = {"cloud": 0, "devops": 1, "fullstack": 1, "sre": 2, "devsecops": 2}
FIELDS = ["priority", "track", "title", "company", "location", "city", "years", "posted", "source",
          "applied_before", "url"]


@dataclass
class Listing:
    source: str
    company: str
    title: str
    location: str
    url: str
    track: str = ""
    city: str = ""
    years: float | None = None
    posted: str = ""
    applied_before: str = ""

    @property
    def priority(self) -> tuple:
        return (TRACK_RANK.get(self.track, 9), jp.city_rank([self.city] if self.city else []))


def title_tokens(title: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (title or "").lower()) if t not in TITLE_STOP}


def same_title(listing: set, applied: set) -> bool:
    """Recruiters repost a client's job under their own name, so a company match alone misses
    repeats. The tell is a title whose words almost all reappear (>= 3 distinctive words)."""
    shared = len(listing & applied)
    return shared >= 3 and shared >= 0.9 * min(len(listing), len(applied))


def flag_applied(listings: list[Listing], store: dict) -> None:
    """Fill applied_before: the same company, or the same title reposted by a recruiter."""
    by_company, titles = {}, []
    for rec in store.values():
        key = contacts.norm_company(rec.get("company") or "")
        if key:
            by_company.setdefault(key, []).append(rec.get("role") or "?")
        titles.append((title_tokens(rec.get("role")), f"{rec.get('role')} @ {rec.get('company')}"))
    for item in listings:
        before = list(by_company.get(contacts.norm_company(item.company), []))
        tokens = title_tokens(item.title)
        before += [label for t, label in titles if same_title(tokens, t) and label not in before]
        item.applied_before = "; ".join(before)


def dedupe(listings: list[Listing]) -> list[Listing]:
    """The same job listed twice (two searches, two locations): keep the first."""
    seen, out = set(), []
    for item in listings:
        key = (contacts.norm_company(item.company), re.sub(r"\W+", "", item.title.lower()), item.city)
        if item.url in seen or key in seen:
            continue
        seen.add(item.url)
        seen.add(key)
        out.append(item)
    return out


def ranked(listings: list[Listing]) -> list[Listing]:
    """New companies first, then best track and city, then freshest."""
    freshest = sorted(listings, key=lambda i: i.posted, reverse=True)      # ISO dates sort as text
    return sorted(freshest, key=lambda i: (bool(i.applied_before), *i.priority))   # stable: keeps freshest first


def write_csv(listings: list[Listing], name: str) -> str:
    folder = config.data_path("found")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{dt.datetime.now():%Y-%m-%d_%H%M}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:   # BOM so Excel reads UTF-8
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for item in listings:
            row = asdict(item)
            row["priority"] = "-".join(map(str, item.priority))
            w.writerow({k: row.get(k, "") for k in FIELDS})
    return str(path)


def track_by_hand(listings: list[Listing], store: dict | None = None) -> int:
    """Add listings (not at a company already applied to) as `by_hand` records. Returns how many."""
    store = store if store is not None else st.load()
    added = 0
    for item in listings:
        if item.applied_before or not item.url:
            continue
        key = f"link::{st.slug(item.company or 'unknown')}-{st.slug(item.title)}-{st.slug(item.url)[-14:]}"
        if key in store:
            continue
        st.update_record(key, {"role": item.title, "company": item.company, "poster": "", "email": "",
                               "track": item.track, "status": "by_hand", "channel": "link",
                               "apply_url": item.url, "locations": [item.city] if item.city else [],
                               "min_years": item.years, "source": item.source, "drafted_at": st.now()})
        store[key] = {}
        added += 1
    return added


def report(listings: list[Listing], csv_name: str, show: int = 60) -> None:
    """The printout every sweep ends with."""
    listings = ranked(listings)
    new = [i for i in listings if not i.applied_before]
    print(f"\n{len(listings)} listing(s): {len(new)} at new companies, "
          f"{len(listings) - len(new)} where you already applied")
    for heading, group in (("NEW COMPANIES", new), ("ALREADY APPLIED AT THIS COMPANY",
                                                    [i for i in listings if i.applied_before])):
        if not group:
            continue
        print(f"\n== {heading}")
        for i in group[:show]:
            yrs = "yrs ?" if i.years is None else f"{i.years:g}+ yrs"
            print(f"* [{i.track}] {i.title} - {i.company}  [{i.source}, {i.city or i.location[:24]}, {yrs}"
                  f"{', ' + i.posted if i.posted else ''}]")
            if i.applied_before:
                print(f"    before: {i.applied_before[:100]}")
            print(f"    {i.url}")
    if listings:
        print(f"\nFull list: {write_csv(listings, csv_name)}")
