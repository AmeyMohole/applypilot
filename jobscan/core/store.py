"""leads.json is the single source of truth; applications.xlsx is a generated view of it."""
from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import fileutil

HUMAN_COLS = ("Notes", "Outcome", "Next step")
FOLLOWUP_DAYS = 3
SECOND_FOLLOWUP_DAYS = 4

# A record in one of these is finished: nothing more is drafted or sent to it. "held" is not here:
# it is a draft you chose to keep back, sendable later.
CLOSED_STATUSES = {"rejected", "withdrawn", "bounced", "hold", "skipped", "duplicate", "discarded"}

IST = timezone(timedelta(hours=5, minutes=30))


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_closed(record: dict) -> bool:
    """True when no application or follow-up may go to this record any more."""
    return (record.get("status") in CLOSED_STATUSES or bool(record.get("do_not_mail"))
            or bool(record.get("bounced")))


def days_since(ts: str | None) -> int | None:
    """Whole calendar days (India time) between that timestamp and today.

    Follow-ups are due by the day, not the hour: sent at 4 pm on the 6th, the 3-day nudge is due all
    of the 9th, so the morning run drafts it. Fractional days reported "nothing due" for mail that
    was due later the same day."""
    if not ts:
        return None
    sent = datetime.fromisoformat(ts)
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    return (datetime.now(IST).date() - sent.astimezone(IST).date()).days


class StoreCorrupt(SystemExit):
    """leads.json cannot be read. Loading {} instead would let the next save overwrite every record."""


BACKUPS_KEPT = 14


def backups_dir(path: Path) -> Path:
    return Path(path).parent / "backups"


def latest_backup(path: Path | None = None) -> Path | None:
    files = sorted(backups_dir(Path(path or config.LEADS)).glob("leads-*.json"))
    return files[-1] if files else None


def load(path: Path | None = None) -> dict:
    path = Path(path or config.LEADS)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        newest = latest_backup(path)
        hint = f"The newest backup is {newest}: copy it over {path.name}." if newest else "There is no backup."
        raise StoreCorrupt(f"{path} is not valid JSON ({exc}). Nothing was changed. {hint}") from exc


def backup(path: Path) -> None:
    """Keep one copy of the tracker per day (taken before the day's first save) and the last 14."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return
    folder = backups_dir(path)
    target = folder / f"leads-{datetime.now():%Y-%m-%d}.json"
    if target.exists():
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        for old in sorted(folder.glob("leads-*.json"))[:-BACKUPS_KEPT]:
            old.unlink()
    except OSError:
        pass          # a backup problem must never stop the save


def update_record(key: str, fields: dict) -> None:
    """Patch one record on top of the file as it is now, not a stale snapshot. None removes a field.

    A process that loads the store, works for a while and saves the whole snapshot wipes whatever
    another process (send, follow-up drafting, mail sync) wrote meanwhile."""
    current = load()
    rec = current.setdefault(key, {})
    for field, value in fields.items():
        if value is None:
            rec.pop(field, None)
        else:
            rec[field] = value
    save(current)


def save_changes(original: dict, modified: dict) -> int:
    """Write only the fields that differ between a snapshot and its edited copy, on top of the file
    as it is now. Returns the number of records patched; writes nothing when it is 0."""
    patches, removed = {}, {}
    for key, record in modified.items():
        before = original.get(key)
        if before == record:
            continue
        if before is None:
            patches[key] = dict(record)
            continue
        changed = {f: v for f, v in record.items() if before.get(f) != v}
        gone = [f for f in before if f not in record]
        if changed:
            patches[key] = changed
        if gone:
            removed[key] = gone
    if not patches and not removed:
        return 0
    current = load()
    for key, fields in patches.items():
        current.setdefault(key, {}).update(fields)
    for key, fields in removed.items():
        for field in fields:
            current.get(key, {}).pop(field, None)
    save(current)
    return len(set(patches) | set(removed))


def save(store: dict, path: Path | None = None, xlsx: bool = True) -> None:
    """Write leads.json (the source of truth), then the CSV sheet and totals derived from it.
    `xlsx` is kept as the name of the flag for compatibility; it controls the sheets."""
    path = Path(path or config.LEADS)
    backup(path)
    fileutil.write_json_atomic(path, store, indent=2)
    if xlsx:
        for writer, name in ((write_csv, "applications.csv"), (write_summary, "summary.csv")):
            try:
                writer(store)
            except PermissionError:
                print(f"! {name} is open in Excel; close it (leads.json was saved, it will catch up).")


def make_record(lead, status: str, draft_id: str = "") -> dict:
    return {
        "role": lead.role, "company": lead.company, "company_confident": lead.company_confident,
        "poster": lead.poster, "email": lead.email, "min_years": lead.min_years,
        "locations": lead.locations, "track": lead.track, "intern": lead.is_intern,
        "stipend": lead.stipend, "status": status, "draft_id": draft_id, "drafted_at": now(),
    }


def known_emails(store: dict) -> set[str]:
    return {r["email"].lower() for r in store.values() if r.get("email")}


# --------------------------------------------------------------------------- spreadsheet

def _days_since(ts: str | None, ref: datetime | None = None) -> float | None:
    if not ts:
        return None
    ref = ref or datetime.now().astimezone()
    return (ref - datetime.fromisoformat(ts)).total_seconds() / 86400


def followup_due(rec: dict, days: float = FOLLOWUP_DAYS, second_days: float = SECOND_FOLLOWUP_DAYS) -> str:
    """Where this application is in the three follow-up rounds, for the sheet."""
    if rec.get("replied"):
        return "replied"
    if is_closed(rec):
        return "closed"
    if rec.get("status") != "sent":
        return ""
    if rec.get("followup3_sent_at") or rec.get("followup3_drafted_at"):
        return "done (3 sent)"
    if rec.get("followup2_sent_at"):
        return "final request available"
    if rec.get("followup2_drafted_at"):
        return "2nd drafted"
    if rec.get("followup_sent_at"):
        gap = _days_since(rec["followup_sent_at"])
        return "2nd DUE" if gap >= second_days else f"2nd in {second_days - gap:.1f}d"
    if rec.get("followup_drafted_at"):
        return "1st drafted"
    age = _days_since(rec.get("sent_at"))
    if age is None:
        return ""
    return "DUE NOW" if age >= days else f"in {days - age:.1f}d"


def write_xlsx(store: dict, path: Path | None = None) -> None:
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return
    path = Path(path or config.XLSX)

    kept: dict[str, tuple] = {}  # the three human columns survive every rewrite
    if path.exists():
        ws = load_workbook(path).active
        head = [c.value for c in ws[1]]
        if "Key" in head:
            ki = head.index("Key")
            idx = [head.index(c) for c in HUMAN_COLS if c in head]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[ki]:
                    kept[row[ki]] = tuple(row[i] for i in idx) if len(idx) == 3 else ("",) * 3

    cols = ["Status", "Channel", "Company", "Role", "Track", "Poster", "Email", "Min yrs",
            "Drafted", "Sent", "Days since", "Follow-up due", "Follow-up sent", "Replied",
            *HUMAN_COLS, "Key"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    ws.append(cols)
    fills = {"sent": "C6EFCE", "drafted": "FFEB9C", "held": "D9D9D9", "applied": "BDD7EE",
             "discarded": "F4B084"}
    ordered = sorted(store.items(), key=lambda kv: kv[1].get("drafted_at", ""))
    for key, r in ordered:
        age = _days_since(r.get("sent_at"))
        h = kept.get(key, ("", "", ""))
        ws.append([
            r.get("status", ""), r.get("channel", "email"), r.get("company", ""), r.get("role", ""),
            r.get("track", ""), r.get("poster", ""), r.get("email", ""), r.get("min_years"),
            r.get("drafted_at", ""), r.get("sent_at", ""),
            round(age, 1) if age is not None else "", followup_due(r),
            r.get("followup_sent_at", ""), "yes" if r.get("replied") else "", *h, key])
        color = fills.get(r.get("status", ""))
        if color:
            ws.cell(row=ws.max_row, column=1).fill = PatternFill("solid", fgColor=color)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(vertical="center")
    for i, name in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = {
            "Role": 38, "Company": 24, "Email": 30, "Notes": 30, "Outcome": 20, "Next step": 24,
            "Drafted": 22, "Sent": 22, "Follow-up sent": 22}.get(name, 14)
    ws.column_dimensions[get_column_letter(len(cols))].hidden = True
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    fileutil.replace_with_retry(tmp, path)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --------------------------------------------------------------------------- CSV sheet

CSV_COLS = ["Status", "Channel", "Company", "Role", "Track", "Poster", "Email", "Min yrs", "Locations",
            "Drafted", "Sent", "Days since sent", "Follow-up due", "Follow-up sent", "2nd follow-up sent",
            "Replied", "Reply received", "Reply outcome", "Reply comment", "Bounced", "Do not mail",
            "Needs review", "Apply link", *HUMAN_COLS, "Key"]


def _csv_row(key: str, r: dict, kept: dict) -> list:
    age = _days_since(r.get("sent_at"))
    human = kept.get(key, {})
    return [
        r.get("status", ""), r.get("channel", "email"), r.get("company", ""), r.get("role", ""),
        r.get("track", ""), r.get("poster", ""), r.get("email", ""),
        "" if r.get("min_years") is None else r["min_years"], "/".join(r.get("locations") or []),
        r.get("drafted_at", "")[:16].replace("T", " "), r.get("sent_at", "")[:16].replace("T", " "),
        round(age, 1) if age is not None else "", followup_due(r),
        r.get("followup_sent_at", "")[:16].replace("T", " "),
        r.get("followup2_sent_at", "")[:16].replace("T", " "), "yes" if r.get("replied") else "",
        r.get("reply_at", ""), r.get("last_reply_outcome", ""), r.get("reply_snippet", ""),
        "yes" if r.get("bounced") else "", "yes" if r.get("do_not_mail") else "",
        "yes" if r.get("status") == "drafted" and r.get("needs_review") else "",
        r.get("apply_url", ""), *[human.get(c, "") for c in HUMAN_COLS], key]


def write_csv(store: dict, path: Path | None = None) -> None:
    """One row per application. Notes / Outcome / Next step are yours: they are read back from the
    existing file and restored on every rewrite, keyed by the record key."""
    path = Path(path or config.CSV)
    kept: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("Key"):
                    kept[row["Key"]] = {c: row.get(c, "") for c in HUMAN_COLS}
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig so Excel reads it right
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for key, r in sorted(store.items(), key=lambda kv: kv[1].get("drafted_at", "")):
            w.writerow(_csv_row(key, r, kept))
    fileutil.replace_with_retry(tmp, path)


def summary_rows(store: dict) -> list[tuple[str, object]]:
    email = [r for r in store.values() if r.get("channel", "email") == "email"]
    sent = [r for r in email if r.get("status") == "sent"]
    replied = [r for r in sent if r.get("replied")]
    today = datetime.now().astimezone().date()

    def on_today(ts):
        return bool(ts) and datetime.fromisoformat(ts).astimezone().date() == today

    rate = f"{100 * len(replied) / len(sent):.0f}%" if sent else "0%"
    return [
        ("Applications sent (email)", len(sent)),
        ("Sent today", sum(on_today(r.get("sent_at")) for r in sent)),
        ("Replied", len(replied)),
        ("Reply rate", rate),
        ("Waiting for a reply", sum(1 for r in sent if not r.get("replied") and not r.get("bounced"))),
        ("Follow-ups sent", sum(1 for r in sent if r.get("followup_sent_at"))),
        ("Follow-ups sent today", sum(on_today(r.get("followup_sent_at")) for r in sent)),
        ("2nd follow-ups sent", sum(1 for r in sent if r.get("followup2_sent_at"))),
        ("Interviews / assessments", sum(1 for r in sent if r.get("last_reply_outcome")
                                         in ("interview", "assessment"))),
        ("Bounced (undeliverable)", sum(1 for r in email if r.get("bounced"))),
        ("Drafts waiting for your review", sum(1 for r in email if r.get("status") == "drafted"
                                              and r.get("needs_review"))),
        ("Roles to apply by hand (link only)", sum(1 for r in store.values() if r.get("status") == "by_hand")),
        ("Applied via web form", sum(1 for r in store.values() if r.get("status") == "applied")),
        ("Last updated", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")),
    ]


def write_summary(store: dict, path: Path | None = None) -> None:
    path = Path(path or config.SUMMARY)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Metric", "Value"])
        w.writerows(summary_rows(store))
    fileutil.replace_with_retry(tmp, path)
