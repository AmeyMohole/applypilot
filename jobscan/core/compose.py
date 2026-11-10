"""Lead -> application email (subject + plain text + HTML).

Rule: every claim here must be on the resume (see config.PROFILE). Skills in
config.MATCH_ONLY_SKILLS may shape which posts are shortlisted, but are never claimed.
"""
from __future__ import annotations

import html
import re
import unicodedata

from config import MATCH_ONLY_SKILLS as PROFILE_UNVERIFIED
from config import PROFILE

GENERIC_MAILBOXES = {
    "careers", "career", "hr", "hrteam", "hiring", "jobs", "job", "info", "resumes", "resume",
    "cv", "cvs", "hello", "contact", "recruitment", "recruiter", "recruiters", "talent", "team",
    "admin", "support", "apply", "applications", "application", "mail", "office", "enquiry",
    "inquiry", "sales", "staffing", "india", "pune", "mumbai", "bangalore", "bengaluru",
    "hyderabad", "chennai", "delhi", "noida", "gurgaon", "gurugram", "nashik", "kolkata",
    "ahmedabad", "no", "reply", "noreply", "ta", "people", "work",
}
GENERIC_PREFIXES = ("career", "recruit", "talent", "hiring", "resume", "support", "contact", "apply")


# --------------------------------------------------------------------------- greeting

def _first_name(poster: str) -> str:
    for tok in re.sub(r"[^\w\s.]", " ", poster).split():
        tok = tok.strip(".")
        if tok.isalpha() and len(tok) >= 3 and tok.lower() not in {"dr", "mr", "mrs", "ms", "prof"}:
            return tok.capitalize()
    return ""


def greeting_name(poster: str, email: str) -> str:
    """The first name to greet, or '' when nothing is safe to say (careers@, hr@, glued names)."""
    if not email:
        return ""
    local = email.split("@")[0].lower()
    first = _first_name(poster)
    if first and first.lower() in local:
        return first
    if not re.search(r"[._\-]", local):
        return ""  # "aryaadendhe" / "angelg": can't tell where the name ends, so don't guess
    tokens = re.split(r"[^a-z]+", local)
    tok = next((t for t in tokens if t), "")
    if (3 <= len(tok) <= 15 and tok not in GENERIC_MAILBOXES
            and not (len(tok) >= 5 and tok.startswith(GENERIC_PREFIXES))):
        return tok.capitalize()
    return ""


def resolve_greeting(poster: str, email: str) -> str:
    """Greet whoever receives the mail, not whoever wrote the post. Plain 'Hello,' when unsure."""
    name = greeting_name(poster, email)
    return f"Hello {name}," if name else "Hello,"


# --------------------------------------------------------------------------- content

STACK = {
    "devops": "My day-to-day stack is Docker, Kubernetes (K3s), Jenkins, Harbor, Terraform and "
              "Linux with Nginx, running on AWS.",
    "devsecops": "I have integrated Semgrep, Trivy and OWASP ZAP into Jenkins CI/CD pipelines with "
                 "a central findings dashboard, and remediated vulnerability assessment findings "
                 "(TLS, security headers) across production hosts.",
    "sre": "I set up Prometheus and Grafana dashboards and alerts for server health and uptime, and "
           "I run production Ubuntu servers that host live internal and client portals.",
    "cloud": "On AWS I work with EC2, VPC, S3, IAM, RDS, ELB, Route 53, CloudFront, Lambda, EKS, "
             "ECR and CloudWatch, provisioned as code with Terraform.",
    "fullstack": "I build with React, FastAPI, Node.js/Express and PostgreSQL, with REST APIs, JWT/RBAC "
                 "and Keycloak/OIDC authentication, and I deploy what I build myself with Docker, "
                 "Jenkins and GitHub Actions.",
}

# How each track introduces the same person. Only the framing changes, never the facts.
TAGLINE = {"cloud": "Cloud & DevOps Engineer", "fullstack": "DevOps and Full-Stack Software Engineer"}

PROJECT = {
    "devops": "I automated AWS infrastructure with Terraform (a VPC with subnets and security "
              "groups, EC2, ELB, RDS, S3 and IAM roles) in repeatable deployments, and I have "
              "built and deployed 3 products on live production servers.",
    "devsecops": "Alongside that, I built features for QStellar, an on-premise vulnerability "
                 "management platform that consolidates Nessus, OpenVAS and ZAP findings, and "
                 "shipped QRGT and Atlas (QView), two full-stack RBAC platforms for "
                 "penetration-test reporting and SOC operations.",
    "sre": "Earlier, as technical head at a startup, I deployed 4+ web applications on live "
           "hosting with CI/CD automated through GitHub Actions, and at Quasar CyberTech I "
           "built and deployed 3 products on live production servers.",
    "cloud": "I automated a full AWS environment with Terraform (a VPC with subnets and security "
             "groups, EC2, ELB, RDS, S3 and IAM roles) and configured CloudWatch metrics, logs "
             "and alarms for the deployed resources.",
    "fullstack": "I built QRGT and Atlas (QView), two full-stack platforms with RBAC (React, FastAPI, "
                 "PostgreSQL, Docker) for penetration-test reporting and SOC operations, and shipped "
                 "them on live production servers.",
}

THEME_PHRASE = {
    "kubernetes": "container orchestration with Kubernetes",
    "cicd": "CI/CD automation",
    "iac": "infrastructure as code",
    "observability": "monitoring and alerting",
    "secpipeline": "security built into delivery pipelines",
    "aws": "AWS infrastructure",
    "gitops": "GitOps-style delivery",
    "reliability": "production reliability",
    "linux": "hands-on Linux server work",
    "fullstack": "full-stack product development",
    "backend": "API and backend engineering",
}

FALLBACK_INTEREST = {
    "devops": "The role fits the work I enjoy most: automating delivery pipelines and keeping "
              "containerised services running reliably in production.",
    "devsecops": "The role fits the work I enjoy most: making security a built-in part of the "
                 "delivery pipeline instead of a late-stage review.",
    "sre": "The role fits the work I enjoy most: keeping production systems observable, "
           "alert-driven and dependable.",
    "cloud": "The role fits the work I enjoy most: designing and automating cloud infrastructure "
             "that is secure and repeatable.",
    "fullstack": "The role fits the work I enjoy most: building a product end to end and then "
                 "owning how it is deployed and run.",
}

LOCAL_LINE_REMOTE = "I'm comfortable working remotely."


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _num(n: float) -> str:
    return f"{n:g}"


def years_line(min_years: float | None, is_intern: bool = False) -> str:
    if is_intern:
        return "I'm keen on the internship format and can start immediately."
    mine = PROFILE["years_professional"]
    if min_years is None or min_years <= mine:
        if min_years is not None and min_years <= 1:
            return (f"Since the role is open to candidates early in their career, I believe my "
                    f"{mine}+ years of hands-on production experience is a strong match.")
        return ""
    return (f"Although the role mentions {_num(min_years)}+ years of experience, I believe my "
            f"hands-on work running production systems would let me contribute quickly.")


def location_line(locations: list[str]) -> str:
    if "remote" in locations:
        return LOCAL_LINE_REMOTE
    cities = [c.title() for c in locations if c not in ("hybrid", "pan india", "ncr")]
    if len(cities) == 1:
        return f"I'm comfortable working from the office in {cities[0]} and open to relocation."
    if cities:  # the post lists several: name them all rather than picking one
        return f"I'm comfortable working from the office in {_join(cities[:4])} and open to relocation."
    return "I'm open to working from the office or relocating if needed."


def clean_role(role: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", role)).strip()


def _signature() -> list[str]:
    p = PROFILE
    return [p["name"], p["phone"], p["linkedin"], p["email"]]


# --------------------------------------------------------------------------- compose

# --------------------------------------------------------------------------- fact check
# Terms the resume does not list. A hand-written (tailored) sentence naming one is only acceptable
# when it states the gap ("no hands-on Helm yet"). config.MATCH_ONLY_SKILLS lets such posts through
# the shortlist; this is what stops an email from claiming them.
UNVERIFIED_TERMS = (*PROFILE_UNVERIFIED, "google cloud", "argo cd", "cloudformation", "gitlab ci", "circleci",
                    "datadog", "splunk", "openshift", "istio", "pulumi", "typescript", "next.js", "angular",
                    "vue", "django", "flask", "mongodb", "graphql", "spring boot", "golang", "scala", "dsa",
                    "data structures", "leetcode", ".net", "c#")
GAP_RE = re.compile(r"\b(no|not|without|gap|haven'?t|have not|yet to|new to|learning|willing to learn|"
                    r"pick(?:ing)? up|ramp(?:ing)? up|limited|instead|rather than|honest)\b", re.I)
NEGATIVE_RE = re.compile(r"\b(have not|haven'?t|has not|not used|not (?:done|worked)|yet to|new to|"
                         r"would be new|gap|lack|no (?:hands-on|experience|exposure))\b", re.I)


def fact_warnings(text: str) -> list[str]:
    """Sentences that claim something the resume cannot back, as 'term: sentence'."""
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        low = f" {sentence.lower()} "
        hits = [t for t in UNVERIFIED_TERMS if re.search(rf"(?<![a-z0-9]){re.escape(t.strip())}(?![a-z0-9])", low)]
        if hits and not GAP_RE.search(sentence):
            out.append(f"{', '.join(hits)}: {sentence.strip()}")
    return out


def negative_sentences(text: str) -> list[str]:
    """Sentences that state something you lack (warned about, not blocked: skills you lack come up
    on a call if they ask). Sentences about years are exempt: the years line says it deliberately."""
    return [x.strip() for x in re.split(r"(?<=[.!?;])\s+|\n+", text or "")
            if NEGATIVE_RE.search(x) and not re.search(r"\byears?\b", x, re.I)]


def blocks_text(blocks: list) -> list[str]:
    """Spec blocks [["p", text] | ["ul", [items]]] as plain-text paragraphs."""
    return [v if kind == "p" else "\n".join(f"- {i}" for i in v) for kind, v in blocks]


# --------------------------------------------------------------------------- compose

def compose(lead, source: str = "post", *, greet: str | None = None, interest: str | None = None,
            blocks: list | None = None, subject: str | None = None) -> dict:
    """lead: parser.Lead (or anything with the same attributes).

    source: "post" (found on LinkedIn) or "direct" (address found on a job board / company site).
    greet:  name to greet; "" means "Hello,"; None derives it from the address.
    interest: a paragraph written against the job description, replacing the generic one.
    blocks: [["p", text] | ["ul", [items]]] written against the JD, replacing the opener, stack,
            project and interest paragraphs entirely.
    subject: override, for posts that ask for a specific subject line.
    """
    track = lead.track if lead.track in STACK else "devops"
    role = clean_role(lead.role)
    company = lead.company if lead.company_confident else ""

    if source == "post":
        what = f"the {role} opening at {company}" if company else f"the {role} opening you shared"
        opener = f"I came across your LinkedIn post about {what}, and I'd like to be considered."
    else:
        what = f"the {role} opening at {company}" if company else f"the {role} opening"
        opener = f"I came across {what}, and I'd like to be considered."

    p = PROFILE
    tagline = TAGLINE.get(track, p["tagline"])
    if track == "fullstack":
        did = "building web platforms end to end and running them in production"
    else:
        did = "deploying, securing and maintaining production Ubuntu servers, containerised services and CI/CD pipelines"
    stack = (f"I'm a {tagline} with {p['years_professional']}+ years of experience "
             f"{did}, most recently as a {p['last_title']} at "
             f"{p['last_employer']}. {STACK[track]}")

    themes = [THEME_PHRASE[t] for t in lead.themes if t in THEME_PHRASE]
    auto_interest = not themes
    interest_para = (f"What draws me to this role is the focus on {_join(themes)}, which is the kind "
                     f"of work I enjoy most." if themes else FALLBACK_INTEREST[track])

    certs = p["cert_line"]
    links = f"You can find my profile on LinkedIn ({p['linkedin']})."
    avail = " ".join(x for x in [
        "I'm available to join immediately.",
        years_line(lead.min_years, getattr(lead, "is_intern", False)),
        location_line(lead.locations),
    ] if x)
    closing = ("I've attached my resume for your reference. I'd welcome the chance to discuss "
               "how I can contribute. Thank you for your time and consideration.")

    if greet is None:
        greeting = resolve_greeting(lead.poster, lead.email)
    else:
        greeting = f"Hello {greet.strip()}," if greet.strip() else "Hello,"
    if blocks:
        middle = [(kind, value) for kind, value in blocks]
        auto_interest = False
    else:
        middle = [("p", opener), ("p", stack), ("p", PROJECT[track]), ("p", (interest or interest_para).strip())]
        if interest:
            auto_interest = False
    parts = [("p", greeting), ("p", "I hope you're doing well."), *middle, ("p", links), ("p", certs),
             ("p", avail), ("p", closing)]
    sig = _signature()
    text = "\n\n".join(blocks_text(parts)) + "\n\nBest regards,\n" + "\n".join(sig)

    def linkify(x: str) -> str:
        x = html.escape(x)
        for url in (p["linkedin"],):
            x = x.replace(url, f'<a href="https://{url}">{url}</a>')
        return x.replace(p["email"], f'<a href="mailto:{p["email"]}">{p["email"]}</a>')

    body = "".join(f"<p>{linkify(v)}</p>" if kind == "p"
                   else "<ul>" + "".join(f"<li>{linkify(i)}</li>" for i in v) + "</ul>" for kind, v in parts)
    body += "<p>Best regards,<br>" + "<br>".join(linkify(x) for x in sig) + "</p>"
    return {
        "subject": subject or f"Application for {role} - {p['name']}",
        "text": text,
        "html": f"<html><body style=\"font-family:Arial,sans-serif;font-size:14px\">{body}</body></html>",
        "auto_interest": auto_interest,
    }


# --------------------------------------------------------------------------- follow-ups
# Three rounds, each a threaded reply: a nudge with a recap, a last short mail with one new reason
# to answer, and a plain status question. Same rule as the application: every claim is on the resume.

def role_focus(rec: dict) -> str:
    role, track = (rec.get("role") or "").lower(), rec.get("track", "")
    if rec.get("intern"):
        return ("I'm early in my career, but my experience comes from production systems rather "
                "than training exercises.")
    if track == "devsecops" or "security" in role:
        return ("The pipeline security work, with Semgrep, Trivy and OWASP ZAP integrated into "
                "Jenkins pipelines, maps directly to what the role describes.")
    if track == "sre" or "reliab" in role:
        return ("My Prometheus and Grafana alerting around production servers is the closest "
                "match to the reliability focus of the role.")
    if track == "fullstack":
        return ("My React, FastAPI and PostgreSQL platform work (QRGT and Atlas), which I also "
                "deployed and ran in production, is directly relevant to the role.")
    if track == "cloud" or "cloud" in role or "aws" in role:
        return ("My Terraform-based AWS provisioning (VPC, EC2, ELB, RDS, IAM) is directly "
                "relevant to the role.")
    return ("My Jenkins CI/CD and Docker/K3s deployment work on live production servers is "
            "directly relevant to the role.")


def _greet(rec: dict) -> str:
    if "greet_name" in rec:   # a hand-set greeting beats the guess from the address
        return f"Hello {rec['greet_name']}," if rec["greet_name"] else "Hello,"
    return resolve_greeting(rec.get("poster", ""), rec.get("email", ""))


def _sent_on(rec: dict) -> str:
    from datetime import datetime
    sent = datetime.fromisoformat(rec["sent_at"])
    return f"{sent.day} {sent:%B}"


def _at(rec: dict) -> str:
    company = rec.get("company") if rec.get("company_confident") else ""
    return f" at {company}" if company and company != "-" else ""


def _mail(paras: list[str]) -> dict:
    sig = _signature()
    text = "\n\n".join(paras) + "\n\nBest regards,\n" + "\n".join(sig)
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paras)
    body += "<p>Best regards,<br>" + "<br>".join(html.escape(s) for s in sig) + "</p>"
    return {"text": text,
            "html": f"<html><body style=\"font-family:Arial,sans-serif;font-size:14px\">{body}</body></html>"}


def build_followup(rec: dict) -> dict:
    """Round 1: references the original mail, recaps the strongest facts, one sentence keyed to the
    advertised role, and an easy way out."""
    p = PROFILE
    recap = rec.get("fu1_paras") or [
        f"As a recap: I'm a {p['tagline']} with {p['years_professional']}+ years of "
        f"experience running production Ubuntu servers, Docker/K3s workloads and Jenkins CI/CD "
        f"pipelines, and I'm AWS Certified Cloud Practitioner. I'm available to join immediately."]
    return _mail([
        _greet(rec),
        f"I wrote to you on {_sent_on(rec)} about the {rec['role']} position{_at(rec)} and wanted "
        f"to follow up in case it got buried.",
        *recap,
        rec.get("fu_focus") or role_focus(rec),
        "If the position has been filled or my profile isn't the right fit, I'd appreciate "
        "knowing, and I'd welcome being considered for similar openings.",
        "Thank you for your time.",
    ])


def build_second_followup(rec: dict) -> dict:
    """Round 2: no recap (it is already in the thread), one new reason to reply, and it says plainly
    that this is the last message."""
    return _mail([
        _greet(rec),
        f"I'm following up once more on my application for the {rec['role']} position{_at(rec)}, "
        f"which I first sent on {_sent_on(rec)}.",
        *(rec.get("fu2_paras") or [
            "I'm available to join immediately, and I'd be glad to walk you through my work on a short "
            "call or complete a small technical task if that would help you assess my profile."]),
        "This will be my last message on this thread, so I don't fill your inbox. If the role has been "
        "filled or isn't a fit, a one-line reply is all I need, and I'd welcome being considered for "
        "similar roles in future.",
        "Thank you for your time.",
    ])


def build_status_check(rec: dict) -> dict:
    """Round 3: one plain question, no attachments (the thread already has the resume)."""
    return _mail([
        _greet(rec),
        f"I wanted to check in on my application for the {rec['role']} position{_at(rec)}, sent on "
        f"{_sent_on(rec)}. Is the position still open, and is my application under consideration?",
        "If it has been filled or isn't a fit, a short note is all I need. Thank you for your time.",
    ])
