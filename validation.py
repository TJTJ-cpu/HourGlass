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


# "1h 17m", "2h", "49m", "28s" — every unit optional, but at least one
# must be present. Screen Time really does use seconds for apps opened
# for under a minute, which sit at the bottom of the list.
DURATION_RE = re.compile(
    r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\s*$"
)

DATETIME_TAG = 306  # EXIF "DateTime", e.g. "2026:07:28 10:32:29"


def parse_duration(text: str) -> int:
    """'1h 17m' -> 77, '28s' -> 0. Raises ValueError if nothing parses.

    Storage is whole minutes, so anything under a minute truncates to 0.
    Those rows are kept rather than dropped: the app genuinely was opened
    that day, and 0 adds nothing to any total.
    """
    match = DURATION_RE.match(text)
    if not match or not any(match.groups()):
        raise ValueError(f"unrecognised duration: {text!r}")
    hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return hours * 60 + minutes + seconds // 60


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
    """Model's raw JSON string -> typed DayUsage.

    A duration the model could not see comes back as an empty string,
    because that is what the prompt asks for. The last row of a screenshot
    is routinely clipped by the screen edge, so those entries are dropped
    and the rest of the day is kept — that is the accepted bottom-of-list
    truncation, not a parse failure.

    A duration that is present but unreadable ('4h9', '3s') still raises:
    that means the model misread something, which is worth noticing.

    The on-screen total is ignored — it includes apps clipped off the
    bottom of the screenshot, so it can't be recomputed from the rows.
    """
    data = json.loads(raw)
    day = resolve_date(data["date_label"], screenshot_path)
    entries = []
    for app in data["apps"]:
        name = app["name"].strip()
        duration = app["duration"].strip()
        if not name or not duration:
            continue
        entries.append(UsageEntry(app_name=name, minutes=parse_duration(duration)))
    return DayUsage(day=day, entries=entries)
