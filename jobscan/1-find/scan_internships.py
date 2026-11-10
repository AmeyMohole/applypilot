"""Same pipeline, internships only, and only those that state a stipend >= --min-stipend.

    python scan_internships.py [capture] [--drafts] [--min-stipend 15000]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import sys

from scan import run

if __name__ == "__main__":
    run(sys.argv[1:], interns="only")
