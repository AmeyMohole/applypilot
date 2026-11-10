"""Unattended once-a-day run, started at login.

    1. capture   open LinkedIn in Chrome; the jobscan extension runs the paced searches and saves files
    2. inbox     mail_sync: replies (receipts and out-of-office don't count), bounces, redirects, confirmations
    3. follow-ups  draft the 1st nudge (3+ days) and the 2nd (4+ days after the 1st) for unanswered
                 applications. Only the 1st is sent unattended; the 2nd waits in Gmail for send.py --go.
                 The final status request is manual: python followup.py --final --drafts
    4. leads     turn the new captures into drafts (applications and internships)
    5. send      send what the gate cleared, capped per day; re-check for replies before each nudge
    6. report    last_run_report.txt (with the health report) + a Windows notification

Stop everything: create a file named AUTO_OFF in this folder (drafts are still made, nothing is sent).

    python run_daily.py              at most three times a day, 4+ hours apart
    python run_daily.py --force      run again today (and capture again, even if one finished recently)
    python run_daily.py --no-capture skip the LinkedIn step (process what is already captured)
    python run_daily.py --startup    (used by the scheduler) wait 60 s for the desktop and network first
    python run_daily.py --delay N    wait N seconds before starting
    python run_daily.py --no-count   do not count this run toward the 2-a-day limit
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
for sub in ("core", "1-find", "2-apply", "3-track"):
    sys.path.insert(0, str(HERE / sub))

import config  # noqa: E402
import cleanup  # noqa: E402
import receiver  # noqa: E402
import store as st  # noqa: E402

MAX_RUNS_PER_DAY = 3
MIN_GAP_HOURS = 4
STAMP = config.DATA / "last_run.txt"
LOCK = config.DATA / "run.lock"
LOG = config.DATA / "run_daily.log"
REPORT = config.DATA / "last_run_report.txt"
CAPTURE_DIR = config.CAPTURES                       # the receiver writes here (overwriting each run)
FALLBACK_DIR = Path.home() / "Downloads" / "jobscan"   # where browser downloads land if the receiver is down
START_URL = ("https://www.linkedin.com/search/results/content/?keywords=cloud%20engineer%20aws%20hiring%20india"
             "&datePosted=%22past-24h%22&sortBy=%22date_posted%22&origin=FACETED_SEARCH#jobscan-auto")
CHROME_PATHS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]

out_lines: list[str] = []
failed_steps: list[str] = []


def say(msg: str = "") -> None:
    print(msg, flush=True)
    out_lines.append(msg)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def step(title: str, *args: str) -> str:
    say(f"\n=== {title} ===")
    env = {**os.environ, "JOBSCAN_NONINTERACTIVE": "1"}  # never open a browser sign-in unattended
    proc = subprocess.run([sys.executable, *args], cwd=config.ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    out = (proc.stdout + proc.stderr).strip()
    say(out)
    if proc.returncode:
        say(f"(step failed, exit {proc.returncode})")
        failed_steps.append(title)
    return out


def wait_for_network(max_wait: int = 300) -> bool:
    end = time.time() + max_wait
    while time.time() < end:
        try:
            socket.create_connection(("www.linkedin.com", 443), 5).close()
            return True
        except OSError:
            time.sleep(10)
    return False


extra_dirs: list[Path] = []        # FALLBACK_DIR is added only when the receiver could not start


def capture_dirs() -> list[Path]:
    return [d for d in [CAPTURE_DIR, *extra_dirs] if d.exists()]


def captures_since(t0: float, suffixes) -> list[Path]:
    return [f for d in capture_dirs() for f in d.iterdir()
            if f.suffix in suffixes and "-done" not in f.name and f.stat().st_mtime >= t0]


def done_markers_since(t0: float) -> list[Path]:
    """The extension's end-of-run marker (run-done.txt from the receiver; <stamp>-done.* from a download)."""
    return [f for d in capture_dirs() for f in d.glob("*-done.*") if f.stat().st_mtime >= t0]


def load_runs() -> list[datetime]:
    try:
        return [datetime.fromisoformat(x) for x in json.loads(STAMP.read_text())["runs"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def may_run() -> str:
    """'' if a run is allowed now, else the reason: at most 3 per day, at least 4 hours apart."""
    now = datetime.now()
    today = [r for r in load_runs() if r.date() == now.date()]
    if len(today) >= MAX_RUNS_PER_DAY:
        return f"Already ran {len(today)} times today."
    if today and (now - max(today)).total_seconds() < MIN_GAP_HOURS * 3600:
        return f"The last run was less than {MIN_GAP_HOURS} hours ago."
    return ""


def record_run() -> None:
    runs = load_runs()[-20:] + [datetime.now()]
    STAMP.write_text(json.dumps({"runs": [r.isoformat(timespec="seconds") for r in runs]}))


def captured_recently(hours: float = 3) -> bool:
    cutoff = time.time() - hours * 3600
    return any(f.stat().st_mtime >= cutoff for d in capture_dirs() for f in d.glob("*-done.*"))


def bring_chrome_to_front() -> None:
    """Best effort: put the LinkedIn window in front so the run can be watched."""
    ps = ("$s = New-Object -ComObject WScript.Shell; Start-Sleep 3; "
          "foreach ($t in 'LinkedIn','Google Chrome') { if ($s.AppActivate($t)) { break } }")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=0x08000000)
    except OSError:
        pass


def capture(force: bool = False) -> None:
    if not force and captured_recently():
        say("A capture finished in the last 3 hours; skipping LinkedIn (--force captures again).")
        return
    chrome = next((p for p in CHROME_PATHS if os.path.exists(p)), None)
    if not chrome:
        say("Chrome not found; skipping capture.")
        return
    if not wait_for_network():
        say("No internet after 5 minutes; skipping capture.")
        return
    server = receiver.CaptureServer(CAPTURE_DIR)
    if server.start():
        say(f"Capture receiver listening on 127.0.0.1:{server.port}: files are saved in the background "
            f"(data/captures/, overwritten each run), no Save dialog.")
    else:
        extra_dirs.append(FALLBACK_DIR)
        say(f"WARNING: port {server.port} is busy, so the extension will fall back to browser downloads "
            f"(Chrome may show a Save dialog). Watching {FALLBACK_DIR} instead.")
    try:
        _capture_with_chrome(chrome)
    finally:
        server.stop()
        extra_dirs.clear()


def _capture_with_chrome(chrome: str) -> None:
    t0 = time.time()
    say("Opening LinkedIn in Chrome; the extension runs the searches (watch the panel at the bottom right of "
        "the page: it shows 'Query n/20'). 20 queries take about 30 minutes...")
    # A new, maximised window in front, so the progress can be watched.
    subprocess.Popen([chrome, "--new-window", "--start-maximized", START_URL])
    bring_chrome_to_front()
    last_report, reported = 0.0, -1
    while True:
        time.sleep(10)
        files = captures_since(t0, {".txt"})
        done = done_markers_since(t0)
        newest = max((f.stat().st_mtime for f in files), default=t0)
        if len(files) != reported or time.time() - last_report > 60:
            latest = max(files, key=lambda f: f.stat().st_mtime).name if files else "-"
            say(f"  capture progress: {len(files)} of 20 queries saved, {int(time.time() - t0)}s elapsed"
                + (f", latest: {latest}" if files else ", waiting for the extension to start"))
            last_report, reported = time.time(), len(files)
        if done and files:
            time.sleep(15)
            say(f"Capture finished: {len(files)} file(s).")
            return
        if not files and time.time() - t0 > 420:
            say("The extension never started (not logged in to LinkedIn, extension off, or a login check). "
                "Continuing without a new capture.")
            return
        if files and time.time() - newest > 420:
            say(f"Capture stalled after {len(files)} file(s). Continuing with what was saved.")
            return
        if time.time() - t0 > 4500:
            say("Capture timed out after 75 minutes. Continuing.")
            return


def count_today(store: dict, field: str) -> int:
    n = 0
    for r in store.values():
        ts = r.get(field)
        if ts and datetime.fromisoformat(ts).astimezone().date() == datetime.now().astimezone().date():
            n += 1
    return n


def notify(text: str) -> None:
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
          "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
          f"$n.Visible=$true;$n.ShowBalloonTip(15000,'jobscan','{text}',[System.Windows.Forms.ToolTipIcon]::Info);"
          "Start-Sleep 16;$n.Dispose()")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=0x08000000)
    except OSError:
        pass


def main() -> None:
    config.setup_console()
    force = "--force" in sys.argv
    if not force:
        reason = may_run()
        if reason:
            print(f"{reason} Use --force to run anyway.")
            with LOG.open("a", encoding="utf-8") as f:   # so a skipped trigger is visible later
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} trigger skipped: {reason}\n")
            return
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
        print("Another run is in progress.")
        return
    LOCK.write_text(str(os.getpid()))
    try:
        if "--no-count" not in sys.argv:  # a catch-up pass must not use up the day's two runs
            record_run()  # counted when it starts, so a crash can never cause a retry loop
        delay = 60 if "--startup" in sys.argv else 0   # let the desktop, Wi-Fi and Chrome settle
        if "--delay" in sys.argv:
            delay = int(sys.argv[sys.argv.index("--delay") + 1])
        time.sleep(delay)
        say(f"jobscan daily run {datetime.now():%Y-%m-%d %H:%M}")
        if not config.RESUME.exists():
            say(f"Resume missing: {config.RESUME}")
            return
        if config.AUTO_OFF_FILE.exists():
            say("AUTO_OFF is present: drafts will be made but nothing will be sent.")
        try:
            old_files = cleanup.purge(dirs=[CAPTURE_DIR, FALLBACK_DIR])
            if old_files:
                say(f"Deleted {len(old_files)} capture file(s) older than {cleanup.RETENTION_DAYS} days.")
        except Exception as exc:   # noqa: BLE001 - housekeeping must never stop a run
            say(f"(capture cleanup failed: {exc})")
        if "--no-capture" not in sys.argv:
            capture(force)
        step("Inbox -> tracker", "3-track/mail_sync.py", "--days", "3")
        step("Replies and follow-up drafts", "3-track/followup.py", "--drafts")
        step("Second follow-up drafts", "3-track/followup.py", "--second", "--drafts")
        step("New leads -> drafts", "1-find/scan.py", "--drafts")
        step("New internships -> drafts", "1-find/scan_internships.py", "--drafts")
        step("Send (gated, capped)", "2-apply/send.py", "--auto")

        store = st.load()
        st.save(store)  # rewrite applications.csv / summary.csv so 'days since' and totals are fresh
        summary = (f"Sent {count_today(store, 'sent_at')} application(s) and "
                   f"{count_today(store, 'followup_sent_at')} follow-up(s) today; "
                   f"{count_today(store, 'replied_detected_at')} new reply(ies). "
                   f"{sum(1 for r in store.values() if r.get('status') == 'drafted' and r.get('needs_review'))} "
                   f"draft(s) wait for your review in Gmail. Sheet: {config.CSV.name}")
        if failed_steps:
            summary += f" ATTENTION: {len(failed_steps)} step(s) failed ({', '.join(failed_steps)}); see run_daily.log."
        say("\n" + summary)
        try:
            import health
            say("\n=== Health ===\n" + health.report())
        except Exception as exc:   # noqa: BLE001 - a report problem must never fail the run
            say(f"(health report failed: {exc})")
        REPORT.write_text(f"{datetime.now():%Y-%m-%d %H:%M}\n{summary}\n\n" + "\n".join(out_lines), encoding="utf-8")
        notify(summary.replace("'", ""))
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
