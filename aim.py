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
import math
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
    from annotations import GEOMETRY, Label

    geometry = [s for s in shapes if isinstance(s, GEOMETRY)][:MAX_GEOMETRY]
    marks = [s for s in shapes if not isinstance(s, (Label, *GEOMETRY))][:MAX_MARKS]
    labels = [s for s in shapes if isinstance(s, Label)][:1]
    if type(ai_client).__name__ == "OllamaClient":
        return marks + geometry + labels  # small local models can't box reliably
    context = f' the user asked: "{question.strip()[:160]}".' if question.strip() else ""
    import time
    deadline = time.monotonic() + timeout_s
    pool = ThreadPoolExecutor(max_workers=max(1, len(marks) + 1))
    futures = [pool.submit(aim_one, ai_client, capture, s, context, dbg) for s in marks]
    figure = pool.submit(aim_geometry, ai_client, capture, geometry, context, dbg) if geometry else None
    aimed = []
    for shape, future in zip(marks, futures):
        try:
            aimed.append(future.result(timeout=max(0.0, deadline - time.monotonic())))
        except FutureTimeout:
            aimed.append(shape)  # too slow: draw the rough mark rather than wait
        except Exception:
            aimed.append(shape)
    drawn_geometry = geometry
    if figure is not None:
        try:
            drawn_geometry = figure.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:
            drawn_geometry = geometry
    pool.shutdown(wait=False, cancel_futures=True)
    # Keep the order the answer gave them in; drop marks nobody could find.
    order = {id(s): i for i, s in enumerate(shapes)}
    pairs = list(zip(marks, aimed)) + list(zip(geometry, drawn_geometry))
    result = sorted(((order[id(o)], a) for o, a in pairs if a is not None), key=lambda p: p[0])
    return [a for _i, a in result] + labels


# --- Geometry: marks drawn exactly on a figure ------------------------------------

MAX_GEOMETRY = 4
"""Geometry marks per answer, at most (a quadrilateral's four angles)."""

GEOMETRY_PROMPT = (
    "these marks are planned on the figure in this close-up, in this image's pixels:\n{tags}\n"
    "correct each one so it sits exactly on the figure: a RING exactly on the circle's outline "
    "(its centre and radius), a LINE's two ends exactly on the points it joins, an ANGLE's first "
    "point exactly on the corner and its other two points on the two sides, a DOT exactly on its point. "
    "keep the order, the kinds and the labels. reply with only the corrected tags."
)


def _points(shape) -> list[tuple[float, float]]:
    from annotations import Angle, Dot, Line, Ring
    if isinstance(shape, Ring):
        return [(shape.x - shape.r, shape.y - shape.r), (shape.x + shape.r, shape.y + shape.r)]
    if isinstance(shape, Line):
        return [(shape.x1, shape.y1), (shape.x2, shape.y2)]
    if isinstance(shape, Angle):
        return [(shape.vx, shape.vy), (shape.ax, shape.ay), (shape.bx, shape.by)]
    if isinstance(shape, Dot):
        return [(shape.x, shape.y)]
    return []


def _moved(shape, f):
    """The shape with every point passed through ``f`` (and lengths scaled
    by ``f``'s scale, given as f.scale)."""
    from annotations import Angle, Dot, Line, Ring
    if isinstance(shape, Ring):
        x, y = f(shape.x, shape.y)
        return Ring(x, y, shape.r * f.scale, shape.label)
    if isinstance(shape, Line):
        return Line(*f(shape.x1, shape.y1), *f(shape.x2, shape.y2), shape.label)
    if isinstance(shape, Angle):
        return Angle(*f(shape.vx, shape.vy), *f(shape.ax, shape.ay), *f(shape.bx, shape.by), shape.label)
    if isinstance(shape, Dot):
        return Dot(*f(shape.x, shape.y), shape.label)
    return shape


def _tag(shape) -> str:
    from annotations import Angle, Line, Ring
    r = lambda v: int(round(v))  # noqa: E731
    if isinstance(shape, Ring):
        return f"[RING:{r(shape.x)},{r(shape.y)},{r(shape.r)}:{shape.label}]"
    if isinstance(shape, Line):
        return f"[LINE:{r(shape.x1)},{r(shape.y1)}->{r(shape.x2)},{r(shape.y2)}:{shape.label}]"
    if isinstance(shape, Angle):
        return (f"[ANGLE:{r(shape.vx)},{r(shape.vy)},{r(shape.ax)},{r(shape.ay)},"
                f"{r(shape.bx)},{r(shape.by)}:{shape.label}]")
    return f"[DOT:{r(shape.x)},{r(shape.y)}:{shape.label}]"


class _Map:
    """Screenshot pixels to a crop's pixels (or back), with its scale."""

    def __init__(self, ox: float, oy: float, k: float, inverse: bool = False) -> None:
        self.ox, self.oy, self.k, self.inverse = ox, oy, k, inverse
        self.scale = 1 / k if inverse else k

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        if self.inverse:
            return self.ox + x / self.k, self.oy + y / self.k
        return (x - self.ox) * self.k, (y - self.oy) * self.k


def figure_window(shapes: list, width: int, height: int) -> tuple[int, int, int, int]:
    """The region holding all the geometry, with room around it."""
    pts = [p for s in shapes for p in _points(s)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    l, t, r, b = min(xs), min(ys), max(xs), max(ys)
    pad = 0.3 * max(r - l, b - t) + 24
    l, t, r, b = l - pad, t - pad, r + pad, b + pad
    cx, cy = (l + r) / 2, (t + b) / 2
    w, h = max(r - l, 0.3 * width), max(b - t, 0.3 * height)
    w, h = min(w, width), min(h, height)
    left = max(0.0, min(cx - w / 2, width - w))
    top = max(0.0, min(cy - h / 2, height - h))
    return round(left), round(top), round(left + w), round(top + h)


def aim_geometry(ai_client, capture, shapes: list, context: str = "", dbg=None) -> list:
    """Put the geometry exactly on the figure: one close-up of the whole
    figure where the AI corrects every mark, then each traced circle is
    fitted to the circle in the image and lines, corners and points that
    belong to it are pinned to its real centre and edge."""
    from annotations import GEOMETRY, Ring, parse_annotations

    if not shapes:
        return []
    sw, sh = capture.image.width, capture.image.height
    full = getattr(capture, "full", None)
    src = full if full is not None else capture.image
    fx = src.width / sw
    left, top, right, bottom = figure_window(shapes, sw, sh)
    crop = src.crop((round(left * fx), round(top * fx), round(right * fx), round(bottom * fx)))
    crop.thumbnail(_SEND_MAX, Image.Resampling.LANCZOS)
    k = crop.width / max(1, right - left)
    to_crop, from_crop = _Map(left, top, k), _Map(left, top, k, inverse=True)

    aimed = shapes
    tags = "".join(_tag(_moved(s, to_crop)) for s in shapes)
    try:
        text = ""
        with ai_client.ask_stream(
            images=[(crop, f"image dimensions: {crop.width}x{crop.height} pixels")],
            transcript=GEOMETRY_PROMPT.format(tags=tags) + context,
            history=[],
            system_prompt=AIM_SYSTEM_PROMPT.replace("json only", "the tags only"),
            max_tokens=400,
        ) as stream:
            for delta in stream.text_deltas():
                text += delta
        _spoken, fixed = parse_annotations(text)
        fixed = [f for f in fixed if isinstance(f, GEOMETRY)]
        if len(fixed) == len(shapes) and all(type(a) is type(b) for a, b in zip(fixed, shapes)):
            aimed = [_relabel(_moved(f, from_crop), s.label) for f, s in zip(fixed, shapes)]
            if dbg is not None:
                dbg.log(f"AIM: geometry corrected in a close-up: {tags} -> {text.strip()[:300]}")
        elif dbg is not None:
            dbg.log(f"AIM: geometry close-up reply didn't match ({text.strip()[:200]!r}); kept as planned")
    except Exception as exc:
        if dbg is not None:
            dbg.log(f"AIM: geometry close-up failed ({type(exc).__name__}); kept as planned")

    # Fit each circle to the image itself, then pin what belongs to it.
    rings = []
    out = []
    for shape in aimed:
        if isinstance(shape, Ring):
            fit = fit_ring(src, shape.x * fx, shape.y * fx, shape.r * fx)
            if fit is not None:
                shape = Ring(fit[0] / fx, fit[1] / fx, fit[2] / fx, shape.label)
                if dbg is not None:
                    dbg.log(f"AIM: circle fitted at ({shape.x:.1f},{shape.y:.1f}) r={shape.r:.1f}")
            rings.append((shape.x, shape.y, shape.r))
        out.append(shape)
    return [snap_to_rings(s, rings) for s in out]


def _relabel(shape, label: str):
    from dataclasses import replace
    return replace(shape, label=label) if label else shape


def snap_to_rings(shape, rings: list[tuple[float, float, float]]):
    """Pin points that belong to a circle: near its centre onto the centre,
    near its edge onto the edge. So a radius really runs from the centre to
    the circle, and an angle at the centre sits on it."""
    from annotations import Ring
    if not rings or isinstance(shape, Ring):
        return shape

    def pin(x: float, y: float) -> tuple[float, float]:
        for cx, cy, r in rings:
            d = math.hypot(x - cx, y - cy)
            if d < max(4.0, 0.12 * r):
                return cx, cy
            if d > 0 and abs(d - r) < max(4.0, 0.1 * r):
                return cx + (x - cx) * r / d, cy + (y - cy) * r / d
        return x, y

    pin.scale = 1.0
    return _moved(shape, pin)


def fit_ring(image: Image.Image, cx: float, cy: float, r: float, *, seed: int = 0):
    """Find the circle drawn near (cx, cy, r) in the image itself: edge
    pixels near the guessed outline, a robust three-point vote, then a least
    squares fit. Returns (cx, cy, r) in image pixels, or None when the
    image has no clear circle there (fewer than half its outline found)."""
    import numpy as np

    if r < 6:
        return None
    pad = 1.45 * r
    box = (max(0, int(cx - pad)), max(0, int(cy - pad)),
           min(image.width, int(cx + pad) + 1), min(image.height, int(cy + pad) + 1))
    if box[2] - box[0] < 12 or box[3] - box[1] < 12:
        return None
    crop = image.crop(box).convert("L")
    scale = min(1.0, 640 / max(crop.size))
    if scale < 1.0:
        crop = crop.resize((max(8, round(crop.width * scale)), max(8, round(crop.height * scale))),
                           Image.Resampling.BILINEAR)
    g = np.asarray(crop, dtype=np.float32)
    mag = np.abs(g[:-1, 1:] - g[:-1, :-1]) + np.abs(g[1:, :-1] - g[:-1, :-1])
    threshold = max(24.0, float(np.percentile(mag, 90)))
    ys, xs = np.nonzero(mag > threshold)
    px, py = xs + 0.5, ys + 0.5
    c0x, c0y, r0 = (cx - box[0]) * scale, (cy - box[1]) * scale, r * scale
    keep = np.abs(np.hypot(px - c0x, py - c0y) - r0) < 0.35 * r0
    px, py = px[keep], py[keep]
    if px.size < 24:
        return None
    rng = np.random.default_rng(seed)
    if px.size > 4000:
        pick = rng.choice(px.size, 4000, replace=False)
        px, py = px[pick], py[pick]
    tol = max(1.5, 0.02 * r0)
    best_n, best = 0, None
    for _ in range(250):
        i, j, k = rng.choice(px.size, 3, replace=False)
        x1, y1, x2, y2, x3, y3 = px[i], py[i], px[j], py[j], px[k], py[k]
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-6:
            continue
        s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
        ux = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
        uy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
        rr = math.hypot(x1 - ux, y1 - uy)
        if abs(rr - r0) > 0.35 * r0 or math.hypot(ux - c0x, uy - c0y) > 0.35 * r0:
            continue
        inliers = np.abs(np.hypot(px - ux, py - uy) - rr) < tol
        n = int(inliers.sum())
        if n > best_n:
            best_n, best = n, inliers
    if best is None:
        return None
    x, y = px[best], py[best]
    a = np.column_stack([x, y, np.ones_like(x)])
    sol = np.linalg.lstsq(a, x * x + y * y, rcond=None)[0]
    ux, uy = sol[0] / 2, sol[1] / 2
    rr = math.sqrt(max(0.0, sol[2] + ux * ux + uy * uy))
    angles = np.arctan2(y - uy, x - ux)
    covered = np.unique(((angles + math.pi) / (2 * math.pi) * 36).astype(int) % 36).size
    if covered < 18 or abs(rr - r0) > 0.3 * r0:
        return None
    return float(box[0] + ux / scale), float(box[1] + uy / scale), float(rr / scale)


# --- Busy map: where the screen is full of detail ------------------------------

def busy_map(image: Image.Image, cols: int = 96, rows: int = 54):
    """How busy each part of the screenshot is (text, icons, edges), as a
    small grid for placing labels somewhere calm."""
    from drawing import BusyMap

    small = image.convert("L").resize((cols * 3, rows * 3), Image.Resampling.BILINEAR)
    edges = small.filter(ImageFilter.FIND_EDGES).resize((cols, rows), Image.Resampling.BOX)
    luma = small.resize((cols, rows), Image.Resampling.BOX)
    return BusyMap(edges.tobytes(), cols, rows, luma.tobytes())
