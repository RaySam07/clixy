"""Aim each mark precisely before the buddy draws it.

The first answer is written for the ear, with its shape tags added at the
end, and the coordinates in those tags are rough: a circle can come out
huge and centred on the wrong thing. So each mark gets a second, focused
look:

1. Zoom: crop the screen around the rough guess (generously, so the real
   thing is inside even when the guess is off), send the crop at full
   resolution, and ask only "where exactly is <label>? give its box".
2. If it isn't in the crop, ask the same about the whole screenshot.
3. If neither finds it, the mark is dropped: no mark beats a wrong one.

The lookups for a turn's marks run in parallel and use the same AI and key
as the answer (with Claude Haiku 5.5 about $0.0001-0.0002 each). Arrows and
written labels are kept as they are.

This module also builds the busy map: where the screenshot is full of
text and detail, so labels can be put somewhere calm, and how light it
is, so a mark's outline suits what's behind it.

Threading: called from the pipeline's worker thread.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from PIL import Image, ImageFilter

AIM_SYSTEM_PROMPT = (
    "you find things in screenshots and give their exact bounding box. "
    "reply with json only, no other words."
)

MAX_MARKS = 3
"""Marks aimed and drawn per answer, at most."""

_SEND_MAX = (1024, 768)
_BOX_RE = re.compile(r'"box"\s*:\s*(null|\[\s*-?\d+(?:\.\d+)?\s*(?:,\s*-?\d+(?:\.\d+)?\s*){3}\])')


def parse_box(text: str) -> tuple[float, float, float, float] | None | bool:
    """The box in a reply: a 4-tuple, None for an explicit "not found", or
    False when the reply can't be read."""
    match = _BOX_RE.search(text)
    if not match:
        return False
    if match.group(1) == "null":
        return None
    x1, y1, x2, y2 = json.loads(match.group(1))
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def crop_window(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    """The region to zoom into around a rough box, in the same pixels: three
    times its size and at least half the screen across, so the real thing
    is in it even when the guess was off, but still a real close-up."""
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    w = max(3 * (x2 - x1), 0.5 * width)
    h = max(3 * (y2 - y1), 0.5 * height, w * height / width * 0.75)
    w, h = min(w, width), min(h, height)
    left = max(0.0, min(cx - w / 2, width - w))
    top = max(0.0, min(cy - h / 2, height - h))
    return round(left), round(top), round(left + w), round(top + h)


def plausible(box, width: int, height: int) -> bool:
    """A found box is inside the image, not a speck, and not the whole
    image (which means the model couldn't tell)."""
    x1, y1, x2, y2 = box
    if x2 - x1 < 3 or y2 - y1 < 3:
        return False
    if x1 < -2 or y1 < -2 or x2 > width + 2 or y2 > height + 2:
        return False
    return (x2 - x1) * (y2 - y1) < 0.85 * width * height


def _ask_box(ai_client, image: Image.Image, label: str, context: str) -> tuple | None | bool:
    prompt = (
        f'find "{label}" in this image.{context}\n'
        'reply with only this json: {"box": [x1, y1, x2, y2]}: the tightest box around it, '
        "in this image's pixels, origin top-left. "
        'if it is not in this image, reply {"box": null}.'
    )
    text = ""
    with ai_client.ask_stream(
        images=[(image, f"image dimensions: {image.width}x{image.height} pixels")],
        transcript=prompt,
        history=[],
        system_prompt=AIM_SYSTEM_PROMPT,
        max_tokens=120,
    ) as stream:
        for delta in stream.text_deltas():
            text += delta
            if "]" in text or "null" in text:
                break
    return parse_box(text)


def _target_box(shape) -> tuple[tuple[float, float, float, float], str] | None:
    """A shape's rough box and what it's of, for the shapes we aim."""
    from annotations import Box, Circle, Highlight, Underline

    if isinstance(shape, Box):
        return (shape.x1, shape.y1, shape.x2, shape.y2), shape.label
    if isinstance(shape, Circle):
        return (shape.x - shape.r, shape.y - shape.r, shape.x + shape.r, shape.y + shape.r), shape.label
    if isinstance(shape, Highlight):
        return (shape.x1, shape.y1, shape.x2, shape.y2), shape.label
    if isinstance(shape, Underline):
        return (shape.x, shape.y - 16, shape.x + shape.w, shape.y + 2), shape.label
    return None


def _rebuild(shape, box):
    """The shape, moved onto the box that was found."""
    from annotations import Box, Circle, Highlight, Underline

    x1, y1, x2, y2 = (round(v) for v in box)
    if isinstance(shape, (Box, Circle)):
        return Box(x1, y1, x2, y2, shape.label)
    if isinstance(shape, Highlight):
        return Highlight(x1, y1, x2, y2, shape.label)
    if isinstance(shape, Underline):
        return Underline(x1, y2 + 1, x2 - x1, shape.label)
    return shape


def aim_one(ai_client, capture, shape, context: str = "", dbg=None):
    """Find one shape's target precisely, in screenshot pixels. Returns the
    moved shape, the shape as it was (couldn't check), or None (not there)."""
    found = _target_box(shape)
    if found is None:
        return shape
    rough, label = found
    if not label:
        return shape
    sw, sh = capture.image.width, capture.image.height
    full = getattr(capture, "full", None)
    src = full if full is not None else capture.image
    fx, fy = src.width / sw, src.height / sh

    # 1. A close-up around the rough guess, from the full-resolution capture.
    left, top, right, bottom = crop_window(rough, sw, sh)
    crop = src.crop((round(left * fx), round(top * fy), round(right * fx), round(bottom * fy)))
    crop.thumbnail(_SEND_MAX, Image.Resampling.LANCZOS)
    try:
        box = _ask_box(ai_client, crop, label, context)
    except Exception as exc:
        if dbg is not None:
            dbg.log(f"AIM: {label!r} close-up failed ({type(exc).__name__}): kept as is")
        return shape
    if box and plausible(box, crop.width, crop.height):
        kx, ky = (right - left) / crop.width, (bottom - top) / crop.height
        aimed = (left + box[0] * kx, top + box[1] * ky, left + box[2] * kx, top + box[3] * ky)
        if dbg is not None:
            dbg.log(f"AIM: {label!r} {tuple(round(v) for v in rough)} -> {tuple(round(v) for v in aimed)} (close-up)")
        return _rebuild(shape, aimed)

    # 2. Not in the close-up: look at the whole screen.
    try:
        box = _ask_box(ai_client, capture.image, label, context)
    except Exception:
        return shape
    if box and plausible(box, sw, sh):
        if dbg is not None:
            dbg.log(f"AIM: {label!r} {tuple(round(v) for v in rough)} -> {tuple(round(v) for v in box)} (whole screen)")
        return _rebuild(shape, box)
    if dbg is not None:
        dbg.log(f"AIM: {label!r} not found, mark dropped")
    return None if box is None else shape


def aim(ai_client, capture, shapes: list, *, question: str = "", dbg=None, timeout_s: float = 6.0) -> list:
    """Aim the turn's marks (screenshot pixels), in parallel. At most
    MAX_MARKS marks are kept; written labels ride along."""
    from annotations import Label

    marks = [s for s in shapes if not isinstance(s, Label)][:MAX_MARKS]
    labels = [s for s in shapes if isinstance(s, Label)][:1]
    if type(ai_client).__name__ == "OllamaClient":
        return marks + labels  # small local models can't box reliably
    context = f' the user asked: "{question.strip()[:160]}".' if question.strip() else ""
    import time
    deadline = time.monotonic() + timeout_s
    pool = ThreadPoolExecutor(max_workers=max(1, len(marks)))
    futures = [pool.submit(aim_one, ai_client, capture, s, context, dbg) for s in marks]
    aimed = []
    for shape, future in zip(marks, futures):
        try:
            aimed.append(future.result(timeout=max(0.0, deadline - time.monotonic())))
        except FutureTimeout:
            aimed.append(shape)  # too slow: draw the rough mark rather than wait
        except Exception:
            aimed.append(shape)
    pool.shutdown(wait=False, cancel_futures=True)
    return [s for s in aimed if s is not None] + labels


# --- Busy map: where the screen is full of detail ------------------------------

def busy_map(image: Image.Image, cols: int = 96, rows: int = 54):
    """How busy each part of the screenshot is (text, icons, edges), as a
    small grid for placing labels somewhere calm."""
    from drawing import BusyMap

    small = image.convert("L").resize((cols * 3, rows * 3), Image.Resampling.BILINEAR)
    edges = small.filter(ImageFilter.FIND_EDGES).resize((cols, rows), Image.Resampling.BOX)
    luma = small.resize((cols, rows), Image.Resampling.BOX)
    return BusyMap(edges.tobytes(), cols, rows, luma.tobytes())
