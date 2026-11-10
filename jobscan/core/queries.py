"""What to search for. One place, so the LinkedIn extension, the job-board sweeps and the Google step
stay in step. Targets: cloud (first), devops / SRE, and full-stack software engineering; India.

tests/test_discovery.py checks LINKEDIN_QUERIES against extension/content.js, so the two cannot drift.
"""
from __future__ import annotations

# LinkedIn post search, run by the browser extension. 30 keywords from the resume, searched 20 at a time
# (the extension's own per-run safety limit): the first LINKEDIN_PINNED run every time, the rest rotate, so
# all 30 are covered within two runs. Short queries return the most posts, and "india" (or a city) keeps
# out the US / Vietnam posts. extension/content.js holds the same list; `python doctor.py` checks they match.
LINKEDIN_QUERIES = [
    "cloud engineer aws hiring india",
    "devops engineer hiring india",
    "aws devops engineer hiring india",
    "site reliability engineer hiring india",
    "production support engineer hiring india",
    "cloud production support hiring india",
    "application production support hiring india",
    "full stack developer hiring india",
    "software engineer python react hiring india",
    "python developer hiring india",
    "software developer hiring india",
    "cloud engineer hiring pune",
    "cloud engineer hiring mumbai",
    "devops engineer hiring nashik",
    "devops engineer hiring pune",
    "production support engineer hiring pune",
    "production support engineer hiring mumbai",
    "terraform aws engineer hiring india",
    "kubernetes devops hiring india",
    "aws cloud support engineer hiring india",
    "full stack developer hiring pune",
    "devsecops engineer hiring india",
    "python backend developer hiring india",
    "backend developer hiring india",
    "infrastructure engineer hiring india",
    "platform engineer hiring india",
    "python devops engineer hiring india",
    "cloud devops engineer hiring india",
    "python developer hiring pune",
    "python developer hiring mumbai",
]
LINKEDIN_PINNED = 2
LINKEDIN_PER_RUN = 20


def linkedin_window(run: int) -> list[str]:
    """The queries the extension offers on run number `run` (0, 1, 2, ...), as extension/content.js does."""
    rest = LINKEDIN_QUERIES[LINKEDIN_PINNED:]
    rotating = LINKEDIN_PER_RUN - LINKEDIN_PINNED
    start = (run * rotating) % len(rest)
    return LINKEDIN_QUERIES[:LINKEDIN_PINNED] + [rest[(start + i) % len(rest)] for i in range(rotating)]

# Job-board listings (JobSpy for Indeed / LinkedIn jobs, Workday search box).
JOB_SEARCH_TERMS = [
    "cloud engineer",
    "aws devops engineer",
    "devops engineer",
    "site reliability engineer",
    "production support engineer",
    "application production support",
    "cloud production support",
    "full stack developer",
    "software engineer python react",
    "python developer",
    "software developer",
]

# (location string for the job site, is it a remote search?). Nashik first: it is home.
JOB_SEARCH_LOCATIONS = [
    ("Nashik, Maharashtra", False),
    ("Pune, Maharashtra", False),
    ("Mumbai, Maharashtra", False),
    ("India", True),
]

# Google finds public LinkedIn posts without the logged-in account (see 1-find/google_posts.py):
# paste each into google.com as written, past week.
GOOGLE_POST_QUERIES = [
    'site:linkedin.com/posts "hiring" "cloud engineer" ("Pune" OR "Mumbai" OR "Nashik") "@"',
    'site:linkedin.com/posts "hiring" "devops engineer" "India" "@"',
    'site:linkedin.com/posts "hiring" "aws" "terraform" "India" "@"',
    'site:linkedin.com/posts "hiring" "full stack" ("React" OR "Node") "India" "@"',
    'site:linkedin.com/posts "hiring" "python developer" ("Pune" OR "Mumbai") "@"',
    'site:linkedin.com/posts "hiring" "software developer" ("Pune" OR "Mumbai" OR "Nashik") "@"',
]

# Words that make a careers-page heading look like one of your roles (1-find/careers_emails.py).
CAREERS_JOB_WORDS = (r"cloud|devops|sre|site reliability|platform engineer|infrastructure|aws|kubernetes|"
                     r"full.?stack|software (?:engineer|developer)|backend|python|node|react|intern|trainee")
