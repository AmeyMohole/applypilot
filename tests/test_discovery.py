"""Discovery: tracks and queries for cloud / devops / full-stack, the listing filter, the API sweeps,
careers pages, ingest scoring and tailored drafting. No test touches the network."""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

import ats_sweep
import careers_emails
import compose
import config
import draft_direct
import found
import google_posts
import ingest
import jobspy_search
import leadqueue
import make_capture
import parser as jp
import queries
import store as st
import workday_sweep
from fakes import FakeGmail

NOW = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)
RECENT = "2026-10-28T00:00:00Z"
OLD = "2026-08-01T00:00:00Z"


# ---- tracks: cloud first, devops, full-stack software

@pytest.mark.parametrize("title, opening, track", [
    ("Cloud Engineer", "", "cloud"),
    ("AWS DevOps Engineer", "", "cloud"),                       # a tie: cloud wins the tie-break
    ("DevOps Engineer", "Jenkins Docker", "devops"),
    ("Site Reliability Engineer", "", "sre"),
    ("Full Stack Developer", "", "fullstack"),
    ("Full Stack Developer (AWS)", "", "fullstack"),            # "full stack" outweighs a stray AWS
    ("Software Engineer", "React, Node.js, PostgreSQL and Docker", "fullstack"),
    ("Backend Developer", "Python FastAPI PostgreSQL REST APIs", "fullstack"),
    ("Software Engineer", "We write Java and Spring", ""),     # no stack signals: not your role
    ("Software Engineer", "", ""),
    ("Systems Engineer", "Docker AWS Python Jenkins Linux", "devops"),   # a generic title votes infra, never full-stack
])
def test_classification(title, opening, track):
    assert jp.classify(title, opening) == track


@pytest.mark.parametrize("title", ["Frontend Developer", "React Developer", "Java Full Stack Developer",
                                   "Angular Developer", "QA Engineer", "Data Engineer", "Mobile Developer"])
def test_off_target_software_titles_stay_off(title):
    lead = jp.Lead(role=title, track="fullstack", email="a@b.com", text="x", min_years=1)
    assert "off-target" in jp.shortlist([lead])[1][0][1]


def test_a_full_stack_post_is_kept_and_python_developer_is_no_longer_blocked():
    text = ("Feed post\nAsha Rao\n• 2nd\n2d •\nAcme is hiring a Full Stack Developer!\nLocation: Pune\n"
            "Experience: 1-3 years\nReact, Node.js, PostgreSQL, Docker\nasha.rao@acme.com\n")
    (lead,) = jp.parse(text)
    assert lead.track == "fullstack" and jp.shortlist([lead])[0] == [lead]


def test_city_preference_and_scan_priority():
    import scan
    assert jp.best_city(["pune", "nashik"]) == "nashik" and jp.best_city(["bangalore"]) == "bengaluru"
    assert jp.city_rank(["nashik"]) < jp.city_rank(["pune"]) < jp.city_rank(["remote"]) \
        < jp.city_rank(["mumbai"]) < jp.city_rank(["delhi"])
    leads = [jp.Lead(role="r", track=t, locations=[c]) for t, c in
             [("sre", "pune"), ("cloud", "mumbai"), ("fullstack", "nashik"), ("cloud", "pune")]]
    assert [(x.track, x.locations[0]) for x in sorted(leads, key=scan.priority)] == [
        ("cloud", "pune"), ("cloud", "mumbai"), ("fullstack", "nashik"), ("sre", "pune")]


def lead(**kw):
    base = dict(role="Full Stack Developer", email="x@acme.com", emails=["x@acme.com"], track="fullstack",
                company="Acme", company_confident=True, poster="Priya Sharma", locations=["pune"])
    return jp.Lead(**{**base, **kw})


def test_full_stack_email_uses_only_resume_facts_and_the_right_framing():
    text = compose.compose(lead(themes=["fullstack", "backend"]))["text"]
    assert "DevOps and Full-Stack Software Engineer" in text and "QRGT and Atlas" in text
    assert "React, FastAPI, Node.js/Express and PostgreSQL" in text and "AWS Certified Cloud Practitioner" in text
    assert compose.fact_warnings(text) == []
    cloud = compose.compose(lead(track="cloud", role="Cloud Engineer"))["text"]
    assert "Cloud & DevOps Engineer" in cloud and "Terraform" in cloud and compose.fact_warnings(cloud) == []
    for track in ("cloud", "devops", "sre", "devsecops", "fullstack"):
        assert compose.fact_warnings(compose.compose(lead(track=track))["text"]) == [], track
        assert "full stack" not in compose.build_followup({"role": "R", "email": "a@b.com", "track": track,
                                                           "sent_at": st.now()})["text"].lower() or track == "fullstack"


def test_tailoring_options_and_the_fact_check():
    mail = compose.compose(lead(), greet="Asha", subject="Re: Full Stack Developer (Pune)",
                           blocks=[["p", "I read the JD closely."], ["ul", ["React dashboards", "FastAPI services"]]])
    assert mail["subject"] == "Re: Full Stack Developer (Pune)" and mail["text"].startswith("Hello Asha,")
    assert "- React dashboards\n- FastAPI services" in mail["text"] and "<ul><li>React dashboards" in mail["html"]
    assert "I build with React" not in mail["text"] and not mail["auto_interest"]      # blocks replace the stack
    assert compose.compose(lead(), greet="")["text"].startswith("Hello,\n")
    assert compose.compose(lead(), interest="Your platform team matches my work.")["text"].count("platform team") == 1
    assert compose.fact_warnings("I have deep Azure and Helm experience.")[0].startswith("azure, helm")
    assert compose.fact_warnings("I have no hands-on Helm yet, but I'm learning.") == []       # states the gap
    assert compose.fact_warnings("I build with FastAPI and Terraform.") == []
    assert compose.negative_sentences("I haven't used Helm. I have 2 years of experience.") == ["I haven't used Helm."]


# ---- the listing filter and the shared helpers

def test_judge_listing():
    assert jp.judge_listing("Cloud Engineer", "Experience: 2 years. AWS, Terraform") == ("cloud", "", 2.0)
    assert jp.judge_listing("Senior DevOps Engineer", "")[1] == "senior title"
    assert jp.judge_listing("Practice Head - Cloud", "")[1] == "senior title"
    assert jp.judge_listing("SDE3, Platform DevOps", "")[1] == "senior title"
    assert jp.judge_listing("DevOps Intern", "")[1] == "internship"
    assert jp.judge_listing("Cloud Engineer", "Experience: 8 years")[1] == "asks 8+ years"
    assert jp.judge_listing("Cloud Engineer", "Must be authorized to work in the US. W2 only")[1].startswith("US")
    assert jp.judge_listing("Account Executive", "")[1] == "not a target role"


def test_wanted_location():
    assert jp.wanted_location("Pune, Maharashtra, India") == "pune"
    assert jp.wanted_location("Navi Mumbai") == "mumbai" and jp.wanted_location("Nashik") == "nashik"
    assert jp.wanted_location("Remote - India") == "remote" and jp.wanted_location("Remote") == ""
    assert jp.wanted_location("Remote", "Open to candidates across India") == "remote"
    assert jp.wanted_location("Austin, TX") == "" and jp.wanted_location("India") == "india"


def listing(**kw):
    base = dict(source="greenhouse", company="Acme", title="Cloud Engineer", location="Pune", url="https://x/1",
                track="cloud", city="pune", years=2.0, posted="2026-10-30")
    return found.Listing(**{**base, **kw})


def test_found_dedupe_rank_and_applied_flags():
    store = {"a": {"company": "Globex Pvt Ltd", "role": "Cloud Engineer"},
             "b": {"company": "Recruiter Co", "role": "Senior AWS Cloud Migration Engineer"}}
    items = [listing(company="Globex", url="u1"), listing(company="Initech", url="u2", track="devops", city="mumbai"),
             listing(company="Initech", url="u3", track="devops", city="mumbai"),         # same job, other url
             listing(company="Hooli", url="u4", title="Senior AWS Cloud Migration Engineer"),
             listing(company="Umbrella", url="u5", track="cloud", city="nashik")]
    items = found.dedupe(items)
    assert len(items) == 4
    found.flag_applied(items, store)
    assert [i.applied_before != "" for i in items] == [True, False, True, False]      # company; title repost
    assert [i.company for i in found.ranked(items)][:2] == ["Umbrella", "Initech"]    # new companies first, best first


def test_track_by_hand_adds_once_and_skips_companies_already_applied_to(tmp_path):
    items = [listing(company="Acme", url="https://acme/jobs/1"), listing(company="Seen", url="u2", applied_before="x")]
    assert found.track_by_hand(items, {}) == 1
    (key, record), = st.load().items()
    assert key.startswith("link::acme-") and record["status"] == "by_hand" and record["apply_url"] == "https://acme/jobs/1"
    assert found.track_by_hand(items, st.load()) == 0
    path = found.write_csv(items, "test")
    assert Path(path).exists() and "Cloud Engineer" in Path(path).read_text(encoding="utf-8-sig")


# ---- API sweeps, from fixtures

def evaluate(**kw):
    args = dict(source="greenhouse", org="acme", title="Cloud Engineer", location="Pune, India", body="Terraform",
                posted=RECENT, url="https://x", days=30, max_years=4, now=NOW)
    return ats_sweep.evaluate(**{**args, **kw})


def test_evaluate_applies_age_place_title_and_years():
    assert evaluate().track == "cloud" and evaluate().city == "pune"
    assert evaluate(posted=OLD) is None and evaluate(location="Austin, TX") is None
    assert evaluate(title="Senior Cloud Engineer") is None and evaluate(body="Experience: 7 years") is None
    assert evaluate(posted=None) is not None                                     # undated postings are kept
    assert evaluate(posted=1_793_000_000_000).posted == "2026-10-26"             # epoch ms (Lever)


def test_greenhouse_parser_including_null_fields():
    data = {"jobs": [
        {"title": "Cloud Engineer", "location": {"name": "Pune"}, "offices": [], "updated_at": RECENT,
         "absolute_url": "https://g/1", "content": "&lt;p&gt;Terraform and AWS. Experience: 2 years&lt;/p&gt;"},
        {"title": "Sales Manager", "location": {"name": "Pune"}, "updated_at": RECENT, "content": ""},
        {"title": "DevOps Engineer", "location": {"name": None}, "offices": [{"name": None}, {"name": "Mumbai"}],
         "updated_at": RECENT, "absolute_url": "https://g/3", "content": ""}]}       # the null that crashed a live sweep
    out = ats_sweep.greenhouse("acme", 30, 4, get=lambda url: data)
    assert [(i.title, i.city, i.years) for i in out] == [("Cloud Engineer", "pune", 2.0), ("DevOps Engineer", "mumbai", None)]
    assert ats_sweep.greenhouse("gone", 30, 4, get=lambda url: None) is None


def test_ashby_lever_and_smartrecruiters_parsers():
    ashby = {"jobs": [{"title": "Platform Engineer, DevOps", "location": "Remote", "isRemote": True, "publishedAt": RECENT,
                       "jobUrl": "https://a/1", "descriptionPlain": "Based in India. Kubernetes"}]}
    assert [i.city for i in ats_sweep.ashby("org", 30, 4, get=lambda u: ashby)] == ["remote"]
    lever = [{"text": "Full Stack Developer", "categories": {"location": "Mumbai", "allLocations": ["Mumbai"]},
              "createdAt": 1_793_000_000_000, "hostedUrl": "https://l/1",
              "descriptionPlain": "React Node.js PostgreSQL. 1-2 years"}, {"text": "Recruiter", "categories": {}}]
    assert [(i.track, i.city) for i in ats_sweep.lever("co", 30, 4, get=lambda u: lever)] == [("fullstack", "mumbai")]
    assert ats_sweep.lever("co", 30, 4, get=lambda u: {"error": 1}) is None
    sr = {"content": [{"name": "Cloud Support Engineer", "id": "1", "releasedDate": RECENT,
                       "location": {"city": "Pune", "country": "in", "remote": False}}]}
    (item,) = ats_sweep.smartrecruiters("Co", 30, 4, get=lambda u: sr)
    assert item.url == "https://jobs.smartrecruiters.com/Co/1" and item.city == "pune"


def test_sweep_isolates_a_broken_board_and_counts_unreachable():
    good = lambda name, days, years, **k: [listing(company=name, url=f"u-{name}")]
    def broken(name, days, years, **k):
        raise TypeError("sequence item 0: expected str instance, NoneType found")
    sources = {"gh": good, "ashby": broken, "lever": lambda *a, **k: None}
    items, dead = ats_sweep.sweep(["gh:one", "ashby:two", "lever:three", "gh:four"], 30, 4, sources=sources)
    assert sorted(i.company for i in items) == ["four", "one"] and dead == ["ashby:two", "lever:three"]
    with pytest.raises(SystemExit):
        ats_sweep.sweep_one("nonsense", 30, 4)


def test_default_targets_cover_every_ats():
    targets = ats_sweep.default_targets()
    assert {t.split(":")[0] for t in targets} == {"gh", "ashby", "lever", "sr"} and len(targets) == len(set(targets))


def test_workday_tenant_sweep():
    postings = {"jobPostings": [
        {"title": "Cloud Engineer", "externalPath": "/job/Pune/Cloud-Engineer_R1", "postedOn": "Posted 3 Days Ago"},
        {"title": "Senior Cloud Engineer", "externalPath": "/job/Pune/Senior_R2", "postedOn": "Posted Today"},
        {"title": "Cloud Engineer", "externalPath": "/job/Pune/Old_R3", "postedOn": "Posted 30+ Days Ago"}]}
    detail = {"jobPostingInfo": {"location": "Pune, India", "jobDescription": "<p>AWS, Terraform. Experience: 2 years</p>"}}
    calls = []

    def call(url, body=None):
        calls.append(url)
        return postings if url.endswith("/jobs") else detail
    out = workday_sweep.sweep_tenant("acme.wd1/Careers", 14, 4, terms=["cloud"], call=call)
    assert [(i.title, i.city, i.years) for i in out] == [("Cloud Engineer", "pune", 2.0)]
    assert out[0].url == "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/Pune/Cloud-Engineer_R1"
    assert len(calls) == 2                                    # one search + one detail: filtered before fetching details
    assert workday_sweep.sweep_tenant("x.wd1/s", 14, 4, call=lambda *a: (_ for _ in ()).throw(OSError("down"))) is None
    assert workday_sweep.fresh_enough("Posted Yesterday", 14) and not workday_sweep.fresh_enough("Posted 30+ Days Ago", 90)


def test_jobspy_rows():
    row = {"title": "AWS DevOps Engineer", "company": "Acme", "location": "Pune, Maharashtra, India", "site": "indeed",
           "description": "Experience: 2 years. Terraform", "job_url": "https://i/1", "date_posted": "2026-10-30",
           "is_remote": False}
    item, why = jobspy_search.row_to_listing(row, False, 4)
    assert why == "" and item.track == "cloud" and item.city == "pune" and item.posted == "2026-10-30"
    assert jobspy_search.row_to_listing({**row, "title": "Senior DevOps Engineer"}, False, 4)[1] == "senior title"
    assert jobspy_search.row_to_listing(row, True, 4)[1] == "remote search, not remote"
    assert jobspy_search.row_to_listing({**row, "location": "Austin, TX"}, False, 4)[1] == "location not wanted"
    remote = {**row, "location": "India", "is_remote": True}
    assert jobspy_search.row_to_listing(remote, True, 4)[0].city == "remote"
    assert jobspy_search.text(float("nan")) == "" and jobspy_search.text(None) == ""
    searches = list(jobspy_search.searches(7))
    assert any("remote" in kw["search_term"] for _, kw, _ in searches) and any("Nashik" in kw["location"] for _, kw, _ in searches)


# ---- careers pages

HOME = ('<html><a href="/careers">Join us</a><a href="https://other.com/jobs">x</a>'
        '<a href="/contact">Contact</a><p>info@acme.com</p></html>')
CAREERS = ('<h2>Cloud Engineer</h2><li>Full Stack Developer</li><h3>Our values</h3>'
           '<a href="mailto:careers@acme.com">Apply</a> sales@acme.com support@acme.com asha.rao@acme.com '
           'someone@gmail.com <span class="__cf_email__" data-cfemail="{cf}"></span>')


def cfemail(address: str, key: int = 0x2A) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in address)


def test_careers_page_walk_ranks_addresses_and_finds_jobs():
    pages = {"https://acme.com": HOME, "https://acme.com/careers": CAREERS.format(cf=cfemail("hr@acme.com")),
             "https://acme.com/contact": "<p>hello@acme.com</p>"}

    def get(url):
        if url in pages:
            return pages[url]
        raise OSError("404")
    result = careers_emails.scan_domain("acme.com", get=get, pause=0)
    ranks = {a: i["rank"] for a, i in result["emails"].items()}
    assert ranks == {"careers@acme.com": 3, "hr@acme.com": 3, "asha.rao@acme.com": 2, "info@acme.com": 1, "hello@acme.com": 1}
    assert result["jobs"] == ["Cloud Engineer", "Full Stack Developer"]
    lead = careers_emails.to_lead(result)
    assert sorted(lead["emails"]) == ["careers@acme.com", "hr@acme.com"] and lead["company"] == "Acme"
    assert careers_emails.decode_cfemail(cfemail("hr@acme.com")) == "hr@acme.com"
    assert careers_emails.site_of("www.careers.acme.co.in") == "acme.co.in"
    assert careers_emails.to_lead({"domain": "x.com", "emails": {}, "jobs": []}) is None


def test_careers_walk_survives_a_dead_site():
    result = careers_emails.scan_domain("dead.example", get=lambda u: (_ for _ in ()).throw(OSError("refused")), pause=0)
    assert result["emails"] == {} and "OSError" in result["error"]


# ---- ingest: ranking and the queue

def raw(**kw):
    return {"emails": ["priya.sharma@acme.com"], "company": "Acme", "role": "Cloud Engineer", "years": 2,
            "locations": ["pune"], "track": "cloud", "text": "Terraform, AWS, Kubernetes", "poster": "Priya Sharma",
            "age": "1d", **kw}


def test_ingest_scoring_prefers_cloud_nashik_fresh_named_recruiters():
    best = ingest.assess(raw(locations=["nashik"]), {}, check_mx=False)
    plain = ingest.assess(raw(emails=["careers@acme.com"], track="sre", locations=["bengaluru"], age="3w", years=3),
                          {}, check_mx=False)
    assert best["verdict"] == "new" and best["score"] > plain["score"] + 4
    assert "named recruiter" in best["reasons"] and "fresh (1d)" in best["reasons"]
    elsewhere = ingest.assess(raw(locations=["delhi"], years=6), {}, check_mx=False)
    assert elsewhere["score"] < 0 and "other city" in elsewhere["reasons"]
    must = ingest.assess(raw(text="Azure experience is mandatory. Terraform"), {}, check_mx=False)
    assert "mandatory skill you don't list" in must["reasons"] and must["score"] < best["score"] - 2


def test_ingest_verdicts_use_the_tracker_and_contact_rules():
    import contacts
    contacts.add_dnc("stop@acme.com", "never again")
    store = {"k": {"email": "seen@acme.com", "company": "Acme", "role": "SRE", "status": "sent"}}
    assert ingest.assess(raw(emails=["stop@acme.com"]), store, check_mx=False)["verdict"].startswith("blocked")
    assert ingest.assess(raw(emails=["seen@acme.com"]), store, check_mx=False)["verdict"].startswith("already mailed")
    assert ingest.assess(raw(emails=["other@acme.com"]), store, check_mx=False)["verdict"] == "new (company already contacted)"
    assert ingest.assess(raw(emails=["noreply@acme.com"]), {}, check_mx=False)["verdict"] == "no usable address"
    assert ingest.assess(raw(text="I'm a job seeker. Open to new opportunities as a Cloud Engineer. I have 3 years"
                              " experience. I'm exploring roles"), {}, check_mx=False)["verdict"].startswith("skip")
    assert ingest.assess(raw(text="Unpaid internship, registration fee applies"), {}, check_mx=False)["verdict"].startswith("skip")


def test_pick_address_and_triage_text():
    assert ingest.pick_address(["hr@gmail.com", "careers@acme.com", "noreply@acme.com", "logo@acme.com.png"], "Acme") == "careers@acme.com"
    assert ingest.pick_address(["hr@gmail.com", "x@other.com"], "Acme") == "x@other.com"
    leads = ingest.parse_triage_text("== a@x.com, b@y.com | yrs=1,2 | loc=ok | age=2d\nWe are hiring | Cloud Engineer\n"
                                     "== c@z.com\nSecond post\n")
    assert leads[0]["emails"] == ["a@x.com", "b@y.com"] and leads[0]["age"] == "2d" and "Cloud Engineer" in leads[0]["text"]
    assert leads[1]["emails"] == ["c@z.com"]


def test_ingest_queues_and_draft_direct_consumes_the_queue(tmp_path, monkeypatch):
    path = tmp_path / "found.json"
    path.write_text(json.dumps([raw(locations=["nashik"]), raw(emails=["x@elsewhere.com"], locations=["delhi"], years=7)]))
    monkeypatch.setattr(sys, "argv", ["ingest.py", str(path), "--queue", "--no-mx"])
    assert ingest.main() == 0
    (entry,) = leadqueue.load()
    assert entry["email"] == "priya.sharma@acme.com" and entry["location"] == "nashik" and entry["track"] == "cloud"
    spec = draft_direct.spec_from_queue(entry)
    prepared = draft_direct.prepare(spec, {}, check_mx=False)
    assert prepared["record"]["needs_review"] and "Cloud & DevOps Engineer" in prepared["mail"]["text"]
    assert "office in Nashik" in prepared["mail"]["text"]


# ---- tailored drafts

def test_draft_direct_checks_before_it_drafts():
    import contacts
    contacts.add_dnc("stop@acme.com", "asked us to stop")
    spec = {"email": "hr@acme.com", "company": "Acme", "role": "Cloud Engineer", "years": 2, "track": "cloud"}
    prepared = draft_direct.prepare(spec, {}, check_mx=False)
    assert prepared["key"] == "hr@acme.com::cloud-engineer" and prepared["record"]["track"] == "cloud"
    assert prepared["record"]["greet_name"] == "" and prepared["mail"]["text"].startswith("Hello,\n")
    with pytest.raises(draft_direct.Blocked, match="do-not-contact"):
        draft_direct.prepare({**spec, "email": "stop@acme.com"}, {}, check_mx=False)
    assert draft_direct.prepare({**spec, "email": "stop@acme.com"}, {}, force=True, check_mx=False)["warnings"]
    with pytest.raises(draft_direct.Blocked, match="already in tracker"):
        draft_direct.prepare(spec, {prepared["key"]: {"status": "sent"}}, check_mx=False)
    with pytest.raises(draft_direct.Blocked, match="track must be"):
        draft_direct.prepare({**spec, "track": "ai"}, {}, check_mx=False)


def test_a_tailored_sentence_claiming_a_skill_you_lack_is_blocked():
    spec = {"email": "hr@acme.com", "company": "Acme", "role": "Cloud Engineer", "track": "cloud",
            "interest": "I have shipped production Azure and Helm charts."}
    with pytest.raises(draft_direct.Blocked, match="not on your resume - azure, helm"):
        draft_direct.prepare(spec, {}, check_mx=False)
    ok = draft_direct.prepare({**spec, "interest": "Terraform on AWS is the work I enjoy most."}, {}, check_mx=False)
    assert "Terraform on AWS" in ok["mail"]["text"] and ok["record"]["tailored"]
    gap = draft_direct.prepare({**spec, "interest": "I haven't used Helm in production yet."}, {}, check_mx=False)
    assert any("states a gap" in w for w in gap["warnings"])


def test_follow_ups_remember_the_tailoring():
    spec = {"email": "asha.rao@acme.com", "company": "Acme", "role": "Cloud Engineer", "track": "cloud", "greet": "Asha",
            "fu_focus": "My Terraform VPC work matches your landing-zone project.", "fu2_paras": ["I can start on 1 Dec."]}
    record = draft_direct.prepare(spec, {}, check_mx=False)["record"]
    record.update(sent_at="2026-11-01T10:00:00+05:30", email="asha.rao@acme.com")
    first, second = compose.build_followup(record)["text"], compose.build_second_followup(record)["text"]
    assert first.startswith("Hello Asha,") and "landing-zone project" in first and "Hello Asha," in second
    assert "I can start on 1 Dec." in second and "short call" not in second


def test_draft_direct_end_to_end_with_a_fake_gmail(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(config, "RESUME", pdf)
    g = FakeGmail()
    monkeypatch.setattr("gmail_draft.get_service", lambda: g)
    specs = tmp_path / "specs.json"
    specs.write_text(json.dumps([
        {"email": "hr@acme.com", "company": "Acme", "role": "Cloud Engineer", "track": "cloud", "location": "pune"},
        {"email": "bad", "company": "-", "role": "SRE"},
        {"email": "ops@globex.com", "company": "Globex", "role": "SRE", "track": "sre", "interest": "I run Azure."}]))
    monkeypatch.setattr(sys, "argv", ["draft_direct.py", "--spec", str(specs), "--no-mx"])
    assert draft_direct.main() == 1                          # two of the three were refused
    out = capsys.readouterr().out
    assert "NOT DRAFTED bad" in out and "not on your resume" in out and "1 drafted, 2 not drafted" in out
    (record,) = st.load().values()
    assert record["draft_id"] in g.drafts and record["needs_review"] and record["status"] == "drafted"
    monkeypatch.setattr(sys, "argv", ["draft_direct.py", "ops@globex.com", "Globex", "SRE", "--preview", "--no-mx", "--track", "sre"])
    assert draft_direct.main() == 0 and len(g.created) == 1                      # preview creates nothing
    assert "subject: Application for SRE" in capsys.readouterr().out


# ---- queries, capture merge, google posts

def test_linkedin_queries_match_the_extension_and_target_the_right_roles():
    js = (Path(__file__).resolve().parent.parent / "jobscan" / "extension" / "content.js").read_text(encoding="utf-8")
    block = re.search(r"DEFAULT_QUERIES = \[(.*?)\];", js, re.S).group(1)
    assert re.findall(r'"([^"]+)"', block) == queries.LINKEDIN_QUERIES
    assert len(queries.LINKEDIN_QUERIES) == 30 and len(set(queries.LINKEDIN_QUERIES)) == 30
    # the extension's hard per-run limit and pinned count are the same numbers queries.py rotates with
    assert int(re.search(r"const MAX_QUERIES = (\d+);", js).group(1)) == queries.LINKEDIN_PER_RUN == 20
    assert int(re.search(r"const PINNED = (\d+);", js).group(1)) == queries.LINKEDIN_PINNED == 2
    joined = " ".join(queries.LINKEDIN_QUERIES).lower()
    assert queries.LINKEDIN_QUERIES[0].startswith("cloud") and "full stack" in joined and "devops" in joined
    assert all(re.search(r"india|pune|mumbai|nashik", q.lower()) for q in queries.LINKEDIN_QUERIES)
    import run_daily
    first = re.search(r"keywords=([^&]+)", run_daily.START_URL).group(1).replace("%20", " ")
    assert first == queries.LINKEDIN_QUERIES[0]                               # the scheduled run starts on query 1
    assert "cloud engineer" in queries.JOB_SEARCH_TERMS and "full stack developer" in queries.JOB_SEARCH_TERMS
    assert queries.JOB_SEARCH_LOCATIONS[0][0].startswith("Nashik")


def test_linkedin_rotation_never_exceeds_the_per_run_limit_and_covers_every_keyword():
    windows = [queries.linkedin_window(run) for run in range(8)]
    assert all(len(w) == queries.LINKEDIN_PER_RUN == len(set(w)) for w in windows)      # 20 per run, no repeats
    assert all(w[:2] == queries.LINKEDIN_QUERIES[:2] for w in windows)                  # cloud + devops every run
    assert windows[0] == queries.LINKEDIN_QUERIES[:20]                                  # run 1 is the first 20
    assert set().union(*map(set, windows[:2])) == set(queries.LINKEDIN_QUERIES)         # all 30 within 2 runs
    assert queries.linkedin_window(0) == queries.linkedin_window(14)                    # the cycle repeats (28 / 18 -> 14 runs)


def test_make_capture_trims_each_dumps_page_chrome():
    a = "Home\nSearch: devops\n\nFeed post\nAsha\nHiring a\nasha@acme.com"
    b = "Home\nSearch: cloud\n\nFeed post\nRavi\nHiring b\nravi@acme.com"
    merged = make_capture.merge([a, b, "Home\nNo results"])
    assert "Search:" not in merged and merged.count("Feed post") == 2


def test_google_post_parsing_and_activity_id_dates(tmp_path):
    page = ('<script type="application/ld+json">{"@type": "SocialMediaPosting", "author": {"name": "Asha Rao"}, '
            '"datePublished": "2026-10-30", "articleBody": "We are hiring a Cloud Engineer &amp; more. a@acme.com"}</script>')
    assert google_posts.parse_post(page) == {"author": "Asha Rao", "date": "2026-10-30",
                                             "body": "We are hiring a Cloud Engineer & more. a@acme.com"}
    assert google_posts.parse_post("<html></html>") is None
    activity = 7_000_000_000_000_000_000
    assert google_posts.posted_at(str(activity)).year == 2022
    (tmp_path / "google-1.json").write_text(
        '"https:\\/\\/in.linkedin.com\\/posts\\/asha-rao_hiring-activity-7000000000000000000-AbCd"')
    assert google_posts.collect_urls(str(tmp_path)) == {
        "7000000000000000000": "https://www.linkedin.com/posts/asha-rao_hiring-activity-7000000000000000000-AbCd"}
    body = "Acme is hiring a Cloud Engineer! Experience: 2 years. Terraform, AWS. Mail asha.rao@acme.com"
    block = google_posts.capture_block("Asha Rao", body, dt.datetime.now(dt.timezone.utc))
    (parsed,) = jp.parse("\n" + block)                  # the capture is in the layout the parser reads
    assert parsed.poster == "Asha Rao" and parsed.email == "asha.rao@acme.com" and parsed.track == "cloud"
