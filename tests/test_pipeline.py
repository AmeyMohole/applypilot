import base64
import email
import json
from datetime import datetime, timedelta
from pathlib import Path

import config
import followup
import gmail_draft
import parser as jp
import scan
import send
import store as st
from compose import compose, resolve_greeting

FIXTURE = Path(__file__).parent / "fixtures" / "sample_capture.txt"


def lead(**kw):
    base = dict(role="DevOps Engineer", email="x@acme.com", emails=["x@acme.com"], track="devops",
                company="Acme", company_confident=True, poster="Priya Sharma", locations=["pune"])
    return jp.Lead(**{**base, **kw})


# ---- greeting

def test_greeting_rules():
    assert resolve_greeting("Priya Sharma", "priya.sharma@acme.com") == "Hello Priya,"
    assert resolve_greeting("Priya Sharma", "rahul.k@acme.com") == "Hello Rahul,"
    assert resolve_greeting("Priya Sharma", "resumes@acme.com") == "Hello,"      # "Hello Resumes," bug
    assert resolve_greeting("Priya Sharma", "careers@acme.com") == "Hello,"
    assert resolve_greeting("Priya Sharma", "recruitment.pune@acme.com") == "Hello,"
    assert resolve_greeting("Priya Sharma", "hr@acme.com") == "Hello,"
    assert resolve_greeting("", "") == "Hello,"
    assert resolve_greeting("Rishikesh C", "aryaadendhe@gmail.com") == "Hello,"  # glued first+last name
    assert resolve_greeting("Rishikesh C", "angelg@diantech.in") == "Hello,"
    assert resolve_greeting("Priya Sharma", "priya@acme.com") == "Hello Priya,"  # poster is the addressee


# ---- compose

def test_company_only_named_when_confident():
    assert "at Acme" in compose(lead())["text"]
    guess = compose(lead(company="Acme", company_confident=False))["text"]
    assert "Acme" not in guess and "opening you shared" in guess


def test_direct_source_does_not_claim_a_linkedin_post():
    assert "LinkedIn post" not in compose(lead(), source="direct")["text"]


def test_years_gap_handling():
    assert "mentions 3+ years" in compose(lead(min_years=3))["text"]
    assert "mentions" not in compose(lead(min_years=2))["text"]
    assert "mentions" not in compose(lead(min_years=None))["text"]
    assert "early in their career" in compose(lead(min_years=0))["text"]


def test_location_line():
    assert "working remotely" in compose(lead(locations=["remote"]))["text"]
    assert "office in Pune" in compose(lead(locations=["pune"]))["text"]


def test_every_email_has_certs_resume_and_availability():
    for track in ("devops", "sre", "devsecops", "cloud"):
        text = compose(lead(track=track))["text"]
        assert "AWS Certified Cloud Practitioner" in text and "Project Management" in text
        assert "join immediately" in text and "attached my resume" in text


def test_no_unclaimed_skills_in_any_email():
    banned = ["azure", "gcp", "google cloud", "helm", "argocd", "argo cd", "ansible"]
    for track in ("devops", "sre", "devsecops", "cloud"):
        text = compose(lead(track=track, themes=["gitops", "iac"]))["text"].lower()
        # themes may say "GitOps-style delivery" (interest) but never claim a tool
        for word in banned:
            assert word not in text, f"{word} claimed in {track} email"


def test_auto_interest_flag():
    assert compose(lead(themes=[]))["auto_interest"] is True
    assert compose(lead(themes=["kubernetes"]))["auto_interest"] is False


def test_html_links_are_clickable():
    mail = compose(lead())
    assert 'href="https://linkedin.com/in/ameymohole"' in mail["html"]
    assert "github" not in (mail["text"] + mail["html"]).lower()   # LinkedIn only, no GitHub


# ---- dedupe

def test_dedupe_layers():
    leads = jp.parse(FIXTURE.read_text(encoding="utf-8"))
    keep, _ = jp.shortlist(leads)
    new, dropped = scan.dedupe(keep, {})
    assert [d[1] for d in dropped] == ["same address earlier in this capture"]
    first = new[0]
    store = {first.key: {"email": first.email}}
    new2, dropped2 = scan.dedupe(keep, store)
    assert first.email not in {n.email for n in new2}
    store = {"someother::key": {"email": first.email}}      # reposted under a new title
    _, dropped3 = scan.dedupe(keep, store)
    assert {d[1] for d in dropped3} == {"already contacted, skipping"}


# ---- store / spreadsheet

def test_xlsx_keeps_human_columns_across_rewrites(tmp_path):
    from openpyxl import load_workbook
    x = tmp_path / "a.xlsx"
    store = {"a@x.com::devops": {"role": "DevOps", "email": "a@x.com", "status": "sent",
                                 "sent_at": st.now(), "drafted_at": st.now()},
             "portal::acme-sre": {"role": "SRE", "company": "Acme", "email": "", "status": "applied",
                                  "channel": "portal", "drafted_at": st.now()}}
    st.write_xlsx(store, x)
    ws = load_workbook(x).active
    head = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        key = row[head.index("Key")].value
        row[head.index("Notes")].value = f"note for {key}"
    wb = ws.parent
    wb.save(x)
    st.write_xlsx(store, x)  # rewrite
    ws = load_workbook(x).active
    rows = {r[head.index("Key")]: r[head.index("Notes")] for r in ws.iter_rows(min_row=2, values_only=True)}
    assert rows == {"a@x.com::devops": "note for a@x.com::devops",
                    "portal::acme-sre": "note for portal::acme-sre"}   # portal rows don't share notes


def test_followup_due_column():
    ago = lambda d: (datetime.now().astimezone() - timedelta(days=d)).isoformat()
    assert st.followup_due({"status": "sent", "sent_at": ago(4)}) == "DUE NOW"
    assert st.followup_due({"status": "sent", "sent_at": ago(1)}).startswith("in ")
    assert st.followup_due({"status": "sent", "sent_at": ago(9), "replied": True}) == "replied"
    sent = {"status": "sent", "sent_at": ago(9)}
    assert st.followup_due({**sent, "followup_drafted_at": "x"}) == "1st drafted"
    assert st.followup_due({**sent, "followup_sent_at": ago(5)}) == "2nd DUE"           # 4 days after the 1st
    assert st.followup_due({**sent, "followup_sent_at": ago(1)}).startswith("2nd in ")
    assert st.followup_due({**sent, "followup_sent_at": ago(5), "followup2_sent_at": ago(1)}) \
        == "final request available"
    assert st.followup_due({**sent, "bounced": True}) == "closed"


# ---- follow-up

def test_followup_candidates():
    ago = lambda d: (datetime.now().astimezone() - timedelta(days=d)).isoformat()
    rec = lambda **k: {"status": "sent", "email": "a@x.com", "role": "SRE", "sent_at": ago(5), **k}
    store = {"due": rec(), "young": rec(sent_at=ago(1)), "replied": rec(replied=True),
             "nudged": rec(followup_drafted_at="x"), "drafted": rec(status="drafted"),
             "portal": rec(email="", status="applied")}
    assert [k for k, _ in followup.candidates(store, 3)] == ["due"]


def test_nudge_text():
    text, html = followup.nudge({"role": "SRE", "email": "priya@x.com", "poster": "Priya Sharma",
                                 "company": "Acme", "company_confident": True, "track": "sre",
                                 "sent_at": "2026-09-19T10:00:00+05:30"})
    assert "19 September" in text and "Hello Priya," in text
    assert "filled or my profile isn't the right fit" in text and "Prometheus" in text


# ---- send matching

def test_send_matching_and_followup_never_overwrites_sent_at():
    store = {"k": {"email": "a@x.com", "status": "sent", "draft_id": "d1", "followup_draft_id": "d2"}}
    assert send.match({"id": "d2", "to": "a@x.com"}, store) == ("k", "followup")
    assert send.match({"id": "d1", "to": "a@x.com"}, store) == (None, "")        # already sent
    assert send.match({"id": "zz", "to": "someone@else.com"}, store) == (None, "")  # not ours


# ---- gmail message building

def test_message_has_attachment_and_thread_headers(tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    raw = gmail_draft.build_raw("a@x.com", "Re: Hi", "text", "<p>html</p>", [pdf],
                                in_reply_to="<abc@mail.gmail.com>")
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["In-Reply-To"] == msg["References"] == "<abc@mail.gmail.com>"
    types = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in types and "text/html" in types and "application/pdf" in types
    assert any(p.get_filename() == "resume.pdf" for p in msg.walk())


def test_missing_resume_fails_loudly(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        gmail_draft.build_raw("a@x.com", "s", "t", "<p>h</p>", [tmp_path / "nope.pdf"])


# ---- end to end with a fake Gmail

def test_scan_drafts_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEADS", tmp_path / "leads.json")
    monkeypatch.setattr(config, "XLSX", tmp_path / "a.xlsx")
    created = []
    monkeypatch.setattr(gmail_draft, "get_service", lambda: object())
    monkeypatch.setattr(gmail_draft, "create_draft",
                        lambda svc, to, subj, text, html, att=None, **kw:
                        created.append((to, att)) or f"draft-{len(created)}")
    scan.run([str(FIXTURE), "--drafts"])
    assert len(created) == 5 and all(a == [config.RESUME] for _, a in created)
    store = json.loads((tmp_path / "leads.json").read_text())
    statuses = sorted(r["status"] for r in store.values())
    assert statuses == ["by_hand"] + ["drafted"] * 5          # the apply-link role is tracked too
    scan.run([str(FIXTURE), "--drafts"])          # second run: nothing new, nothing duplicated
    assert len(created) == 5 and len(json.loads((tmp_path / "leads.json").read_text())) == 6


def test_dry_run_does_not_consume_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEADS", tmp_path / "leads.json")
    scan.run([str(FIXTURE)])
    assert not (tmp_path / "leads.json").exists()


# ---- multi-file captures (one file per query)

def test_parse_many_keeps_files_separate_and_dedupes_across_queries(tmp_path):
    a = ("# jobscan capture\n# url: x\n\nHome\nFeed post\nView Priya Sharma’s profile\nPriya Sharma\n• 2nd\n--\n3h\nFollow\n"
         "Acme is hiring a DevOps Engineer!\nExperience: 2-3 years\nJenkins, Docker, Kubernetes\npriya.sharma@acme.com\n")
    b = ("# jobscan capture\nHome\nNetwork\nJobs\nFeed post\nView Priya Sharma’s profile\nPriya Sharma\n• 2nd\n--\n3h\nFollow\n"
         "Acme is hiring a DevOps Engineer!\nExperience: 2-3 years\nJenkins, Docker, Kubernetes\npriya.sharma@acme.com\n"
         "Feed post\nView Sam Lee’s profile\nSam Lee\n• 3rd+\n--\n1h\nFollow\n"
         "VaultGrid is hiring a DevSecOps Engineer!\nExperience: 1-2 years\nTrivy, Semgrep, SAST\nsam.lee@vaultgrid.io\n")
    leads = jp.parse_many([a, b])
    assert [lead.email for lead in leads] == ["priya.sharma@acme.com", "sam.lee@vaultgrid.io"]
    assert "Network" not in leads[0].text            # file b's chrome did not leak into file a's post


def test_real_page_layout_and_truncation_flag():
    page = ("Search\nFeed post\nView Ravi K’s profile\nRavi K\n\n  • 3rd+\n\n--\n\nnow\n\n\nFollow\n\n"
            "We are hiring a Cloud Engineer at Nimbus. Experience: 2 years. AWS, Terraform #hiring… more\n"
            "Show translation\nAbout\n\nAccessibility\nHelp Center\n")
    (lead,) = jp.parse(page)
    assert lead.poster == "Ravi K" and lead.truncated and lead.track == "cloud"
    assert "Help Center" not in lead.text            # footer cut


def test_parse_many_prefers_the_expanded_copy_of_a_repeated_post():
    head = "Feed post\nView Priya Sharma’s profile\nPriya Sharma\n• 2nd\n--\n3h\nFollow\n"
    opening = ("Acme is hiring a DevOps Engineer! Jenkins, Docker, Kubernetes, Terraform, AWS, Prometheus "
               "and a very long description of the role and the team, which LinkedIn would cut off here")
    cut = head + opening + "… more\n"
    full = head + opening + "\nExperience: 2-3 years\npriya.sharma@acme.com\n"
    for order in ([cut, full], [full, cut]):
        (lead,) = jp.parse_many(order)
        assert lead.email == "priya.sharma@acme.com" and not lead.truncated



def test_location_line_names_every_listed_city():
    line = compose(lead(locations=["hyderabad", "bengaluru", "pune", "hybrid"]))["text"]
    assert "office in Hyderabad, Bengaluru and Pune and open to relocation" in line


# ---- the CSV sheet

def test_csv_sheet_rows_comments_and_human_columns(tmp_path):
    import csv
    path = tmp_path / "sheet.csv"
    now = st.now()
    store = {
        "a@x.com::sre": {"role": "SRE", "company": "Acme", "email": "a@x.com", "status": "sent", "track": "sre",
                         "sent_at": now, "drafted_at": now, "replied": True,
                         "reply_snippet": "Please share your notice period", "reply_at": "Mon, 2 Nov"},
        "link::barclays-devops-x": {"role": "DevOps", "company": "Barclays", "status": "by_hand",
                                    "channel": "link", "apply_url": "https://lnkd.in/x", "drafted_at": now},
    }
    st.write_csv(store, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    assert [r["Status"] for r in rows] == ["sent", "by_hand"]
    assert rows[0]["Replied"] == "yes" and rows[0]["Reply comment"] == "Please share your notice period"
    assert rows[1]["Apply link"] == "https://lnkd.in/x" and rows[1]["Channel"] == "link"
    # the human columns survive a rewrite, and by_hand rows keep their own notes
    for r in rows:
        r["Notes"] = f"my note on {r['Key']}"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    store["a@x.com::sre"]["role"] = "SRE II"
    st.write_csv(store, path)
    again = {r["Key"]: r for r in csv.DictReader(path.open(encoding="utf-8-sig", newline=""))}
    assert again["a@x.com::sre"]["Notes"] == "my note on a@x.com::sre" and again["a@x.com::sre"]["Role"] == "SRE II"
    assert again["link::barclays-devops-x"]["Notes"] == "my note on link::barclays-devops-x"


def test_summary_counts(tmp_path):
    now = st.now()
    store = {"1": {"email": "a@x.com", "status": "sent", "sent_at": now, "replied": True},
             "2": {"email": "b@x.com", "status": "sent", "sent_at": now, "followup_sent_at": now},
             "3": {"email": "c@x.com", "status": "sent", "sent_at": now, "bounced": True},
             "4": {"email": "d@x.com", "status": "drafted", "needs_review": True},
             "5": {"email": "", "status": "by_hand", "channel": "link"},
             "6": {"email": "", "status": "applied", "channel": "portal"}}
    d = dict(st.summary_rows(store))
    assert d["Applications sent (email)"] == 3 and d["Replied"] == 1 and d["Reply rate"] == "33%"
    assert d["Waiting for a reply"] == 1 and d["Follow-ups sent"] == 1 and d["Bounced (undeliverable)"] == 1
    assert d["Drafts waiting for your review"] == 1 and d["Roles to apply by hand (link only)"] == 1
    assert d["Applied via web form"] == 1 and d["Sent today"] == 3


def test_save_writes_the_sheets(tmp_path):
    st.save({"a@x.com::r": {"role": "r", "email": "a@x.com", "status": "sent", "sent_at": st.now(),
                            "drafted_at": st.now()}})
    assert config.CSV.exists() and config.SUMMARY.exists() and config.LEADS.exists()
    assert "Applications sent (email)" in config.SUMMARY.read_text(encoding="utf-8-sig")
