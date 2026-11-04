"""3D on the note card (scene3d.NoteModel): meshes that face the right way,
every item building, bad input refused, labels that never overlap, moving
parts that move, frames that stay quick, and the card that shows it."""
from __future__ import annotations

import itertools
import math
import time

import numpy as np
import pytest

import scene3d
import sketch


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


ENGINE = {"title": "Two strokes, one power cycle", "topic": "engines", "view": {"3d": True}, "items": [
    {"type": "box3d", "at": [0, 0, -1.25], "size": [1.5, 1.1, 0.95], "color": "grey", "material": "glass",
     "label": "crankcase"},
    {"type": "cylinder", "id": "crank", "at": [0, 0, -1.25], "r": 0.36, "h": 0.14, "axis": [0, 1, 0],
     "material": "metal", "spin": 0.35, "label": "crankshaft"},
    {"type": "cylinder", "id": "pin", "at": [0.26, 0.08, -1.25], "r": 0.05, "h": 0.22, "axis": [0, 1, 0],
     "spin": 0.35, "pivot": [0, 0, -1.25]},
    {"type": "cylinder", "from": "pin", "to": "piston", "r": 0.05, "label": "connecting rod"},
    {"type": "cylinder", "id": "piston", "at": [0, 0, 0.05], "r": 0.4, "h": 0.42, "color": "orange",
     "follow": "pin", "label": "piston"},
    {"type": "cutaway", "at": [0, 0, 0.2], "r": 0.47, "h": 1.3, "label": "cylinder"},
    {"type": "cylinder", "at": [0, 0, 1.04], "r": 0.06, "h": 0.22, "color": "ink", "label": "spark plug"},
    {"type": "sphere", "at": [0, 0, 0.8], "r": 0.06, "color": "yellow", "material": "glow"},
    {"type": "cylinder", "at": [0, -0.68, 0.05], "r": 0.12, "h": 0.4, "axis": [0, 1, 0], "color": "red",
     "label": "exhaust port"},
    {"type": "tube", "points": [[0.05, 0.56, -0.95], [0.05, 0.74, -0.6], [0.05, 0.74, -0.15], [0.05, 0.48, 0]],
     "r": 0.08, "color": "blue", "label": "transfer port"},
    {"type": "cylinder", "at": [0, -0.72, -1.15], "r": 0.11, "h": 0.3, "axis": [0, 1, 0], "color": "green",
     "label": "intake"},
    {"type": "text", "text": "one turn of the crank = one power stroke"},
], "followups": ["How is four-stroke different?"]}

WATER = {"title": "Why water is bent", "view": {"3d": True}, "items": [
    {"type": "molecule", "atoms": [["O", 0, 0, 0], ["H", 0.757, 0, 0.586], ["H", -0.757, 0, 0.586]],
     "bonds": [[0, 1], [0, 2]], "label": "water"},
    {"type": "text", "text": "104.5°", "at": [0, 0, 0.42]},
]}

MATHS = {"title": "Saddle and vectors", "view": {"3d": True}, "items": [
    {"type": "surface", "expr": "x^2 - y^2", "x": [-1.5, 1.5], "y": [-1.5, 1.5], "label": "saddle"},
    {"type": "axes3d"},
    {"type": "vector3d", "to": [2, -0.5, 0.5], "label": "a"},
    {"type": "vector3d", "from": [2, -0.5, 0.5], "to": [2.5, 1.5, 2], "color": "red", "label": "b"},
    {"type": "point3d", "at": [0, 0, 0], "label": "saddle point"},
    {"type": "line3d", "from": [0, 0, 0], "to": [1, 1, 0], "dashed": True},
    {"type": "curve3d", "x": "2*cos(t)", "y": "2*sin(t)", "z": "0", "travel": True, "label": "orbit"},
    {"type": "field", "x": "-y", "y": "x", "z": "0", "range": 1, "count": 3},
]}

EVERY_ITEM = [
    {"type": "box3d", "at": [0, 0, 0], "size": [1, 2, 0.5]},
    {"type": "cylinder", "at": [0, 0, 0], "r": 0.5, "h": 1, "axis": [1, 0, 0]},
    {"type": "cylinder", "at": [0, 0, 0], "r": 0.5, "h": 1, "hollow": True},
    {"type": "cylinder", "from": [0, 0, 0], "to": [1, 1, 1], "r": 0.1},
    {"type": "cone", "at": [0, 0, 0], "r": 0.5, "h": 1},
    {"type": "cutaway", "at": [0, 0, 0], "r": 0.6, "h": 1.5},
    {"type": "sphere", "at": [0, 0, 0], "r": 1},
    {"type": "torus", "r": 1, "tube": 0.2},
    {"type": "gear", "r": 0.6, "teeth": 12, "spin": 0.2},
    {"type": "tube", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "r": 0.1},
    {"type": "plane", "normal": [0, 1, 1], "size": [2, 1]},
    {"type": "molecule", "atoms": [["C", 0, 0, 0], ["O", 1.16, 0, 0], ["O", -1.16, 0, 0]], "bonds": [[0, 1, 2], [0, 2, 2]]},
    {"type": "vector3d", "to": [1, 1, 1]},
    {"type": "point3d", "at": [1, 0, 0]},
    {"type": "line3d", "from": [0, 0, 0], "to": [1, 0, 0]},
    {"type": "curve3d", "x": "cos(t)", "y": "sin(t)", "z": "t/6", "from": 0, "to": 12.6},
    {"type": "surface", "expr": "sin(x)*cos(y)"},
    {"type": "field", "x": "-y", "y": "x", "z": "0"},
    {"type": "axes3d"},
]

def _canvas():
    from PyQt6.QtCore import QRectF
    return QRectF(22, 98, 426, 276)


# --- Meshes ---------------------------------------------------------------------------------

@pytest.mark.parametrize("mesh", [
    scene3d._m_box(1, 2, 3),
    scene3d._m_prism(scene3d._circle(0.5, 24), 2.0),
    scene3d._m_prism(scene3d._circle(0.5, 24), 2.0, top=0.0),
    scene3d._m_prism(scene3d._gear_outline(0.6, 14), 0.2),
    scene3d._m_hollow(1.0, 0.8, 2.0)[:2],
    scene3d._m_hollow(1.0, 0.8, 2.0, arc=230)[:2],
    scene3d._m_torus(1.0, 0.25),
    scene3d._m_arrow(2.0, 0.05),
])
def test_closed_solids_face_outward(mesh):
    """Back faces are culled, so every face of a closed solid must point out
    (its signed volume is positive)."""
    v, q, *ends = mesh
    assert scene3d._volume(v, q, ends[0] if ends else ()) > 0


def test_turning_plus_z_to_any_direction():
    for d in ([0, 0, 1], [0, 0, -1], [1, 0, 0], [0.3, -0.5, 0.8]):
        d = scene3d._unit(np.array(d, float))
        assert np.allclose(scene3d._turn_to(d) @ [0, 0, 1], d)


# --- Building ---------------------------------------------------------------------------------

@pytest.mark.parametrize("item", EVERY_ITEM, ids=[i["type"] for i in EVERY_ITEM])
def test_every_item_builds(qapp, item):
    model = scene3d.NoteModel.from_note({"title": "t", "view": {"3d": True}, "items": [item]})
    assert model is not None
    assert model.solids or model.balls or model.lines


@pytest.mark.parametrize("items", [
    [], [{"type": "mystery"}], ["nope", 3, None], [{"type": 3}], [{"type": "molecule", "atoms": [["H", "x", 0, 0]]}],
    [{"type": "surface", "expr": "__import__('os').system('calc')"}], [{"type": "curve3d", "x": "os.system()"}],
    [{"type": "tube", "points": [[0, 0, 0]]}], [{"type": "molecule", "atoms": "H2O"}],
    [{"type": "vector3d", "from": [0, 0, 0], "to": [0, 0, 0]}], [{"type": "field", "x": "exec('1')"}],
])
def test_nothing_drawable_means_no_model(qapp, items):
    assert scene3d.NoteModel.from_note({"title": "t", "view": {"3d": True}, "items": items}) is None


def test_odd_numbers_are_tamed(qapp):
    model = scene3d.NoteModel.from_note({"view": {"3d": True, "turn": "x", "tilt": float("nan")}, "items": [
        {"type": "box3d", "at": [float("inf"), 0, 0], "size": [1e30, -2, "3"]},
        {"type": "cylinder", "r": -1, "h": 1e99, "spin": 1e9, "axis": [0, 0, 0]},
        {"type": "gear", "teeth": 10 ** 9},
        {"type": "sphere", "r": 0},
    ]})
    assert model is not None
    v, _balls, _anchors = model._frame(3.0)
    assert np.isfinite(v).all()
    assert all(math.isfinite(x) for x in (model.az0, model.el))


def test_huge_models_are_capped(qapp):
    items = [{"type": "torus", "at": [k, 0, 0], "r": 1, "tube": 0.3} for k in range(40)]
    model = scene3d.NoteModel.from_note({"view": {"3d": True}, "items": items})
    assert len(model._q) <= scene3d.MAX_FACES


# --- Motion --------------------------------------------------------------------------------------

def test_moving_parts_move_and_the_rod_stays_joined(qapp):
    model = scene3d.NoteModel.from_note(ENGINE)
    ids = {s.id: k for k, s in enumerate(model.solids) if s.id}
    rod = next(k for k, s in enumerate(model.solids) if s.link)
    _v0, _b0, at0 = model._frame(0.0)
    v1, _b1, at1 = model._frame(0.7)
    assert not np.allclose(at0[ids["pin"]], at1[ids["pin"]]), "the crank pin goes round"
    shift = at1[ids["piston"]] - at0[ids["piston"]]
    assert abs(shift[2]) > 0.05 and np.allclose(shift[:2], 0, atol=1e-9), "the piston slides up and down only"
    s = model.solids[rod]
    ends = v1[s.offset:s.offset + len(s.unit)]
    assert np.linalg.norm(ends.mean(0) - (at1[ids["pin"]] + at1[ids["piston"]]) / 2) < 0.05


def test_a_traveller_goes_round_its_curve(qapp):
    model = scene3d.NoteModel.from_note(MATHS)
    _v, b0, _a = model._frame(0.0)
    _v, b1, _a = model._frame(3.0)
    moved = np.linalg.norm(b1 - b0, axis=1)
    assert moved.max() > 0.5


# --- Drawing -----------------------------------------------------------------------------------

def _paint(model, t=0.0, turn=0.0, reveal=99.0):
    from PyQt6.QtGui import QColor, QImage, QPainter
    image = QImage(470, 430, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(251, 248, 242))
    p = QPainter(image)
    start = time.perf_counter()
    model.paint(p, _canvas(), turn=turn, t=t, reveal=reveal)
    took = time.perf_counter() - start
    p.end()
    return image, took


@pytest.mark.parametrize("note", [ENGINE, WATER, MATHS], ids=["engine", "water", "maths"])
def test_models_draw_quickly_and_fill_the_canvas(qapp, note):
    model = scene3d.NoteModel.from_note(note)
    _paint(model)                                    # the first frame fits it to the card
    times = []
    for k in range(6):
        image, took = _paint(model, t=k * 0.4, turn=model.TURN * math.sin(k))
        times.append(took)
    times.sort()
    assert times[3] < 0.15, f"a frame took {times[3] * 1000:.0f} ms"
    # Something solid was drawn in the middle of the canvas, not just paper.
    paper = (251, 248, 242)
    canvas = _canvas()
    inked = sum(image.pixelColor(int(x), int(y)).getRgb()[:3] != paper
                for x in np.linspace(canvas.left() + 60, canvas.right() - 60, 24)
                for y in np.linspace(canvas.top() + 20, canvas.bottom() - 30, 16))
    assert inked > 24


def test_curved_sides_are_drawn_as_smooth_strips(qapp):
    model = scene3d.NoteModel.from_note({"view": {"3d": True}, "items": [{"type": "cylinder", "r": 1, "h": 2}]})
    model.fit(_canvas())
    eye, basis = model._camera(0.0)
    v, balls, _anchors = model._frame(0.0)

    def project(pts):
        cam = (pts - eye) @ basis.T
        z = cam[:, 2]
        return np.column_stack([model._ox + model._scale * cam[:, 0] / z, model._oy - model._scale * cam[:, 1] / z]), z
    prims = model._primitives(v, balls, eye, basis, project)
    assert len(prims["runs"]) == 1, "the visible side is one gradient-filled strip"
    assert len(prims["runs"][0][4]) > 5, "with a colour stop per column"
    assert any(kind == 3 for kind, _i in prims["order"]), "and the top is one flat end"


@pytest.mark.parametrize("turn", [-26, -10, 0, 13, 26])
def test_labels_never_overlap(qapp, turn):
    from PyQt6.QtCore import QRectF
    busy = {"view": {"3d": True}, "items": ENGINE["items"] + [
        {"type": "vector3d", "from": [0, 0, 0], "to": [0.2, 0.1, 0.3], "label": "force"},
        {"type": "point3d", "at": [0, 0.1, 0.9], "label": "spark"},
        {"type": "text", "text": "top dead centre", "at": [0, 0, 0.8]},
    ]}
    model = scene3d.NoteModel.from_note(busy)
    for t in (0.0, 1.3, 2.9):
        _paint(model, t=t, turn=turn)
        rects = [lb.rect for lb in model.labels if not lb.rect.isEmpty()]
        assert len(rects) >= len(model.labels) - 1, "every label finds a place"
        for a, b in itertools.combinations(rects, 2):
            assert not a.intersects(b), f"labels overlap: {a} {b}"
        canvas = _canvas().adjusted(-3, -7, 3, 3)
        assert all(canvas.contains(QRectF(r)) for r in rects)


def test_labels_reveal_one_after_another(qapp):
    model = scene3d.NoteModel.from_note(ENGINE)
    assert model.reveal_end() > 0.75 + 0.3 * len(model.labels)
    _paint(model, reveal=-1.0)                     # nothing yet, and no crash
    _paint(model, reveal=1.0)


def test_a_flat_molecule_is_not_shown_edge_on(qapp):
    """Atoms laid out in the plane the usual camera looks along get a view that shows them."""
    d = np.array([math.cos(math.radians(35)), math.sin(math.radians(35)), 0])
    atoms = [["C", *(d * s + np.array([0, 0, 1.0]) * u)] for s, u in ((0, 0), (1.4, 0), (-0.7, 1.2), (-0.7, -1.2))]
    model = scene3d.NoteModel.from_note({"view": {"3d": True}, "items": [
        {"type": "molecule", "atoms": [[a[0], *map(float, a[1:])] for a in atoms], "bonds": [[0, 1], [0, 2], [0, 3]]}]})
    assert (model.az0, model.el) != (35.0, 22.0)


def test_a_chosen_view_is_kept(qapp):
    model = scene3d.NoteModel.from_note({**WATER, "view": {"3d": True, "turn": 70, "tilt": 12}})
    assert (model.az0, model.el) == (70.0, 12.0)


# --- On the note card --------------------------------------------------------------------------

def _card(monkeypatch, note):
    clock = [100.0]
    monkeypatch.setattr(sketch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sketch, "save_note", lambda n, img: None)
    card = sketch.NoteCard()
    card.show_note(note)
    return card, clock


def test_the_card_shows_a_solid_model_that_rests_and_wakes(qapp, monkeypatch):
    card, clock = _card(monkeypatch, ENGINE)
    try:
        assert card._model is not None and not card._strokes, "solid, not the wireframe"
        assert card.card.height() == sketch.NoteCard.H3D
        for t in (0.1, 1.0, 3.0, 6.0):
            clock[0] = card._t0 + t
            card._tick()
            card.grab()
        assert card._clock > 0, "it turns while it's new"
        # Long after: it comes to rest at the end of a swing, and the timer stops.
        for k in range(2000):
            clock[0] += 0.05
            card._tick()
            if not card._timer.isActive():
                break
        assert card._resting and not card._timer.isActive()
        assert abs(math.cos(card._clock * card.TURN_RATE)) < 0.1, "it stopped where the swing turns"
        card.enterEvent(None)
        assert card._timer.isActive() and not card._resting, "the mouse on the card turns it again"
    finally:
        card.close()


def test_a_2d_note_after_a_3d_one_gets_the_usual_card(qapp, monkeypatch):
    card, clock = _card(monkeypatch, ENGINE)
    try:
        card.show_note({"title": "t", "items": [{"type": "points", "points": ["a", "b"]}]})
        assert card._model is None and card.card.height() == sketch.NoteCard.H
    finally:
        card.close()


def test_a_model_that_fails_to_draw_falls_back_to_the_wireframe(qapp, monkeypatch):
    card, clock = _card(monkeypatch, MATHS)
    try:
        def broken(*_a, **_k):
            raise RuntimeError("no")
        monkeypatch.setattr(card._model, "paint", broken)
        card.grab()
        assert card._model is None and card._strokes, "the sketched wireframe instead"
    finally:
        card.close()


def test_3d_questions_get_a_note_and_the_prompts_offer_3d():
    import ai
    assert sketch.wants_sketch("show me a two stroke engine in 3d")
    assert sketch.wants_sketch("can you make a 3D model of a water molecule")
    for word in ('"cutaway"', '"molecule"', '"spin"', '"model"', '"part"', '"blend"', "never point to another app"):
        assert word in sketch.PROMPT
    for prompt in (ai._CLICKY_SYSTEM_PROMPT, ai._CLICKY_ANNOTATION_SYSTEM_PROMPT):
        assert "3d" in prompt and "another app or ai" in prompt
