"""Milestone 3: SQLite storage.

Two tables, per the schema in CLAUDE.md. Dates are stored as ISO strings
('2026-07-24') because SQLite has no date type and ISO strings sort
correctly. The raw model response is kept alongside every screenshot —
it costs nothing and makes bad parses debuggable months later.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from validation import DayUsage

DB_PATH = Path(__file__).parent / "screentime.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS screenshots (
    id                 INTEGER PRIMARY KEY,
    day                TEXT NOT NULL,
    file_path          TEXT NOT NULL,
    uploaded_at        TEXT NOT NULL,
    raw_model_response TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_entries (
    id            INTEGER PRIMARY KEY,
    day           TEXT NOT NULL,
    app_name      TEXT NOT NULL,
    minutes       INTEGER NOT NULL,
    screenshot_id INTEGER REFERENCES screenshots(id),
    UNIQUE (day, app_name)
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database, creating tables on first use."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def imported_file_names(conn: sqlite3.Connection) -> set[str]:
    """File names already recorded in screenshots.

    Lets a long bulk import resume where it left off instead of paying
    for the model again on files it already read.
    """
    rows = conn.execute("SELECT file_path FROM screenshots")
    return {Path(row[0]).name for row in rows}


def save_day(
    conn: sqlite3.Connection,
    day_usage: DayUsage,
    file_path: Path,
    raw_model_response: str,
) -> int:
    """Record one screenshot and upsert its usage entries.

    Upsert, never insert: (day, app_name) is unique, and on conflict the
    new minutes win. Screenshots of the same day taken later show higher
    numbers, so newest-wins is correct; re-uploading an identical file
    writes identical values and is harmless.

    Everything happens in one transaction — either the screenshot row and
    all its entries land, or none do.
    """
    day = day_usage.day.isoformat()
    with conn:
        cursor = conn.execute(
            "INSERT INTO screenshots (day, file_path, uploaded_at, raw_model_response)"
            " VALUES (?, ?, ?, ?)",
            (
                day,
                str(file_path),
                datetime.now().isoformat(timespec="seconds"),
                raw_model_response,
            ),
        )
        screenshot_id = cursor.lastrowid
        for entry in day_usage.entries:
            conn.execute(
                """
                INSERT INTO usage_entries (day, app_name, minutes, screenshot_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (day, app_name) DO UPDATE SET
                    minutes = excluded.minutes,
                    screenshot_id = excluded.screenshot_id
                """,
                (day, entry.app_name, entry.minutes, screenshot_id),
            )
    return screenshot_id
