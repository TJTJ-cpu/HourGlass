"""Milestone 5: the questions worth asking of the history.

Queries only. Nothing here knows about NiceGUI — app.py renders whatever
these return, and show_db.py could too. Each function opens and closes its
own connection, which is cheap for a local file and keeps the UI free of
connection bookkeeping.

Minutes stay integers all the way through; format_minutes is the single
place they become text, at the display edge.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from storage import connect


@dataclass
class AppTotal:
    app_name: str
    minutes: int
    days: int


@dataclass
class DayTotal:
    day: str
    minutes: int
    apps: int


@dataclass
class DayMinutes:
    day: str
    minutes: int


@dataclass
class Summary:
    days: int
    apps: int
    minutes: int
    first_day: str
    last_day: str


@dataclass
class Window:
    """A stretch of calendar days and the app totals inside it."""

    start: str
    end: str
    days_with_data: int
    span: int
    minutes: int
    apps: list["AppTotal"]


@dataclass
class AppStats:
    app_name: str
    minutes: int
    days: int
    best_day: str
    best_minutes: int
    first_day: str
    last_day: str


def format_minutes(minutes: int) -> str:
    """126 -> '2h 6m', 9 -> '9m'. The display edge; storage stays integers.

    Averages are often under an hour, and a column of '0h 9m' is harder to
    read than '9m'.
    """
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def summary() -> Summary:
    """Headline numbers for the top of the window."""
    row = _rows(
        "SELECT COUNT(DISTINCT day), COUNT(DISTINCT app_name),"
        " COALESCE(SUM(minutes), 0), MIN(day), MAX(day) FROM usage_entries"
    )[0]
    return Summary(
        days=row[0],
        apps=row[1],
        minutes=row[2],
        first_day=row[3] or "—",
        last_day=row[4] or "—",
    )


def lifetime_totals() -> list[AppTotal]:
    """Every app, most-used first — the 'hours played' board."""
    rows = _rows(
        "SELECT app_name, SUM(minutes), COUNT(DISTINCT day) FROM usage_entries"
        " GROUP BY app_name ORDER BY SUM(minutes) DESC, app_name"
    )
    return [AppTotal(name, minutes, days) for name, minutes, days in rows]


def recent_days(limit: int = 14) -> list[DayTotal]:
    """The most recent days that have data, newest first."""
    rows = _rows(
        "SELECT day, SUM(minutes), COUNT(*) FROM usage_entries"
        " GROUP BY day ORDER BY day DESC LIMIT ?",
        (limit,),
    )
    return [DayTotal(day, minutes, apps) for day, minutes, apps in rows]


def recent_window(span: int = 14) -> Window | None:
    """Per-app totals over the last `span` calendar days — Steam's two weeks.

    Counted back from the newest day in the database rather than today,
    because the newest screenshot may be several days old. Calendar days,
    not days-with-data: if a week is missing, the window should say so
    rather than silently reaching further back to make up the numbers.
    """
    newest = _rows("SELECT MAX(day) FROM usage_entries")[0][0]
    if newest is None:
        return None
    end = date.fromisoformat(newest)
    start = end - timedelta(days=span - 1)
    bounds = (start.isoformat(), end.isoformat())

    days_with_data, minutes = _rows(
        "SELECT COUNT(DISTINCT day), COALESCE(SUM(minutes), 0) FROM usage_entries"
        " WHERE day BETWEEN ? AND ?",
        bounds,
    )[0]
    apps = [
        AppTotal(name, total, days)
        for name, total, days in _rows(
            "SELECT app_name, SUM(minutes), COUNT(DISTINCT day) FROM usage_entries"
            " WHERE day BETWEEN ? AND ?"
            " GROUP BY app_name ORDER BY SUM(minutes) DESC, app_name",
            bounds,
        )
    ]
    return Window(
        start=start.isoformat(),
        end=end.isoformat(),
        days_with_data=days_with_data,
        span=span,
        minutes=minutes,
        apps=apps,
    )


def app_stats(app_name: str) -> AppStats | None:
    """Headline numbers for one app, so the day list has context."""
    row = _rows(
        "SELECT SUM(minutes), COUNT(DISTINCT day), MIN(day), MAX(day)"
        " FROM usage_entries WHERE app_name = ?",
        (app_name,),
    )[0]
    if row[1] == 0:
        return None
    best = _rows(
        "SELECT day, minutes FROM usage_entries WHERE app_name = ?"
        " ORDER BY minutes DESC, day DESC LIMIT 1",
        (app_name,),
    )[0]
    return AppStats(
        app_name=app_name,
        minutes=row[0],
        days=row[1],
        best_day=best[0],
        best_minutes=best[1],
        first_day=row[2],
        last_day=row[3],
    )


def app_detail(app_name: str) -> list[DayMinutes]:
    """One app, every day it appears, newest first."""
    rows = _rows(
        "SELECT day, minutes FROM usage_entries WHERE app_name = ?"
        " ORDER BY day DESC",
        (app_name,),
    )
    return [DayMinutes(day, minutes) for day, minutes in rows]


def app_names() -> list[str]:
    """Every app ever seen, most-used first — for the detail picker."""
    rows = _rows(
        "SELECT app_name FROM usage_entries"
        " GROUP BY app_name ORDER BY SUM(minutes) DESC, app_name"
    )
    return [row[0] for row in rows]
