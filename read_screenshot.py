"""Bulk importer: screenshots in, days in the database.

Reads iOS Screen Time screenshots with the local LM Studio model, turns
each one into typed values, and saves it. Screenshots already imported
are skipped, so an interrupted run resumes where it stopped.

Usage:
    python read_screenshot.py path/to/screenshots/    # saves everything
    python read_screenshot.py --review path/to/one.png  # confirm each first

Only run one import at a time — two processes sending images at once
exhaust the model's context and the server starts rejecting requests.
"""

import base64
import json
import sys
import time
from pathlib import Path

import requests

from storage import connect, imported_file_names, save_day
from validation import validate

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

# LM Studio routes to whatever model is loaded, but naming it explicitly
# means a wrong/missing model fails loudly instead of silently answering
# with whatever happens to be in memory. Must match the id shown in
# LM Studio's Developer tab.
MODEL = "qwen/qwen3-vl-8b"

# How hard to try when the server isn't answering. Enough to ride out a
# restart, not so much that a genuinely stopped server hangs for minutes.
RETRIES = 3
RETRY_WAIT = 15

PROMPT = (
    "This is an iOS Screen Time screenshot. Transcribe exactly what you see. "
    "Copy durations as written (e.g. '2h 6m', '45m') — do not convert or "
    "calculate anything. List the apps in the order they appear. If a value "
    "is not visible in the image, use an empty string."
)

# The schema is the contract: the model physically cannot return prose or
# markdown fences. Everything is a string — the model transcribes, Python
# (in milestone 2) computes.
RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "screen_time_transcription",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "date_label": {
                    "type": "string",
                    "description": "Date text as shown, e.g. 'Yesterday, 27 July'",
                },
                "total": {
                    "type": "string",
                    "description": "Total screen time as shown, e.g. '5h 32m'",
                },
                "apps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "duration": {"type": "string"},
                        },
                        "required": ["name", "duration"],
                    },
                },
            },
            "required": ["date_label", "total", "apps"],
        },
    },
}


class TranscriptionFailed(Exception):
    """The server answered, but not with a usable transcription.

    Raised per screenshot so a bulk import can note the failure and carry
    on with the next file instead of losing the whole run.
    """


def encode_image(path: Path) -> str:
    """Return the image as a data URL for the OpenAI-compatible API."""
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def transcribe(image_path: Path) -> str:
    """Send the screenshot to LM Studio, return the raw JSON string."""
    payload = {
        "model": MODEL,
        "temperature": 0,
        "response_format": RESPONSE_SCHEMA,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": encode_image(image_path)},
                    },
                ],
            }
        ],
    }
    # First request after loading a model can be slow: the vision encoder
    # has to process the image before any tokens come back.
    #
    # Retry rather than give up: an unattended import runs for an hour, and
    # losing it because the server blinked once is a bad trade for a few
    # seconds of waiting. The two failures are treated differently — a
    # refused connection means the server is gone and every later file
    # would fail too, while an error response is often transient and worth
    # retrying before moving on to the next screenshot.
    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.post(LM_STUDIO_URL, json=payload, timeout=300)
        except requests.exceptions.ConnectionError:
            if attempt == RETRIES:
                sys.exit(
                    f"can't reach LM Studio at {LM_STUDIO_URL} — "
                    "is the server started in the Developer tab?"
                )
            print(f"  LM Studio unreachable, retrying in {RETRY_WAIT}s…")
            time.sleep(RETRY_WAIT)
            continue

        if response.ok:
            return response.json()["choices"][0]["message"]["content"]
        if attempt == RETRIES:
            raise TranscriptionFailed(f"HTTP {response.status_code}: {response.text[:200]}")
        print(f"  HTTP {response.status_code}, retrying in {RETRY_WAIT}s…")
        time.sleep(RETRY_WAIT)


def main() -> None:
    # Saving is automatic: this script is the bulk importer, and confirming
    # hundreds of screenshots by hand is review in name only. --review
    # restores the per-screenshot prompt when you do want to look first.
    review = "--review" in sys.argv
    targets = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if len(targets) != 1:
        sys.exit("usage: python read_screenshot.py [--review] <screenshot or folder>")

    target = Path(targets[0])
    if target.is_dir():
        images = sorted(
            p for p in target.iterdir()
            if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        if not images:
            sys.exit(f"no images found in: {target}")
    elif target.is_file():
        images = [target]
    else:
        sys.exit(f"not a file or folder: {target}")

    conn = connect()
    # A big import can be interrupted, and re-reading a file costs half a
    # minute of model time for no new information. Files that failed were
    # never recorded, so they are retried on the next run.
    already_done = imported_file_names(conn)
    saved = skipped = failed = 0

    for position, image_path in enumerate(images, start=1):
        progress = f"[{position}/{len(images)}] {image_path.name}"
        if image_path.name in already_done:
            print(f"{progress}: already imported")
            skipped += 1
            continue

        print(f"\n=== {progress} ===")

        # json.JSONDecodeError is a ValueError, so a server error, bad JSON,
        # an unparseable duration and a missing key all land here — one bad
        # screenshot is noted and skipped, never fatal to the run.
        try:
            raw = transcribe(image_path)
            day_usage = validate(raw, image_path)
        except (TranscriptionFailed, ValueError, KeyError) as error:
            print(f"  failed: {error}")
            failed += 1
            continue

        if not review:
            save_day(conn, day_usage, image_path, raw)
            minutes = sum(entry.minutes for entry in day_usage.entries)
            print(f"  saved {day_usage.day}: {len(day_usage.entries)} apps, {minutes} min")
            saved += 1
            continue

        # Review before save — the printout below is the review.
        print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
        print(f"\nvalidated: {day_usage.day}, {len(day_usage.entries)} apps")
        for entry in day_usage.entries:
            print(f"  {entry.app_name:<20} {entry.minutes:>4} min")
        if input("\nsave to database? [y/N] ").strip().lower() == "y":
            save_day(conn, day_usage, image_path, raw)
            print(f"saved {day_usage.day}")
            saved += 1
        else:
            print("skipped")
            skipped += 1

    conn.close()
    print(f"\ndone: {saved} saved, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
