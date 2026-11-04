"""Real 3D: the modelling language (model3d), the ready-made models and the GPU card model (render3d)."""
import json
import math

import numpy as np
import pytest

import model3d
import render3d
import sketch


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _part(**spec):
    return model3d.Part({"shapes": spec.pop("shapes"), **spec})


def _mesh(spec, h=0.04):
    return model3d.mesh_part(_part(**spec), h)


# --- Shapes and parts -----------------------------------------------------------------------

def test_every_shape_builds_a_closed_surface_facing_out():
    shapes = [
        {"shape": "sphere", "r": 0.5},
        {"shape": "ellipsoid", "size": [0.6, 0.4, 0.3], "turn": [10, 20, 30]},
        {"shape": "box", "size": [1, 0.6, 0.4], "round": 0.05},
        {"shape": "rod", "from": [0, 0, 0], "to": [0, 0, 1], "r": 0.2, "r2": 0.1},
        {"shape": "cylinder", "from": [0, 0, 0], "to": [0, 0, 0.8], "r": 0.3, "round": 0.03},
        {"shape": "cone", "at": [0, 0, 0], "height": 0.8, "r": 0.4},
        {"shape": "torus", "r": 0.5, "tube": 0.12, "axis": [1, 0, 0]},
        {"shape": "tube", "points": [[0, 0, 0], [0.5, 0, 0.3], [1, 0.2, 0]], "r": [0.12, 0.1, 0.06]},
        {"shape": "helix", "r": 0.4, "pitch": 0.25, "turns": 2, "tube": 0.06},
        {"shape": "lathe", "profile": [[0.3, 0], [0.45, 0.3], [0.2, 0.8], [0.25, 1]]},
        {"shape": "extrude", "outline": [[0, 0], [1, 0], [0.5, 0.8]], "height": 0.2, "round": 0.03},
        {"shape": "gear", "r": 0.5, "teeth": 12, "thickness": 0.15},
    ]
    for s in shapes:
        m = _mesh({"shapes": [s]})
        assert m is not None and len(m.tri) > 50, s["shape"]
        # Closed: every edge is shared by exactly two triangles.
        e = np.sort(np.vstack([m.tri[:, [0, 1]], m.tri[:, [1, 2]], m.tri[:, [2, 0]]]), axis=1)
        _u, counts = np.unique(e, axis=0, return_counts=True)
        assert (counts == 2).mean() > 0.99, s["shape"]
        # Facing out: normals point away from the middle on average.
        assert np.einsum("ij,ij->i", m.nrm, m.pos - m.pos.mean(0)).mean() > 0, s["shape"]


def test_blend_melts_shapes_together_and_subtract_carves():
    a = {"shape": "sphere", "at": [-0.35, 0, 0], "r": 0.4}
    b = {"shape": "sphere", "at": [0.35, 0, 0], "r": 0.4}
    hard = _part(shapes=[a, b])
    soft = _part(shapes=[a, b], blend=0.3)
    waist = np.array([[0.0, 0.5, 0.0]], dtype=np.float32)
    assert soft.sdf(waist)[0] < hard.sdf(waist)[0], "the blend fills the waist between them"
    hollow = _part(shapes=[{"shape": "sphere", "r": 0.5}, {"shape": "sphere", "r": 0.4, "subtract": True}])
    assert hollow.sdf(np.zeros((1, 3), np.float32))[0] > 0, "the middle is carved out"


def test_colours_follow_the_shape_that_makes_the_surface():
    part = _part(color="#ff0000", blend=0.2, shapes=[
        {"shape": "sphere", "at": [-0.5, 0, 0], "r": 0.4},
        {"shape": "sphere", "at": [0.5, 0, 0], "r": 0.4, "color": "#0000ff"}])
    c = part.colors(np.array([[-0.9, 0, 0], [0.9, 0, 0]], np.float32))
    assert c[0, 0] > 0.9 and c[0, 2] < 0.1, "the plain sphere keeps the part's colour"
    assert c[1, 2] > 0.9 and c[1, 0] < 0.1, "the blue sphere is blue"


def test_mirror_repeat_shell_and_cut():
    mirrored = _part(mirror="x", shapes=[{"shape": "sphere", "at": [0.5, 0, 0], "r": 0.2}])
    assert mirrored.sdf(np.array([[-0.5, 0, 0]], np.float32))[0] < 0
    spikes = _part(repeat={"count": 6, "axis": "z"}, shapes=[{"shape": "sphere", "at": [1, 0, 0], "r": 0.1}])
    assert spikes.sdf(np.array([[math.cos(math.pi / 3), math.sin(math.pi / 3), 0]], np.float32))[0] < 0
    shell = _part(shell=0.05, shapes=[{"shape": "sphere", "r": 0.5}])
    assert shell.sdf(np.zeros((1, 3), np.float32))[0] > 0, "hollow"
    cut = _mesh({"shapes": [{"shape": "sphere", "r": 0.5}], "cut": {"normal": [0, -1, 0], "at": [0, 0, 0]}})
    assert cut.pos[:, 1].min() > -0.05, "the -y half is gone"
    assert cut.cut.sum() > 0, "the cut face is marked (painted as the inside)"


def test_bad_json_never_breaks_a_model():
    assert model3d.make_shape({"shape": "tube", "points": "nope"}) is None
    assert model3d.make_shape({"shape": "unknown"}) is None
    assert model3d.make_shape({"shape": "sphere", "r": float("nan")}) is not None
    p = model3d.Part({"shapes": [{"shape": "sphere", "r": 1e9, "at": ["a", None, 3]}], "color": 7, "material": "?"})
    assert p.material == "plastic" and p.shapes
    assert model3d.spec_from_note({"items": [{"type": "part", "shapes": []}]}) is not None
    assert model3d.spec_from_note({"items": [{"type": "points", "points": ["a"]}]}) is None


def test_motions():
    spin = model3d.Motion("spin", axis=np.array([0, 0, 1.0]), at=np.zeros(3), speed=90)
    m = spin.matrix(1.0)
    assert np.allclose(m[:3, :3] @ [1, 0, 0], [0, 1, 0], atol=1e-9)
    slide = model3d.Motion("slide", axis=np.array([0, 0, 1.0]), speed=1.0, amount=0.5)
    assert abs(slide.matrix(0.25)[2, 3] - 0.5) < 1e-9
    beat = model3d.Motion("beat", at=np.zeros(3), speed=1.0, amount=0.1)
    assert beat.matrix(0.25)[0, 0] > 1.0


# --- Notes, the library, older notes ----------------------------------------------------------

def test_every_ready_made_model_reads_and_is_labelled():
    names = model3d.library_names()
    assert "heart" in names
    for name in names:
        data = json.loads((model3d.LIBRARY / f"{name}.json").read_text(encoding="utf-8"))
        assert data.get("title") and isinstance(data.get("parts"), list), name
        spec = model3d.spec_from_note({"items": [{"type": "model", "name": name}]})
        assert spec is not None, name
        model = model3d.Model(spec)
        assert model.parts and all(p.shapes for p in model.parts), name
        info = model3d.library_index()[name]
        assert 3 <= len(info["labels"]) <= 14, f"{name}: {len(info['labels'])} labels"


def test_a_model_note_can_choose_labels_highlight_and_cut_open():
    note = {"items": [{"type": "model", "name": "heart", "labels": ["aorta", "left ventricle"],
                       "highlight": ["aorta"], "cutaway": True}]}
    spec = model3d.spec_from_note(note)
    labels = [p.get("label") for p in spec.parts if p.get("label")]
    labels += [m["text"] for p in spec.parts for m in p.get("labels") or []]
    assert sorted(labels) == ["aorta", "left ventricle"]
    assert spec.cut, "the heart's own cutaway"
    model = model3d.Model(spec)
    assert any(p.world_cuts for p in model.parts)
    assert [p.label for p in model.parts if not p.dim] == ["aorta"]
    assert model3d.find_library("cardiac") is not None, "aliases find it too"


def test_older_solid_notes_and_maths_convert_to_parts():
    water = {"items": [{"type": "molecule", "atoms": [["O", 0, 0, 0], ["H", 0.76, 0, 0.59], ["H", -0.76, 0, 0.59]],
                        "bonds": [[0, 1], [0, 2]]}]}
    spec = model3d.spec_from_note(water)
    assert len(spec.parts) == 5 and {p["label"] for p in spec.parts if p.get("label")} == {"oxygen", "hydrogen"}
    old = {"items": [{"type": "cylinder", "at": [0, 0, 0], "r": 0.5, "h": 2, "axis": [1, 0, 0], "spin": 1}]}
    part = model3d.Model(model3d.spec_from_note(old)).parts[0]
    lo, hi = part.bounds()
    assert hi[0] - lo[0] > 1.9, "the axis points it along x"
    assert part.motion is not None and part.motion.kind == "spin"
    vectors = {"items": [{"type": "axes3d"}, {"type": "vector3d", "from": [0, 0, 0], "to": [1, 0, 0], "label": "F"}]}
    assert not model3d.needs_painter(vectors) and not model3d.needs_painter(water)
    spec = model3d.spec_from_note(vectors)
    assert len(spec.parts) == 4 and spec.view.get("ground") is False, "three axes and the vector; no floor under axes"
    assert {"text": "F", "at": [1.0, 0.0, 0.0], "tip": True} in spec.parts[-1]["labels"]
    assert spec.arrows and np.allclose(spec.arrows[0], [1, 0, 0])
    linked = {"items": [{"type": "cylinder", "id": "crank"}, {"type": "cylinder", "follow": "crank"}]}
    assert model3d.needs_painter(linked), "parts linked by id: the painter's 3D"
    assert model3d.needs_painter({"items": [{"type": "cutaway", "r": 1}]})


def test_meshes_are_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(model3d, "CACHE", tmp_path)
    note = {"items": [{"type": "part", "label": "ball", "shapes": [{"shape": "sphere", "r": 0.5}]}]}
    first = model3d.Model(model3d.spec_from_note(note), resolution=60)
    built = first.build()
    again = model3d.Model(model3d.spec_from_note(note), resolution=60)
    assert again._load() and len(again.meshes[0].tri) == len(built[0].tri)
    assert again.meshes[0].part.label == "ball"


# --- On the GPU and the card ------------------------------------------------------------------

needs_gpu = pytest.mark.skipif(render3d.GPU.get() is None, reason="no OpenGL 3.3 here")


@needs_gpu
def test_the_gpu_draws_a_lit_model_with_its_labels_on_their_parts(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    monkeypatch.setattr(model3d, "CACHE", tmp_path)
    note = {"view": {"3d": True, "detail": 120}, "items": [
        {"type": "part", "label": "ball", "color": "red", "material": "glossy", "shapes": [{"shape": "sphere", "r": 0.6}]},
        {"type": "part", "label": "post", "color": "steel", "material": "steel",
         "shapes": [{"shape": "cylinder", "from": [1.2, 0, -0.6], "to": [1.2, 0, 0.6], "r": 0.15}]}]}
    model = render3d.RealModel.from_note(note)
    assert model is not None and model.wait(60)
    img = QImage(600, 400, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0xFFFFFFFF)
    canvas = QRectF(20, 20, 560, 360)
    for reveal in (0.0, 30.0):
        p = QPainter(img)
        model.paint(p, canvas, reveal=reveal)
        p.end()
    assert model.ready and len(model.labels) == 2
    # The model is drawn: reds in the middle of the picture.
    centre = img.pixelColor(int(model._area.center().x()), int(model._area.center().y()))
    reds = sum(1 for x in range(0, 600, 6) for y in range(0, 400, 6)
               if img.pixelColor(x, y).red() > img.pixelColor(x, y).blue() + 60)
    assert reds > 200, "the red ball is there, lit"
    assert centre.alpha() == 255
    # Each label's anchor lands on its own part in the pick buffer.
    r = model.renderer
    ids, _z, vp = r.pick(200, 130, model.az0, model.el, model._fit, model.zoom)
    for lb in model.labels:
        h = vp @ np.append(lb.anchor, 1.0)
        x, y = (h[0] / h[3] + 1) / 2 * 200, (1 - h[1] / h[3]) / 2 * 130
        assert ids[min(129, int(y)), min(199, int(x))] == lb.k, lb.text
    before = model.drag_az
    model.drag(40, 0)
    assert model.drag_az != before, "dragging turns it"
    model.release()


@needs_gpu
def test_the_card_uses_the_gpu_model_for_things_and_maths(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(model3d, "CACHE", tmp_path)
    monkeypatch.setattr(sketch, "save_note", lambda n, img: None)
    card = sketch.NoteCard()
    try:
        card.show_note({"title": "ball", "view": {"3d": True, "detail": 120},
                        "items": [{"type": "part", "label": "ball", "shapes": [{"shape": "sphere", "r": 1}]}]})
        assert isinstance(card._model, render3d.RealModel)
        assert card.card.width() == sketch.NoteCard.W3D
        card.show_note({"title": "vectors", "view": {"3d": True},
                        "items": [{"type": "axes3d"}, {"type": "vector3d", "from": [0, 0, 0], "to": [1, 1, 1]},
                                  {"type": "surface", "expr": "x*y"}]})
        assert isinstance(card._model, render3d.RealModel), "maths is real 3D too"
        card.show_note({"title": "engine", "view": {"3d": True},
                        "items": [{"type": "cylinder", "id": "crank", "r": 0.3, "h": 0.2},
                                  {"type": "cylinder", "follow": "crank", "r": 0.2, "h": 0.5}]})
        import scene3d
        assert isinstance(card._model, scene3d.NoteModel), "linked parts stay with the painter's 3D"
    finally:
        card.clear()
        card.close()


def test_the_mesh_cache_is_kept_small_dropping_the_oldest(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(model3d, "CACHE", tmp_path)
    for k in range(4):
        f = tmp_path / f"m{k}.npz"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (1000 + k, 1000 + k))
    model3d.prune_cache(limit=2500)
    assert sorted(f.name for f in tmp_path.glob("*.npz")) == ["m2.npz", "m3.npz"]


def test_an_old_note_is_shown_with_the_hand_built_model_it_is_about():
    import model3d
    old = {"title": "The heart: a four-chamber pump", "topic": "human heart", "view": {"3d": True},
           "followups": ["Why are there valves?"],
           "items": [{"type": "sphere", "at": [0, 0, 0], "r": 1, "label": "left ventricle"},
                     {"type": "sphere", "at": [1, 0, 0], "r": 1, "label": "right ventricle"},
                     {"type": "tube", "points": [[0, 0, 1], [0, 0, 2]], "label": "aorta"},
                     {"type": "text", "text": "Four chambers, two pumps"}]}
    new = model3d.upgrade_note(old)
    model = new["items"][0]
    assert model["type"] == "model" and model["name"] == "heart"
    assert set(model["labels"]) == {"aorta", "right ventricle", "left ventricle"}, "the labels it shared"
    assert new["items"][1] == {"type": "text", "text": "Four chambers, two pumps"}
    assert new["title"] == old["title"] and new["followups"] == old["followups"]
    assert old["items"][0]["type"] == "sphere", "the kept note itself is left alone"


def test_only_a_real_match_upgrades():
    import model3d
    assert model3d.library_match({"topic": "two-stroke engine"}) != "four_stroke_engine", \
        "a two-stroke is not the four-stroke's generic 'engine'"
    assert model3d.library_match({"topic": "cell division"}) is None
    assert model3d.library_match({"topic": "how a jet engine works"}) == "jet_engine"
    flat = {"title": "Taylor series", "items": [{"type": "function", "fn": "x"}]}
    assert model3d.upgrade_note(flat) is flat, "2D notes stay as they are"
    modern = {"title": "Heart", "topic": "heart", "view": {"3d": True}, "items": [{"type": "model", "name": "heart"}]}
    assert model3d.upgrade_note(modern) is modern, "already the hand-built model"


def _slider_crank_note(**rod_move):
    move = {"link": "crank", "pin": [0, 0, 0.35], "to": [0, 0, 1.65], "slide": "z"}
    move.update(rod_move)
    return {"title": "Slider crank", "view": {"3d": True}, "items": [
        {"type": "part", "id": "crank", "label": "crankshaft", "shapes": [
            {"shape": "cylinder", "from": [-0.3, 0, 0], "to": [0.3, 0, 0], "r": 0.1},
            {"shape": "cylinder", "from": [-0.08, 0, 0.35], "to": [0.08, 0, 0.35], "r": 0.08}],
         "move": {"spin": "x", "about": [0, 0, 0], "speed": 120}},
        {"type": "part", "id": "rod", "label": "connecting rod", "at": [0, 0, 0.35], "shapes": [
            {"shape": "cylinder", "from": [0, 0, 0], "to": [0, 0, 1.3], "r": 0.06}], "move": move},
        {"type": "part", "id": "piston", "label": "piston", "shapes": [
            {"shape": "cylinder", "from": [0, 0, 1.5], "to": [0, 0, 2.0], "r": 0.42}], "move": {"ride": "rod"}},
        {"type": "part", "id": "weight", "shapes": [{"shape": "sphere", "at": [0, 0, -0.3], "r": 0.1}],
         "move": {"follow": "crank"}}]}


def _parts(note):
    import model3d
    m = model3d.Model(model3d.spec_from_note(note))
    return m, {p.spec.get("id"): p for p in m.parts}


def test_a_connecting_rod_never_comes_off_its_crank_or_its_piston():
    import math
    import numpy as np
    m, parts = _parts(_slider_crank_note())
    rod = parts["rod"].motion
    assert rod.kind == "link" and parts["piston"].motion.kind == "ride"
    r, length = 0.35, 1.3
    for t in np.linspace(0, 3, 61):
        pose = m.pose(float(t))
        on_crank = (pose[id(parts["crank"])] @ np.append(rod.pin, 1))[:3]
        big = (pose[id(parts["rod"])] @ np.append(rod.pin, 1))[:3]
        small = (pose[id(parts["rod"])] @ np.append(rod.to, 1))[:3]
        wrist = (pose[id(parts["piston"])] @ np.append(rod.to, 1))[:3]
        assert np.allclose(on_crank, big, atol=1e-9), "the big end stays on the crankpin"
        assert np.allclose(small, wrist, atol=1e-9), "the small end stays in the piston"
        assert abs(np.linalg.norm(small - big) - length) < 1e-9, "the rod keeps its length"
        assert abs(small[0]) < 1e-9 and abs(small[1]) < 1e-9, "the piston stays on the cylinder's line"
        th = math.radians(120 * t)
        assert abs(small[2] - (r * math.cos(th) + math.sqrt(length ** 2 - (r * math.sin(th)) ** 2))) < 1e-9
        assert np.allclose(pose[id(parts["weight"])], pose[id(parts["crank"])]), "the counterweight turns with it"


def test_a_rod_without_its_ends_finds_them_from_its_shape():
    import numpy as np
    m, parts = _parts(_slider_crank_note(pin=None, to=None, slide=None))
    rod = parts["rod"].motion
    assert rod is not None and np.allclose(rod.pin, [0, 0, 0.35], atol=0.07) and np.allclose(rod.to, [0, 0, 1.65], atol=0.07)
    assert np.allclose(rod.slide_dir, [0, 0, 1])


def test_a_part_can_be_carried_by_another_and_loops_are_safe():
    import numpy as np
    import model3d
    note = {"title": "Carrier", "view": {"3d": True}, "items": [
        {"type": "part", "id": "carrier", "shapes": [{"shape": "box", "at": [0, 0, 0], "size": [3, 0.3, 0.1]}],
         "move": {"spin": "z", "about": [0, 0, 0], "speed": 90}},
        {"type": "part", "id": "planet", "shapes": [{"shape": "sphere", "at": [1.2, 0, 0], "r": 0.3}],
         "move": {"spin": "z", "about": [1.2, 0, 0], "speed": -200, "on": "carrier"}},
        {"type": "part", "id": "a", "shapes": [{"shape": "sphere", "at": [0, 2, 0], "r": 0.2}], "move": {"follow": "b"}},
        {"type": "part", "id": "b", "shapes": [{"shape": "sphere", "at": [0, 3, 0], "r": 0.2}], "move": {"follow": "a"}}]}
    m = model3d.Model(model3d.spec_from_note(note))
    parts = {p.spec.get("id"): p for p in m.parts}
    pose = m.pose(1.0)                                     # the carrier has turned 90 degrees
    centre = (pose[id(parts["planet"])] @ np.array([1.2, 0, 0, 1]))[:3]
    assert np.allclose(centre, [0, 1.2, 0], atol=1e-9), "the planet went round with its carrier"
    assert np.allclose(pose[id(parts["a"])], np.eye(4)) or np.allclose(pose[id(parts["b"])], np.eye(4))


def test_the_four_stroke_engine_is_one_mechanism():
    import numpy as np
    import model3d
    m = model3d.Model(model3d.spec_from_note({"view": {"3d": True},
                                              "items": [{"type": "model", "name": "four_stroke_engine"}]}))
    parts = {p.label: p for p in m.parts if p.label}
    rod = parts["connecting rod"].motion
    assert rod.kind == "link" and parts["piston"].motion.kind == "ride"
    for t in np.linspace(0, 3, 13):
        pose = m.pose(float(t))
        on_crank = (pose[id(parts["crankshaft"])] @ np.append(rod.pin, 1))[:3]
        big = (pose[id(parts["connecting rod"])] @ np.append(rod.pin, 1))[:3]
        assert np.allclose(on_crank, big, atol=1e-9)
