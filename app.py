"""Milestone 4: the desktop app. Step 2 — upload, transcribe, show a table.

Usage:
    python app.py

Flow: drag a screenshot in -> bytes are saved to uploads/ -> the model
transcribes it (in a background thread, so the window stays alive) ->
the validated result appears as a read-only table. Editing and saving
come in the next steps.
"""

from datetime import datetime
from pathlib import Path

from nicegui import events, run, ui

from read_screenshot import transcribe
from validation import validate

UPLOAD_DIR = Path(__file__).parent / "uploads"

ui.label("Screen Time Tracker").classes("text-2xl font-bold")
status = ui.label("Upload a Screen Time screenshot to begin.")

day_label = ui.label().classes("text-lg font-semibold")
table = ui.table(
    columns=[
        {"name": "app_name", "label": "App", "field": "app_name", "align": "left"},
        {"name": "minutes", "label": "Minutes", "field": "minutes", "align": "right"},
    ],
    rows=[],
)
table.visible = False


async def handle_upload(e: events.UploadEventArguments) -> None:
    """Runs when a file finishes uploading into the app."""
    # Write the bytes to disk: EXIF lives inside them, and validation
    # needs a real file to read it from. Timestamped name so re-uploads
    # of the same filename never overwrite each other.
    UPLOAD_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_path = UPLOAD_DIR / f"{stamp}-{e.name}"
    saved_path.write_bytes(e.content.read())

    status.set_text(f"Reading {e.name} — the model takes a few seconds…")
    table.visible = False
    try:
        # io_bound = run in a background thread; await = UI stays alive.
        raw = await run.io_bound(transcribe, saved_path)
        day_usage = validate(raw, saved_path)
    except Exception as error:
        status.set_text(f"Failed: {error}")
        return

    day_label.set_text(f"{day_usage.day} — {len(day_usage.entries)} apps")
    table.rows = [
        {"app_name": entry.app_name, "minutes": entry.minutes}
        for entry in day_usage.entries
    ]
    table.visible = True
    status.set_text("Review the table. (Editing and saving arrive in step 3.)")


ui.upload(
    label="Drop screenshot here",
    auto_upload=True,
    on_upload=handle_upload,
).props("accept=.png,.jpg,.jpeg").classes("max-w-full")

ui.run(native=True, title="Screen Time Tracker", reload=False)
