"""Print every application chronologically with totals, and rewrite applications.xlsx."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from collections import Counter

import config
import store as st


def main() -> None:
    config.setup_console()
    store = st.load()
    rows = sorted(store.values(), key=lambda r: r.get("drafted_at", ""))
    for r in rows:
        due = st.followup_due(r)
        print(f"{r.get('drafted_at', '')[:10]}  {r.get('channel', 'email'):<6} {r['status']:<9} "
              f"{(r.get('company') or '?')[:22]:<22} {r['role'][:40]:<40} {due}")
    print("\n" + "  ".join(f"{k}: {v}" for k, v in Counter(r["status"] for r in rows).items()))
    try:
        st.write_csv(store)
        st.write_summary(store)
        print(f"Sheet:  {config.CSV}")
        print(f"Totals: {config.SUMMARY}")
        if "--xlsx" in __import__("sys").argv:
            st.write_xlsx(store)
            print(f"Excel view: {config.XLSX}")
    except PermissionError:
        print("Close the sheet in Excel and run again.")


if __name__ == "__main__":
    main()
