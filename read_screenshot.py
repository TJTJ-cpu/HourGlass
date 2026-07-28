"""Milestone 1: screenshot in, JSON out.

Sends one iOS Screen Time screenshot to the local LM Studio server and
prints the model's JSON transcription. No parsing, no date maths, no
database — that comes in later milestones. The point of this script is
to answer one question: can the model read these screenshots accurately?

Usage:
    python read_screenshot.py path/to/screenshot.png
"""

import base64
import json
import sys
from pathlib import Path

import requests

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

# LM Studio routes to whatever model is loaded, but naming it explicitly
# means a wrong/missing model fails loudly instead of silently answering
# with whatever happens to be in memory. Must match the id shown in
# LM Studio's Developer tab.
MODEL = "qwen/qwen3-vl-8b"

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
    try:
        response = requests.post(LM_STUDIO_URL, json=payload, timeout=300)
    except requests.exceptions.ConnectionError:
        sys.exit(
            f"can't reach LM Studio at {LM_STUDIO_URL} — "
            "is the server started in the Developer tab?"
        )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python read_screenshot.py <screenshot or folder>")

    target = Path(sys.argv[1])
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

    for image_path in images:
        print(f"\n=== {image_path.name} ===")
        raw = transcribe(image_path)

        # Pretty-print if it's valid JSON; show it raw if not, because a bad
        # response is exactly what this milestone needs to make visible.
        try:
            print(json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print("model returned invalid JSON:")
            print(raw)


if __name__ == "__main__":
    main()
