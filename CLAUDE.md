# Screen Time Tracker

A local desktop app that turns iOS Screen Time screenshots into a long-term
history of app usage. Think Steam's "hours played," but for phone apps.

Screenshot -> local vision LLM -> structured JSON -> human review -> SQLite -> views.

This is a personal learning project, not production software. Prefer clear,
boring, readable code over clever or optimised code. Taking it slow is the point.

## Working style

I write most of the code myself. Your job is to explain, review, and hand me
small snippets when I'm stuck — not to implement whole features unprompted.

- Explain the *why* before the *how*.
- When I ask for code, keep it small enough that I can read and understand it.
- If I'm about to do something that will hurt later, say so plainly.
- Don't refactor or expand scope I didn't ask for.

## Stack

- **Python** — end to end, no other languages
- **NiceGUI** — desktop UI via `ui.run(native=True)`
- **SQLite** — via stdlib `sqlite3`
- **LM Studio** — local vision model, OpenAI-compatible API on `localhost:1234`
- **requests**, **Pillow** — HTTP and EXIF reading
- **PyInstaller** — packaging to `.exe`, at the very end

Windows laptop, RTX 3060 Laptop (6GB VRAM). VS Code.

Rejected: Tauri and Electron. Both need a Rust or Node toolchain plus a Python
sidecar, which is days of setup before any real work. NiceGUI gives a genuine
desktop window with none of that.

## Settled decisions

**The LLM transcribes; Python computes.** The model returns `"2h 6m"` as a
string. Python parses it to `126`. Never let the model do arithmetic, date
maths, or normalisation — every calculation handed to the model is a place it
can be silently wrong.

**Durations are stored as integer minutes.** Parse at the boundary, never store
the display string.

**Upsert, never insert.** Unique constraint on `(day, app_name)`. Screenshots
only show a partial list, so a single day needs several screenshots merged.
Upserting makes re-uploading the same file harmless.

**Review before save.** Model output renders as an editable table. I check it
and confirm. Vision models occasionally misread a digit or drop a row; a silent
commit would quietly corrupt the history.

**Date resolution ignores the label prefix.** iOS always prints day and month
after the comma — `"Yesterday, 27 July"`, `"Saturday, 25 July"`. Split on the
comma, discard the left half, parse the right. Year comes from the screenshot's
EXIF timestamp, falling back to file mtime. If the resolved date lands in the
future, subtract a year (handles the New Year boundary).

**Bottom-of-list truncation is accepted.** Screenshots clip the small entries.
The bias is known and fine — headline numbers stay accurate, the 2-minute tail
is undercounted.

**Structured output, not prompt begging.** Use `response_format` with a JSON
schema so the model physically cannot emit prose or markdown fences.
`temperature: 0` always.

## Schema

```
screenshots
    id, day, file_path, uploaded_at, raw_model_response

usage_entries
    id, day, app_name, minutes, screenshot_id
    UNIQUE (day, app_name)
```

Keep `raw_model_response` — it costs nothing and makes bad parses debuggable.

## Build order

Each milestone must run before starting the next. Do not jump ahead to the UI.

1. **Script: screenshot in, JSON out** — `read_screenshot.py`. Written, not yet
   verified against real screenshots.
2. **Validation layer** — raw JSON to typed objects, durations to int minutes,
   date resolution.
3. **SQLite** — schema and upsert logic, driven from the script.
4. **NiceGUI** — upload, review table, save.
5. **Views** — lifetime totals, last 14 days, per-app detail.
6. **Package** — PyInstaller to `.exe`.

Milestone 1 comes first because it's the only part that might just not work.
Everything else assumes the model can read these screenshots accurately, and
that assumption is currently unverified.

## Model

Start with **Qwen2.5-VL-3B-Instruct, Q4_K_M** (~2GB). Only escalate to 7B if
accuracy is actually poor — 6GB VRAM makes 7B tight once the vision encoder and
context are loaded, and a slow feedback loop kills iteration.

Must be a vision model. A text-only model loads fine and then returns confident
nonsense about an image it cannot see.

## Deferred

- **App name aliases** (`X` / `Twitter`, `Bloons TD 6` / `Bloons TD6`). A small
  alias table mapping raw to canonical name. Wait until the pain is real.
- **Manual row entry in the review table** — whether I can add apps the model
  missed, or only edit what it found. Decide at milestone 4, once I've seen how
  often the model actually drops rows.
- Charts, styling, anything cosmetic — after milestone 5.

## Notes

- Screen Time only retains ~4 weeks of history, so lifetime totals accumulate
  forward from first upload, not backwards.
- iOS PNG screenshots often lack an EXIF block; the mtime fallback is the path
  that usually runs. Some transfer methods clobber mtime, which only matters
  across a New Year boundary.
