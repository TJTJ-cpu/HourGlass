"""Milestone 2: turn the model's raw JSON into trusted, typed values.

The model transcribes; Python computes. Everything the model returns is a
string copied off the screen. This module is the boundary where those
strings become real types — durations become integer minutes, the date
label plus the file's own timestamp become a full date. Past this point,
nothing downstream should ever touch a display string again.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from PIL import Image


@dataclass
class UsageEntry:
    app_name: str
    minutes: int


@dataclass
class DayUsage:
    day: date
    entries: list[UsageEntry]


# "1h 17m", "2h", "49m" — hours and minutes both optional, but not both
# absent. Screen Time never shows seconds.
DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$")

DATETIME_TAG = 306  # EXIF "DateTime", e.g. "2026:07:28 10:32:29"


def parse_duration(text: str) -> int:
    """'1h 17m' -> 77. Raises ValueError on anything unrecognisable."""
    match = DURATION_RE.match(text)
    if not match or (match.group(1) is None and match.group(2) is None):
        raise ValueError(f"unrecognised duration: {text!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return hours * 60 + minutes


def screenshot_year(path: Path) -> int:
    """Year the screenshot was taken — EXIF first, file mtime as fallback."""
    stamp = Image.open(path).getexif().get(DATETIME_TAG)
    if stamp:
        return int(stamp[:4])
    return datetime.fromtimestamp(path.stat().st_mtime).year


def resolve_date(date_label: str, screenshot_path: Path) -> date:
    """'Friday, 24 July' + the file's year -> date(2026, 7, 24).

    The label says which day the data is about; the file's timestamp
    contributes only the year, because iOS never prints one. The left
    half of the label ("Friday", "Yesterday") is decoration — discard it.
    """
    day_month = date_label.split(",")[-1].strip()
    year = screenshot_year(screenshot_path)
    resolved = datetime.strptime(f"{day_month} {year}", "%d %B %Y").date()
    # A screenshot taken in January can show "31 December" — that was
    # last year. Data from the future is impossible, so this check is safe.
    if resolved > date.today():
        resolved = resolved.replace(year=resolved.year - 1)
    return resolved


def validate(raw: str, screenshot_path: Path) -> DayUsage:
    """Model's raw JSON string -> typed DayUsage. Raises on anything off.

    Raising (rather than skipping bad rows) is deliberate for now: until
    the review UI exists, a loud failure is the only way to notice the
    model misbehaving. The on-screen total is ignored — it includes apps
    clipped off the bottom of the screenshot, so it can't be recomputed
    from the rows and isn't stored.
    """
    data = json.loads(raw)
    day = resolve_date(data["date_label"], screenshot_path)
    entries = [
        UsageEntry(app_name=app["name"].strip(), minutes=parse_duration(app["duration"]))
        for app in data["apps"]
    ]
    return DayUsage(day=day, entries=entries)
