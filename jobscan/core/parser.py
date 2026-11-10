"""Raw search-result text -> structured leads, plus the shortlist filter chain."""
from __future__ import annotations

import html as htmllib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cache

from config import GMAIL_ACCOUNT, PROFILE

# --------------------------------------------------------------------------- matching

@cache
def _term_re(term: str) -> re.Pattern:
    """Boundary-anchored regex. A trailing '*' opts out of the trailing boundary (prefix term)."""
    prefix = term.endswith("*")
    body = re.escape(term.rstrip("*")).replace(r"\ ", r"[\s\-]+")
    tail = "" if prefix else r"(?![A-Za-z0-9])"
    return re.compile(rf"(?<![A-Za-z0-9]){body}{tail}", re.I)


def has(text: str, term: str) -> bool:
    return bool(_term_re(term).search(text))


def found(text: str, terms) -> list[str]:
    return [t for t in terms if has(text, t)]


# --------------------------------------------------------------------------- vocabularies

# Tie-break order, and the order of preference: cloud first, then devops and full-stack software.
TRACKS = ("cloud", "devops", "devsecops", "sre", "fullstack")
INFRA_TRACKS = ("cloud", "devops", "devsecops", "sre")   # the tracks a generic title can vote for

# Terms searched for in the advert opening.
TRACK_TERMS = {
    "devsecops": ["devsecops", "appsec", "application security", "product security",
                  "cloud security", "container security", "sast", "dast", "semgrep", "trivy",
                  "owasp", "vulnerability management", "secure sdlc", "shift left",
                  "security automation", "pipeline security", "kubernetes security"],
    "sre": ["sre", "site reliability", "reliability engineer", "production engineer",
            "observability", "prometheus", "grafana", "slo", "slos", "incident management",
            "incident response", "on-call", "error budget", "monitoring"],
    "devops": ["devops", "ci/cd", "cicd", "jenkins", "docker", "kubernetes", "k8s", "k3s", "helm",
               "argocd", "argo cd", "gitops", "github actions", "gitlab ci", "ansible",
               "release engineer", "platform engineer", "infrastructure engineer",
               "infra engineer", "build engineer", "sysops", "linux administrator"],
    "cloud": ["cloud engineer", "cloud infrastructure", "cloud administrator",
              "cloud operations", "cloud support", "cloud devops", "aws", "ec2", "eks", "vpc",
              "cloudformation", "lambda", "azure", "gcp", "google cloud", "terraform",
              "infrastructure as code", "iac", "cloud native", "cloudwatch", "s3"],
    # Full-stack signals. Only used together (see FULLSTACK_WEAK_TITLES): "python" alone is not a role.
    "fullstack": ["full stack", "fullstack", "react", "reactjs", "node.js", "nodejs", "express",
                  "fastapi", "python", "javascript", "typescript", "postgresql", "postgres", "mysql",
                  "rest api", "rest apis", "restful", "microservices", "kafka", "redis", "docker"],
}

# Terms that, found in the *title*, qualify the post on their own.
TITLE_TERMS = {
    "devsecops": ["devsecops", "appsec", "application security", "product security",
                  "cloud security"],
    "sre": ["sre", "site reliability", "reliability engineer", "production engineer",
            "observability"],
    "devops": ["devops", "platform engineer", "release engineer", "build engineer",
               "infrastructure engineer", "infra engineer", "kubernetes", "k8s", "sysops",
               "ci/cd"],
    "cloud": ["cloud", "aws"],
    "fullstack": ["full stack", "fullstack"],
}

# "Full stack" in a title is a role on its own; it outweighs a stray "AWS" in brackets.
FULLSTACK_STRONG_TITLES = ["full stack", "fullstack"]
# Software titles that only qualify when the opening names enough of your actual stack.
FULLSTACK_WEAK_TITLES = ["software engineer", "software developer", "backend engineer",
                         "backend developer", "back end engineer", "back end developer",
                         "python developer", "python engineer", "node developer", "nodejs developer",
                         "node.js developer", "web developer", "application developer",
                         "platform developer", "sde", "product engineer"]
FULLSTACK_MIN_SIGNALS = 3

# Titles that only qualify when the opening backs them up with several track signals.
TITLE_GENERIC = ["security engineer", "systems engineer", "system engineer",
                 "systems administrator", "system administrator", "linux administrator",
                 "linux engineer", "infrastructure", "automation engineer",
                 "deployment engineer", "production support"]

ROLE_NOUN_RE = re.compile(
    r"\b(engineer|developer|administrator|admin|architect|specialist|analyst|consultant|sre|"
    r"intern|internship|trainee|associate|technician|lead|programmer|manager)\b", re.I)

OFF_TARGET_TITLE = [
    "qa", "quality assurance", "tester", "test engineer", "sdet", "salesforce", "sap",
    "mobile", "android", "ios", "frontend", "front end", "front-end", "react developer",
    "react js developer", "reactjs developer", "react.js developer", "react native", "angular",
    "vue", "java developer", "java full stack", "java fullstack", "java full-stack", "spring boot",
    ".net full stack", "php",
    "dotnet", "data engineer", "data scientist", "data analyst", "machine learning",
    "ml engineer", "ai engineer", "network engineer", "soc analyst", "grc", "auditor", "audit",
    "compliance", "penetration tester", "pentester", "ethical hacker", "sales", "marketing",
    "business development", "bdm", "recruiter", "telephony", "telecaller", "customer support",
    "technical support", "desktop support", "helpdesk", "help desk", "embedded", "firmware",
    "mechanical", "electrical", "civil", "game", "blockchain", "wordpress", "ui/ux",
    "designer", "content", "seo", "accountant", "teacher", "trainer", "faculty", "instructor",
    "security guard", "physical security",
]

INTERN_TERMS = ["intern", "internship", "interns", "trainee", "apprentice"]

SENIOR_HARD = ["architect", "principal", "tech lead", "technical lead", "team lead", "head of",
               "head", "director", "vp", "vice president", "staff", "manager"]
SENIOR_SOFT = ["senior", "sr", "lead", "iii", "iv", "l3", "l4", "l5", "l6", "sde3", "sde 3", "sde4",
               "sde 4", "sde iii", "sde iv"]

JOBSEEKER_PHRASES = ["open to work", "open to new opportunities", "open to opportunities",
                     "looking for new opportunities", "looking for opportunities",
                     "looking for a job", "looking for a new role", "looking for a change",
                     "actively looking", "actively seeking", "seeking new opportunities",
                     "seeking opportunities", "available for opportunities",
                     "available for hire", "exploring new opportunities", "exploring opportunities"]

VENDOR_PHRASES = ["share your requirements", "candidate profiles", "profiles available",
                  "bench resources", "available on bench", "our bench", "our consultants",
                  "resource augmentation", "happy to share suitable"]
# Weak on their own ("C2C" is also a contract tag on real adverts): need two together.
VENDOR_WEAK = ["c2c", "we supply", "bench", "submit profiles", "profile submission"]

# US-only roles are no use from India: two signals, or one unambiguous phrase.
US_ONLY_STRONG = ["authorized to work in the us", "authorised to work in the us",
                  "must be located in the us", "based in the usa", "candidates based in the us",
                  "local candidates only", "us citizens only", "usc only", "gc ead"]
US_ONLY_WEAK = ["w2", "1099", "usc", "us citizen", "green card", "h1b", "gc", "opt", "stem opt",
                "cpt", "usa only", "us only"]

COURSE_PHRASES = ["bootcamp", "enroll", "enrol", "training program", "certification course",
                  "placement assistance", "100% placement", "job guarantee", "demo class",
                  "register now", "free webinar", "course fee", "batch starts", "batch starting",
                  "interview preparation", "mock interview", "live classes", "learn devops"]

ROUNDUP_RE = re.compile(
    r"\b(\d+\+?\s+(?:open\s+)?(?:roles|openings|positions|vacancies|jobs)|"
    r"multiple\s+(?:positions|openings|roles|vacancies)|various\s+(?:positions|roles)|"
    r"several\s+(?:positions|openings|roles)|openings\s+for\s+the\s+following)\b", re.I)

CITIES = {
    "nashik": "nashik", "pune": "pune", "mumbai": "mumbai", "navi mumbai": "mumbai",
    "thane": "thane", "bengaluru": "bengaluru", "bangalore": "bengaluru",
    "hyderabad": "hyderabad", "chennai": "chennai", "delhi": "delhi", "new delhi": "delhi",
    "noida": "noida", "gurgaon": "gurugram", "gurugram": "gurugram", "ncr": "ncr",
    "kolkata": "kolkata", "ahmedabad": "ahmedabad", "jaipur": "jaipur", "indore": "indore",
    "nagpur": "nagpur", "kochi": "kochi", "coimbatore": "coimbatore",
    "thiruvananthapuram": "thiruvananthapuram", "trivandrum": "thiruvananthapuram",
    "chandigarh": "chandigarh", "bhubaneswar": "bhubaneswar", "vadodara": "vadodara",
    "surat": "surat", "lucknow": "lucknow", "mysuru": "mysuru", "mysore": "mysuru",
    "remote": "remote", "wfh": "remote", "work from home": "remote", "pan india": "pan india",
    "hybrid": "hybrid",
}

THEME_TERMS = {
    "kubernetes": ["kubernetes", "k8s", "k3s", "eks", "container orchestration"],
    "cicd": ["ci/cd", "cicd", "jenkins", "github actions", "gitlab ci", "pipelines"],
    "iac": ["terraform", "ansible", "infrastructure as code", "iac", "cloudformation"],
    "observability": ["prometheus", "grafana", "observability", "monitoring", "alerting"],
    "secpipeline": ["devsecops", "sast", "dast", "trivy", "semgrep", "shift left",
                    "security scanning", "vulnerability"],
    "aws": ["aws", "ec2", "cloud infrastructure"],
    "gitops": ["argocd", "argo cd", "helm", "gitops"],
    "reliability": ["sre", "site reliability", "slo", "slos", "incident", "on-call", "uptime"],
    "linux": ["linux", "ubuntu", "server hardening", "server administration"],
    "fullstack": ["full stack", "fullstack", "react", "node.js", "nodejs", "typescript"],
    "backend": ["rest api", "rest apis", "fastapi", "microservices", "api development", "postgresql"],
}

# Where you would rather work, best first (Nashik is home: no relocation). Remote ranks after Pune.
CITY_PRIORITY = ["nashik", "pune", "mumbai", "bengaluru", "hyderabad"]
_CITY_ALIASES = {"thane": "mumbai", "navi mumbai": "mumbai", "bangalore": "bengaluru"}


def best_city(locations) -> str:
    """The most preferred city in the list, or ''."""
    cities = {_CITY_ALIASES.get(c, c) for c in locations or []}
    return next((c for c in CITY_PRIORITY if c in cities), "")


def city_rank(locations) -> int:
    """0 = best. Remote comes after Pune; unknown or other cities last."""
    city = best_city(locations)
    if city:
        return CITY_PRIORITY.index(city) if city in ("nashik", "pune") else CITY_PRIORITY.index(city) + 1
    return 2 if "remote" in (locations or []) else len(CITY_PRIORITY) + 2


FREEMAIL = {"gmail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com", "rediffmail.com",
            "proton.me", "protonmail.com", "icloud.com", "live.com"}

COMPANY_STOP = {"we", "our", "the", "this", "a", "an", "i", "my", "team", "company", "urgent",
                "immediate", "hiring", "job", "opening", "role", "position", "location",
                "experience", "skills", "apply", "email", "salary", "if", "you", "your", "join",
                "and", "for", "us", "linkedin", "multiple", "various"}

ACRONYMS = {"aws": "AWS", "sre": "SRE", "ci/cd": "CI/CD", "devops": "DevOps",
            "devsecops": "DevSecOps", "sast": "SAST", "dast": "DAST", "it": "IT", "ii": "II",
            "iii": "III", "k8s": "K8s", "gcp": "GCP", "qa": "QA", "sdet": "SDET",
            "iam": "IAM", "api": "API", "ml": "ML", "ai": "AI", "sap": "SAP"}


# --------------------------------------------------------------------------- Lead

@dataclass
class Lead:
    poster: str = ""
    headline: str = ""
    text: str = ""
    role: str = ""
    company: str = ""
    company_confident: bool = False
    emails: list[str] = field(default_factory=list)
    email: str = ""
    min_years: float | None = None
    locations: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    track: str = ""
    is_intern: bool = False
    stipend: str = ""
    stipend_monthly: float | None = None
    unpaid: bool = False
    duration: str = ""
    links: list[str] = field(default_factory=list)
    truncated: bool = False  # '...more' was never expanded, so a contact email may be hidden

    @property
    def key(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.role.lower()).strip("-")
        return f"{self.email}::{slug}"

    @property
    def remote(self) -> bool:
        return "remote" in self.locations


# --------------------------------------------------------------------------- normalise & segment

def _strip_html(text: str) -> str:
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h\d|/tr)\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<(script|style).*?</\1>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return htmllib.unescape(text)


def normalize(text: str) -> str:
    if re.search(r"</?(div|p|br|span|li|a)\b", text, re.I):
        text = _strip_html(text)
    text = unicodedata.normalize("NFKC", text)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")


# Real pages mark each post with a bare "Feed post" line; older captures say "Feed post number N".
POST_MARKER = re.compile(r"(?m)^[ \t]*Feed post(?: number \d+)?[ \t]*$", re.I)
FOOTER = re.compile(r"\n[ \t]*About[ \t]*\n\s*Accessibility[ \t]*\n")
TRUNCATED = re.compile(r"[ \t]*(?:…|\.\.\.)[ \t]*(?:see[ \t]+)?more\b", re.I)  # same line only
VIEW_LINE = re.compile(r"^View .+(?:profile|company|page)\b.*$", re.I)
DEGREE = re.compile(r"[•·]\s*(?:1st|2nd|3rd\+?|following)\b", re.I)
TIMESTAMP = re.compile(r"^(?:now|just now|\d+\s*(?:s|m|h|d|w|mo|yr|y)\b)\s*(?:[•·].*)?$", re.I)
CHROME = re.compile(
    r"^(?:follow|following|connect|\+\s*follow|…|\.\.\.|like|comment|repost|send|share|"
    r"promoted|suggested|edited|more|see more|show translation|view image|--|"
    r"\d+|[\d,]+\s+(?:comments?|reposts?|likes?|reactions?)|visible to anyone.*|[•·])$", re.I)


def segment(text: str) -> list[str]:
    footer = FOOTER.search(text)
    if footer:  # page footer ("About / Accessibility / ...") would land inside the last post
        text = text[:footer.start()]
    parts = POST_MARKER.split(text)
    if len(parts) > 1:
        return [p for p in parts[1:] if p.strip()]  # parts[0] is page chrome before post 1
    return [p for p in re.split(r"\n\s*\n\s*\n+", text) if p.strip()]


def _dedupe_name(name: str) -> str:
    words = name.split()
    half = len(words) // 2
    if half and len(words) % 2 == 0 and words[:half] == words[half:]:
        return " ".join(words[:half])
    return name


def _split_chunk(chunk: str) -> tuple[str, str, str]:
    """Return (poster, headline, advert_text)."""
    lines = [ln.strip() for ln in chunk.split("\n") if ln.strip()]
    poster, start = "", 0
    d = next((i for i, ln in enumerate(lines[:10]) if DEGREE.search(ln)), None)
    if d is not None:
        before = lines[d][:DEGREE.search(lines[d]).start()].strip()
        poster = before or (lines[d - 1] if d > 0 else "")
        start = d + 1
    elif lines:
        poster, start = lines[0], 1
    poster = _dedupe_name(re.sub(r"\s*(?:verified|premium)\b.*$", "", poster, flags=re.I).strip())

    t = next((i for i in range(start, min(start + 12, len(lines))) if TIMESTAMP.match(lines[i])),
             None)
    headline = " ".join(lines[start:t]) if t is not None else ""
    body = lines[t + 1:] if t is not None else lines[start:]
    body = [ln for ln in body if not CHROME.match(ln)]
    return poster, headline, "\n".join(body)


# --------------------------------------------------------------------------- field extraction

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:https?://|lnkd\.in/)[^\s)>\]]+")


def extract_emails(text: str) -> list[str]:
    text = re.sub(r"\s*[\[\(]\s*at\s*[\]\)]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[\(]\s*dot\s*[\]\)]\s*", ".", text, flags=re.I)
    out: list[str] = []
    own = {PROFILE["email"].lower(), GMAIL_ACCOUNT.lower()}
    for m in EMAIL_RE.findall(text):
        e = m.strip(".").lower()
        if e in own or re.search(r"\.(png|jpe?g|gif|svg)$", e):
            continue
        if re.match(r"(?:no-?reply|do-?not-?reply)@", e):
            continue
        if e not in out:
            out.append(e)
    return out


def pick_email(emails: list[str], poster: str) -> str:
    if not emails:
        return ""
    first = re.sub(r"[^a-z]", "", (poster.split() or [""])[0].lower())
    if len(first) >= 3:
        for e in emails:
            if first in e.split("@")[0].lower():
                return e
    return emails[0]


def _title_case(title: str) -> str:
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        return " ".join(ACRONYMS.get(w.lower(), w.capitalize()) for w in title.split())
    return title


_CUT_RE = re.compile(
    r"\s*(?:\||–|—|\s-\s|\(|\[|:|,|!|(?<!Sr)(?<!Jr)\.\s+(?=[A-Z]))\s*|\s+(?:for|at|in|with|to|who|having|located|"
    r"location|on|near)\b|\s@\s", re.I)
_LEAD_RE = re.compile(r"^(?:an?|the|our|multiple|urgent(?:ly)?|immediate|opening|openings|"
                      r"position|for|now|highly|motivated|talented|experienced|skilled|passionate|"
                      r"dynamic|proactive|qualified|strong|seasoned|self-motivated|driven)\s+", re.I)


def _clean_title(s: str) -> str:
    s = re.sub(r"[^\w\s/&+.,()\[\]\-–—|:'’]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(3):
        s = _LEAD_RE.sub("", s)
    s = _CUT_RE.split(s, maxsplit=1)[0].strip(" -–—|:,.")
    words = s.split()
    if not (1 <= len(words) <= 8) or len(s) > 70 or s[:1].isdigit() or "#" in s:
        return ""
    if not ROLE_NOUN_RE.search(s):
        return ""
    return _title_case(s)


HIRE_PATTERNS = [
    re.compile(r"\b(?:job[ \t]+title|role|position|designation|vacancy)[ \t]*[:\-–][ \t]*(.+)", re.I),
    re.compile(r"(?:we(?:'|’)?re|we are|is|are)[ \t]+(?:now[ \t]+|urgently[ \t]+|actively[ \t]+)?"
               r"(?:hiring|looking[ \t]+for|seeking)[ \t]*(?:for|:|-|–)?[ \t]*(.+)", re.I),
    re.compile(r"\bhiring[ \t]*(?:for)?[ \t]*[:\-–|][ \t]*(.+)", re.I),
    re.compile(r"\bhiring[ \t]+(?:an?[ \t]+)?(.+)", re.I),
    re.compile(r"\b(?:opening|vacancy|opportunity)[ \t]+for[ \t]+(?:an?[ \t]+)?(.+)", re.I),
]


def extract_role(lines: list[str]) -> str:
    head = lines[:25]
    for pat in HIRE_PATTERNS:
        for ln in head:
            m = pat.search(ln)
            if m:
                title = _clean_title(m.group(1))
                if title:
                    return title
    for ln in head:  # a line that is itself a job title
        if len(ln) <= 60 and len(ln.split()) <= 7:
            title = _clean_title(ln)
            if title:
                return title
    return ""


_NAME = r"([A-Z][\w&.'’\-]*(?:[ \t]+(?:&[ \t]+)?[A-Z][\w&.'’\-]*){0,4})"
COMPANY_PATTERNS = [
    re.compile(r"(?im)^[ \t\W]*(?:company|organi[sz]ation|client|employer)(?:[ \t]+name)?"
               r"[ \t]*[:\-–][ \t]*([^\n|,(]{2,50})"),
    re.compile(_NAME + r"[ \t]+is[ \t]+(?i:now[ \t]+|urgently[ \t]+|actively[ \t]+)?"
               r"(?i:hiring|looking|seeking|recruiting)"),
    re.compile(r"(?i:opportunity|opening|openings|position|role|vacancy)[ \t]+(?i:at|with)[ \t]+"
               + _NAME),
    re.compile(r"(?i:hiring|join|joining)[ \t]+(?i:at|with)[ \t]+" + _NAME),
    re.compile(r"\b(?i:engineer|developer|administrator|specialist|intern|sre)[ \t]+(?i:at)[ \t]+"
               + _NAME),
]


def _valid_company(name: str) -> str:
    name = name.strip(" \t.,:-–|")
    words = name.split()
    if not words or words[0].lower() in COMPANY_STOP or name.lower() in COMPANY_STOP:
        return ""
    if len(name) < 2 or len(name) > 50 or "@" in name or "#" in name:
        return ""
    return name


def extract_company(text: str, email: str) -> tuple[str, bool]:
    for pat in COMPANY_PATTERNS:
        m = pat.search(text)
        if m:
            name = _valid_company(m.group(1))
            if name:
                return name, True
    domain = email.split("@")[-1] if "@" in email else ""
    if domain and domain not in FREEMAIL:
        return domain.split(".")[0].replace("-", " ").title(), False
    return "", False


YRS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*"
                 r"(?:\+|plus)?)?\s*(?:years?|yrs?|yr)\b", re.I)
EXP_LINE = re.compile(r"^\W*(?:total\s+|work\s+|required\s+|relevant\s+)?(?:experience|exp)\b"
                      r"[^\n:\-–]*[:\-–]\s*(.+)$", re.I)


def extract_years(advert: str) -> float | None:
    no_tags = re.sub(r"#\w+", " ", advert)
    for ln in no_tags.split("\n"):
        m = EXP_LINE.match(ln)
        if m:
            ym = YRS.search(m.group(1))
            if ym:
                return float(ym.group(1))
            if re.search(r"\bfreshers?\b", m.group(1), re.I):
                return 0.0
    for m in YRS.finditer(no_tags):
        ctx = no_tags[max(0, m.start() - 60): m.end() + 60]
        if re.search(r"\bexp(?:erience|erienced|\.)?\b", ctx, re.I):
            return float(m.group(1))
    if re.search(r"\bfreshers?\b", no_tags[:700], re.I):
        return 0.0
    return None


def extract_locations(advert: str) -> list[str]:
    no_tags = re.sub(r"#\w+", " ", advert)
    m = re.search(r"(?im)^\W*(?:work\s+|job\s+)?(?:location|city)\s*[:\-–]\s*(.+)$", no_tags)
    scope = m.group(1) if m else no_tags
    out: list[str] = []
    for term, canon in CITIES.items():
        if has(scope, term) and canon not in out:
            out.append(canon)
    return out


def extract_themes(opening: str) -> list[str]:
    scored = []
    for i, (theme, terms) in enumerate(THEME_TERMS.items()):
        n = len(found(opening, terms))
        if n:
            scored.append((-n, i, theme))
    return [t for _, _, t in sorted(scored)[:3]]


def extract_stipend(text: str) -> tuple[str, float | None, bool]:
    unpaid = bool(re.search(r"\b(?:unpaid|no stipend|without stipend|voluntary)\b", text, re.I))
    for m in re.finditer(r"(?i)\b(?:stipend|salary|ctc|compensation)\b", text):
        win = text[m.start(): m.start() + 130]
        a = re.search(r"(?i)(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(k|lpa|lakhs?)?"
                      r"(?:\s*(?:-|–|to)\s*(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(k)?)?"
                      r"(?:\s*(?:/|per\s*)?\s*(month|mo|pm|monthly|annum|year|pa))?", win[8:])
        if not a or not a.group(1).replace(",", "").replace(".", "").isdigit():
            continue
        val = float(a.group(1).replace(",", ""))
        unit = (a.group(2) or "").lower()
        if unit == "k" or (not unit and a.group(4) and a.group(4).lower() == "k"):
            val *= 1000
        elif unit in ("lpa", "lakh", "lakhs"):
            val = val * 100000 / 12
        elif (a.group(5) or "").lower() in ("annum", "year", "pa"):
            val = val / 12
        if val < 1000:  # "stipend: 3 months" and similar noise
            continue
        return win.split("\n")[0].strip(), val, unpaid
    return "", None, unpaid


def extract_duration(text: str) -> str:
    m = re.search(r"(?i)\b(\d+(?:\s*(?:-|–|to)\s*\d+)?)\s*months?\b", text)
    return f"{m.group(1)} months" if m else ""


# --------------------------------------------------------------------------- classification

def classify(title: str, opening: str) -> str:
    """Return the best-fitting track, or '' when the post isn't a target role."""
    scores = {t: len(found(title, TITLE_TERMS[t])) for t in TRACKS}
    if found(title, FULLSTACK_STRONG_TITLES):
        scores["fullstack"] = 2
    if any(scores.values()):
        return max(TRACKS, key=lambda t: (scores[t], -TRACKS.index(t)))
    if title and found(title, TITLE_GENERIC):
        body = {t: len(found(opening, TRACK_TERMS[t])) for t in INFRA_TRACKS}
        if sum(body.values()) >= 3:
            return max(INFRA_TRACKS, key=lambda t: (body[t], -INFRA_TRACKS.index(t)))
    if title and found(title, FULLSTACK_WEAK_TITLES):
        if len(found(opening, TRACK_TERMS["fullstack"])) >= FULLSTACK_MIN_SIGNALS:
            return "fullstack"
    return ""


def is_job_seeker(advert: str) -> bool:
    """Three signals together: phrase near the top, not a hashtag, first-person context."""
    opening = re.sub(r"#\w+", " ", advert[:500])
    for phrase in JOBSEEKER_PHRASES:
        m = _term_re(phrase).search(opening)
        if not m:
            continue
        win = opening[max(0, m.start() - 150): m.end() + 150]
        first_person = re.search(r"\b(?:i|i'm|i’m|i am|i've|i have|my)\b", win, re.I)
        addressing_reader = re.search(
            r"\b(?:if you|you are|you're|your (?:cv|resume)|send (?:your|ur)|we are|we're|"
            r"we have|hiring|candidates?|apply|join (?:us|our))\b", win, re.I)
        if first_person and not addressing_reader:
            return True
    return False


def is_vendor(advert: str) -> bool:
    return bool(found(advert, VENDOR_PHRASES)) or len(found(advert, VENDOR_WEAK)) >= 2


def is_us_only(advert: str) -> bool:
    return bool(found(advert, US_ONLY_STRONG)) or len(found(advert, US_ONLY_WEAK)) >= 2


def is_course(advert: str) -> bool:
    return len(found(advert, COURSE_PHRASES)) >= 2


def is_roundup(advert: str) -> bool:
    if ROUNDUP_RE.search(advert):
        return True
    titles = {ln.strip().lower() for ln in advert.split("\n")
              if len(ln) < 80 and ROLE_NOUN_RE.search(ln) and not re.search(r"[.!?]\s*$", ln)
              and re.match(r"^\W{0,3}[\w/&+. \-]+$", ln.strip())}
    return len(titles) >= 4


def has_term(text: str, terms) -> bool:
    return any(has(text, t) for t in terms)


# --------------------------------------------------------------------------- parse

def post_sig(poster: str, advert: str) -> str:
    """Identity of a post across captures. Short on purpose: LinkedIn cuts a post off at ~200
    characters, so the cut-off copy and the expanded copy only agree on their opening."""
    return re.sub(r"\W+", "", (poster + advert[:100]).lower())


def parse(text: str) -> list[Lead]:
    text = normalize(text)
    leads: list[Lead] = []
    seen: set[str] = set()
    for chunk in segment(text):
        poster, headline, advert = _split_chunk(chunk)
        truncated = bool(TRUNCATED.search(advert))
        advert = TRUNCATED.sub(" ", advert)
        if len(advert) < 40:
            continue
        sig = post_sig(poster, advert)
        if sig in seen:  # same post captured twice
            continue
        seen.add(sig)
        lines = advert.split("\n")
        emails = extract_emails(advert)
        email = pick_email(emails, poster)
        role = extract_role(lines)
        company, confident = extract_company(advert, email)
        opening = advert[:700]
        lead = Lead(
            poster=poster, headline=headline, text=advert, role=role, company=company,
            company_confident=confident, emails=emails, email=email,
            min_years=extract_years(advert), locations=extract_locations(advert),
            themes=extract_themes(opening), track=classify(role, opening),
            is_intern=has_term(role, INTERN_TERMS),
            links=list(dict.fromkeys(URL_RE.findall(advert)))[:3], truncated=truncated,
        )
        lead.stipend, lead.stipend_monthly, lead.unpaid = extract_stipend(advert)
        lead.duration = extract_duration(advert)
        leads.append(lead)
    return leads


def parse_many(texts) -> list[Lead]:
    """Parse several captures separately (each starts with its own page chrome, which must not
    land inside the previous file's last post), then drop posts seen under more than one query."""
    best: dict[str, Lead] = {}

    def quality(lead: Lead):  # expanded beats cut off, an email beats none, then more text
        return (not lead.truncated, bool(lead.email), len(lead.text))

    for text in texts:
        for lead in parse(text):
            sig = post_sig(lead.poster, lead.text)
            if sig not in best or quality(lead) > quality(best[sig]):
                best[sig] = lead
    return list(best.values())


# --------------------------------------------------------------------------- shortlist

def shortlist(leads: list[Lead], max_years: float = 4.0, locations: set[str] | None = None,
              interns: str = "exclude", min_stipend: float = 15000):
    """Return (keep, [(lead, reason), ...]).

    interns: "exclude" (default) or "only" (internship scan: normal roles are dropped).
    Order matters; the email check is last on purpose so that 'no contact email' means
    'matches your profile, apply by hand'.
    """
    keep: list[Lead] = []
    skipped: list[tuple[Lead, str]] = []
    for lead in leads:
        reason = _reject(lead, max_years, locations, interns, min_stipend)
        (skipped.append((lead, reason)) if reason else keep.append(lead))
    return keep, skipped


def _reject(lead: Lead, max_years, locations, interns, min_stipend) -> str:
    advert = lead.text
    if is_job_seeker(advert):
        return "job-seeker post, not an advert"
    if is_vendor(advert):
        return "staffing-vendor pitch"
    if is_course(advert):
        return "course / interview-prep pitch"
    if not lead.track:
        return "not a cloud / devops / sre / devsecops / full-stack role"
    if not lead.role:
        return "no role title"
    if is_roundup(advert):
        return "roundup post (several roles)"
    if is_us_only(advert):
        return "US work authorization required"
    if has_term(lead.role, OFF_TARGET_TITLE):
        return "title is off-target"
    if lead.is_intern and interns == "exclude":
        return "internship (use scan_internships.py)"
    if not lead.is_intern and interns == "only":
        return "not an internship"
    if has_term(lead.role, SENIOR_HARD):
        return "seniority mismatch (architect / lead / principal / manager)"
    first_lines = " ".join(advert.split("\n")[:2])
    if has_term(first_lines, ["architect", "principal", "staff engineer", "tech lead"]) and \
            not has_term(lead.role, ["architect", "principal"]):
        return "seniority mismatch in the advert headline"
    if not lead.is_intern:
        if has_term(lead.role, SENIOR_SOFT) and lead.min_years is None:
            return "'senior' with no years stated"
        if lead.min_years is not None and lead.min_years > max_years:
            return f"needs {lead.min_years:g}+ years (limit {max_years:g})"
    else:
        if lead.unpaid:
            return "unpaid internship"
        if lead.stipend_monthly is None:
            return "stipend not stated (check by hand)"
        if lead.stipend_monthly < min_stipend:
            return f"stipend {lead.stipend_monthly:,.0f}/month is below {min_stipend:,.0f}"
    if locations and lead.locations and not (set(lead.locations) & (locations | {"remote"})):
        return "location not wanted"
    if not lead.email:
        return "no contact email (apply by hand)"
    return ""


# --------------------------------------------------------------------------- job-board listings

def judge_listing(title: str, body: str = "", max_years: float = 4.0, interns: bool = False
                  ) -> tuple[str, str, float | None]:
    """Filter a listing from a job board or ATS API: (track, reason, min years).

    A non-empty reason says why to skip it. Same vocabulary as the post filters, so a title that
    would be rejected from a LinkedIn post is rejected here too. Listings are stricter on seniority:
    any "senior" / "lead" title is skipped, because boards state years less reliably than posts."""
    years = extract_years(body) if body else None
    track = classify(title, body[:700])
    if not track:
        return "", "not a target role", years
    if has_term(title, OFF_TARGET_TITLE):
        return track, "off-target title", years
    if has_term(title, INTERN_TERMS) and not interns:
        return track, "internship", years
    if has_term(title, SENIOR_HARD) or has_term(title, SENIOR_SOFT):
        return track, "senior title", years
    if years is not None and years > max_years:
        return track, f"asks {years:g}+ years", years
    if body and is_us_only(body):
        return track, "US work authorization required", years
    return track, "", years


def wanted_location(location: str, body: str = "") -> str:
    """The preferred city a listing offers ('' if none). 'India remote' counts as 'remote'."""
    text = location or ""
    cities = [canon for term, canon in CITIES.items() if has(text, term)]
    city = best_city(cities)
    if city:
        return city
    if "remote" in cities and re.search(r"\bindia\b", f"{text} {body[:3000]}", re.I):
        return "remote"
    if re.search(r"\bindia\b", text, re.I) and not cities:
        return "india"          # country only: the listing may sit in any city
    return ""
