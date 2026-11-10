"""Sweep Workday career sites (their public CXS JSON API) for cloud / devops / full-stack roles.

    python workday_sweep.py                       the tenants in core/boards.py
    python workday_sweep.py nvidia.wd5/NVIDIAExternalCareerSite --days 14

A tenant is "<tenant>.<wdN>/<site>": the part of a myworkdayjobs.com URL,
https://<tenant>.<wdN>.myworkdayjobs.com/en-US/<site>. Broken or renamed tenants are skipped and
counted, so the list can be grown freely. Same filters and output as ats_sweep.py (--track adds the
new ones to applications.csv as by_hand).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import concurrent.futures as cf
import json
import logging
import re
import urllib.error
import urllib.request

import boards
import config
import found
import parser as jp
import queries
import store as st

log = logging.getLogger("jobscan.workday")
HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"}


def request_json(url: str, body: dict | None = None, timeout: int = 25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fresh_enough(posted_on: str, days: int) -> bool:
    """Workday says 'Posted Today', 'Posted Yesterday', 'Posted 5 Days Ago', 'Posted 30+ Days Ago'."""
    text = (posted_on or "").lower()
    if "30+" in text:
        return False
    if "today" in text:
        return True
    if "yesterday" in text:
        return days >= 1
    m = re.search(r"(\d+)\s*days? ago", text)
    return bool(m) and int(m.group(1)) <= days


def posted_date(posted_on: str) -> str:
    import datetime as dt
    text = (posted_on or "").lower()
    m = re.search(r"(\d+)\s*days? ago", text)
    offset = int(m.group(1)) if m else 1 if "yesterday" in text else 0
    return (dt.date.today() - dt.timedelta(days=offset)).isoformat()


def sweep_tenant(spec: str, days: int, max_years: float, terms=None, call=request_json) -> list | None:
    """Listings for one tenant; None when the tenant is unreachable."""
    host, _, site = spec.partition("/")
    tenant = host.split(".")[0]
    base = f"https://{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    candidates = {}
    try:
        for term in terms or queries.JOB_SEARCH_TERMS:
            data = call(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
            for job in data.get("jobPostings", []):
                path, title = job.get("externalPath", ""), job.get("title", "")
                if not path or path in candidates or not fresh_enough(job.get("postedOn", ""), days):
                    continue
                track, reason, _ = jp.judge_listing(title, "", max_years)
                if track and not reason:
                    candidates[path] = job
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.info("%s unreachable: %s", spec, exc)
        return None
    out = []
    for path, job in candidates.items():
        try:
            info = call(f"{api}{path}").get("jobPostingInfo", {})
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        location = " ".join(filter(None, [info.get("location"), *(info.get("additionalLocations") or []),
                                          job.get("locationsText")]))
        body = re.sub(r"<[^>]+>", " ", info.get("jobDescription", ""))
        city = jp.wanted_location(location, body)
        track, reason, years = jp.judge_listing(job.get("title", ""), body, max_years)
        if city and track and not reason:
            out.append(found.Listing(source="workday", company=tenant, title=job.get("title", ""),
                                     location=location[:70], url=f"{base}/en-US/{site}{path}", track=track,
                                     city=city, years=years, posted=posted_date(job.get("postedOn", ""))))
    return out


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("tenants", nargs="*")
    cli.add_argument("--days", type=int, default=14)
    cli.add_argument("--max-years", type=float, default=config.DEFAULT_MAX_YEARS)
    cli.add_argument("--track", action="store_true", help="add new-company listings to the tracker as by_hand")
    args = cli.parse_args()

    specs = args.tenants or list(dict.fromkeys(boards.WORKDAY))
    listings, dead = [], []
    with cf.ThreadPoolExecutor(8) as pool:
        for spec, result in zip(specs, pool.map(lambda s: sweep_tenant(s, args.days, args.max_years), specs)):
            if result is None:
                dead.append(spec)
            else:
                listings += result
    listings = found.dedupe(listings)
    print(f"{len(specs) - len(dead)} of {len(specs)} tenants answered, {len(dead)} unreachable")
    store = st.load()
    found.flag_applied(listings, store)
    found.report(listings, "workday")
    if dead:
        print("unreachable:", " ".join(dead))
    if args.track:
        print(f"\n{found.track_by_hand(listings, store)} added to applications.csv (status by_hand)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
