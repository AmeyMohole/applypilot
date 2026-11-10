"""Sweep public ATS job-board APIs (Greenhouse, Ashby, Lever, SmartRecruiters) for cloud / devops /
full-stack roles you can apply to: your cities (Nashik > Pune > Mumbai > Bengaluru > Hyderabad) or
India-remote, posted in the last 30 days, no seniority in the title, <= 4 years asked.

    python ats_sweep.py                          sweep every board in core/boards.py
    python ats_sweep.py gh:postman ashby:atlan lever:cred sr:Freshworks   just these
    python ats_sweep.py --days 14 --track        fresher window; add the new ones to the tracker (by_hand)

No browser and no account: these are the same JSON endpoints each company's own careers page reads.
Listings carry an apply link, not an address, so they are applied to by hand; --track lists them in
applications.csv (status by_hand) so you can note the outcome. Listings at a company you already
applied to are flagged and not tracked again.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import re
import time
import urllib.error
import urllib.request

import boards
import config
import logging
import found
import parser as jp
import store as st

log = logging.getLogger("jobscan.ats")
UA = {"User-Agent": "Mozilla/5.0 (compatible; jobscan)", "Accept": "application/json"}


def fetch_json(url: str, retries: int = 2, timeout: int = 25):
    """GET a JSON endpoint. None when the board does not exist or stays unreachable."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 400, 403):       # no such board: don't retry
                return None
            if exc.code == 429 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            if attempt == retries:
                return None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if attempt == retries:
                return None
            time.sleep(1 + attempt)
    return None


def plain(markup: str) -> str:
    """HTML (possibly entity-escaped, as Greenhouse sends it) -> one line of text."""
    text = html.unescape(markup or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text))).strip()


def parse_time(value) -> dt.datetime | None:
    """ISO text or epoch milliseconds -> aware datetime."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc)
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def evaluate(source: str, org: str, title: str, location: str, body: str, posted, url: str,
             days: int, max_years: float, now: dt.datetime | None = None) -> found.Listing | None:
    """One posting -> a Listing, or None when it is not for you."""
    now = now or dt.datetime.now(dt.timezone.utc)
    when = parse_time(posted)
    if when and (now - when).days > days:
        return None
    city = jp.wanted_location(location, body)
    if not city:
        return None
    track, reason, years = jp.judge_listing(title, body, max_years)
    if reason:
        return None
    return found.Listing(source=source, company=org, title=title.strip(), location=location.strip()[:70],
                         url=url, track=track, city=city, years=years,
                         posted=when.date().isoformat() if when else "")


# ---- one parser per ATS: JSON -> [Listing]

def greenhouse(token: str, days: int, max_years: float, get=fetch_json) -> list | None:
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if data is None:
        return None
    out = []
    for job in data.get("jobs", []):
        location = " ".join(filter(None, [(job.get("location") or {}).get("name")]
                                  + [o.get("name") for o in job.get("offices") or []]))
        item = evaluate("greenhouse", token, job.get("title", ""), location, plain(job.get("content")),
                        job.get("updated_at") or job.get("first_published"), job.get("absolute_url", ""),
                        days, max_years)
        if item:
            out.append(item)
    return out


def ashby(org: str, days: int, max_years: float, get=fetch_json) -> list | None:
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    if data is None:
        return None
    out = []
    for job in data.get("jobs", []):
        location = " ".join(filter(None, [job.get("location")] + [s.get("location") for s in
                                                                  job.get("secondaryLocations") or []]))
        if job.get("isRemote"):
            location += " remote"
        body = plain(job.get("descriptionHtml") or job.get("descriptionPlain"))
        item = evaluate("ashby", org, job.get("title", ""), location, body, job.get("publishedAt"),
                        job.get("jobUrl", ""), days, max_years)
        if item:
            out.append(item)
    return out


def lever(company: str, days: int, max_years: float, get=fetch_json) -> list | None:
    data = get(f"https://api.lever.co/v0/postings/{company}?mode=json")
    if data is None or not isinstance(data, list):
        return None
    out = []
    for job in data:
        cats = job.get("categories") or {}
        location = " ".join(filter(None, [cats.get("location"), *(cats.get("allLocations") or [])]))
        if (job.get("workplaceType") or "").lower() == "remote":
            location += " remote"
        body = job.get("descriptionPlain") or plain(job.get("description"))
        item = evaluate("lever", company, job.get("text", ""), location, body, job.get("createdAt"),
                        job.get("hostedUrl", ""), days, max_years)
        if item:
            out.append(item)
    return out


def smartrecruiters(company: str, days: int, max_years: float, get=fetch_json) -> list | None:
    data = get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?country=in&limit=100")
    if data is None:
        return None
    out = []
    for job in data.get("content", []):
        loc = job.get("location") or {}
        location = " ".join(str(v) for v in (loc.get("city"), loc.get("region"), loc.get("country")) if v)
        if loc.get("remote"):
            location += " remote"
        item = evaluate("smartrecruiters", company, job.get("name", ""), location, "", job.get("releasedDate"),
                        f"https://jobs.smartrecruiters.com/{company}/{job.get('id', '')}", days, max_years)
        if item:
            out.append(item)
    return out


SOURCES = {"gh": greenhouse, "ashby": ashby, "lever": lever, "sr": smartrecruiters}


def default_targets() -> list[str]:
    return ([f"gh:{t}" for t in dict.fromkeys(boards.GREENHOUSE)] + [f"ashby:{t}" for t in dict.fromkeys(boards.ASHBY)]
            + [f"lever:{t}" for t in dict.fromkeys(boards.LEVER)]
            + [f"sr:{t}" for t in dict.fromkeys(boards.SMARTRECRUITERS)])


def probe(targets: list[str]) -> list[str]:
    """Targets whose board does not exist (404), so core/boards.py can be pruned."""
    def gone(target: str) -> bool:
        kind, _, name = target.partition(":")
        url = {"gh": f"https://boards-api.greenhouse.io/v1/boards/{name}/jobs",
               "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{name}",
               "lever": f"https://api.lever.co/v0/postings/{name}?mode=json",
               "sr": f"https://api.smartrecruiters.com/v1/companies/{name}/postings?limit=1"}[kind]
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25):
                return False
        except urllib.error.HTTPError as exc:
            return exc.code in (404, 400)
        except (urllib.error.URLError, TimeoutError, OSError):
            return False                  # a network blip is not proof the board is gone
    with cf.ThreadPoolExecutor(8) as pool:
        return [t for t, dead in zip(targets, pool.map(gone, targets)) if dead]


def sweep_one(target: str, days: int, max_years: float, sources=SOURCES) -> list | None:
    """Listings from one board; None when it is unreachable or its data is unreadable."""
    kind, _, name = target.partition(":")
    if kind not in sources or not name:
        raise SystemExit(f"bad target {target!r}: use gh:<token> ashby:<org> lever:<co> sr:<Company>")
    try:
        return sources[kind](name, days, max_years)
    except Exception as exc:    # noqa: BLE001 - one odd board must not sink the whole sweep
        log.warning("%s: unreadable board data (%s: %s)", target, type(exc).__name__, exc)
        return None


def sweep(targets: list[str], days: int, max_years: float, workers: int = 8, sources=SOURCES):
    """([Listing], [unreachable targets])."""
    listings, dead = [], []
    with cf.ThreadPoolExecutor(workers) as pool:
        for target, result in zip(targets, pool.map(lambda t: sweep_one(t, days, max_years, sources), targets)):
            if result is None:
                dead.append(target)
            else:
                listings += result
    return found.dedupe(listings), dead


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("targets", nargs="*", help="gh:<token> ashby:<org> lever:<co> sr:<Company> (default: all)")
    cli.add_argument("--days", type=int, default=30, help="ignore postings older than this")
    cli.add_argument("--max-years", type=float, default=config.DEFAULT_MAX_YEARS)
    cli.add_argument("--track", action="store_true", help="add new-company listings to the tracker as by_hand")
    cli.add_argument("--probe", action="store_true",
                     help="only list boards that no longer exist (to prune core/boards.py)")
    args = cli.parse_args()

    targets = args.targets or default_targets()
    if args.probe:
        gone = probe(targets)
        print(f"{len(gone)} of {len(targets)} boards do not exist:\n" + " ".join(gone))
        return 0
    listings, dead = sweep(targets, args.days, args.max_years)
    print(f"{len(targets) - len(dead)} of {len(targets)} boards answered, {len(dead)} unreachable")
    store = st.load()
    found.flag_applied(listings, store)
    found.report(listings, "ats")
    if args.track:
        print(f"\n{found.track_by_hand(listings, store)} added to applications.csv (status by_hand)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
