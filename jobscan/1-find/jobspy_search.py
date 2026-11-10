"""Search Indeed and LinkedIn job listings (not posts) with JobSpy and shortlist the fresh ones.

    python jobspy_search.py              last 7 days: Nashik, Pune, Mumbai and India-remote
    python jobspy_search.py --days 14    widen the window
    python jobspy_search.py --track      also add new-company listings to the tracker (by_hand)

Complements the LinkedIn post scan: listings rarely carry an email and are applied to on the site.
The same filters as everywhere else apply (cloud / devops / sre / full-stack titles, no seniority,
<= 4 years asked). Listings at a company you already applied to, or a recruiter's repost of a role
you applied to, are flagged. The queries are in core/queries.py.

Needs the JobSpy library (the PyPI release lags the repo):
    pip install https://github.com/speedyapply/JobSpy/archive/refs/heads/main.zip
Naukri is not scraped: it answers with a recaptcha. Keep Naukri in the browser.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import logging

import config
import found
import parser as jp
import queries
import store as st

log = logging.getLogger("jobscan.jobspy")


def text(value) -> str:
    """JobSpy rows are pandas: missing values are NaN (which is != itself)."""
    return "" if value is None or value != value else str(value)


def looks_remote(row: dict) -> bool:
    # Not the description: an office job's text mentions "remote" in passing.
    blob = " ".join(text(row.get(k)) for k in ("title", "location", "work_from_home_type")).lower()
    return bool(row.get("is_remote") is True or "remote" in blob)


def row_to_listing(row: dict, remote_search: bool, max_years: float) -> tuple[found.Listing | None, str]:
    """(Listing, '') or (None, reason it was skipped)."""
    title, body = text(row.get("title")), text(row.get("description"))
    if remote_search and not looks_remote(row):
        return None, "remote search, not remote"
    track, reason, years = jp.judge_listing(title, body, max_years)
    if reason:
        return None, reason.split(" (")[0] if not reason.startswith("asks") else "asks too many years"
    location = text(row.get("location"))
    city = jp.wanted_location(("remote " if looks_remote(row) else "") + location, body)
    if not city:
        return None, "location not wanted"
    return found.Listing(source=text(row.get("site")) or "jobspy", company=text(row.get("company")), title=title,
                         location=location[:70], url=text(row.get("job_url")), track=track, city=city,
                         years=years, posted=text(row.get("date_posted"))[:10]), ""


def searches(days: int):
    """(site, kwargs, is remote search) for every term x location. Indeed allows only one of
    hours_old / is_remote per search, so its remote search puts 'remote' in the query."""
    hours = days * 24
    for term in queries.JOB_SEARCH_TERMS:
        for location, remote in queries.JOB_SEARCH_LOCATIONS:
            if remote:
                yield "indeed", dict(search_term=f"{term} remote", location=location, country_indeed="india",
                                     hours_old=hours), True
                yield "linkedin", dict(search_term=term, location=location, is_remote=True, hours_old=hours,
                                       linkedin_fetch_description=True), True
            else:
                yield "indeed", dict(search_term=term, location=location, country_indeed="india",
                                     hours_old=hours), False
                yield "linkedin", dict(search_term=term, location=f"{location}, India", hours_old=hours,
                                       linkedin_fetch_description=True), False


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--days", type=int, default=7)
    cli.add_argument("--results", type=int, default=20, help="per site per search")
    cli.add_argument("--max-years", type=float, default=config.DEFAULT_MAX_YEARS)
    cli.add_argument("--track", action="store_true", help="add new-company listings to the tracker as by_hand")
    args = cli.parse_args()

    try:
        from jobspy import scrape_jobs
    except ImportError:
        raise SystemExit("JobSpy is not installed:\n  pip install https://github.com/speedyapply/JobSpy/"
                         "archive/refs/heads/main.zip") from None
    logging.getLogger("JobSpy").setLevel(logging.CRITICAL)   # its per-page chatter

    listings, skipped, seen_urls = [], {}, set()
    for site, kwargs, remote in searches(args.days):
        label = f"{site:8} {kwargs['search_term']!r} @ {kwargs['location']}"
        try:
            frame = scrape_jobs(site_name=[site], results_wanted=args.results, **kwargs)
        except Exception as exc:    # noqa: BLE001 - one blocked site shouldn't sink the run
            print(f"  {label}: failed ({type(exc).__name__}: {exc})")
            continue
        print(f"  {label}: {len(frame)}")
        for row in frame.to_dict("records"):
            if text(row.get("job_url")) in seen_urls:
                continue
            seen_urls.add(text(row.get("job_url")))
            item, why = row_to_listing(row, remote, args.max_years)
            if item:
                listings.append(item)
            else:
                skipped[why] = skipped.get(why, 0) + 1
    listings = found.dedupe(listings)
    store = st.load()
    found.flag_applied(listings, store)
    print("skipped: " + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])))
    found.report(listings, "jobspy")
    if args.track:
        print(f"\n{found.track_by_hand(listings, store)} added to applications.csv (status by_hand)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
