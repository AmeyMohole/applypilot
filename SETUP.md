# Setup and daily use

Commands run from the `jobscan` folder unless noted. Python 3.10+.

## One-time

1. Put `Amey_Mohole_DevOps_Resume_Updated.pdf` next to `requirements.txt`.
2. `pip install -r requirements.txt` (JobSpy for `jobspy_search.py` is optional: see `requirements-optional.txt`).
3. Gmail API (must be the **ameymohole@gmail.com** account):
   - console.cloud.google.com -> new project -> enable **Gmail API**.
   - OAuth consent screen -> External -> add the address as a test user.
   - Credentials -> Create credentials -> OAuth client ID -> **Desktop app** -> download the JSON, save it as
     `jobscan/secrets/credentials.json`.
   - Run `python 2-apply/send.py` once: a browser opens to sign in. Any other account is refused.
     (While the Google app is in Testing mode the sign-in expires about every 7 days; the unattended run says so.)
4. `python doctor.py` should end with "All required checks passed." (`--online` also tests the Gmail sign-in.)
5. Chrome extension: `chrome://extensions` -> Developer mode -> Load unpacked -> `jobscan/extension`.
   Reload it there after any change to `extension/` (version 1.1 added the background receiver: reload once).
6. Scheduler (Windows): `powershell -ExecutionPolicy Bypass -File tools\install_scheduled_task.ps1`
   (`-Remove` uninstalls). The task runs at logon, 10:00, 14:00 and 18:00; `run_daily.py` allows at most 3 runs a day,
   4+ hours apart.

## What it searches for

Targets: **cloud (first priority)**, DevOps / SRE, and **full-stack software engineering** in India.
Everything is in one file, `core/queries.py`:

| Where | Queries |
|---|---|
| LinkedIn posts (extension) | **30 keywords** (cloud, aws, terraform, kubernetes, devsecops, platform, infrastructure, SRE, production support, full stack, python / backend developer, python devops, software developer, plus Pune / Mumbai / Nashik variants), **20 per run**: the first two (cloud engineer aws, devops engineer) run every time and 18 rotate, so all 30 are covered in 2 runs |
| Job boards (JobSpy, Workday) | cloud engineer, aws devops engineer, devops engineer, site reliability engineer, full stack developer, software engineer python react, python developer, software developer; in Nashik, Pune, Mumbai, India-remote |
| ATS APIs | every board in `core/boards.py` (Greenhouse, Ashby, Lever, SmartRecruiters, Workday), filtered by title |

Preference order: **cloud > devops / full-stack > sre / devsecops**, and **Nashik > Pune > Mumbai > Bengaluru >
Hyderabad > remote**. The daily send cap means this order decides who is mailed first.
Skipped on purpose: senior / lead / architect titles, more than 4 years asked, internships (own script),
frontend-only, mobile, QA, Java/.NET-stack "full stack", US-only roles.
If you change the LinkedIn list, change it in both `core/queries.py` and `extension/content.js`
(`python doctor.py` and the tests warn when they differ), then reload the extension. The 6-per-run limit is the
extension's account-safety limit and stays fixed: widen coverage by adding keywords to the pool, not by raising it.

## Every day (manual path)

1. LinkedIn capture: start `python tools/receiver.py` in a terminal, then the extension panel -> **Run all queries**
   (6 queries, paced). Captures are sent to the receiver in the background and saved in `data/captures/`,
   overwriting the previous run's: no Save dialog. (The scheduled run starts the receiver itself.)
2. `python 1-find/scan.py --show-skipped` previews (nothing saved); `python 1-find/scan.py --drafts` creates drafts.
3. `python 3-track/mail_sync.py` then `python 3-track/health.py`.
4. `python 3-track/followup.py --drafts` (1st nudge), `--second --drafts`, `--final --drafts` (by hand only).
5. `python 2-apply/send.py` (dry run) then `python 2-apply/send.py --go`.
6. `python 1-find/scan_internships.py --drafts`: internships that state a stipend of at least 15,000/month.

## More leads (no LinkedIn needed)

| Command | Finds |
|---|---|
| `python 1-find/ats_sweep.py [--track]` | cloud / devops / full-stack openings on ~120 company boards (apply link) |
| `python 1-find/workday_sweep.py [--track]` | the same on Workday career sites |
| `python 1-find/jobspy_search.py [--track]` | Indeed + LinkedIn job listings (needs JobSpy) |
| `python 1-find/careers_emails.py acme.com ...` | the careers inbox a company publishes, then `ingest.py` |
| `python 1-find/ingest.py found.json --queue` | scores and queues emailable leads, then `python 2-apply/draft_direct.py --queue` |
| `python 1-find/google_posts.py <dir>` | LinkedIn hiring posts via Google, without your LinkedIn login |

Sweeps print listings with an apply link and write a CSV to `data/found/`. `--track` adds the ones at new
companies to `applications.csv` as `by_hand`. Run `python 1-find/ats_sweep.py --probe` now and then and delete
the boards it reports from `core/boards.py`.

## Tailored drafts

`python 2-apply/draft_direct.py hr@acme.com Acme "Cloud Engineer" --track cloud --preview` prints the email.
`--spec specs.json` takes per-job paragraphs (`interest`, `blocks`, `greet`, `subject`, `fu_focus`, `fu1_paras`,
`fu2_paras`; see the docstring). Every hand-written sentence is checked against the resume: naming a skill it
doesn't list (Azure, GCP, Helm, ArgoCD, Ansible, TypeScript, ...) blocks the draft unless the sentence states the
gap. Direct drafts are always flagged for review, so the unattended run never sends them.

## Fully automatic mode

`run_daily.py` (via the scheduled task): opens LinkedIn in a maximised Chrome window so you can watch (the panel at
the bottom right shows `Query n/20`), starts the background receiver, so the extension does the searches
and saves every capture with no Save dialog and no clicking; then `mail_sync` (replies, bounces, redirects,
confirmations), drafts the 1st and 2nd follow-ups, drafts leads,
sends what the gate cleared, and writes `data/last_run_report.txt` (with the health report) plus a Windows
notification that says **ATTENTION** if a step failed. Log: `data/run_daily.log`; detailed log:
`data/logs/jobscan.log`. **Watch a run live:** `python tools/watch.py` (follows the log, works for hidden scheduled
runs too); `data/captures/manifest.json` shows which queries are saved and how many posts each had. Run it now in a
visible window: `python run_daily.py --force`.

Safeguards (do not loosen without deciding to):
- at most **10 sends a day** (applications and nudges together);
- only first applications and **1st-round nudges** are sent unattended; 2nd/final follow-ups, `draft_direct`
  and queue drafts, and cut-off posts with unknown years wait in Gmail for `python 2-apply/send.py --go`;
- a nudge is cancelled if a person replied since it was drafted (checked against the tracker and again against
  Gmail right before sending);
- never sent in any mode: bounced / rejected / withdrawn / `do_not_mail` records, domains with no mail server,
  do-not-contact entries; leads at a company where someone is already talking to you are skipped;
- an empty file named `AUTO_OFF` in `jobscan/` stops all sending at once (drafts are still made).

Stop completely: `powershell tools\install_scheduled_task.ps1 -Remove`.
Needs: laptop on, internet, Chrome logged in to LinkedIn with the extension loaded.
LinkedIn's terms restrict automated collection. The extension runs only in your own logged-in tab, paced
(1.3-2.6 s pauses, 6 queries, 8 scroll rounds, 3 runs a day), and stops at any login or verification page; the
risk of a temporary account restriction is real, so keep to those limits.

## The tracking sheet

`applications.csv`: one row per application: Status, Channel, Company, Role, Track, Poster, Email, Min yrs,
Locations, Drafted, Sent, Days since sent, Follow-up due, Follow-up sent, 2nd follow-up sent, Replied, Reply
received, Reply outcome, **Reply comment**, Bounced, Do not mail, Needs review, Apply link, and three columns
that are yours: **Notes, Outcome, Next step** (kept across every rewrite). `summary.csv`: totals and reply rate.
Statuses: `sent`, `drafted`, `held`, `by_hand`, `applied`, `rejected`, `discarded`.
Both are rewritten after every change; close Excel when you are not looking (the rewrite catches up). Excel
view: `python 3-track/report.py --xlsx`.

## Inbox sync, follow-ups, checks

- `mail_sync.py [--days N] [--dry-run]` is read-only on Gmail. Only a **person answering** counts as a reply:
  receipts, out-of-office mail and broadcasts are noted but follow-ups continue. A bounce kills the address;
  "I've left, write to X" retires it and queues X (`draft_direct.py --queue`); ATS confirmations add
  applications made elsewhere; interview / assessment / "share details" mail is listed under ACTION NEEDED
  (`data/mail_actions.json`).
- `followup.py`: 3 rounds, all threaded replies, waits in calendar days (India time): 3 after sending, 4 after
  the 1st, 4 after the 2nd. A record with no findable thread is skipped, never mailed fresh.
- `health.py`: reply rate by source / inbox kind / track, follow-ups due, drafts waiting, open conversations.
- `repair_replies.py [--apply --all]`: resets reply flags that were really receipts / out-of-office.
- Do-not-contact: `data/do_not_contact.json`, entries `{"value": "a@x.com" or "@domain.com", "reason": "..."}`.

## Reliability

- **Captures are deleted after 7 days** (they hold other people's names and emails): each run clears old ones in
  `data/captures/` and `Downloads/jobscan/`, matching only files the pipeline creates (never a capture you pasted
  in by hand). By hand: `python tools/cleanup.py [--days N] [--dry-run]`.
- Every state file is written atomically; `data/leads.json` is backed up once a day (`data/backups/`, 14 kept).
  If it ever becomes unreadable, commands stop with a message pointing at the newest backup instead of starting
  over: copy that file over `leads.json`.
- Gmail calls retry on 429 / 5xx / dropped connections.
- `python doctor.py` checks the whole install; `python -m pytest` runs the offline test suite;
  `ruff check .` lints. CI config: `.github/workflows/ci.yml`.
- Secrets (`secrets/`), the tracker and `data/` hold other people's contact details: never commit or share them
  (they are gitignored).
