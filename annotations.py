"""Teaching-annotation tag grammar for draw-on-screen mode.

The vision model appends shape tags to its spoken answer: Farza's exact
native pattern (dimension-labeled screenshot + [SHAPE:coords] tags, NO
grid-locator). This module strips the tags (so TTS never reads coordinates
aloud) and returns shape objects in screenshot-pixel space. Model-agnostic:
Claude (natively accurate at coordinates) and GPT-4o (selectable) both emit
the same grammar.

Grammar (coords are integer screenshot pixels, origin top-left):
    [BOX:x1,y1,x2,y2:label] loop hugging an element's box, optional :label
    [CIRCLE:x,y,r:label]    circle center (x,y) radius r, optional :label
    [HIGHLIGHT:x1,y1,x2,y2:text] highlighter stroke over text
    [UNDERLINE:x,y,w:text]  horizontal underline at (x,y), width w
    [ARROW:x1,y1->x2,y2]    arrow from (x1,y1) to (x2,y2)
    [LABEL:x,y:text]        floating text at (x,y)

Geometry, drawn exactly on a figure rather than around it:
    [RING:cx,cy,r:label]    trace a circle's outline (circumference)
    [LINE:x1,y1->x2,y2:label]  a straight segment (radius, side, height)
    [ANGLE:vx,vy,ax,ay,bx,by:label]  the angle at vertex v between the
                            sides toward a and b, e.g. "60°"
    [DOT:x,y:label]         a point, e.g. the centre O

The same `[SHAPE:coords]` text-tag + regex pattern the `[POINT:x,y]` cursor
already uses (ai.parse_point_tag): extending the cursor to shapes is just
more tag types.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Arrow:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class Circle:
    x: int
    y: int
    r: int
    label: str = ""


@dataclass(frozen=True)
class Underline:
    x: int
    y: int
    w: int
    label: str = ""


@dataclass(frozen=True)
class Label:
    x: int
    y: int
    text: str


@dataclass(frozen=True)
class Box:
    """An element's box, (x1, y1) top-left to (x2, y2) bottom-right."""
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""


@dataclass(frozen=True)
class Ring:
    """A circle in a figure, traced exactly on its outline."""
    x: int
    y: int
    r: int
    label: str = ""


@dataclass(frozen=True)
class Line:
    """A straight segment between two exact points."""
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""


@dataclass(frozen=True)
class Angle:
    """The angle at vertex (vx, vy) between the sides toward a and b."""
    vx: int
    vy: int
    ax: int
    ay: int
    bx: int
    by: int
    label: str = ""


@dataclass(frozen=True)
class Dot:
    """A point in a figure."""
    x: int
    y: int
    label: str = ""


GEOMETRY = (Ring, Line, Angle, Dot)
"""Marks drawn exactly on a figure (aimed and fitted, never clamped)."""


@dataclass(frozen=True)
class Highlight:
    """Text to mark like a highlighter, (x1, y1) top-left to (x2, y2)."""
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""


# All shape regexes are case-INSENSITIVE + tolerate whitespace after '['.
# The annotation prompt asks for all-lowercase prose, so the model may well
# emit `[circle:...]` instead of `[CIRCLE:...]`: both must parse AND strip,
# or lowercase coordinates would leak to TTS (the never-speak-coords invariant).
# The `\s*` BEFORE each colon matters too: a model could emit `[circle : ...]`
# with a space before the colon; that variant must also parse AND strip so
# coordinates never reach TTS (the never-speak-coords invariant).
_ARROW_RE = re.compile(r"\[\s*ARROW\s*:\s*(\d+)\s*,\s*(\d+)\s*->\s*(\d+)\s*,\s*(\d+)\s*\]", re.IGNORECASE)
_CIRCLE_RE = re.compile(r"\[\s*CIRCLE\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE)
_UNDERLINE_RE = re.compile(r"\[\s*UNDERLINE\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE)
_LABEL_RE = re.compile(r"\[\s*LABEL\s*:\s*(\d+)\s*,\s*(\d+)\s*:([^\]]*)\]", re.IGNORECASE)
_BOX_RE = re.compile(
    r"\[\s*BOX\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE
)
_HIGHLIGHT_RE = re.compile(
    r"\[\s*HIGHLIGHT\s*:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?::([^\]]*))?\]", re.IGNORECASE
)

# Strips any COMPLETE shape tag from the spoken text. Narrow to the shape
# keywords so we never eat unrelated bracketed text.
_N = r"\s*(-?\d+)\s*"
_LABEL_TAIL = r"(?::([^\]]*))?\]"
_RING_RE = re.compile(r"\[\s*RING\s*:" + _N + "," + _N + "," + _N + _LABEL_TAIL, re.IGNORECASE)
_LINE_RE = re.compile(r"\[\s*LINE\s*:" + _N + "," + _N + "->" + _N + "," + _N + _LABEL_TAIL, re.IGNORECASE)
_ANGLE_RE = re.compile(r"\[\s*ANGLE\s*:" + ",".join([_N] * 6) + _LABEL_TAIL, re.IGNORECASE)
_DOT_RE = re.compile(r"\[\s*DOT\s*:" + _N + "," + _N + _LABEL_TAIL, re.IGNORECASE)

_KEYWORDS = r"(?:ARROW|CIRCLE|UNDERLINE|LABEL|BOX|HIGHLIGHT|RING|LINE|ANGLE|DOT)"
_ANY_TAG_RE = re.compile(r"\[\s*" + _KEYWORDS + r"\s*:[^\]]*\]", re.IGNORECASE)

# Fail-closed strip: removes an UNTERMINATED shape tag (and everything after
# it), e.g. a truncated `look here [CIRCLE:120,40,15` with no closing `]`.
# Without this, a malformed/truncated tag would survive into the spoken text
# and TTS would read the coordinates aloud, violating the hard invariant that
# coordinates are never spoken. Matches the pipeline's "stop at the first '['"
# streaming guard. DOTALL so it eats across newlines to end-of-string.
_UNTERMINATED_TAG_RE = re.compile(r"\[\s*" + _KEYWORDS + r"\s*:.*$", re.IGNORECASE | re.DOTALL)


_DRAW_WORDS = re.compile(
    r"\b(draw|drawing|annotate|annotation|mark|label|highlight|underline|circle|sketch|trace|"
    r"point out|show me|radius|radii|diameter|circumference|angles?|triangle|geometry|perimeter|"
    r"tangent|chord|arc|vertex|vertices|hypotenuse|diagram|graph)\b",
    re.IGNORECASE,
)


def wants_drawing(question: str) -> bool:
    """Does the question ask to be shown on screen (draw, mark, the radius,
    the angles...)? Then Clicky draws even with drawing switched off."""
    return bool(_DRAW_WORDS.search(question or ""))


def parse_annotations(text: str) -> tuple[str, list]:
    """Return ``(spoken_text_with_tags_stripped, [Annotation, ...])``.

    Annotations are returned in their order of appearance so the overlay can
    render them in a sensible sequence. Malformed tags are dropped silently:
    a half-formed tag must never crash the pipeline mid-response.
    """
    found: list[tuple[int, object]] = []

    for m in _ARROW_RE.finditer(text):
        found.append((m.start(), Arrow(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
        )))
    for m in _CIRCLE_RE.finditer(text):
        found.append((m.start(), Circle(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            (m.group(4) or "").strip(),
        )))
    for m in _UNDERLINE_RE.finditer(text):
        found.append((m.start(), Underline(
            int(m.group(1)), int(m.group(2)), int(m.group(3)), (m.group(4) or "").strip(),
        )))
    for m in _LABEL_RE.finditer(text):
        found.append((m.start(), Label(
            int(m.group(1)), int(m.group(2)), m.group(3).strip(),
        )))

    for m in _BOX_RE.finditer(text):
        x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
        found.append((m.start(), Box(
            min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), (m.group(5) or "").strip(),
        )))
    for m in _HIGHLIGHT_RE.finditer(text):
        x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
        found.append((m.start(), Highlight(
            min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), (m.group(5) or "").strip(),
        )))

    for m in _RING_RE.finditer(text):
        found.append((m.start(), Ring(int(m.group(1)), int(m.group(2)), abs(int(m.group(3))),
                                      (m.group(4) or "").strip())))
    for m in _LINE_RE.finditer(text):
        found.append((m.start(), Line(*(int(m.group(i)) for i in range(1, 5)), (m.group(5) or "").strip())))
    for m in _ANGLE_RE.finditer(text):
        found.append((m.start(), Angle(*(int(m.group(i)) for i in range(1, 7)), (m.group(7) or "").strip())))
    for m in _DOT_RE.finditer(text):
        found.append((m.start(), Dot(int(m.group(1)), int(m.group(2)), (m.group(3) or "").strip())))

    found.sort(key=lambda pair: pair[0])
    annotations = [ann for _, ann in found]

    # Strip complete tags, then fail-closed-strip any unterminated tag tail so
    # a truncated `[CIRCLE:120,40,15` can never be spoken aloud.
    spoken = _ANY_TAG_RE.sub("", text)
    spoken = _UNTERMINATED_TAG_RE.sub("", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken, annotations
