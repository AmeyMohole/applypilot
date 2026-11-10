import sys
from pathlib import Path

JOBSCAN = Path(__file__).resolve().parent.parent / "jobscan"
for sub in ("", "core", "1-find", "2-apply", "3-track", "tools"):   # same layout the scripts add themselves
    sys.path.insert(0, str(JOBSCAN / sub))
sys.path.insert(0, str(Path(__file__).resolve().parent))


import pytest


@pytest.fixture(autouse=True)
def _isolated_files(tmp_path, monkeypatch):
    import config
    import contacts
    for name, fname in (("LEADS", "leads.json"), ("XLSX", "applications.xlsx"),
                        ("CSV", "applications.csv"), ("SUMMARY", "summary.csv")):
        monkeypatch.setattr(config, name, tmp_path / fname)
    # State files (do-not-contact, MX cache, lead queue, send lock...) never touch the real data dir.
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    import cleanup
    monkeypatch.setattr(cleanup, "default_dirs", lambda: [tmp_path / "captures"])   # never the real Downloads
    import log
    monkeypatch.setattr(log, "_configured", True)      # no log file handlers inside tests
    # No test makes a DNS lookup; tests of mx_status itself override this.
    monkeypatch.setattr(contacts, "_lookup_mx", lambda domain: "ok")
