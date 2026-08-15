"""The desktop app: upload a screenshot, review it, save it, read the history.

Usage:
    python app.py

Four tabs. Upload runs a screenshot through the model and shows the result
as an editable table — the review step, where a misread digit gets fixed
before it reaches the database. The other three are the views: lifetime
totals, recent days, and one app's history.

LM Studio must be running for uploads. The views work without it.
"""

from datetime import datetime
from pathlib import Path

import views
from nicegui import events, run, ui
from read_screenshot import TranscriptionFailed, transcribe
from storage import connect, save_day
from validation import DayUsage, UsageEntry, validate

UPLOAD_DIR = Path(__file__).parent / "uploads"

# What the upload tab is currently holding: the screenshot on disk, the raw
# model response to store alongside it, and the editable row widgets.
pending: dict = {}


@ui.refreshable
def summary_bar() -> None:
    stats = views.summary()
    if stats.days == 0:
        ui.label("No data yet — upload a screenshot to begin.")
        return
    ui.label(
        f"{stats.days} days · {stats.apps} apps · "
        f"{views.format_minutes(stats.minutes)} · "
        f"{stats.first_day} to {stats.last_day}"
    ).classes("text-sm opacity-70")


@ui.refreshable
def lifetime_view() -> None:
    totals = views.lifetime_totals()
    if not totals:
        ui.label("Nothing stored yet.")
        return
    # "Lifetime" means since the first screenshot, not since you got the
    # phone — Screen Time only keeps four weeks, so history accumulates
    # forward from the first upload. Say so rather than let it be assumed.
    stats = views.summary()
    ui.label(f"Since {stats.first_day} · {stats.days} days tracked").classes(
        "font-semibold"
    )
    ui.label(
        f"{views.format_minutes(stats.minutes)} total · "
        f"{views.format_minutes(stats.minutes // stats.days)} a day on average"
    ).classes("text-sm opacity-70 q-mb-sm")
    ui.table(
        columns=[
            {"name": "app", "label": "App", "field": "app", "align": "left"},
            {"name": "total", "label": "Total", "field": "total", "align": "right"},
            {
                "name": "average",
                "label": "Avg / day",
                "field": "average",
                "align": "right",
            },
        ],
        # Averaged over every tracked day, including days the app went
        # unopened — that is what makes the column comparable between apps.
        rows=[
            {
                "app": row.app_name,
                "total": views.format_minutes(row.minutes),
                "average": views.format_minutes(row.minutes // stats.days),
            }
            for row in totals
        ],
        pagination=25,
    ).classes("w-full")


@ui.refreshable
def two_weeks_view() -> None:
    """Which apps ate the last fortnight — the question worth asking."""
    window = views.recent_window(14)
    if window is None or not window.apps:
        ui.label("Nothing stored yet.")
        return
    ui.label(f"{window.start} to {window.end}").classes("font-semibold")
    gaps = window.span - window.days_with_data
    coverage = f"{window.days_with_data} of {window.span} days have data"
    if gaps:
        coverage += f" — {gaps} missing, so totals are undercounted"
    ui.label(f"{views.format_minutes(window.minutes)} total · {coverage}").classes(
        "text-sm opacity-70 q-mb-sm"
    )
    ui.table(
        columns=[
            {"name": "app", "label": "App", "field": "app", "align": "left"},
            {"name": "total", "label": "Time", "field": "total", "align": "right"},
            {
                "name": "average",
                "label": "Avg / day",
                "field": "average",
                "align": "right",
            },
        ],
        # Divided by the full 14-day span, not by days the app was opened,
        # so the column reads as "this much of an average day".
        rows=[
            {
                "app": row.app_name,
                "total": views.format_minutes(row.minutes),
                "average": views.format_minutes(row.minutes // window.span),
            }
            for row in window.apps
        ],
        pagination=25,
    ).classes("w-full")


@ui.refreshable
def app_detail_view() -> None:
    names = views.app_names()
    if not names:
        ui.label("Nothing stored yet.")
        return

    # The picker is built first so it sits above the results — with 78 rows
    # of history below it, a selector at the bottom means scrolling past
    # everything to change apps.
    ui.select(
        names, value=names[0], label="App", on_change=lambda e: show(e.value)
    ).classes("w-64 q-mb-sm")
    detail_table = ui.column().classes("w-full")

    def show(app_name: str) -> None:
        detail_table.clear()
        rows = views.app_detail(app_name)
        stats = views.app_stats(app_name)
        with detail_table:
            if stats is None:
                ui.label("No data for this app.")
                return
            ui.label(
                f"{stats.app_name} — {views.format_minutes(stats.minutes)}"
            ).classes("text-lg font-semibold")
            # Average over days it was actually used, not over the whole
            # history: an app used twice isn't a "2 minutes a day" app.
            average = stats.minutes // stats.days
            ui.label(
                f"Used on {stats.days} days · {views.format_minutes(average)} "
                f"per day used · busiest {stats.best_day} "
                f"({views.format_minutes(stats.best_minutes)})"
            ).classes("text-sm opacity-70")
            ui.label(
                f"First seen {stats.first_day} · last seen {stats.last_day}"
            ).classes("text-sm opacity-70 q-mb-sm")
            ui.table(
                columns=[
                    {"name": "day", "label": "Day", "field": "day", "align": "left"},
                    {
                        "name": "minutes",
                        "label": "Time",
                        "field": "minutes",
                        "align": "right",
                    },
                ],
                rows=[
                    {"day": row.day, "minutes": views.format_minutes(row.minutes)}
                    for row in rows
                ],
                pagination=25,
            ).classes("w-full")

    show(names[0])


def refresh_all_views() -> None:
    summary_bar.refresh()
    lifetime_view.refresh()
    two_weeks_view.refresh()
    app_detail_view.refresh()


async def handle_upload(e: events.UploadEventArguments) -> None:
    """Model reads the screenshot; the result becomes an editable table."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    # Keep the bytes on disk: EXIF inside them carries the year, and the
    # saved row records where the day came from. Timestamped so re-uploading
    # the same filename never overwrites an earlier one.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_path = UPLOAD_DIR / f"{stamp}-{e.name}"
    saved_path.write_bytes(e.content.read())

    review_area.clear()
    status.set_text(f"Reading {e.name} — the model takes a few seconds…")
    try:
        # io_bound runs the slow call on a thread so the window stays alive.
        raw = await run.io_bound(transcribe, saved_path)
        day_usage = validate(raw, saved_path)
    except (TranscriptionFailed, ValueError, KeyError) as error:
        status.set_text(f"Failed: {error}")
        return

    pending.clear()
    pending.update(path=saved_path, raw=raw, day=day_usage.day, rows=[])
    status.set_text("Check the numbers against the screenshot, then save.")

    with review_area:
        ui.label(f"{day_usage.day} — {len(day_usage.entries)} apps").classes(
            "text-lg font-semibold"
        )
        for entry in day_usage.entries:
            with ui.row().classes("items-center gap-2"):
                name = ui.input(value=entry.app_name).classes("w-56")
                minutes = ui.number(value=entry.minutes, format="%d").classes("w-28")
                ui.label("min").classes("opacity-60")
            pending["rows"].append((name, minutes))
        ui.button("Save to database", on_click=save_pending).props("color=primary")


def save_pending() -> None:
    """Write the reviewed rows, taking the edited values rather than the model's."""
    if not pending:
        return
    entries = [
        UsageEntry(app_name=name.value.strip(), minutes=int(minutes.value or 0))
        for name, minutes in pending["rows"]
        if name.value and name.value.strip()
    ]
    conn = connect()
    try:
        save_day(
            conn,
            DayUsage(day=pending["day"], entries=entries),
            pending["path"],
            pending["raw"],
        )
    finally:
        conn.close()

    ui.notify(f"Saved {pending['day']} — {len(entries)} apps", type="positive")
    review_area.clear()
    status.set_text("Saved. Upload another screenshot whenever you like.")
    pending.clear()
    refresh_all_views()


ui.label("Screen Time Tracker").classes("text-2xl font-bold")
summary_bar()

with ui.tabs().classes("w-full") as tabs:
    tab_upload = ui.tab("Upload")
    tab_lifetime = ui.tab("Lifetime")
    tab_recent = ui.tab("Past 2 weeks")
    tab_app = ui.tab("Per app")

with ui.tab_panels(tabs, value=tab_upload).classes("w-full"):
    with ui.tab_panel(tab_upload):
        ui.upload(
            label="Drop screenshot here",
            auto_upload=True,
            on_upload=handle_upload,
        ).props("accept=.png,.jpg,.jpeg").classes("max-w-full")
        status = ui.label("Upload a Screen Time screenshot to begin.")
        review_area = ui.column().classes("w-full")
    with ui.tab_panel(tab_lifetime):
        lifetime_view()
    with ui.tab_panel(tab_recent):
        two_weeks_view()
    with ui.tab_panel(tab_app):
        app_detail_view()

ui.run(native=True, title="Screen Time Tracker", reload=False, window_size=(1000, 800))
