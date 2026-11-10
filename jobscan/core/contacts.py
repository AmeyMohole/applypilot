"""Who may be written to: do-not-contact rules, mail-server checks, company dedupe.

blockers() runs before a lead is drafted (scan, draft_direct) and send.py repeats the mail-server
check before sending. Things it catches that are otherwise found by hand after the mail went out:

- a domain with no mail server at all, or one that does not resolve: the draft was written before
  anyone looked;
- staffing firms and scam templates you decided never to mail again, which lived only in free-text
  notes;
- several recruiters at one company written to separately, after which one reply marked all of them
  as answered.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess

import config
import fileutil
import store as st

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "outlook.com",
             "hotmail.com", "live.com", "icloud.com", "rediffmail.com", "ymail.com", "proton.me",
             "protonmail.com", "outlook.in", "aol.com", "zoho.com", "zohomail.in"}

MX_TTL_DAYS = 14

# Notes that mean "never write here again".
DNC_NOTE_RE = re.compile(r"(never (?:mail|contact|follow up)|do not (?:mail|contact)|don'?t (?:mail|contact)|"
                         r"no (?:more|further) (?:mails?|follow-?ups)|not (?:genuine|a real job)|scam)", re.I)


def _dnc_path():
    return config.data_path("do_not_contact.json")


def _mx_path():
    return config.data_path("mx_cache.json")


def domain_of(address: str) -> str:
    return (address or "").strip().lower().rsplit("@", 1)[-1] if "@" in (address or "") else ""


def is_free_mail(address_or_domain: str) -> bool:
    dom = domain_of(address_or_domain) or (address_or_domain or "").lower()
    return dom in FREE_MAIL


def inbox_kind(address: str, poster: str = "") -> str:
    """"person" for a named recruiter, "generic" for careers@/hr@ and the like."""
    import compose
    return "person" if compose.greeting_name(poster, address) else "generic"


# ---------------------------------------------------------------- do-not-contact

def load_dnc() -> list:
    return fileutil.read_json(_dnc_path(), [])


def add_dnc(value: str, reason: str) -> None:
    """Add an address or a whole domain ("@example.com" or the bare domain)."""
    entries = load_dnc()
    value = value.strip().lower()
    if any(e.get("value") == value for e in entries):
        return
    entries.append({"value": value, "reason": reason, "date": dt.date.today().isoformat()})
    fileutil.write_json_atomic(_dnc_path(), entries)


def dnc_reason(address: str, store: dict | None = None) -> str:
    """Why this address must not be mailed, or ''.

    Checks the explicit list (addresses and domains) and the tracker itself: a bounced or withdrawn
    record, do_not_mail, or a note saying never again."""
    address = (address or "").strip().lower()
    dom = domain_of(address)
    for entry in load_dnc():
        value = entry.get("value", "").lstrip("@")
        if value == address or (value == dom and "@" not in value):
            return f"do-not-contact list: {entry.get('reason', '')}".rstrip(": ")
    for record in (store or {}).values():
        if (record.get("email") or "").lower() != address:
            continue
        if record.get("bounced") or record.get("status") == "bounced" or record.get("bounce_note"):
            return f"bounced before ({record.get('bounce_note') or 'undeliverable'})"
        if record.get("do_not_mail"):
            return f"do_not_mail ({record.get('do_not_mail_reason') or 'set on the record'})"
        note = (record.get("note") or "") + " " + (record.get("reply_note") or "")
        if record.get("status") == "withdrawn" and DNC_NOTE_RE.search(note):
            return "withdrawn with a do-not-contact note"
    return ""


# ---------------------------------------------------------------- mail servers

def _parse_nslookup(output: str) -> str:
    """'ok', 'no-mx', 'nxdomain' or 'unknown' from `nslookup -type=mx` text."""
    if "Non-existent domain" in output or "NXDOMAIN" in output:
        return "nxdomain"
    exchangers = re.findall(r"mail exchanger = (\S+)", output)
    if exchangers:
        # A null MX ("mail exchanger = (root)" or ".") says the domain takes no mail.
        return "ok" if any(x not in ("(root)", ".") for x in exchangers) else "no-mx"
    if "primary name server" in output or "responsible mail addr" in output or "No answer" in output:
        return "no-mx"
    return "unknown"


def _lookup_mx(domain: str) -> str:
    try:
        import dns.resolver  # optional; nslookup is the fallback that is always there
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=8)
            hosts = [str(a.exchange).rstrip(".") for a in answers]
            return "ok" if any(hosts) else "no-mx"
        except dns.resolver.NXDOMAIN:
            return "nxdomain"
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return "no-mx"
        except Exception:
            return "unknown"
    except ImportError:
        pass
    # A home router's resolver sometimes times out on non-existent domains instead of answering
    # NXDOMAIN, so ask a public resolver when it gives up.
    for server in ([], ["8.8.8.8"]):
        try:
            out = subprocess.run(["nslookup", "-type=mx", domain, *server], capture_output=True,
                                 text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        verdict = _parse_nslookup(out)
        if verdict != "unknown":
            return verdict
    return "unknown"


def mx_status(address_or_domain: str, use_cache: bool = True) -> str:
    """Can this domain receive mail? 'ok', 'no-mx', 'nxdomain' or 'unknown'. Cached for two weeks."""
    dom = domain_of(address_or_domain) or (address_or_domain or "").strip().lower()
    if not dom:
        return "unknown"
    if dom in FREE_MAIL:
        return "ok"
    cache = {}
    if use_cache:
        cache = fileutil.read_json(_mx_path(), {})
        hit = cache.get(dom)
        if hit and hit.get("status") != "unknown":
            age = (dt.date.today() - dt.date.fromisoformat(hit["date"])).days
            if age <= MX_TTL_DAYS:
                return hit["status"]
    status = _lookup_mx(dom)
    if use_cache:
        cache[dom] = {"status": status, "date": dt.date.today().isoformat()}
        try:
            fileutil.write_json_atomic(_mx_path(), cache)
        except OSError:
            pass
    return status


# ---------------------------------------------------------------- company dedupe

def norm_company(name: str) -> str:
    """'Acme Pvt. Ltd.' and 'acme' compare equal."""
    name = re.sub(r"\b(in india|india|pvt|private|ltd|limited|llp|inc|technologies|technology|solutions|services|"
                  r"software|systems|labs|consulting|group|hiring team|careers?|recruiting|people services|"
                  r"talent acquisition|team)\b\.?", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", name)


def same_company(store: dict, address: str, company: str = "") -> list:
    """Earlier applications at the same company: (key, record) pairs.

    Same non-free-mail domain, or the same normalised company name (which catches a recruiter
    writing from gmail for a company already mailed)."""
    address = (address or "").lower()
    dom = domain_of(address)
    nc = norm_company(company)
    found = []
    for key, record in store.items():
        other = (record.get("email") or "").lower()
        if other == address:
            continue
        if dom and dom not in FREE_MAIL and domain_of(other) == dom:
            found.append((key, record))
        elif nc and len(nc) > 3 and norm_company(record.get("company") or "") == nc:
            found.append((key, record))
    return found


def describe(record: dict) -> str:
    when = (record.get("sent_at") or record.get("drafted_at") or "")[:10]
    state = record.get("status", "?") + (", replied" if record.get("replied") else "")
    return f"{record.get('email') or '(portal)'} - {record.get('role') or '?'} ({state}{', ' + when if when else ''})"


def open_conversation(record: dict) -> bool:
    """A person answered and nothing has closed the thread."""
    return bool(record.get("replied")) and not st.is_closed(record) and record.get("last_reply_outcome") != "rejected"


def blockers(store: dict, address: str, company: str = "", role: str = "", check_mx: bool = True) -> list:
    """Reasons not to draft to this address, as (level, message).

    "block": do not draft. "warn": printed for you to judge (a second role at a company already
    mailed can be fine)."""
    out = []
    address = (address or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", address):
        return [("block", f"not an email address: {address!r}")]
    reason = dnc_reason(address, store)
    if reason:
        out.append(("block", reason))
    for key, record in store.items():
        if (record.get("email") or "").lower() == address and not reason:
            out.append(("warn", f"already written to: {describe(record)}"))
    for key, record in same_company(store, address, company):
        text = f"same company: {describe(record)}"
        if open_conversation(record):
            text += " - a conversation is open there; write in that thread instead?"
        out.append(("warn", text))
    if check_mx:
        mx = mx_status(address)
        if mx == "nxdomain":
            out.append(("block", f"domain {domain_of(address)} does not exist"))
        elif mx == "no-mx":
            out.append(("block", f"domain {domain_of(address)} has no mail server (MX)"))
        elif mx == "unknown":
            out.append(("warn", f"could not check the mail server for {domain_of(address)}"))
    return out
