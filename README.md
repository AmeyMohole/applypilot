# jobscan

A local, single-user pipeline that finds cloud / DevOps / full-stack software roles in India, drafts a tailored
Gmail application for each, sends the ones that pass strict gates, then tracks every application and follows up
on a schedule. Everything runs on one machine: no server, no database. State is `jobscan/data/leads.json`
(the single source of truth) plus a few small JSON files; `applications.csv` is a generated view of it.

See [SETUP.md](SETUP.md) for installation and daily use.

## Design principles

1. **A wrong detail is worse than a missing one.** Company names carry a confidence flag (unsure -> neutral
   wording); greetings avoid generic mailboxes ("Hello," not "Hello Careers,"); the composer only claims what
   is on the resume (`config.PROFILE`), and hand-written sentences are fact-checked.
2. **Never write to the same person twice.** Dedupe by lead key, by address across all history, by address within
   a run, and by company (someone already answering there blocks a second recruiter).
3. **Only a person answering counts as a reply.** Receipts, out-of-office mail and bounces never cancel follow-ups
   (`core/replies.py`).
4. **Skipped is not deleted.** Roles that fit but only have an apply link are tracked as `by_hand`.
5. **Unattended sending is gated.** A daily cap, a kill switch, a review gate for uncertain drafts, a live reply
   check before every nudge, and refusal to send to dead addresses. See SETUP.md, "Fully automatic mode".
6. **Crash-safe.** Atomic writes, per-record patching (no stale-snapshot overwrites), a send lock, daily backups,
   and a loud stop (never a silent reset) if the tracker is unreadable.

## Layout

```
jobscan/
  run_daily.py            orchestrator started by the scheduled task
  doctor.py               health check for the installation
  1-find/                 get leads
    scan.py               LinkedIn captures -> parse -> shortlist -> dedupe -> screen -> drafts
    scan_internships.py   same pipeline, internships only (stipend gate)
    ats_sweep.py          Greenhouse / Ashby / Lever / SmartRecruiters public APIs
    workday_sweep.py      Workday career sites
    jobspy_search.py      Indeed + LinkedIn job listings
    careers_emails.py     inbox a company publishes on its own site
    ingest.py             score + dedupe found leads -> lead queue
    google_posts.py       LinkedIn hiring posts via Google, logged out
    make_capture.py       merge browser-automation dumps into one capture
  2-apply/
    send.py               send drafts (dry run / --go / --auto), with refusals, cap and lock
    draft_direct.py       drafts for found addresses and tailored specs, with fact checks
    log_portal.py         record an application made on a web form
  3-track/
    mail_sync.py          inbox -> tracker (replies, bounces, redirects, confirmations, action items)
    followup.py           3 threaded follow-up rounds
    health.py             one-screen pipeline report
    repair_replies.py     reset reply flags that were not real replies
    report.py             print the tracker, rewrite the sheets
  core/                   shared modules (config, parser, compose, replies, contacts, store, gmail_draft,
                          queries, boards, found, leadqueue, runlock, fileutil, log)
  extension/              Chrome MV3 capture extension
  tools/                  scheduler installer, expand_posts.js (browser-console helper)
  data/  secrets/         state and credentials (gitignored)
tests/                    offline suite with an in-memory Gmail fake
```

## Pipeline

```
 capture (extension / Google / sweeps) -> parse -> shortlist (≈12 ordered filters) -> dedupe
   -> screen (do-not-contact, MX, company in conversation) -> compose -> Gmail draft
   -> send (gated)  -> mail_sync -> follow-up rounds 1..3 -> health / sheets
```

Tracks: `cloud`, `devops`, `sre`, `devsecops`, `fullstack`. Cloud wins ties; a full-stack title is a role on its
own, while generic software titles ("Software Engineer", "Backend Developer") need three stack signals in the
advert's opening. Each track has its own framing but the same verified facts.

## Development

```
pip install -r requirements-dev.txt
python -m pytest          # offline; a fake Gmail; state redirected to a temp folder
ruff check .
python doctor.py
```

Adding a search term: `core/queries.py` (and `extension/content.js` for LinkedIn posts). Adding a company
board: `core/boards.py`. Adapting to someone else: `core/config.py` (profile, resume), the track vocabularies in
`core/parser.py`, and the paragraphs in `core/compose.py`.
