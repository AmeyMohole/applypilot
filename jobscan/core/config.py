"""Paths and the applicant profile. Everything personal lives here."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # the jobscan folder (core/ lives inside it)
PROJECT = ROOT.parent

LEADS = ROOT / "data" / "leads.json"
XLSX = PROJECT / "applications.xlsx"          # optional Excel view: python report.py --xlsx
CSV = PROJECT / "applications.csv"            # the tracking sheet, rewritten after every change
SUMMARY = PROJECT / "summary.csv"             # totals: sent, replied, reply rate, ...
CREDENTIALS = ROOT / "secrets" / "credentials.json"   # OAuth client secret: never commit
TOKEN = ROOT / "secrets" / "token.json"          # cached OAuth token: never commit
CAPTURES = ROOT / "data" / "captures"
RESUME = PROJECT / "Amey_Mohole_DevOps_Resume_Updated.pdf"

# Small state files the pipeline keeps besides leads.json: do_not_contact.json, mx_cache.json,
# lead_queue.json, mail_sync_state.json, mail_actions.json, send.lock. Created on first write.
DATA = ROOT / "data"


def data_path(name: str) -> Path:
    """Path of a state file under DATA (read at call time, so tests can point DATA elsewhere)."""
    DATA.mkdir(parents=True, exist_ok=True)
    return DATA / name

# Every fact below comes from the resume. compose.py must never claim anything that is not here.
PROFILE = {
    "name": "Amey Mohole",
    "tagline": "DevOps & DevSecOps Engineer",
    "phone": "+91-7083049287",
    "linkedin": "linkedin.com/in/ameymohole",
    "github": "github.com/AmeyMohole",
    "email": "ameymohole@gmail.com",
    "address": "Nashik, India",
    "years_professional": 2,
    "last_employer": "Quasar CyberTech Pvt Ltd",
    "last_title": "Security Product Engineer (DevOps & Platform)",
    # Mentioned in every email, exactly as listed on the resume.
    "cert_line": "I hold the AWS Certified Cloud Practitioner certification (Feb 2025), along "
                 "with two Google certificates: Foundations of Project Management and Project "
                 "Initiation: Starting a Successful Project (both Oct 2025).",
}

# The Gmail account the tool signs in to and sends from; gmail_draft refuses any other account.
GMAIL_ACCOUNT = PROFILE["email"]

# Treated as "in range" when shortlisting posts, but NOT on the resume, so emails never claim them.
# Promote a term to the resume (and to compose.py) only once you have genuinely used it.
MATCH_ONLY_SKILLS = ["azure", "gcp", "helm", "argocd", "ansible"]

# Unattended sending. Create a file named AUTO_OFF in this folder to stop all auto-sending at once
# (drafts are still made and nothing is lost).
AUTO_OFF_FILE = ROOT / "AUTO_OFF"
MAX_AUTO_SENDS_PER_DAY = None   # None = no daily cap (user's choice, 2026-11-06); a number caps first applications + follow-ups together

# Calendar days to wait before each follow-up round (counted from the previous mail on the thread).
# The final round (a plain "is the role still open?") is never part of the scheduled run.
FOLLOWUP_AFTER_DAYS = {1: 3, 2: 4, 3: 4}

DEFAULT_MAX_YEARS = 4.0
DEFAULT_MIN_STIPEND = 15000  # INR per month, for scan_internships.py


def setup_console() -> None:
    """Every command calls this first. Windows consoles default to cp1252, which chokes on rupee
    signs and emoji in posts; it also starts logging (data/logs/jobscan.log)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        import log
        log.setup()
    except Exception:    # logging must never stop a command
        pass
    try:
        import cleanup
        cleanup.auto()     # captures older than a week are deleted automatically (at most every 12 h)
    except Exception:    # housekeeping must never stop a command
        pass
