"""Real 3D models for note cards: smooth solids described as JSON, built into meshes.

A model is a list of parts. A part is one or more shapes (sphere, ellipsoid, box,
rod, cylinder, cone, torus, tube, lathe, extrude, helix, gear) joined smoothly or
cut from each other, then optionally hollowed, roughened, mirrored, repeated round
an axis or cut open. Each part becomes a signed distance field, then a smooth
triangle mesh (surface nets on a narrow band, normals from the field, ambient
occlusion from the whole model) that render3d.py draws on the GPU.

The AI writes the JSON (``{"type": "part", ...}`` items in a sketch note) or picks a
ready-made model from models3d/ (``{"type": "model", "name": "heart"}``). It never
writes code: every field is read, checked and clamped here. docs/3d-models.md is
the language in full.

Coordinates: z is up. Units are whatever the model likes; it is scaled to fit.
Building takes a second or two, so it runs off the Qt thread; meshes are cached
in ~/.clixy/cache/3d by the model's JSON.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

LIBRARY = Path(__file__).with_name("models3d")
CACHE = Path.home() / ".clixy" / "cache" / "3d"
CACHE_VERSION = 5
MAX_PARTS = 60
MAX_SHAPES = 240          # all parts together
MAX_PART_SHAPES = 64      # in one part
F = np.float32

# Colours that read as real materials, not crayons. Any "#rrggbb" works too.
COLORS = {
    "red": "#c23b32", "crimson": "#a51d2d", "blood": "#8e1c1f", "muscle": "#a83a33", "artery": "#c7372f",
    "flesh": "#d98d7c", "pink": "#e9a3a6", "rose": "#d7707b", "salmon": "#ec8f78",
    "blue": "#3a66c4", "vein": "#4d5ea8", "navy": "#1f3a72", "sky": "#86bde8", "cyan": "#38b6d4",
    "teal": "#2a9b8e", "green": "#3f9a4b", "leaf": "#4f8c2c", "lime": "#9cc63e", "olive": "#7d8a3a",
    "yellow": "#f0c43a", "gold": "#d8aa3c", "orange": "#e27c2c", "amber": "#e6a23a", "fat": "#efd27e",
    "brown": "#7b4b2b", "tan": "#c8a27a", "wood": "#a3713f", "sand": "#dbc48f", "skin": "#e3b59b",
    "purple": "#7a4cb2", "violet": "#9361d4", "magenta": "#c2408f", "lilac": "#b9a0de",
    "white": "#f1efe9", "ivory": "#f3ecd9", "bone": "#e8dcc2", "cream": "#f2e6c9",
    "grey": "#8b8e92", "gray": "#8b8e92", "silver": "#c9cdd3", "steel": "#9da4ab", "iron": "#5f6368",
    "black": "#2a2a2d", "charcoal": "#3b3c40", "copper": "#c26b3e", "brass": "#cfa64b", "bronze": "#a8743d",
    "rock": "#7f776d", "stone": "#9a9389", "lava": "#ff5a1f", "magma": "#ff7a2a", "ice": "#d4ecf7",
    "water": "#3b8ad8", "glass": "#dcecf2", "ink": "#2b2b2b", "coral": "#ee6c55", "mint": "#8fd6b4",
}

# Materials: roughness, metal, subsurface (soft light through flesh), clearcoat (a wet or
# lacquered layer), bump (fine surface grain), emission, opacity. render3d.py shades them.
MATERIALS = {
    "flesh":   dict(rough=0.42, metal=0.0, sss=0.75, coat=0.55, bump=0.35, emit=0.0, opacity=1.0),
    "muscle":  dict(rough=0.45, metal=0.0, sss=0.65, coat=0.45, bump=0.55, emit=0.0, opacity=1.0),
    "skin":    dict(rough=0.55, metal=0.0, sss=0.6, coat=0.15, bump=0.3, emit=0.0, opacity=1.0),
    "bone":    dict(rough=0.62, metal=0.0, sss=0.3, coat=0.05, bump=0.45, emit=0.0, opacity=1.0),
    "tooth":   dict(rough=0.25, metal=0.0, sss=0.35, coat=0.6, bump=0.1, emit=0.0, opacity=1.0),
    "plastic": dict(rough=0.32, metal=0.0, sss=0.05, coat=0.35, bump=0.0, emit=0.0, opacity=1.0),
    "glossy":  dict(rough=0.16, metal=0.0, sss=0.05, coat=0.85, bump=0.0, emit=0.0, opacity=1.0),
    "matte":   dict(rough=0.78, metal=0.0, sss=0.05, coat=0.0, bump=0.1, emit=0.0, opacity=1.0),
    "rubber":  dict(rough=0.88, metal=0.0, sss=0.0, coat=0.0, bump=0.15, emit=0.0, opacity=1.0),
    "fabric":  dict(rough=0.95, metal=0.0, sss=0.15, coat=0.0, bump=0.4, emit=0.0, opacity=1.0),
    "ceramic": dict(rough=0.2, metal=0.0, sss=0.1, coat=0.9, bump=0.0, emit=0.0, opacity=1.0),
    "wood":    dict(rough=0.6, metal=0.0, sss=0.1, coat=0.15, bump=0.35, emit=0.0, opacity=1.0),
    "rock":    dict(rough=0.86, metal=0.0, sss=0.0, coat=0.0, bump=0.9, emit=0.0, opacity=1.0),
    "soil":    dict(rough=0.95, metal=0.0, sss=0.0, coat=0.0, bump=0.8, emit=0.0, opacity=1.0),
    "leaf":    dict(rough=0.45, metal=0.0, sss=0.55, coat=0.35, bump=0.15, emit=0.0, opacity=1.0),
    "metal":   dict(rough=0.3, metal=1.0, sss=0.0, coat=0.0, bump=0.0, emit=0.0, opacity=1.0),
    "steel":   dict(rough=0.28, metal=1.0, sss=0.0, coat=0.0, bump=0.0, emit=0.0, opacity=1.0),
    "brushed": dict(rough=0.45, metal=1.0, sss=0.0, coat=0.0, bump=0.05, emit=0.0, opacity=1.0),
    "chrome":  dict(rough=0.07, metal=1.0, sss=0.0, coat=0.0, bump=0.0, emit=0.0, opacity=1.0),
    "gold":    dict(rough=0.22, metal=1.0, sss=0.0, coat=0.0, bump=0.0, emit=0.0, opacity=1.0),
    "copper":  dict(rough=0.28, metal=1.0, sss=0.0, coat=0.0, bump=0.0, emit=0.0, opacity=1.0),
    "brass":   dict(rough=0.26, metal=1.0, sss=0.0, coat=0.0, bump=0.0, emit=0.0, opacity=1.0),
    "glass":   dict(rough=0.04, metal=0.0, sss=0.0, coat=1.0, bump=0.0, emit=0.0, opacity=0.22),
    "water":   dict(rough=0.06, metal=0.0, sss=0.2, coat=1.0, bump=0.1, emit=0.0, opacity=0.45),
    "jelly":   dict(rough=0.2, metal=0.0, sss=0.8, coat=0.8, bump=0.1, emit=0.0, opacity=0.6),
    "glow":    dict(rough=0.5, metal=0.0, sss=0.0, coat=0.0, bump=0.0, emit=1.6, opacity=1.0),
    "lava":    dict(rough=0.7, metal=0.0, sss=0.0, coat=0.0, bump=0.7, emit=1.2, opacity=1.0),
}
MATERIAL_COLORS = {"steel": "steel", "metal": "steel", "brushed": "silver", "chrome": "silver", "gold": "gold",
                   "copper": "copper", "brass": "brass", "glass": "glass", "water": "water", "bone": "bone",
                   "tooth": "ivory", "rock": "rock", "wood": "wood", "lava": "lava", "leaf": "leaf",
                   "skin": "skin", "flesh": "flesh", "muscle": "muscle", "soil": "brown"}


# --- Reading the JSON safely -----------------------------------------------------------------

def _f(value, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return min(hi, max(lo, v))


def _v3(value, default=(0.0, 0.0, 0.0), lim: float = 1e4) -> np.ndarray:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            v = np.array([float(x) for x in value], dtype=np.float64)
            if np.all(np.isfinite(v)):
                return np.clip(v, -lim, lim)
        except (TypeError, ValueError):
            pass
    return np.array(default, dtype=np.float64)


def _pts(value, dims: int, cap: int = 200) -> np.ndarray | None:
    if not isinstance(value, (list, tuple)):
        return None
    out = []
    for p in value[:cap]:
        if isinstance(p, (list, tuple)) and len(p) == dims:
            try:
                q = [float(x) for x in p]
            except (TypeError, ValueError):
                continue
            if all(math.isfinite(x) for x in q):
                out.append([min(1e4, max(-1e4, x)) for x in q])
    return np.array(out, dtype=np.float64) if out else None


def color_rgb(value, default: str = "#c9c4bb") -> np.ndarray:
    """A colour name or #rrggbb as sRGB 0..1."""
    text = value.strip().lower() if isinstance(value, str) else ""
    text = COLORS.get(text, text)
    if not (isinstance(text, str) and len(text) == 7 and text.startswith("#")):
        text = COLORS.get(default, default)
    try:
        return np.array([int(text[i:i + 2], 16) / 255.0 for i in (1, 3, 5)], dtype=np.float64)
    except ValueError:
        return np.array([0.79, 0.77, 0.73])


def _rot(degrees) -> np.ndarray:
    """Turn by x, y, z degrees (x first)."""
    rx, ry, rz = (math.radians(a) for a in _v3(degrees, lim=3600))
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def _frame_to(axis: np.ndarray) -> np.ndarray:
    """A rotation taking +z to ``axis``."""
    z = axis / (np.linalg.norm(axis) or 1.0)
    helper = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(helper, z)
    x /= np.linalg.norm(x)
    return np.column_stack([x, np.cross(z, x), z])


# --- Distance functions (numpy, float32, after Inigo Quilez) ---------------------------------

def _len(a, axis=1):
    return np.sqrt(np.einsum("ij,ij->i", a, a)) if axis == 1 else np.sqrt((a * a).sum(axis))


def smin(a, b, k):
    if k <= 0:
        return np.minimum(a, b)
    h = np.maximum(k - np.abs(a - b), 0.0) / k
    return np.minimum(a, b) - h * h * (k * 0.25)


def smax(a, b, k):
    return -smin(-a, -b, k)


def sd_round_cone(P, a, b, r1, r2):
    ba = (b - a).astype(F)
    l2 = float(ba @ ba)
    if l2 < 1e-12:
        return _len(P - a.astype(F)) - max(r1, r2)
    rr = r1 - r2
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    pa = P - a.astype(F)
    y = pa @ ba
    z = y - l2
    q = pa * l2 - y[:, None] * ba
    x2 = np.einsum("ij,ij->i", q, q)
    y2 = y * y * l2
    z2 = z * z * l2
    k = math.copysign(1.0, rr) * rr * rr * x2
    if a2 <= 1e-9:     # one end swallows the other: a sphere
        big = (a, r1) if r1 >= r2 else (b, r2)
        return _len(P - big[0].astype(F)) - big[1]
    d_end = np.sqrt(x2 + z2) * il2 - r2
    d_start = np.sqrt(x2 + y2) * il2 - r1
    d_side = (np.sqrt(np.maximum(x2 * a2 * il2, 0.0)) + y * rr) * il2 - r1
    return np.where(np.sign(z) * a2 * z2 > k, d_end, np.where(np.sign(y) * a2 * y2 < k, d_start, d_side))


def sd_capped_cone(P, a, b, ra, rb):
    """A cylinder (ra == rb) or truncated cone from a (radius ra) to b (radius rb), flat ends."""
    rba = rb - ra
    ab = (b - a).astype(F)
    baba = float(ab @ ab)
    if baba < 1e-12:
        return _len(P - a.astype(F)) - max(ra, rb)
    pa = P - a.astype(F)
    papa = np.einsum("ij,ij->i", pa, pa)
    paba = (pa @ ab) / baba
    x = np.sqrt(np.maximum(papa - paba * paba * baba, 0.0))
    cax = np.maximum(0.0, x - np.where(paba < 0.5, ra, rb))
    cay = np.abs(paba - 0.5) - 0.5
    kk = rba * rba + baba
    f = np.clip((rba * (x - ra) + paba * baba) / kk, 0.0, 1.0)
    cbx = x - ra - f * rba
    cby = paba - f
    s = np.where((cbx < 0) & (cay < 0), -1.0, 1.0)
    return s * np.sqrt(np.minimum(cax * cax + cay * cay * baba, cbx * cbx + cby * cby * baba))


def sd_round_box(L, half, r):
    q = np.abs(L) - (half - r).astype(F)
    outside = _len(np.maximum(q, 0.0))
    inside = np.minimum(np.max(q, axis=1), 0.0)
    return outside + inside - r


def sd_ellipsoid(L, radii):
    r = radii.astype(F)
    k0 = _len(L / r)
    k1 = _len(L / (r * r))
    return k0 * (k0 - 1.0) / np.maximum(k1, 1e-9)


def sd_polygon(px, py, vx, vy):
    """Signed distance to a closed 2D polygon (negative inside)."""
    d = (px - vx[0]) ** 2 + (py - vy[0]) ** 2
    s = np.ones_like(px)
    n = len(vx)
    for i in range(n):
        j = i - 1
        ex, ey = vx[j] - vx[i], vy[j] - vy[i]
        wx, wy = px - vx[i], py - vy[i]
        ee = ex * ex + ey * ey
        t = np.clip((wx * ex + wy * ey) / ee, 0.0, 1.0) if ee > 1e-18 else np.zeros_like(px)
        bx, by = wx - ex * t, wy - ey * t
        d = np.minimum(d, bx * bx + by * by)
        c1 = py >= vy[i]
        c2 = py < vy[j]
        c3 = ex * wy > ey * wx
        flip = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
        s = np.where(flip, -s, s)
    return s * np.sqrt(d)


def polygon_table(vx, vy, cells: int = 448):
    """The distance to a 2D outline, worked out once on a fine table and then looked up
    (bilinear): a lathe's profile or a gear's outline costs the same however many points
    it has. Beyond the table, the distance to its edge is added (never too small)."""
    lo = np.array([vx.min(), vy.min()], dtype=np.float64)
    hi = np.array([vx.max(), vy.max()], dtype=np.float64)
    span = float(np.max(hi - lo)) or 1.0
    lo, hi = lo - 0.3 * span, hi + 0.3 * span
    n = np.maximum(16, np.ceil((hi - lo) / ((hi - lo).max() / cells))).astype(int)
    xs, ys = np.linspace(lo[0], hi[0], n[0]), np.linspace(lo[1], hi[1], n[1])
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    table = sd_polygon(gx.ravel().astype(F), gy.ravel().astype(F), vx, vy).reshape(n[0], n[1]).astype(F)
    step = ((hi - lo) / (n - 1)).astype(F)
    lo32, hi32 = lo.astype(F), hi.astype(F)

    def fn(px, py):
        cx, cy = np.clip(px, lo32[0], hi32[0]), np.clip(py, lo32[1], hi32[1])
        u, v = (cx - lo32[0]) / step[0], (cy - lo32[1]) / step[1]
        i, j = np.minimum(u.astype(np.int64), n[0] - 2), np.minimum(v.astype(np.int64), n[1] - 2)
        fu, fv = u - i, v - j
        d = ((table[i, j] * (1 - fu) + table[i + 1, j] * fu) * (1 - fv)
             + (table[i, j + 1] * (1 - fu) + table[i + 1, j + 1] * fu) * fv)
        return d + np.sqrt((px - cx) ** 2 + (py - cy) ** 2)
    return fn


def catmull_rom(pts: np.ndarray, per: int = 8, closed: bool = False) -> np.ndarray:
    """A smooth curve through the points."""
    if len(pts) < 3:
        if len(pts) == 2 and per > 1:
            t = np.linspace(0, 1, per + 1)[:, None]
            return pts[0] * (1 - t) + pts[1] * t
        return pts
    p = np.vstack([pts[-1:], pts, pts[:2]]) if closed else np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])
    segs = len(pts) if closed else len(pts) - 1
    t = np.linspace(0, 1, per, endpoint=False)[:, None]
    out = []
    for i in range(segs):
        p0, p1, p2, p3 = p[i], p[i + 1], p[i + 2], p[i + 3]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    if not closed:
        out.append(pts[-1:])
    return np.vstack(out)


_LATTICE = np.random.default_rng(1307).uniform(-1.0, 1.0, 64 ** 3).astype(F)   # the same bumps every time


def value_noise(P: np.ndarray) -> np.ndarray:
    """Smooth 3D noise in -1..1 (a random lattice that repeats every 64 units, cubic fade)."""
    i = np.floor(P).astype(np.int64)
    f = (P - i).astype(F)
    u = f * f * (3 - 2 * f)
    ix, iy, iz = i[:, 0] & 63, i[:, 1] & 63, i[:, 2] & 63
    jx, jy, jz = (ix + 1) & 63, (iy + 1) & 63, (iz + 1) & 63

    def h(dx, dy, dz):
        return _LATTICE[((jx if dx else ix) * 64 + (jy if dy else iy)) * 64 + (jz if dz else iz)]

    x0 = h(0, 0, 0) * (1 - u[:, 0]) + h(1, 0, 0) * u[:, 0]
    x1 = h(0, 1, 0) * (1 - u[:, 0]) + h(1, 1, 0) * u[:, 0]
    x2 = h(0, 0, 1) * (1 - u[:, 0]) + h(1, 0, 1) * u[:, 0]
    x3 = h(0, 1, 1) * (1 - u[:, 0]) + h(1, 1, 1) * u[:, 0]
    y0 = x0 * (1 - u[:, 1]) + x1 * u[:, 1]
    y1 = x2 * (1 - u[:, 1]) + x3 * u[:, 1]
    return y0 * (1 - u[:, 2]) + y1 * u[:, 2]


# --- Shapes -------------------------------------------------------------------------------

@dataclass
class Shape:
    kind: str
    fn: object                 # P (n,3) float32 in the part's frame -> distance (n,)
    lo: np.ndarray
    hi: np.ndarray
    op: str = "add"            # add | subtract | intersect
    k: float | None = None     # smoothness of the join with what came before
    color: np.ndarray | None = None


def _local(at: np.ndarray, R: np.ndarray):
    at32, R32 = at.astype(F), R.astype(F)
    return lambda P: (P - at32) @ R32


def _box_bounds(lo, hi, at, R):
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    w = corners @ R.T + at
    return w.min(0), w.max(0)


def _tube_fn(path: np.ndarray, radii: np.ndarray):
    segs = []
    for i in range(len(path) - 1):
        a, b, r1, r2 = path[i], path[i + 1], float(radii[i]), float(radii[i + 1])
        segs.append((a, b, r1, r2, ((a + b) / 2).astype(F), float(np.linalg.norm(b - a)) / 2 + max(r1, r2)))

    def fn(P):
        d = None
        for a, b, r1, r2, c, reach in segs:
            if d is None:
                d = sd_round_cone(P, a, b, r1, r2)
                continue
            # Only where this piece could be nearer than the nearest so far.
            near = np.flatnonzero(_len(P - c) - reach < d)
            if len(near) == len(P):
                d = np.minimum(d, sd_round_cone(P, a, b, r1, r2))
            elif len(near):
                d[near] = np.minimum(d[near], sd_round_cone(P[near], a, b, r1, r2))
        return d
    return fn


def _gear_outline(r: float, teeth: int) -> np.ndarray:
    depth = min(0.25 * r, 2.2 * r / teeth)
    out = []
    for k in range(teeth):
        a0 = 2 * math.pi * k / teeth
        step = 2 * math.pi / teeth
        for frac, rad in ((0.0, r - depth * 0.6), (0.18, r - depth * 0.6), (0.3, r + depth * 0.45),
                          (0.5, r + depth * 0.5), (0.7, r + depth * 0.45), (0.82, r - depth * 0.6)):
            a = a0 + frac * step
            out.append((rad * math.cos(a), rad * math.sin(a)))
    return np.array(out)


def make_shape(spec: dict, scale_hint: float = 1.0) -> Shape | None:
    """One shape from its JSON. None when it can't be read."""
    if not isinstance(spec, dict):
        return None
    kind = str(spec.get("shape") or spec.get("type") or "").lower()
    at = _v3(spec.get("at"))
    R = _rot(spec.get("turn"))
    if spec.get("axis") is not None and spec.get("turn") is None:   # "axis": [x, y, z] points local z there
        axis = _v3(spec.get("axis"), (0, 0, 1))
        if np.linalg.norm(axis) > 1e-9:
            R = _frame_to(axis)
    big = 1e3
    loc = _local(at, R)
    shape = None
    if kind == "sphere":
        r = _f(spec.get("r", spec.get("radius")), 0.5, 1e-4, big)
        c = at.astype(F)
        shape = Shape(kind, lambda P: _len(P - c) - r, at - r, at + r)
    elif kind in ("ellipsoid", "egg", "blob"):
        size = _v3(spec.get("size"), (0.6, 0.45, 0.35))
        size = np.clip(np.abs(size), 1e-4, big)
        lo, hi = _box_bounds(-size, size, at, R)
        shape = Shape(kind, lambda P: sd_ellipsoid(loc(P), size), lo, hi)
    elif kind in ("box", "cube", "slab"):
        size = np.clip(np.abs(_v3(spec.get("size"), (1, 1, 1))), 1e-4, big)
        half = size / 2
        rr = min(_f(spec.get("round"), 0.0, 0.0, big), float(half.min()) * 0.98)
        lo, hi = _box_bounds(-half, half, at, R)
        shape = Shape(kind, lambda P: sd_round_box(loc(P), half, rr), lo, hi)
    elif kind in ("rod", "capsule", "limb", "bone", "cylinder", "cone", "disc", "disk"):
        r = _f(spec.get("r", spec.get("radius")), 0.1, 1e-4, big)
        if "from" in spec or "to" in spec:
            a, b = _v3(spec.get("from")), _v3(spec.get("to"), (0, 0, 1))
        else:
            h = _f(spec.get("height", spec.get("length", spec.get("h"))), 1.0 if kind not in ("disc", "disk") else 0.1,
                   1e-4, big)
            axis = R @ np.array([0, 0, 1.0])
            a, b = at - axis * h / 2, at + axis * h / 2
        if kind == "cone":
            r2 = _f(spec.get("r2"), 0.0, 0.0, big)
        else:
            r2 = _f(spec.get("r2"), r, 0.0, big)
        rmax = max(r, r2)
        lo, hi = np.minimum(a, b) - rmax, np.maximum(a, b) + rmax
        if kind in ("rod", "capsule", "limb", "bone"):
            shape = Shape(kind, lambda P: sd_round_cone(P, a, b, r, r2), lo, hi)
        else:
            rr = _f(spec.get("round"), 0.0, 0.0, big)
            length = float(np.linalg.norm(b - a))
            rr = min(rr, 0.45 * length, 0.9 * max(min(r, r2), 1e-4))
            if rr > 0:
                axis = (b - a) / (length or 1.0)
                a2, b2 = a + axis * rr, b - axis * rr
                shape = Shape(kind, lambda P: sd_capped_cone(P, a2, b2, max(r - rr, 1e-5), max(r2 - rr, 1e-5)) - rr,
                              lo, hi)
            else:
                shape = Shape(kind, lambda P: sd_capped_cone(P, a, b, r, r2), lo, hi)
    elif kind in ("torus", "ring", "donut"):
        big_r = _f(spec.get("r", spec.get("radius")), 0.5, 1e-4, big)
        small = _f(spec.get("tube", spec.get("thickness")), 0.1, 1e-4, big)

        def fn(P, big_r=big_r, small=small):
            L = loc(P)
            q = np.sqrt(L[:, 0] ** 2 + L[:, 1] ** 2) - big_r
            return np.sqrt(q * q + L[:, 2] ** 2) - small
        e = big_r + small
        lo, hi = _box_bounds(np.array([-e, -e, -small]), np.array([e, e, small]), at, R)
        shape = Shape(kind, fn, lo, hi)
    elif kind in ("tube", "vessel", "path", "helix", "spring", "coil"):
        if kind in ("helix", "spring", "coil"):
            hr = _f(spec.get("r", spec.get("radius")), 0.4, 1e-4, big)
            pitch = _f(spec.get("pitch"), 0.3, 1e-4, big)
            turns = _f(spec.get("turns"), 3.0, 0.1, 40.0)
            n = int(min(400, max(12, turns * 24)))
            a = np.linspace(0, 2 * math.pi * turns, n)
            local = np.column_stack([hr * np.cos(a), hr * np.sin(a), pitch * a / (2 * math.pi) - pitch * turns / 2])
            path = local @ R.T + at
            radii = np.full(len(path), _f(spec.get("tube", spec.get("thickness")), 0.05, 1e-4, big))
        else:
            pts = _pts(spec.get("points"), 3)
            if pts is None or len(pts) < 2:
                return None
            rv = spec.get("r", spec.get("radius", 0.1))
            if isinstance(rv, (list, tuple)) and rv:
                rs = np.array([_f(x, 0.1, 1e-4, big) for x in rv][: len(pts)])
                rs = np.concatenate([rs, np.full(len(pts) - len(rs), rs[-1])])
            else:
                rs = np.full(len(pts), _f(rv, 0.1, 1e-4, big))
            seg = float(np.median(rs)) * 0.9 or 0.05
            length = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
            per = int(min(24, max(2, length / max(seg, 1e-3) / max(1, len(pts) - 1))))
            closed = bool(spec.get("loop"))
            path = catmull_rom(pts, per, closed)
            if closed:
                path = np.vstack([path, path[:1]])
                radii = np.interp(np.linspace(0, len(rs), len(path)), np.arange(len(rs) + 1), np.append(rs, rs[0]))
            else:
                radii = np.interp(np.linspace(0, len(rs) - 1, len(path)), np.arange(len(rs)), rs)
            while len(path) > 140:   # keep it quick: a long tube gets fewer, longer segments
                path, radii = path[::2], radii[::2]
                if not np.allclose(path[-1], pts[-1]):
                    path, radii = np.vstack([path, pts[-1:]]), np.append(radii, rs[-1])
        rmax = float(radii.max())
        shape = Shape(kind, _tube_fn(path, radii), path.min(0) - rmax, path.max(0) + rmax)
    elif kind in ("lathe", "revolve", "vase"):
        prof = _pts(spec.get("profile"), 2)
        if prof is None or len(prof) < 2:
            return None
        prof[:, 0] = np.abs(prof[:, 0])
        curve = catmull_rom(prof, 10) if spec.get("smooth", True) else prof
        curve[:, 0] = np.maximum(curve[:, 0], 0.0)
        poly = np.vstack([[0.0, curve[0, 1]], curve, [0.0, curve[-1, 1]]])
        profile = polygon_table(poly[:, 0].astype(F), poly[:, 1].astype(F))

        def fn(P, profile=profile):
            L = loc(P)
            rho = np.sqrt(L[:, 0] ** 2 + L[:, 1] ** 2)
            return profile(rho, L[:, 2])
        e = float(curve[:, 0].max())
        lo, hi = _box_bounds(np.array([-e, -e, curve[:, 1].min()]), np.array([e, e, curve[:, 1].max()]), at, R)
        shape = Shape(kind, fn, lo, hi)
    elif kind in ("extrude", "plate", "gear", "cog"):
        if kind in ("gear", "cog"):
            gr = _f(spec.get("r", spec.get("radius")), 0.5, 1e-3, big)
            teeth = int(_f(spec.get("teeth"), 16, 5, 80))
            outline = _gear_outline(gr, teeth)
            height = _f(spec.get("thickness", spec.get("height")), gr * 0.25, 1e-4, big)
            rr = _f(spec.get("round"), min(height, gr) * 0.06, 0.0, big)
            hole = _f(spec.get("hole"), gr * 0.18, 0.0, gr * 0.8)
        else:
            outline = _pts(spec.get("outline"), 2)
            if outline is None or len(outline) < 3:
                return None
            if spec.get("smooth"):
                outline = catmull_rom(outline, 6, closed=True)
            height = _f(spec.get("height", spec.get("thickness")), 0.2, 1e-4, big)
            rr = _f(spec.get("round"), 0.0, 0.0, big)
            hole = 0.0
        rr = min(rr, height * 0.45)
        flat = polygon_table(outline[:, 0].astype(F), outline[:, 1].astype(F))

        def fn(P, flat=flat, height=height, rr=rr, hole=hole):
            L = loc(P)
            d2 = flat(L[:, 0], L[:, 1]) + rr
            if hole > 0:
                d2 = np.maximum(d2, hole + rr - np.sqrt(L[:, 0] ** 2 + L[:, 1] ** 2))
            wz = np.abs(L[:, 2]) - (height / 2 - rr)
            return np.minimum(np.maximum(d2, wz), 0.0) + np.sqrt(np.maximum(d2, 0) ** 2 + np.maximum(wz, 0) ** 2) - rr
        lo2, hi2 = outline.min(0), outline.max(0)
        lo, hi = _box_bounds(np.array([lo2[0], lo2[1], -height / 2]), np.array([hi2[0], hi2[1], height / 2]), at, R)
        shape = Shape(kind, fn, lo, hi)
    if shape is None:
        return None
    if spec.get("subtract") or spec.get("op") == "subtract":
        shape.op = "subtract"
    elif spec.get("intersect") or spec.get("op") == "intersect":
        shape.op = "intersect"
    if spec.get("blend") is not None:
        shape.k = _f(spec.get("blend"), 0.0, 0.0, 10.0) * scale_hint
    if spec.get("color") is not None:
        shape.color = color_rgb(spec.get("color"))
    return shape


# --- Parts ---------------------------------------------------------------------------------

_AXES = {"x": 0, "y": 1, "z": 2}


@dataclass
class Motion:
    kind: str                       # spin | swing | slide | beat
    axis: np.ndarray = field(default_factory=lambda: np.array([0, 0, 1.0]))
    at: np.ndarray = field(default_factory=lambda: np.zeros(3))
    speed: float = 30.0             # spin: degrees a second; others: cycles a second
    amount: float = 0.0             # swing: degrees; slide: distance; beat: fraction
    phase: float = 0.0              # 0..1 of a cycle

    def matrix(self, t: float) -> np.ndarray:
        """This motion at time t, as a 4x4 matrix in model space."""
        m = np.eye(4)
        if self.kind == "spin":
            ang = math.radians(self.speed * t)
        elif self.kind == "swing":
            ang = math.radians(self.amount) * math.sin(2 * math.pi * (self.speed * t + self.phase))
        else:
            ang = None
        if ang is not None:
            R = _axis_angle(self.axis, ang)
            m[:3, :3] = R
            m[:3, 3] = self.at - R @ self.at
        elif self.kind == "slide":
            m[:3, 3] = self.axis * self.amount * math.sin(2 * math.pi * (self.speed * t + self.phase))
        elif self.kind == "beat":
            u = math.sin(2 * math.pi * (self.speed * t + self.phase))
            s = 1.0 + self.amount * max(0.0, u) ** 2 - self.amount * 0.35 * max(0.0, -u)
            m[:3, :3] *= s
            m[:3, 3] = self.at - s * self.at
        return m


def _axis_angle(axis: np.ndarray, ang: float) -> np.ndarray:
    a = axis / (np.linalg.norm(axis) or 1.0)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * (K @ K)


def _motion(spec, centre: np.ndarray) -> Motion | None:
    if not isinstance(spec, dict):
        return None
    for kind in ("spin", "swing", "slide", "beat"):
        if kind in spec:
            break
    else:
        return None
    m = Motion(kind)
    val = spec.get(kind)
    if kind in ("spin", "swing", "slide"):
        if isinstance(val, str) and val.lower() in _AXES:
            m.axis = np.eye(3)[_AXES[val.lower()]]
        else:
            m.axis = _v3(val, (0, 0, 1))
        n = np.linalg.norm(m.axis)
        m.axis = m.axis / n if n > 1e-9 else np.array([0, 0, 1.0])
    m.at = _v3(spec.get("about", spec.get("at")), tuple(centre))
    if kind == "spin":
        m.speed = _f(spec.get("speed"), 40.0, -720, 720)
    else:
        m.speed = _f(spec.get("speed", spec.get("rate")), 0.6, 0.01, 4.0)
        m.amount = _f(spec.get("amount", val if isinstance(val, (int, float)) else None),
                      {"swing": 20.0, "slide": 0.3, "beat": 0.06}[kind], -1e3, 1e3)
        if kind == "beat":
            m.amount = min(0.25, abs(m.amount))
    m.phase = _f(spec.get("phase"), 0.0, -10, 10)
    return m


class Part:
    """One part of a model: shapes joined into one solid, with its look and motion."""

    def __init__(self, spec: dict, unit: float = 1.0) -> None:
        self.spec = spec
        self.label = str(spec.get("label") or "")[:48]
        self.name = str(spec.get("name") or self.label)[:48]
        material = str(spec.get("material") or "plastic").lower()
        self.material = material if material in MATERIALS else "plastic"
        self.color = color_rgb(spec.get("color"), MATERIAL_COLORS.get(self.material, "#c9c4bb"))
        inside = spec.get("inside")
        self.inside = color_rgb(inside) if inside is not None else None
        self.opacity = _f(spec.get("opacity"), MATERIALS[self.material]["opacity"], 0.05, 1.0)
        self.k = _f(spec.get("blend"), 0.0, 0.0, 10.0)
        self.anchor = _v3(spec.get("anchor")) if spec.get("anchor") is not None else None
        # More labels on the same part, each pointing at a spot (in the part's frame).
        # ("tip": the name sits right beside the spot, like an arrow's or an axis's name.)
        self.marks: list[tuple[str, np.ndarray, bool]] = []
        for m in spec.get("labels") or [] if isinstance(spec.get("labels"), list) else []:
            if isinstance(m, dict) and str(m.get("text") or "").strip() and m.get("at") is not None:
                self.marks.append((str(m["text"])[:48], _v3(m.get("at")), bool(m.get("tip"))))
        self.at = _v3(spec.get("at"))
        self.R = _rot(spec.get("turn"))
        self.scale = _f(spec.get("scale"), 1.0, 1e-3, 1e3)
        mirror = spec.get("mirror")
        mirror = [mirror] if isinstance(mirror, str) else mirror if isinstance(mirror, list) else []
        self.mirror = sorted({_AXES[m.lower()] for m in mirror if isinstance(m, str) and m.lower() in _AXES})
        rep = spec.get("repeat")
        self.repeat = None
        if isinstance(rep, dict):
            n = int(_f(rep.get("count"), 0, 0, 64))
            ax = str(rep.get("axis") or "z").lower()
            if n >= 2:
                self.repeat = (n, _AXES.get(ax, 2))
        elif isinstance(rep, (int, float)) and rep >= 2:
            self.repeat = (int(min(64, rep)), 2)
        bumps = spec.get("bumps")
        self.bumps = None
        if isinstance(bumps, dict):
            self.bumps = (_f(bumps.get("size"), 0.2, 1e-3, 1e3), _f(bumps.get("depth"), 0.02, 0.0, 1e2),
                          str(bumps.get("style") or "smooth").lower())
        elif isinstance(bumps, (int, float)) and bumps > 0:
            self.bumps = (0.25, float(min(bumps, 1.0)) * 0.05, "smooth")
        self.shell = _f(spec.get("shell"), 0.0, 0.0, 1e2)
        self.cuts: list[tuple[np.ndarray, np.ndarray]] = []
        for c in (spec.get("cut") if isinstance(spec.get("cut"), list) else [spec.get("cut")]):
            if isinstance(c, dict):
                n = _v3(c.get("normal"), (0, -1, 0))
                if np.linalg.norm(n) > 1e-9:
                    self.cuts.append((n / np.linalg.norm(n), _v3(c.get("at"))))
        self.uncut = bool(spec.get("uncut"))
        self.dim = bool(spec.get("dim"))
        self.shapes: list[Shape] = []
        raw = spec.get("shapes")
        raw = raw if isinstance(raw, list) else [spec] if spec.get("shape") else []
        for s in raw[:MAX_PART_SHAPES]:
            shape = make_shape(s)
            if shape is not None:
                self.shapes.append(shape)
        if self.shapes and self.shapes[0].op != "add":
            self.shapes = []
        self.sheet = _sheet(spec["_sheet"]) if isinstance(spec.get("_sheet"), dict) else None
        if self.sheet is not None and not spec.get("_sheet", {}).get("ramp"):
            self.sheet = (self.sheet[0], self.sheet[1], np.tile(self.color.astype(F), (len(self.sheet[0]), 1)))
        self.motion = None
        self._motion_spec = spec.get("move")
        self.world_cuts: list[tuple[np.ndarray, np.ndarray]] = []

    # The part's own frame (at, turn, scale, mirror, repeat) from model space.
    def _domain(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        Q = ((P - self.at.astype(F)) @ self.R.astype(F)) / F(self.scale)
        raw = Q
        if self.mirror:
            Q = Q.copy()
            for i in self.mirror:
                Q[:, i] = np.abs(Q[:, i])
        if self.repeat:
            n, ax = self.repeat
            i, j = [(1, 2), (2, 0), (0, 1)][ax]
            ang = np.arctan2(Q[:, j], Q[:, i])
            sector = 2 * math.pi / n
            a = np.mod(ang + sector / 2, sector) - sector / 2
            rho = np.sqrt(Q[:, i] ** 2 + Q[:, j] ** 2)
            Q = Q.copy()
            Q[:, i], Q[:, j] = rho * np.cos(a), rho * np.sin(a)
        return Q.astype(F), raw

    margin: float | None = None    # set while meshing: how far from a shape its exact distance matters

    def _dist(self, s: Shape, Q: np.ndarray, k: float) -> np.ndarray:
        """A shape's distance; far from its box (where it can't change the surface) just the
        distance to the box, which is quick and never more than the true distance."""
        if self.margin is None or len(Q) < 2048:
            return s.fn(Q)
        reach = self.margin + k
        lo, hi = (s.lo - reach).astype(F), (s.hi + reach).astype(F)
        near = np.all((Q >= lo) & (Q <= hi), axis=1)
        if near.all():
            return s.fn(Q)
        gap = np.maximum(np.maximum(s.lo.astype(F) - Q, Q - s.hi.astype(F)), 0)
        out = _len(gap).astype(F)
        if near.any():
            out[near] = s.fn(Q[near])
        return out

    def sdf(self, P: np.ndarray, with_cut: bool = True) -> np.ndarray:
        Q, raw = self._domain(P)
        d = None
        for s in self.shapes:
            k = self.k if s.k is None else s.k
            ds = self._dist(s, Q, k)
            if d is None:
                d = ds
            elif s.op == "add":
                d = smin(d, ds, k)
            elif s.op == "subtract":
                d = smax(d, -ds, k)
            else:
                d = smax(d, ds, k)
        d = d * F(self.scale)
        if self.shell > 0:
            d = np.abs(d) - F(self.shell / 2)
        if self.bumps:
            size, depth, style = self.bumps
            # Only near the surface: further out the bumps can't change which side a point is on.
            near = np.flatnonzero(np.abs(d) < F(max(6 * depth, 1e-6)))
            if len(near):
                q = raw[near]
                n = value_noise(q * F(self.scale / size) + 17.3) + 0.5 * value_noise(q * F(2.03 * self.scale / size))
                n = n / 1.5
                if style == "ridged":      # folds, like a brain or a walnut
                    n = 1.0 - 2.0 * np.abs(n)
                d = d.copy()
                d[near] += F(depth) * n
        if with_cut:
            for nrm, at in self.cuts:          # in the part's own frame
                d = np.maximum(d, ((raw - at.astype(F)) @ nrm.astype(F)) * F(self.scale))
            for nrm, at in self.world_cuts:    # the model's cutaway
                d = np.maximum(d, (P - at.astype(F)) @ nrm.astype(F))
        return d.astype(F)

    def cut_mask(self, P: np.ndarray, eps: float) -> np.ndarray:
        """Points that lie on a cut face (the inside showing)."""
        if not (self.cuts or self.world_cuts):
            return np.zeros(len(P), bool)
        whole = self.sdf(P, with_cut=False)
        _q, raw = self._domain(P)
        out = np.zeros(len(P), bool)
        planes = [((raw - at.astype(F)) @ nrm.astype(F)) * F(self.scale) for nrm, at in self.cuts]
        planes += [(P - at.astype(F)) @ nrm.astype(F) for nrm, at in self.world_cuts]
        for plane in planes:
            out |= (np.abs(plane) < eps) & (whole < -eps * 0.5)
        return out

    def colors(self, P: np.ndarray) -> np.ndarray:
        """Each point's colour: the colour of whichever shape makes the surface there,
        blended where shapes melt together (followed through the same joins as the shape)."""
        base = np.tile(self.color.astype(F), (len(P), 1))
        if all(s.color is None for s in self.shapes):
            return base
        Q, _raw = self._domain(P)
        d = None
        col = base
        for s in self.shapes:
            ds = s.fn(Q)
            k = (self.k if s.k is None else s.k) * 0.6     # colours change a little more crisply than shapes
            if d is None:
                d = ds
                if s.color is not None:
                    col = np.tile(s.color.astype(F), (len(P), 1))
                continue
            if s.op == "add":
                w = np.clip(0.5 + 0.5 * (d - ds) / k, 0, 1) if k > 0 else (ds < d).astype(F)
                d = smin(d, ds, k / 0.6)
                c = s.color if s.color is not None else self.color
            elif s.op == "subtract":
                w = np.clip(0.5 + 0.5 * (-ds - d) / k, 0, 1) if k > 0 else (-ds > d).astype(F)
                d = smax(d, -ds, k / 0.6)
                c = s.color
            else:
                w = np.clip(0.5 + 0.5 * (ds - d) / k, 0, 1) if k > 0 else (ds > d).astype(F)
                d = smax(d, ds, k / 0.6)
                c = s.color
            if c is not None:
                col = col + (c.astype(F) - col) * w[:, None].astype(F)
        return col.astype(F)

    def to_model(self, p: np.ndarray) -> np.ndarray:
        """A point in the part's frame, in model space."""
        return self.at + self.scale * (self.R @ np.asarray(p, float))

    def bounds(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self.sheet is not None:
            return self.sheet[0].min(0).astype(float), self.sheet[0].max(0).astype(float)
        adds = [s for s in self.shapes if s.op == "add"]
        if not adds:
            return None
        lo = np.min([s.lo for s in adds], axis=0)
        hi = np.max([s.hi for s in adds], axis=0)
        if self.mirror:
            for i in self.mirror:
                m = max(abs(lo[i]), abs(hi[i]))
                lo[i], hi[i] = -m, m
        if self.repeat:
            _n, ax = self.repeat
            i, j = [(1, 2), (2, 0), (0, 1)][ax]
            rho = max(abs(lo[i]), abs(hi[i]), abs(lo[j]), abs(hi[j]))
            rho = math.hypot(rho, rho)
            lo[i], hi[i], lo[j], hi[j] = -rho, rho, -rho, rho
        lo, hi = lo * self.scale, hi * self.scale
        lo, hi = _box_bounds(lo, hi, self.at, self.R)
        pad = self.k * self.scale + (self.bumps[1] if self.bumps else 0.0) + self.shell
        return lo - pad, hi + pad


# --- Meshing: surface nets on a narrow band --------------------------------------------------

@dataclass
class Mesh:
    part: Part
    pos: np.ndarray            # (n,3) float32, model space
    nrm: np.ndarray            # (n,3)
    col: np.ndarray            # (n,3) sRGB 0..1
    ao: np.ndarray             # (n,)
    cut: np.ndarray            # (n,) 1 on cut faces
    tri: np.ndarray            # (m,3) uint32
    centre: np.ndarray = field(default_factory=lambda: np.zeros(3))
    grid: tuple | None = None  # (D, origin, h) for occlusion lookups, dropped after building


_CORNERS = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]])
_EDGES = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]


def _field(part: Part, lo: np.ndarray, h: float, dims: tuple[int, int, int]) -> np.ndarray:
    """The part's distance at every grid point: exactly near the surface, roughly elsewhere."""
    nx, ny, nz = dims
    step = 4
    cx, cy, cz = (nx + step - 1) // step + 1, (ny + step - 1) // step + 1, (nz + step - 1) // step + 1
    hc = h * step
    gi = np.stack(np.meshgrid(np.arange(cx), np.arange(cy), np.arange(cz), indexing="ij"), -1).reshape(-1, 3)
    coarse = part.sdf((lo + gi * hc).astype(F)).reshape(cx, cy, cz)
    # Fine grid from the coarse one (each fine point takes its nearest coarse value)...
    idx = [np.minimum((np.arange(n) + step // 2) // step, c - 1) for n, c in ((nx, cx), (ny, cy), (nz, cz))]
    D = coarse[np.ix_(*idx)].astype(F)
    # ...then exact values in every coarse cell the surface may pass through.
    near_c = np.minimum.reduce([np.abs(coarse[i:cx - 1 + i, j:cy - 1 + j, k:cz - 1 + k])
                                for i in (0, 1) for j in (0, 1) for k in (0, 1)]) < hc * 1.9
    cells = np.argwhere(near_c)
    if len(cells):
        off = np.stack(np.meshgrid(*(np.arange(step + 1),) * 3, indexing="ij"), -1).reshape(-1, 3)
        fine = (cells[:, None, :] * step + off[None]).reshape(-1, 3)
        fine = fine[(fine[:, 0] < nx) & (fine[:, 1] < ny) & (fine[:, 2] < nz)]
        mark = np.zeros(nx * ny * nz, bool)
        mark[fine[:, 0] * (ny * nz) + fine[:, 1] * nz + fine[:, 2]] = True
        lin = np.flatnonzero(mark)
        fi = np.column_stack([lin // (ny * nz), (lin // nz) % ny, lin % nz])
        for s in range(0, len(fi), 400_000):
            chunk = fi[s:s + 400_000]
            D[chunk[:, 0], chunk[:, 1], chunk[:, 2]] = part.sdf((lo + chunk * h).astype(F))
    return D


def surface_nets(D: np.ndarray, lo: np.ndarray, h: float) -> tuple[np.ndarray, np.ndarray]:
    """Vertices and triangles of the zero surface of a grid of distances."""
    nx, ny, nz = D.shape
    inside = D < 0
    s = inside.astype(np.uint8)
    count = sum(s[i:nx - 1 + i, j:ny - 1 + j, k:nz - 1 + k] for i in (0, 1) for j in (0, 1) for k in (0, 1))
    active = (count > 0) & (count < 8)
    cells = np.argwhere(active)
    if not len(cells):
        return np.zeros((0, 3), F), np.zeros((0, 3), np.uint32)
    vals = np.stack([D[cells[:, 0] + c[0], cells[:, 1] + c[1], cells[:, 2] + c[2]] for c in _CORNERS], 1)
    acc = np.zeros((len(cells), 3), np.float64)
    n = np.zeros(len(cells), np.float64)
    for a, b in _EDGES:
        da, db = vals[:, a], vals[:, b]
        cross = (da < 0) != (db < 0)
        t = np.where(cross, da / np.where(cross, da - db, 1.0), 0.0)
        p = _CORNERS[a] + (_CORNERS[b] - _CORNERS[a]) * t[:, None]
        acc += p * cross[:, None]
        n += cross
    verts = (lo + (cells + acc / np.maximum(n, 1)[:, None]) * h).astype(F)
    cy, cz = ny - 1, nz - 1
    key = cells[:, 0] * (cy * cz) + cells[:, 1] * cz + cells[:, 2]

    def vid(ci, cj, ck):
        return np.searchsorted(key, ci * (cy * cz) + cj * cz + ck)

    quads = []
    # Edges along x between (i,j,k) and (i+1,j,k): the four cells around it.
    e = np.argwhere(inside[:-1, 1:-1, 1:-1] != inside[1:, 1:-1, 1:-1])
    if len(e):
        i, j, k = e[:, 0], e[:, 1] + 1, e[:, 2] + 1
        q = np.stack([vid(i, j - 1, k - 1), vid(i, j, k - 1), vid(i, j, k), vid(i, j - 1, k)], 1)
        flip = ~inside[i, j, k]
        q[flip] = q[flip][:, ::-1]
        quads.append(q)
    e = np.argwhere(inside[1:-1, :-1, 1:-1] != inside[1:-1, 1:, 1:-1])
    if len(e):
        i, j, k = e[:, 0] + 1, e[:, 1], e[:, 2] + 1
        q = np.stack([vid(i - 1, j, k - 1), vid(i - 1, j, k), vid(i, j, k), vid(i, j, k - 1)], 1)
        flip = ~inside[i, j, k]
        q[flip] = q[flip][:, ::-1]
        quads.append(q)
    e = np.argwhere(inside[1:-1, 1:-1, :-1] != inside[1:-1, 1:-1, 1:])
    if len(e):
        i, j, k = e[:, 0] + 1, e[:, 1] + 1, e[:, 2]
        q = np.stack([vid(i - 1, j - 1, k), vid(i, j - 1, k), vid(i, j, k), vid(i - 1, j, k)], 1)
        flip = ~inside[i, j, k]
        q[flip] = q[flip][:, ::-1]
        quads.append(q)
    if not quads:
        return verts, np.zeros((0, 3), np.uint32)
    q = np.vstack(quads)
    # Split each quad along its shorter diagonal.
    d02 = np.einsum("ij,ij->i", verts[q[:, 0]] - verts[q[:, 2]], verts[q[:, 0]] - verts[q[:, 2]])
    d13 = np.einsum("ij,ij->i", verts[q[:, 1]] - verts[q[:, 3]], verts[q[:, 1]] - verts[q[:, 3]])
    a = d02 <= d13
    tri = np.vstack([np.column_stack([q[a, 0], q[a, 1], q[a, 2]]), np.column_stack([q[a, 0], q[a, 2], q[a, 3]]),
                     np.column_stack([q[~a, 0], q[~a, 1], q[~a, 3]]), np.column_stack([q[~a, 1], q[~a, 2], q[~a, 3]])])
    return verts, tri.astype(np.uint32)


def _sheet_mesh(part: Part) -> Mesh:
    verts, tri, col = part.sheet
    v0, v1, v2 = verts[tri[:, 0]], verts[tri[:, 1]], verts[tri[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    nrm = np.zeros_like(verts)
    for k in range(3):
        np.add.at(nrm, tri[:, k], fn)
    nrm /= np.maximum(_len(nrm), 1e-12)[:, None]
    return Mesh(part, verts, nrm.astype(F), col, np.ones(len(verts), F), np.zeros(len(verts), F), tri,
                centre=verts.mean(0).astype(np.float64))


def mesh_part(part: Part, h: float) -> Mesh | None:
    if part.sheet is not None:
        return _sheet_mesh(part)
    if 0 < part.shell < 2.5 * h:    # a very thin wall (glass, a vessel) gets a finer grid, or it breaks into steps
        h = max(part.shell / 2.5, h * 0.72)
    b = part.bounds()
    if b is None:
        return None
    lo, hi = b
    lo, hi = lo - 2.5 * h, hi + 2.5 * h
    dims = tuple(int(x) for x in np.ceil((hi - lo) / h).astype(int) + 1)
    while np.prod(dims, dtype=np.int64) > 14_000_000:   # never a runaway grid (memory, time)
        h *= 1.25
        dims = tuple(int(x) for x in np.ceil((hi - lo) / h).astype(int) + 1)
    part.margin = 4 * h * 2.5 / part.scale + (part.bumps[1] if part.bumps else 0.0) / part.scale + part.shell
    try:
        D = _field(part, lo, h, dims)
    finally:
        part.margin = None
    verts, tri = surface_nets(D, lo, h)
    if len(tri) == 0:
        return None
    # Normals from the field, then each vertex moved onto the surface.
    e = F(h * 0.35)
    nrm = np.zeros_like(verts)
    for i in range(3):
        dv = np.zeros(3, F)
        dv[i] = e
        nrm[:, i] = part.sdf(verts + dv) - part.sdf(verts - dv)
    nrm /= np.maximum(_len(nrm), 1e-9)[:, None]
    d = part.sdf(verts)
    verts = verts - nrm * np.clip(d, -h, h)[:, None]
    # Triangles facing the wrong way (rare, at thin spots) are turned round.
    fn = np.cross(verts[tri[:, 1]] - verts[tri[:, 0]], verts[tri[:, 2]] - verts[tri[:, 0]])
    wrong = np.einsum("ij,ij->i", fn, nrm[tri[:, 0]] + nrm[tri[:, 1]] + nrm[tri[:, 2]]) < 0
    tri[wrong] = tri[wrong][:, ::-1]
    cut = part.cut_mask(verts, h * 0.75).astype(F)
    col = part.colors(verts)
    if cut.any():
        inner = part.inside if part.inside is not None else None
        if inner is None:
            inner_col = np.clip(col * 0.82 + 0.16, 0, 1)
        else:
            inner_col = np.tile(inner.astype(F), (len(col), 1))
        col = np.where(cut[:, None] > 0, inner_col, col).astype(F)
    return Mesh(part, verts, nrm.astype(F), col, np.ones(len(verts), F), cut, tri,
                centre=verts.mean(0).astype(np.float64), grid=(D, lo, h))


def _lookup(meshes: list[Mesh], P: np.ndarray, skip: int | None = None) -> np.ndarray:
    """The whole model's distance at points (from each part's grid)."""
    out = np.full(len(P), 1e3, F)
    for k, m in enumerate(meshes):
        if m.grid is None or k == skip:
            continue
        D, lo, h = m.grid
        g = (P - lo) / h
        inside = np.all((g >= 0) & (g <= np.array(D.shape) - 1), axis=1)
        if not inside.any():
            continue
        vals = ndimage.map_coordinates(D, g[inside].T, order=1, mode="nearest")
        out[inside] = np.minimum(out[inside], vals)
    return out


def ambient_occlusion(meshes: list[Mesh], size: float) -> None:
    """How enclosed each vertex is by the model around it (dark in creases and gaps)."""
    steps = np.array([0.012, 0.03, 0.055, 0.085, 0.12]) * size
    for m in meshes:
        occ = np.zeros(len(m.pos), F)
        w = 1.0
        for s in steps:
            P = (m.pos + m.nrm * F(s)).astype(F)
            d = _lookup(meshes, P)
            occ += F(w) * np.maximum(F(s) - d, 0) / F(s)
            w *= 0.75
        m.ao = np.clip(1.0 - 0.42 * occ, 0.18, 1.0).astype(F)


# --- Models --------------------------------------------------------------------------------

def library_names() -> list[str]:
    return sorted(p.stem for p in LIBRARY.glob("*.json"))


def library_index() -> dict[str, dict]:
    """name -> {"title", "labels", "aliases"} for every ready-made model."""
    out = {}
    for path in LIBRARY.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        labels = [p.get("label") for p in data.get("parts", []) if isinstance(p, dict) and p.get("label")]
        labels += [m.get("text") for p in data.get("parts", []) if isinstance(p, dict)
                   for m in (p.get("labels") or []) if isinstance(m, dict) and m.get("text")]
        out[path.stem] = {"title": data.get("title", path.stem), "labels": labels,
                          "aliases": data.get("aliases", []), "cutaway": bool(data.get("cutaway"))}
    return out


def find_library(name: str) -> dict | None:
    if not isinstance(name, str) or not name.strip():
        return None
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    path = LIBRARY / f"{key}.json"
    if not path.exists():
        for stem, info in library_index().items():
            if key in [a.lower().replace(" ", "_").replace("-", "_") for a in info["aliases"]]:
                path = LIBRARY / f"{stem}.json"
                break
        else:
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


LEGACY_SOLIDS = ("sphere", "ellipsoid", "box3d", "box", "cylinder", "cone", "torus", "tube", "gear", "molecule",
                 "rod", "capsule", "helix", "lathe", "extrude")


ELEMENTS = {"H": ("hydrogen", "white", 0.25), "C": ("carbon", "charcoal", 0.36), "N": ("nitrogen", "blue", 0.35),
            "O": ("oxygen", "red", 0.34), "S": ("sulfur", "yellow", 0.42), "P": ("phosphorus", "orange", 0.42),
            "Cl": ("chlorine", "green", 0.4), "F": ("fluorine", "lime", 0.3), "Na": ("sodium", "violet", 0.45),
            "K": ("potassium", "purple", 0.5), "Ca": ("calcium", "grey", 0.48), "Fe": ("iron", "copper", 0.45),
            "Mg": ("magnesium", "green", 0.45), "Br": ("bromine", "brown", 0.44), "I": ("iodine", "purple", 0.48)}


def _molecule(item: dict) -> list[dict]:
    """A molecule: glossy atoms in element colours (named once each) and bonds (double, triple)."""
    atoms = []
    for a in (item.get("atoms") or [])[:60]:
        if isinstance(a, (list, tuple)) and len(a) >= 4 and isinstance(a[0], str):
            el, at = a[0].strip()[:2].capitalize(), list(a[1:4])
        elif isinstance(a, dict):
            el, at = str(a.get("element") or a.get("el") or "C").strip()[:2].capitalize(), a.get("at")
        else:
            continue
        p = _v3(at, (math.nan, 0, 0))
        if np.all(np.isfinite(p)):
            atoms.append((el, p))
    if not atoms:
        return []
    bonds = []
    for b in (item.get("bonds") or [])[:90]:
        if isinstance(b, (list, tuple)) and len(b) >= 2:
            try:
                i, j, order = int(b[0]), int(b[1]), int(b[2]) if len(b) > 2 else 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(atoms) and 0 <= j < len(atoms) and i != j:
                bonds.append((i, j, max(1, min(3, order))))
    pos = np.array([p for _e, p in atoms])
    if bonds:
        unit = float(np.median([np.linalg.norm(pos[i] - pos[j]) for i, j, _o in bonds])) / 1.1
    elif len(pos) > 1:
        dist = np.linalg.norm(pos[:, None] - pos[None], axis=2) + np.eye(len(pos)) * 1e9
        unit = float(np.median(dist.min(1))) / 1.1
    else:
        unit = 1.0
    unit = max(unit, 1e-3)
    out, named = [], set()
    for el, p in atoms:
        name, col, r = ELEMENTS.get(el, (el, "grey", 0.4))
        out.append({"label": name if name not in named else "", "color": col, "material": "glossy",
                    "shapes": [{"shape": "sphere", "at": list(p), "r": r * unit}]})
        named.add(name)
    if item.get("label") and out:
        out[0]["label"] = str(item["label"])
    for i, j, order in bonds:
        a, b = pos[i], pos[j]
        d = b - a
        side = np.cross(d, [0, 0, 1.0])
        if np.linalg.norm(side) < 1e-6:
            side = np.cross(d, [0, 1.0, 0])
        side = side / np.linalg.norm(side) * 0.12 * unit
        offsets = [0.0] if order == 1 else [-0.5, 0.5] if order == 2 else [-1.0, 0.0, 1.0]
        for o in offsets:
            out.append({"color": "silver", "material": "glossy",
                        "shapes": [{"shape": "rod", "from": list(a + side * o), "to": list(b + side * o),
                                    "r": (0.09 if order == 1 else 0.06) * unit}]})
    return out


def _legacy_move(item: dict) -> dict | None:
    """The older notes' "spin" (turns a second) and "slide" on the item itself."""
    if isinstance(item.get("move"), dict):
        return item["move"]
    axis = item.get("axis") if isinstance(item.get("axis"), list) else [0, 0, 1]
    if isinstance(item.get("spin"), (int, float)) and item["spin"]:
        return {"spin": axis, "speed": float(item["spin"]) * 360.0, "about": item.get("pivot") or item.get("at")}
    if isinstance(item.get("slide"), list) and len(item["slide"]) == 3:
        d = _v3(item["slide"])
        n = float(np.linalg.norm(d))
        if n > 1e-9:
            return {"slide": list(d / n), "amount": n / 2, "speed": _f(item.get("rate"), 1.0, 0.05, 4.0)}
    return None


def _legacy_part(item: dict) -> list[dict]:
    """The older sketch-note solid items as parts, so they get the real look too."""
    t = item.get("type")
    base = {k: item[k] for k in ("label", "color", "material", "opacity") if k in item}
    move = _legacy_move(item)
    if move:
        base["move"] = move
    if t == "box3d":
        size = item.get("size") or [item.get("w", 1), item.get("d", 1), item.get("h", 1)]
        return [{**base, "shapes": [{"shape": "box", "at": item.get("at"), "size": size,
                                     "turn": item.get("turn"), "round": item.get("round", 0.02)}]}]
    if t == "molecule":
        return _molecule(item)
    shape = dict(item)
    shape["shape"] = {"sphere": "sphere", "box": "box"}.get(t, t)
    if t == "tube" and "r" not in shape:
        shape["r"] = item.get("radius", 0.1)
    for k in ("label", "color", "material", "move", "opacity", "spin", "slide", "rate", "pivot"):
        shape.pop(k, None)
    if t in ("cylinder", "cone") and shape.get("hollow"):
        return [{**base, "shell": _f(item.get("r"), 0.5, 1e-3, 1e3) * 0.16, "shapes": [shape]}]
    return [{**base, "shapes": [shape]}]


# 3D maths: vectors, axes, points, lines, curves, surfaces, fields, planes, as real solids.
MATHS_ITEMS = ("axes3d", "vector3d", "arrow3d", "point3d", "line3d", "curve3d", "surface", "field", "plane")


def needs_painter(note: dict) -> bool:
    """A note only the painter's 3D (scene3d) can draw: its old "cutaway" wall, or parts
    linked to each other by id (a piston following its crank)."""
    for item in note.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").lower() == "cutaway":
            return True
        if isinstance(item.get("follow"), str) or isinstance(item.get("from"), str) or isinstance(item.get("to"), str):
            return True
    return False


def _coords(item: dict) -> list:
    out = []
    for k in ("at", "from", "to"):
        v = _v3(item.get(k), (math.nan, 0, 0))
        if np.all(np.isfinite(v)):
            out.append(v)
    pts = _pts(item.get("points"), 3)
    if pts is not None:
        out.extend(pts)
    if item.get("type") in ("surface", "field"):
        span = _f(item.get("range"), 2.0, 1e-3, 1e4)
        xr = item.get("x") if isinstance(item.get("x"), list) and len(item["x"]) == 2 else [-span, span]
        yr = item.get("y") if isinstance(item.get("y"), list) and len(item["y"]) == 2 else [-span, span]
        out += [np.array([_f(xr[0], -span, -1e4, 1e4), _f(yr[0], -span, -1e4, 1e4), 0.0]),
                np.array([_f(xr[1], span, -1e4, 1e4), _f(yr[1], span, -1e4, 1e4), 0.0])]
    return out


def _arrow(a, b, r: float, head: float) -> list[dict]:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    n = float(np.linalg.norm(d))
    if n < 1e-9:
        return [{"shape": "sphere", "at": list(a), "r": r * 2}]
    u = d / n
    hl = min(head * 2.6, n * 0.45)
    neck = b - u * hl
    return [{"shape": "rod", "from": list(a), "to": list(neck), "r": r},
            {"shape": "cone", "from": list(neck), "to": list(b), "r": head, "r2": 0.0, "round": head * 0.08}]


def _maths_parts(items: list[dict]) -> list[dict]:
    """Vectors, axes, points, lines, curves, surfaces, fields and planes, sized to the note."""
    pts = [c for i in items for c in _coords(i)]
    if pts:
        allp = np.array(pts)
        size = float(np.max(allp.max(0) - allp.min(0)))
        reach = float(np.max(np.abs(allp)))
    else:
        size = reach = 2.0
    size = max(size, 1e-3)
    reach = max(reach, size * 0.5)
    rod, head = 0.018 * size, 0.05 * size
    out = []
    try:
        from sketch import compile_expr
    except ImportError:          # model3d on its own (tests): formulas need sketch's evaluator
        compile_expr = None
    for item in items:
        t = str(item.get("type") or "").lower()
        label = str(item.get("label") or "")
        if t == "axes3d":
            names = item.get("labels") if isinstance(item.get("labels"), list) else ["x", "y", "z"]
            low = -reach * 0.3 if any((p < -1e-9).any() for p in pts) else 0.0
            for k in range(3):
                a, b = np.zeros(3), np.zeros(3)
                a[k], b[k] = low, reach * 1.15
                text = str(names[k]) if k < len(names) else "xyz"[k]
                out.append({"color": "charcoal", "material": "matte", "shapes": _arrow(a, b, rod * 0.55, head * 0.7),
                            "labels": [{"text": text, "at": list(b), "tip": True}] if text else []})
        elif t in ("vector3d", "arrow3d"):
            a, b = _v3(item.get("from")), _v3(item.get("to"), (1, 0, 0))
            out.append({"color": item.get("color", "blue"), "material": "glossy", "shapes": _arrow(a, b, rod, head),
                        "labels": [{"text": label, "at": list(b), "tip": True}] if label else []})
        elif t == "point3d":
            at = list(_v3(item.get("at")))
            out.append({"color": item.get("color", "red"), "material": "glossy",
                        "shapes": [{"shape": "sphere", "at": at, "r": 0.035 * size}],
                        "labels": [{"text": label, "at": at, "tip": True}] if label else []})
        elif t == "line3d":
            out.append({"label": label, "color": item.get("color", "grey"), "material": "matte",
                        "shapes": [{"shape": "rod", "from": list(_v3(item.get("from"))),
                                    "to": list(_v3(item.get("to"), (1, 0, 0))), "r": rod * 0.6}]})
        elif t == "curve3d" and compile_expr is not None:
            fx, fy, fz = (compile_expr(item.get(k, "0"), ("t",)) for k in ("x", "y", "z"))
            if None in (fx, fy, fz):
                continue
            t0, t1 = _f(item.get("from"), 0.0, -1e4, 1e4), _f(item.get("to"), 2 * math.pi, -1e4, 1e4)
            ts = np.linspace(t0, t1, 90)
            path = np.array([[fx(v), fy(v), fz(v)] for v in ts])
            path = path[np.all(np.isfinite(path), axis=1)]
            if len(path) < 2:
                continue
            closed = np.linalg.norm(path[0] - path[-1]) < size * 1e-3
            out.append({"label": label, "color": item.get("color", "teal"), "material": "glossy",
                        "shapes": [{"shape": "tube", "points": path[::2].tolist(), "r": rod * 1.2, "loop": bool(closed)}]})
        elif t == "surface" and compile_expr is not None:
            fn = compile_expr(item.get("expr", ""), ("x", "y"))
            if fn is None:
                continue
            out.append({"label": label, "color": item.get("color"), "material": item.get("material", "glossy"),
                        "_sheet": {"x": item.get("x"), "y": item.get("y"), "range": item.get("range"),
                                   "expr": item.get("expr", ""), "ramp": not item.get("color")}})
        elif t == "field" and compile_expr is not None:
            fx, fy, fz = (compile_expr(item.get(k, "0"), ("x", "y", "z")) for k in ("x", "y", "z"))
            if None in (fx, fy, fz):
                continue
            span = _f(item.get("range"), 2.0, 1e-3, 1e4)
            flat = str(item.get("z", "0")).strip() in ("0", "0.0")
            grid = np.linspace(-span, span, 4)
            zs = [0.0] if flat else np.linspace(-span, span, 3)
            shapes = []
            for x in grid:
                for y in grid:
                    for z in zs:
                        v = np.array([fx(x, y, z), fy(x, y, z), fz(x, y, z)])
                        n = float(np.linalg.norm(v))
                        if not np.isfinite(n) or n < 1e-9:
                            continue
                        a = np.array([x, y, z])
                        shapes += _arrow(a, a + v / n * span * 0.42, rod * 0.9, head * 0.75)
            if shapes:
                out.append({"label": label, "color": item.get("color", "teal"), "material": "glossy", "shapes": shapes[:120]})
        elif t == "plane":
            normal = _v3(item.get("normal"), (0, 0, 1))
            wh = item.get("size") if isinstance(item.get("size"), list) and len(item["size"]) == 2 else [size, size]
            out.append({"label": label, "color": item.get("color", "sky"), "material": "glass",
                        "shapes": [{"shape": "box", "at": list(_v3(item.get("at"))), "axis": list(normal),
                                    "size": [_f(wh[0], size, 1e-3, 1e4), _f(wh[1], size, 1e-3, 1e4), rod]}]})
    return out


_RAMP = np.array([[0.24, 0.38, 0.86], [0.18, 0.66, 0.74], [0.55, 0.80, 0.45], [0.98, 0.80, 0.30]])


def _sheet(spec: dict):
    """A surface z = f(x, y), sampled: (vertices, triangles, colours) or None."""
    try:
        from sketch import compile_expr
    except ImportError:
        return None
    fn = compile_expr(spec.get("expr", ""), ("x", "y"))
    if fn is None:
        return None
    span = _f(spec.get("range"), 2.0, 1e-3, 1e4)
    xr = spec.get("x") if isinstance(spec.get("x"), list) and len(spec["x"]) == 2 else [-span, span]
    yr = spec.get("y") if isinstance(spec.get("y"), list) and len(spec["y"]) == 2 else [-span, span]
    x0, x1 = _f(xr[0], -span, -1e4, 1e4), _f(xr[1], span, -1e4, 1e4)
    y0, y1 = _f(yr[0], -span, -1e4, 1e4), _f(yr[1], span, -1e4, 1e4)
    if x1 <= x0 or y1 <= y0:
        return None
    n = 72
    xs, ys = np.linspace(x0, x1, n), np.linspace(y0, y1, n)
    z = np.array([[fn(x, y) for x in xs] for y in ys], dtype=float)
    if not np.isfinite(z).any():
        return None
    cap = 2.5 * max(x1 - x0, y1 - y0)
    z = np.clip(np.nan_to_num(z, nan=float(np.nanmean(z))), -cap, cap)
    gx, gy = np.meshgrid(xs, ys)
    verts = np.column_stack([gx.ravel(), gy.ravel(), z.ravel()]).astype(F)
    i, j = np.meshgrid(np.arange(n - 1), np.arange(n - 1))
    a = (j * n + i).ravel()
    tri = np.vstack([np.column_stack([a, a + 1, a + n + 1]), np.column_stack([a, a + n + 1, a + n])]).astype(np.uint32)
    lo, hi = float(z.min()), float(z.max())
    u = np.clip((verts[:, 2] - lo) / (hi - lo), 0, 1) if hi > lo else np.full(len(verts), 0.5)
    pos = u * (len(_RAMP) - 1)
    k = np.minimum(pos.astype(int), len(_RAMP) - 2)
    ramp = (_RAMP[k] + (_RAMP[k + 1] - _RAMP[k]) * (pos - k)[:, None]).astype(F)
    return verts, tri, ramp


@dataclass
class Spec:
    parts: list[dict]
    captions: list[str]
    view: dict
    cut: list[dict]
    resolution: int = 190     # grid steps across the model's longest side
    arrows: list = field(default_factory=list)   # vector directions, so the view never looks down one


def spec_from_note(note: dict) -> Spec | None:
    """The parts a note describes: ready-made models, its own parts, older solid items."""
    items = [i for i in note.get("items") or [] if isinstance(i, dict)]
    parts: list[dict] = []
    captions: list[str] = []
    cut: list[dict] = []
    view = dict(note.get("view") or {})
    detail = int(_f(view.get("detail"), 190, 120, 260))
    lib_detail = []
    for item in items:
        t = str(item.get("type") or "").lower()
        if t == "model":
            lib = find_library(str(item.get("name") or ""))
            if lib is None:
                continue
            show = item.get("labels")
            show = {str(s).lower() for s in show} if isinstance(show, list) else None
            highlight = {str(s).lower() for s in item.get("highlight") or []} if isinstance(item.get("highlight"), list) else set()
            place = {"at": item.get("at"), "turn": item.get("turn"), "scale": item.get("scale")}
            for p in lib.get("parts", []):
                if not isinstance(p, dict):
                    continue
                p = dict(p)
                if show is not None and str(p.get("label", "")).lower() not in show:
                    p["label"] = ""
                if show is not None and isinstance(p.get("labels"), list):
                    p["labels"] = [m for m in p["labels"] if isinstance(m, dict) and str(m.get("text", "")).lower() in show]
                names = {str(p.get("label", "")).lower(), str(p.get("name", "")).lower()} - {""}
                names |= {str(m.get("text", "")).lower() for m in p.get("labels") or [] if isinstance(m, dict)}
                if highlight and not (names & highlight):
                    p["dim"] = True
                if any(v is not None for v in place.values()):
                    p = {"shapes": [], "_wrap": p, **{k: v for k, v in place.items() if v is not None}}
                parts.append(p)
            lib_detail.append(int(_f(lib.get("resolution"), 190, 120, 320)))
            if item.get("cutaway") and isinstance(lib.get("cutaway"), dict):
                cut.append(lib["cutaway"])
            for k in ("turn", "tilt", "zoom", "ground"):
                if k in (lib.get("view") or {}) and k not in view:
                    view[k] = lib["view"][k]
            for c in lib.get("captions", []) if item.get("captions") else []:
                captions.append(str(c))
        elif t == "part":
            parts.append(item)
        elif t == "cut":
            cut.append(item)
        elif t == "text":
            text = str(item.get("text") or "").strip()
            if text:
                captions.append(text[:90])
        elif t in LEGACY_SOLIDS:
            parts.extend(_legacy_part(item))
    if lib_detail and view.get("detail") is None:   # a ready-made model's own resolution, up or down
        detail = max(lib_detail)
    maths = [i for i in items if str(i.get("type") or "").lower() in MATHS_ITEMS]
    if maths:
        parts.extend(_maths_parts(maths))
        if any(str(i.get("type")).lower() == "axes3d" for i in maths):
            view.setdefault("ground", False)      # axes float in space; a floor under them only confuses
    parts = [p for p in parts if isinstance(p, dict)][:MAX_PARTS]
    if not parts:
        return None
    arrows = []
    for i in maths:
        if str(i.get("type")).lower() in ("vector3d", "arrow3d"):
            d = _v3(i.get("to"), (1, 0, 0)) - _v3(i.get("from"))
            if np.linalg.norm(d) > 1e-9:
                arrows.append(d / np.linalg.norm(d))
    return Spec(parts, captions[:3], view, cut, detail, arrows)


def _unwrap(p: dict) -> dict:
    """A ready-made part placed somewhere (at/turn/scale of the whole model)."""
    inner = p.get("_wrap")
    if not isinstance(inner, dict):
        return p
    out = dict(inner)
    # Place it: the wrapper's frame applies after the part's own.
    out["_place"] = (_v3(p.get("at")), _rot(p.get("turn")), _f(p.get("scale"), 1.0, 1e-3, 1e3))
    return out


_FAILED = object()      # a part whose meshing failed (not one that is simply empty)


class Model:
    """A note's 3D model, built into meshes (``build`` is slow: run it off the Qt thread)."""

    def __init__(self, spec: Spec, resolution: int | None = None) -> None:
        self.spec = spec
        self.resolution = resolution or spec.resolution
        self.parts: list[Part] = []
        shapes = 0
        for raw in spec.parts:
            p = _unwrap(raw)
            part = Part(p)
            place = p.get("_place")
            if place is not None:   # fold the model's placement into the part's frame
                at, R, s = place
                part.at = at + s * (R @ part.at)
                part.R = R @ part.R
                part.scale *= s
            if (not part.shapes and part.sheet is None) or shapes + len(part.shapes) > MAX_SHAPES:
                continue
            shapes += len(part.shapes)
            part.dim = bool(p.get("dim"))
            self.parts.append(part)
        self.meshes: list[Mesh] = []
        self.lo = np.zeros(3)
        self.hi = np.ones(3)
        self.size = 1.0
        bounds = [p.bounds() for p in self.parts]
        bounds = [b for b in bounds if b is not None]
        if bounds:
            self.lo = np.min([b[0] for b in bounds], axis=0)
            self.hi = np.max([b[1] for b in bounds], axis=0)
            self.size = float(np.max(self.hi - self.lo)) or 1.0
        for c in spec.cut:
            n = _v3(c.get("normal"), (0, -1, 0))
            if np.linalg.norm(n) > 1e-9:
                at = _v3(c.get("at"), tuple((self.lo + self.hi) / 2))
                for part in self.parts:
                    if not part.uncut:
                        part.world_cuts.append((n / np.linalg.norm(n), at))
        for part in self.parts:
            centre = np.zeros(3)
            b = part.bounds()
            if b is not None:
                centre = (b[0] + b[1]) / 2
            part.motion = _motion(part._motion_spec, centre)

    # What a part shows but doesn't change about its shape or colours: choosing labels or
    # picking parts out never rebuilds a model.
    _LOOKS_ONLY = ("label", "labels", "dim", "anchor", "move", "opacity", "name")

    def key(self) -> str:
        def shape_only(p):
            if not isinstance(p, dict):
                return p
            out = {k: v for k, v in p.items() if k not in self._LOOKS_ONLY}
            if isinstance(out.get("_wrap"), dict):
                out["_wrap"] = shape_only(out["_wrap"])
            return out
        parts = [shape_only(p) for p in self.spec.parts]
        blob = json.dumps({"v": CACHE_VERSION, "r": self.resolution, "parts": parts, "cut": self.spec.cut},
                          sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]

    def cached(self) -> bool:
        return self._path().exists()

    def build(self, cache: bool = True, workers: int | None = None, low_priority: bool = False) -> list[Mesh]:
        """Every part as a mesh, with ambient occlusion. Cached by the model's JSON."""
        if cache and self._load():
            return self.meshes
        h = self.size / self.resolution

        def one(part):
            if low_priority:
                _lower_thread_priority()
            try:
                return mesh_part(part, h)
            except (ValueError, FloatingPointError, MemoryError, IndexError):
                return _FAILED
        # Parts are built side by side (numpy lets go of the GIL for the heavy sums).
        workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1))
        if workers == 1:
            built = [one(part) for part in self.parts]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                built = list(pool.map(one, self.parts))
        failed = any(m is _FAILED for m in built)
        meshes = [m for m in built if m is not None and m is not _FAILED]
        ambient_occlusion(meshes, self.size)
        for m in meshes:
            m.grid = None
        self.meshes = meshes
        if cache and meshes and not failed:      # a part that failed (memory) is tried again next time
            self._save()
        return meshes

    def _path(self) -> Path:
        return CACHE / f"{self.key()}.npz"

    def _save(self) -> None:
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            arrays = {}
            for k, m in enumerate(self.meshes):
                arrays[f"p{k}"], arrays[f"n{k}"], arrays[f"c{k}"] = m.pos, m.nrm.astype(np.float16), m.col.astype(np.float16)
                arrays[f"a{k}"], arrays[f"u{k}"], arrays[f"t{k}"] = m.ao.astype(np.float16), m.cut.astype(np.uint8), m.tri
                arrays[f"i{k}"] = np.array([self.parts.index(m.part)])
            tmp = self._path().with_suffix(".tmp.npz")
            np.savez_compressed(tmp, **arrays)
            tmp.replace(self._path())
        except OSError:
            pass

    def _load(self) -> bool:
        path = self._path()
        if not path.exists():
            return False
        try:
            with np.load(path) as z:
                meshes = []
                k = 0
                while f"p{k}" in z:
                    part = self.parts[int(z[f"i{k}"][0])]
                    pos = z[f"p{k}"].astype(F)
                    meshes.append(Mesh(part, pos, z[f"n{k}"].astype(F), z[f"c{k}"].astype(F), z[f"a{k}"].astype(F),
                                       z[f"u{k}"].astype(F), z[f"t{k}"].astype(np.uint32), centre=pos.mean(0)))
                    k += 1
        except (OSError, ValueError, KeyError, IndexError):
            return False
        self.meshes = meshes
        return bool(meshes)


def _lower_thread_priority() -> None:
    """Let everything else on the PC go first (Windows; elsewhere nothing)."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), -2)   # THREAD_PRIORITY_LOWEST
    except (AttributeError, OSError):
        pass


def prewarm(stop=None) -> int:
    """Builds every ready-made model (and its cutaway) that isn't cached yet, quietly and one
    at a time, so the first time one is asked for it shows at once. Returns how many it built."""
    _lower_thread_priority()
    built = 0
    index = library_index()
    for name in library_names():
        for cut in (False, True):
            if stop is not None and stop.is_set():
                return built
            item = {"type": "model", "name": name}
            if cut:
                if not index.get(name, {}).get("cutaway"):
                    continue
                item["cutaway"] = True
            spec = spec_from_note({"items": [item]})
            if spec is None:
                continue
            model = Model(spec)
            if model.cached():
                continue
            try:
                model.build(workers=1, low_priority=True)
                built += 1
            except Exception:      # a model that won't build is skipped; the card falls back for it
                continue
    return built
