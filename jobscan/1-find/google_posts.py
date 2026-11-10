"""Read LinkedIn hiring posts found through Google, without a LinkedIn login.

    python google_posts.py <url-dump-dir> [--max-days 30] [--limit 150]

Google indexes public posts at linkedin.com/posts/<slug>-activity-<id>-<x>, and LinkedIn serves those
pages to logged-out visitors with the full post text in a JSON-LD block. So the account that applies
never has to search:

  1. In the browser, on any google.com page, run each query in core/queries.py GOOGLE_POST_QUERIES
     (past week) and save the result pages as google-*.json files in a folder (the script only needs
     the linkedin.com/posts/... URLs it can find in them).
  2. This script fetches each post anonymously and writes a capture file in the same "Feed post"
     layout the browser captures use (Downloads/jobscan/), and scan.py takes it from there.

Only the post body is kept. The page also carries comments, where candidates paste their own
addresses, and mailing those would be an application sent to another job seeker. Posts older than
--max-days are dropped (the age is encoded in the activity id). Fetches are 2-4.5 s apart and stop at
the first login wall or three failures in a row; fetched posts are cached in data/google_posts_cache.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import datetime as dt
import glob
import html
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

import config
import fileutil

URL_RE = re.compile(r"https://(?:[a-z]+\.)?linkedin\.com/posts/[A-Za-z0-9_%\-]+?activity-(\d{19})-[A-Za-z0-9]+")
LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}


def posted_at(activity_id: str) -> dt.datetime:
    """The top 41 bits of a LinkedIn activity id are the post time in milliseconds."""
    return dt.datetime.fromtimestamp((int(activity_id) >> 22) / 1000, dt.timezone.utc)


def collect_urls(dump_dir: str) -> dict:
    """Map activity id -> canonical post URL across every google-*.json dump."""
    found = {}
    for path in sorted(glob.glob(os.path.join(dump_dir, "google-*.json"))):
        with open(path, encoding="utf-8") as handle:
            text = handle.read().replace("\\/", "/")
        for match in URL_RE.finditer(text):
            url = re.sub(r"://[a-z]+\.linkedin", "://www.linkedin", match.group(0))   # one host keeps ids unique
            found.setdefault(match.group(1), url)
    return found


def parse_post(page: str) -> dict | None:
    """{author, date, body} from a public post page's JSON-LD block, or None."""
    for block in LD_RE.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if data.get("@type") not in ("SocialMediaPosting", "DiscussionForumPosting", "Article"):
            continue
        author = data.get("author") or {}
        if isinstance(author, list):
            author = author[0] if author else {}
        return {"author": html.unescape(author.get("name", "")).strip() or "Unknown",
                "date": data.get("datePublished", ""),
                "body": html.unescape(data.get("articleBody") or data.get("text") or "").strip()}
    return None


def fetch(url: str) -> dict | None:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        if "authwall" in resp.geturl() or "login" in resp.geturl():
            raise PermissionError("redirected to the login wall")
        return parse_post(resp.read().decode("utf-8", "replace"))


def age_label(when: dt.datetime) -> str:
    """Mimic LinkedIn's "5h •" / "3d •" line, which the parser uses as the boundary between the
    poster's name and the advert."""
    hours = max(1, int((dt.datetime.now(dt.timezone.utc) - when).total_seconds() // 3600))
    return f"{hours}h •" if hours < 24 else f"{hours // 24}d •"


def capture_block(author: str, body: str, when: dt.datetime) -> str:
    # The post URL stays out of the text: scan.py would report it as an apply link.
    return "\n".join(["Feed post", author, "• 3rd+", age_label(when), body, ""])


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("dump_dir")
    cli.add_argument("--max-days", type=int, default=30)
    cli.add_argument("--limit", type=int, default=150)
    args = cli.parse_args()

    cache_path = config.data_path("google_posts_cache.json")
    cache = fileutil.read_json(cache_path, {})
    urls = collect_urls(args.dump_dir)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.max_days)
    fresh = {aid: u for aid, u in urls.items() if posted_at(aid) >= cutoff}
    todo = [aid for aid in fresh if aid not in cache][:args.limit]
    print(f"{len(urls)} post URLs, {len(fresh)} within {args.max_days} days, "
          f"{len(fresh) - len(todo)} already cached, fetching {len(todo)}")

    failures = 0
    for n, aid in enumerate(todo, 1):
        try:
            post = fetch(fresh[aid])
            failures = 0
        except PermissionError as exc:
            print(f"  stop: {exc} after {n - 1} fetches (LinkedIn is throttling this IP; retry later)")
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            failures += 1
            print(f"  {aid}: {exc}")
            if failures >= 3:
                print("  stop: 3 failures in a row")
                break
            continue
        cache[aid] = {"url": fresh[aid], "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                      **(post or {"author": "", "date": "", "body": ""})}
        if n % 10 == 0:
            print(f"  {n}/{len(todo)}")
        time.sleep(random.uniform(2.0, 4.5))
    fileutil.write_json_atomic(cache_path, cache)

    # Every fresh post goes in the capture, cached or not: scan.py's own dedupe decides what is new.
    blocks = [capture_block(cache[aid]["author"], cache[aid]["body"], posted_at(aid))
              for aid in fresh if cache.get(aid) and cache[aid].get("body")]
    out = Path.home() / "Downloads" / "jobscan" / f"linkedin-jobscan-google-{dt.datetime.now():%Y-%m-%dT%H-%M-%S}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n" + "\n".join(blocks), encoding="utf-8")
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}", "\n".join(blocks)))
    print(f"{len(blocks)} posts with text -> {out}\n{len(emails)} distinct emails in post bodies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
