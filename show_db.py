"""
TEMPORARY CODE
Show what's in screentime.db. Read-only — never writes anything.

Usage:
    python show_db.py

This is the terminal draft of milestone 5: the same queries will later
power the real views in the NiceGUI window.
"""

import sqlite3

from storage import DB_PATH


def hm(minutes: int) -> str:
    """77 -> '1h 17m'. Display formatting stays out of the database."""
    return f"{minutes // 60}h {minutes % 60}m"


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"no database yet at {DB_PATH} — save something first")

    conn = sqlite3.connect(DB_PATH)

    print("-- days stored --")
    for day, apps, total in conn.execute(
        "SELECT day, COUNT(*), SUM(minutes) FROM usage_entries"
        " GROUP BY day ORDER BY day"
    ):
        print(f"  {day}  {apps} apps  {hm(total)}")

    print("\n-- lifetime per app --")
    for app, total in conn.execute(
        "SELECT app_name, SUM(minutes) AS t FROM usage_entries"
        " GROUP BY app_name ORDER BY t DESC"
    ):
        print(f"  {app:<20} {hm(total)}")

    grand_total = conn.execute("SELECT SUM(minutes) FROM usage_entries").fetchone()[0]
    uploads = conn.execute("SELECT COUNT(*) FROM screenshots").fetchone()[0]
    print(f"\ngrand total: {hm(grand_total)} across {uploads} uploads")

    conn.close()


if __name__ == "__main__":
    main()
