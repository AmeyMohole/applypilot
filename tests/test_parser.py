from pathlib import Path

import pytest

import parser as jp

FIXTURE = Path(__file__).parent / "fixtures" / "sample_capture.txt"


@pytest.fixture(scope="module")
def leads():
    return jp.parse(FIXTURE.read_text(encoding="utf-8"))


def by_email(leads, email):
    return [lead for lead in leads if lead.email == email]


def verdicts(leads, **kw):
    keep, skipped = jp.shortlist(leads, **kw)
    return {lead.email or lead.role: "KEEP" for lead in keep} | \
           {(lead.email or lead.role) + "#" + lead.role: reason for lead, reason in skipped}


# ---- boundary matching: the substring bugs that bit the original system

def test_word_boundaries():
    assert not jp.has("claws and laws", "aws")
    assert not jp.has("the last forecast", "sast")
    assert not jp.has("encouraged", "sre")
    assert jp.has("AWS/GCP", "aws")
    assert jp.has("CI/CD pipelines", "ci/cd")
    assert jp.has("Site-Reliability Engineer", "site reliability")


# ---- segmentation and field extraction

def test_segments_every_post(leads):
    assert len(leads) == 17


def test_poster_individual_and_group_layout(leads):
    assert by_email(leads, "priya.sharma@acmetech.com")[0].poster == "Priya Sharma"
    assert by_email(leads, "rahul.patil@nimbusworks.in")[0].poster == "Rahul Patil"  # group post


def test_headline_is_not_the_advert(leads):
    acme = by_email(leads, "priya.sharma@acmetech.com")[0]
    assert "Talent Acquisition" in acme.headline and "Talent Acquisition" not in acme.text


def test_core_fields(leads):
    acme = by_email(leads, "priya.sharma@acmetech.com")[0]
    assert (acme.role, acme.company, acme.company_confident) == ("DevOps Engineer", "Acme Technologies", True)
    assert acme.min_years == 2 and acme.locations == ["pune", "hybrid"] and acme.track == "devops"
    sec = by_email(leads, "careers@securenet.io")[0]
    assert (sec.role, sec.company, sec.track, sec.min_years) == ("DevSecOps Engineer", "SecureNet Labs", "devsecops", 1)
    assert "remote" in sec.locations


def test_company_guess_from_domain_is_flagged(leads):
    sneha = by_email(leads, "sneha.kapoor@loopstack.io")[0]
    assert sneha.company_confident  # "at LoopStack" was in the advert
    karan = by_email(leads, "karan.mehta@gmail.com")[0]
    assert karan.company == "" and not karan.company_confident  # freemail is never a company


def test_years_forms():
    assert jp.extract_years("Experience: 2-4 years") == 2
    assert jp.extract_years("Looking for 5+ yrs of experience in DevOps") == 5
    assert jp.extract_years("Senior Architect (10+ yrs) experience needed") == 10
    assert jp.extract_years("Experience: Freshers") == 0
    assert jp.extract_years("We have 10 years in business and love tea") is None


def test_email_deobfuscation_and_own_address_excluded():
    assert jp.extract_emails("mail jobs [at] acme [dot] com") == ["jobs@acme.com"]
    assert jp.extract_emails("cc ameymohole@gmail.com") == []


def test_role_title_needs_a_role_noun_and_no_hash():
    assert jp.extract_role(["We're hiring 2026 Freshers #hiring"]) == ""
    assert jp.extract_role(["Hiring: Sr. DevOps Engineer | Pune"]) == "Sr. DevOps Engineer"
    assert jp.extract_role(["SITE RELIABILITY ENGINEER"]) == "Site Reliability Engineer"


# ---- shortlist: each filter, against the fixture

def test_shortlist_default(leads):
    v = verdicts(leads)
    kept = {k for k, val in v.items() if val == "KEEP"}
    assert kept == {"priya.sharma@acmetech.com", "careers@securenet.io",
                    "rahul.patil@nimbusworks.in", "sneha.kapoor@loopstack.io",
                    "ishaan@vaultgrid.io"}


def test_reasons(leads):
    reasons = {lead.email or lead.role: r for lead, r in jp.shortlist(leads)[1]}
    assert "needs 6+" in reasons["rohit@bigco.com"]
    assert "job-seeker" in reasons["karan.mehta@gmail.com"]
    assert "course" in reasons["admissions@devopsacademy.in"]
    assert "roundup" in reasons["sunita@staffco.com"]
    assert "not a cloud" in reasons["deepak@mlcorp.com"]
    assert "off-target" in reasons["amit@salesforcejobs.com"]
    assert "seniority" in reasons["manoj@archcorp.com"]
    assert "no years" in reasons["tara@devhire.in"]
    assert "no contact email" in reasons["Cloud Engineer"]
    assert "internship" in reasons["vikram.singh@byteforge.io"]


def test_hashtag_open_to_work_does_not_flag_a_real_advert(leads):
    sneha = by_email(leads, "sneha.kapoor@loopstack.io")[0]
    assert not jp.is_job_seeker(sneha.text)


def test_helm_argocd_ansible_posts_are_in_range(leads):
    sneha = by_email(leads, "sneha.kapoor@loopstack.io")[0]
    assert sneha.track == "devops" and sneha.min_years == 3


def test_years_limit_is_configurable(leads):
    keep, _ = jp.shortlist(leads, max_years=2)
    assert "sneha.kapoor@loopstack.io" not in {lead.email for lead in keep}


def test_location_filter_off_by_default_and_works_when_set(leads):
    keep, skipped = jp.shortlist(leads, locations={"pune"})
    assert "rahul.patil@nimbusworks.in" not in {lead.email for lead in keep}  # Mumbai
    assert "sneha.kapoor@loopstack.io" in {lead.email for lead in keep}       # remote always ok


def test_internship_mode(leads):
    keep, skipped = jp.shortlist(leads, interns="only", min_stipend=15000)
    assert [lead.email for lead in keep] == ["vikram.singh@byteforge.io"]
    low = {lead.email: r for lead, r in skipped}["anita@smallshop.in"]
    assert "below" in low
    v = by_email(leads, "vikram.singh@byteforge.io")[0]
    assert v.stipend_monthly == 20000 and v.duration == "3 months"


# ---- found while auditing a real capture

def test_c2c_tag_alone_does_not_make_a_vendor_pitch():
    assert not jp.is_vendor("We're hiring an SRE. Employment Type: C2C / W2. Send your resume.")
    assert jp.is_vendor("Available Bench Resources. We submit profiles on C2C.")
    assert jp.is_vendor("Happy to share suitable candidate profiles")


def test_us_only_roles_are_flagged_but_plain_mentions_are_not():
    assert jp.is_us_only("Location: USA. Employment Type: C2C / W2. USC and GC only")
    assert jp.is_us_only("Available for candidates based in the USA")
    assert not jp.is_us_only("We work with US clients. Remote from India welcome. Contract basis.")


def test_pipe_separated_hiring_headline():
    assert jp.extract_role(["We're Hiring | Site Reliability Engineer (SRE) | USA"]) == "Site Reliability Engineer"
