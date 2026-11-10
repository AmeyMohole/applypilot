"""Record an application submitted on a company's web form.

    python log_portal.py <company> <role> <url> [location]

Status 'applied', channel 'portal', no address: the follow-up engine ignores it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import sys

import config
import store as st


def main() -> None:
    config.setup_console()
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    company, role, url = sys.argv[1:4]
    location = sys.argv[4] if len(sys.argv) > 4 else ""
    store = st.load()
    key = f"portal::{st.slug(company)}-{st.slug(role)}"
    store[key] = {"role": role, "company": company, "company_confident": True, "poster": "",
                  "email": "", "min_years": None, "locations": [location] if location else [],
                  "track": "", "status": "applied", "channel": "portal", "apply_url": url,
                  "drafted_at": st.now(), "sent_at": st.now()}
    st.save(store)
    print(f"Logged {role} @ {company}")


if __name__ == "__main__":
    main()
