"""Check that the installation is healthy. Changes nothing; exit code 1 if anything is broken.

    python doctor.py            offline checks: dependencies, resume, credentials, tracker, backups,
                                extension queries, kill switch, scheduled task, last run
    python doctor.py --online   also sign in to Gmail and confirm it is the right account

Run it after changing anything, and when a scheduled run looks quiet.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for sub in ("core", "1-find", "2-apply", "3-track"):
    sys.path.insert(0, str(HERE / sub))

import argparse  # noqa: E402
import importlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime  # noqa: E402

import config  # noqa: E402
import queries  # noqa: E402
import store as st  # noqa: E402

OK, WARN, FAIL = "ok", "warn", "FAIL"
NEEDED_SCOPES = {"https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.readonly"}


def check_python():
    ok = sys.version_info >= (3, 10)
    return (OK if ok else FAIL), f"Python {sys.version.split()[0]}" + ("" if ok else " (3.10 or newer is required)")


def check_imports():
    missing = []
    for module, package in (("googleapiclient", "google-api-python-client"), ("google_auth_oauthlib", "google-auth-oauthlib"),
                            ("google_auth_httplib2", "google-auth-httplib2"), ("openpyxl", "openpyxl")):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        return FAIL, "missing: " + ", ".join(missing) + "  (pip install -r requirements.txt)"
    optional = [name for name in ("dns.resolver", "jobspy") if _missing(name)]
    return OK, "required packages present" + (f"; optional not installed: {', '.join(optional)}" if optional else "")


def _missing(name: str) -> bool:
    try:
        importlib.import_module(name)
        return False
    except ImportError:
        return True


def check_resume():
    return (OK, str(config.RESUME.name)) if config.RESUME.exists() else (FAIL, f"resume missing: {config.RESUME}")


def check_credentials():
    if not config.CREDENTIALS.exists() and not config.TOKEN.exists():
        return FAIL, f"no {config.CREDENTIALS} (SETUP.md step 2)"
    if not config.TOKEN.exists():
        return WARN, "no token yet: run python 2-apply/send.py once by hand to sign in"
    try:
        scopes = set(json.loads(config.TOKEN.read_text(encoding="utf-8")).get("scopes") or [])
    except ValueError:
        return FAIL, "secrets/token.json is not valid JSON: delete it and sign in again"
    if not scopes >= NEEDED_SCOPES:
        return FAIL, f"token lacks scope(s): {', '.join(sorted(NEEDED_SCOPES - scopes))} - delete the token and sign in again"
    return OK, "credentials and token present, scopes correct"


def check_tracker():
    try:
        store = st.load()
    except SystemExit as exc:
        return FAIL, str(exc)
    sent = sum(1 for r in store.values() if r.get("status") == "sent")
    newest = st.latest_backup()
    note = f"; newest backup {newest.name}" if newest else "; no backup yet (the first save of a day makes one)"
    return OK, f"{len(store)} records ({sent} sent){note}"


def check_data_dir():
    try:
        probe = config.data_path(".write-test")
        probe.write_text("x")
        probe.unlink()
        return OK, str(config.DATA)
    except OSError as exc:
        return FAIL, f"{config.DATA} is not writable: {exc}"


def check_extension():
    js = HERE / "extension" / "content.js"
    try:
        block = re.search(r"DEFAULT_QUERIES = \[(.*?)\];", js.read_text(encoding="utf-8"), re.S).group(1)
    except (OSError, AttributeError):
        return FAIL, "cannot read the query list from extension/content.js"
    text = js.read_text(encoding="utf-8")
    pinned = re.search(r"const PINNED = (\d+);", text)
    per_run = re.search(r"const MAX_QUERIES = (\d+);", text)
    if not (pinned and per_run and (int(pinned.group(1)), int(per_run.group(1)))
            == (queries.LINKEDIN_PINNED, queries.LINKEDIN_PER_RUN)):
        return WARN, "extension/content.js PINNED / MAX_QUERIES differ from core/queries.py"
    if re.findall(r'"([^"]+)"', block) != queries.LINKEDIN_QUERIES:
        return WARN, "extension/content.js queries differ from core/queries.py: edit one to match, then reload the extension"
    return OK, f"{len(queries.LINKEDIN_QUERIES)} LinkedIn queries ({queries.LINKEDIN_PER_RUN} per run), extension in sync"


def check_receiver():
    """The extension saves captures through a local receiver; its port must be free for a run to start it."""
    import receiver
    probe = receiver.CaptureServer(config.CAPTURES, port=0)
    if not probe.start():
        return FAIL, "cannot open a local port"
    probe.stop()
    busy = receiver.CaptureServer(config.CAPTURES)
    if busy.start():
        busy.stop()
        return OK, f"port {receiver.PORT} is free; captures are saved in the background, no Save dialog"
    return WARN, f"port {receiver.PORT} is in use (a run in progress, or another program): the extension would fall back to downloads"


def check_kill_switch():
    return (WARN, "AUTO_OFF is present: nothing is being sent") if config.AUTO_OFF_FILE.exists() else (OK, "auto-send enabled")


def check_last_run():
    stamp = config.DATA / "last_run.txt"
    try:
        runs = [datetime.fromisoformat(x) for x in json.loads(stamp.read_text(encoding="utf-8"))["runs"]]
    except (OSError, ValueError, KeyError, TypeError):
        return WARN, "no run recorded yet"
    hours = (datetime.now() - max(runs)).total_seconds() / 3600
    return (OK if hours < 36 else WARN), f"last run {max(runs):%Y-%m-%d %H:%M} ({hours:.0f}h ago)"


def check_scheduler():
    if os.name != "nt":
        return OK, "not Windows: scheduling is up to cron / launchd"
    try:
        out = subprocess.run(["schtasks", "/Query", "/TN", "jobscan"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return WARN, "could not query Task Scheduler"
    if out.returncode:
        return WARN, "no scheduled task named 'jobscan' (tools/install_scheduled_task.ps1 creates it)"
    return OK, "scheduled task 'jobscan' is registered"


def check_gmail():
    os.environ["JOBSCAN_NONINTERACTIVE"] = "1"          # never open a browser from a health check
    try:
        import gmail_draft
        svc = gmail_draft.get_service()
        who = svc.users().getProfile(userId="me").execute()["emailAddress"]
    except SystemExit as exc:
        return FAIL, str(exc)
    except Exception as exc:    # noqa: BLE001 - report any failure as the finding
        return FAIL, f"{type(exc).__name__}: {exc}"
    return OK, f"signed in as {who}"


CHECKS = [("python", check_python), ("packages", check_imports), ("resume", check_resume),
          ("credentials", check_credentials), ("tracker", check_tracker), ("data dir", check_data_dir),
          ("queries", check_extension), ("receiver", check_receiver), ("kill switch", check_kill_switch), ("last run", check_last_run),
          ("scheduler", check_scheduler)]


def run_checks(online: bool = False) -> list[tuple[str, str, str]]:
    checks = CHECKS + ([("gmail", check_gmail)] if online else [])
    results = []
    for name, fn in checks:
        try:
            status, detail = fn()
        except Exception as exc:    # noqa: BLE001 - a crashing check is itself a finding
            status, detail = FAIL, f"check crashed: {type(exc).__name__}: {exc}"
        results.append((name, status, detail))
    return results


def main() -> int:
    config.setup_console()
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--online", action="store_true", help="also sign in to Gmail")
    args = cli.parse_args()
    results = run_checks(args.online)
    for name, status, detail in results:
        print(f"  [{status:>4}] {name:<12} {detail}")
    bad = [n for n, s, _ in results if s == FAIL]
    print("\n" + (f"{len(bad)} problem(s): {', '.join(bad)}" if bad else "All required checks passed."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
