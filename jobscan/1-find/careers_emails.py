"""Find the application inbox a company publishes on its own website.

    python careers_emails.py acme.com globex.io initech.in
    python careers_emails.py --file domains.txt --out found.json
    python ingest.py found.json --queue            then rank / dedupe / queue as usual

Walks one site politely: the home page, then up to four careers / jobs / join-us / contact pages on
the same site, collecting mailto links, plain-text addresses and Cloudflare-obfuscated ones
(data-cfemail), plus job-like headings. One request at a time, 1.5 s apart, five pages per domain at
most. Sites that build the page in JavaScript show up as "no address found": use the browser.

Each address is ranked: careers@ / jobs@ / hr@ / talent@ and named recruiters on the company domain
first; info@ / contact@ / hello@ only as a last resort; sales@, support@, privacy@ and other domains
never.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import argparse
import html as htmllib
import json
import re
import time
import urllib.parse
import urllib.request

import config
import contacts
import queries

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
HREF_RE = re.compile(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
LINK_WORDS_RE = re.compile(r"career|jobs?\b|join|hiring|work-?with|openings|vacanc|contact|opportunit", re.I)
JOB_RE = re.compile(rf"\b({queries.CAREERS_JOB_WORDS})\b", re.I)
HEADING_RE = re.compile(r"<(h[1-4]|li|a)\b[^>]*>(.*?)</\1>", re.I | re.S)

APPLY_LOCALS = ("career", "careers", "jobs", "job", "hr", "hiring", "talent", "recruit", "recruitment",
                "recruiter", "people", "resume", "resumes", "cv", "hrd", "join", "apply", "ta",
                "talentacquisition", "humanresources")
FALLBACK_LOCALS = ("info", "contact", "hello", "admin", "office", "enquiry", "enquiries", "mail")
NEVER_LOCALS = ("sales", "support", "help", "billing", "accounts", "press", "media", "pr", "marketing",
                "partners", "partner", "privacy", "legal", "abuse", "security", "compliance", "investor",
                "investors", "ir", "webmaster", "dpo", "grievance", "noreply", "no-reply", "feedback")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


def decode_cfemail(hexstring: str) -> str:
    """Cloudflare's email protection: the first byte is the XOR key for the rest."""
    data = bytes.fromhex(hexstring)
    return "".join(chr(b ^ data[0]) for b in data[1:])


def site_of(domain_or_host: str) -> str:
    """'www.careers.acme.co.in' -> 'acme.co.in' (good enough for Indian and .com sites)."""
    host = domain_or_host.lower().split("@")[-1].split(":")[0].strip(".")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "ac", "gov") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def extract_emails(page: str) -> set:
    found = {raw.lower().rstrip(".") for raw in EMAIL_RE.findall(htmllib.unescape(page))}
    for hexstring in CFEMAIL_RE.findall(page):
        try:
            found.add(decode_cfemail(hexstring).lower())
        except ValueError:
            pass
    return {e for e in found if not re.search(r"\.(png|jpe?g|gif|svg|webp|css|js)$", e)
            and not e.endswith(("example.com", "domain.com", "email.com", "sentry.io", "wixpress.com"))}


def rank(address: str, domain: str) -> tuple:
    """(rank, label): 3 apply inbox, 2 named person, 1 generic contact, 0 never."""
    local, _, dom = address.partition("@")
    if site_of(dom) != site_of(domain):
        return 0, "other domain"
    tokens = [t for t in re.split(r"[._+-]+", local) if t]
    if any(t in NEVER_LOCALS for t in tokens):
        return 0, "not for applications"
    if any(t in APPLY_LOCALS or t.startswith(("career", "recruit", "talent", "hiring")) for t in tokens):
        return 3, "application inbox"
    if any(t in FALLBACK_LOCALS for t in tokens):
        return 1, "generic contact inbox (a last resort)"
    return 2, "named person"


def candidate_links(page: str, base_url: str, limit: int = 4) -> list:
    """Same-site careers / jobs / contact pages, careers first."""
    site = site_of(urllib.parse.urlparse(base_url).hostname or "")
    links = []
    for href, label in HREF_RE.findall(page):
        url = urllib.parse.urljoin(base_url, htmllib.unescape(href))
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or site_of(parsed.hostname or "") != site:
            continue
        if not LINK_WORDS_RE.search(f"{parsed.path} {re.sub(r'<[^>]+>', ' ', label)}"):
            continue
        url = url.split("#")[0]
        if url not in links and url.rstrip("/") != base_url.rstrip("/"):
            links.append(url)
    links.sort(key=lambda u: 0 if re.search(r"career|job|join|hiring|opening", u, re.I) else 1)
    return links[:limit]


def job_headings(page: str, limit: int = 12) -> list:
    out = []
    for _, inner in HEADING_RE.findall(page):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", htmllib.unescape(inner))).strip()
        if 4 < len(text) < 90 and JOB_RE.search(text) and text not in out:
            out.append(text)
    return out[:limit]


def fetch(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(2_000_000).decode(charset, "replace")


def scan_domain(domain: str, get=fetch, pause: float = 1.5) -> dict:
    """Walk one company site. `get` is injectable so the tests need no network."""
    result = {"domain": domain, "pages": [], "emails": {}, "jobs": [], "error": ""}
    pages = []
    for start in (f"https://{domain}", f"https://www.{domain}"):
        try:
            pages.append((start, get(start)))
            break
        except Exception as exc:    # noqa: BLE001 - any network failure means "try the next form"
            result["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
    if not pages:
        return result
    result["error"] = ""
    for url in candidate_links(pages[0][1], pages[0][0]):
        time.sleep(pause)
        try:
            pages.append((url, get(url)))
        except Exception:    # noqa: BLE001
            continue
    for url, page in pages:
        result["pages"].append(url)
        for address in extract_emails(page):
            level, label = rank(address, domain)
            if level and (address not in result["emails"] or result["emails"][address]["rank"] < level):
                result["emails"][address] = {"rank": level, "label": label, "page": url}
        result["jobs"] += [j for j in job_headings(page) if j not in result["jobs"]]
    return result


def to_lead(result: dict) -> dict | None:
    """ingest.py's input shape, or None when nothing usable was published."""
    ranked = sorted(result["emails"].items(), key=lambda kv: -kv[1]["rank"])
    if not ranked:
        return None
    best = ranked[0]
    company = result["domain"].split(".")[0]
    return {
        "emails": [address for address, info in ranked if info["rank"] == best[1]["rank"]],
        "company": company[:1].upper() + company[1:],
        "text": f"Careers page {best[1]['page']}\nOpenings listed: " + "; ".join(result["jobs"]),
        "url": best[1]["page"], "source": "careers-page", "inbox_note": best[1]["label"],
    }


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("domains", nargs="*")
    cli.add_argument("--file", help="one domain per line (# for comments)")
    cli.add_argument("--out", help="write ingest.py input (JSON) here")
    cli.add_argument("--no-mx", action="store_true", help="skip the mail-server check")
    args = cli.parse_args()
    domains = list(args.domains)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            domains += [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    domains = [re.sub(r"^https?://(www\.)?", "", d).strip("/").lower() for d in domains]
    if not domains:
        cli.error("give domains or --file")

    leads = []
    for domain in domains:
        result = scan_domain(domain)
        lead = to_lead(result)
        mx = "" if args.no_mx or not lead else contacts.mx_status(domain)
        if lead and mx in ("no-mx", "nxdomain"):
            print(f"== {domain}: published {', '.join(lead['emails'])} but the domain takes no mail ({mx})")
            continue
        if not lead:
            print(f"== {domain}: no address found" + (f" ({result['error']})" if result["error"] else "")
                  + f"  [{len(result['pages'])} pages]")
            continue
        leads.append(lead)
        others = [f"{a} ({i['label']})" for a, i in result["emails"].items() if a not in lead["emails"]]
        print(f"== {domain}: {', '.join(lead['emails'])}  [{lead['inbox_note']}]")
        if others:
            print(f"   also: {'; '.join(others[:5])}")
        if result["jobs"]:
            print(f"   openings: {'; '.join(result['jobs'][:8])}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(leads, fh, indent=1, ensure_ascii=False)
        print(f"\n{len(leads)} written to {args.out}; next: python ingest.py {args.out} --queue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
