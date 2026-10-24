"""Tests for theme: avatars, pixel snapping, the current-avatar setting."""
import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_five_avatars_each_with_its_own_theme():
    import theme
    assert len(theme.AVATARS) == 5
    assert len({a.id for a in theme.AVATARS}) == 5
    assert len({a.palette.name for a in theme.AVATARS}) == 5


def test_unknown_avatar_falls_back_to_the_default():
    import theme
    assert theme.avatar("nope").id == theme.DEFAULT_AVATAR


def test_cells_are_whole_device_pixels_at_any_scaling():
    import theme
    for dpr in (1.0, 1.25, 1.5, 1.75, 2.0):
        cell = theme.snapped_cell(1.0, dpr)
        assert abs(cell * dpr - round(cell * dpr)) < 1e-9
        assert cell * dpr >= 1


def test_blinks_now_and_then_but_not_all_the_time():
    import theme
    blinks = [theme.is_blinking(f) for f in range(470)]
    assert any(blinks)
    assert sum(blinks) < len(blinks) * 0.1


def test_set_avatar_switches_live_and_persists(monkeypatch):
    import keyring
    import theme
    saved = {}
    monkeypatch.setattr(keyring, "set_password", lambda svc, name, value: saved.__setitem__(name, value))
    seen = []
    theme.on_avatar_changed(seen.append)
    try:
        theme.set_avatar("mochi")
        assert theme.current_avatar_id() == "mochi"
        assert theme.current_palette().name == "Midnight"
        assert saved["AVATAR"] == "mochi"
        assert seen[-1] == "mochi"
    finally:
        theme._listeners.remove(seen.append)
        theme.set_avatar(theme.DEFAULT_AVATAR, persist=False)


def test_stylesheet_uses_the_brand(qapp):
    import theme
    theme.load_fonts()
    css = theme.app_stylesheet()
    for color in (theme.INK, theme.BUTTER, theme.PAPER, theme.CORAL):
        assert color.name() in css
    assert "QPushButton#primary" in css and "QWidget#rail" in css


def test_the_logo_is_ember_on_an_ink_tile(qapp):
    """The app icon is Ember, the pixel fire, on an ink tile: dark corners,
    warm fire in the middle, at every icon size."""
    import theme
    for size in (16, 32, 64, 256):
        image = theme.app_icon_image(size)
        assert image.width() == size and not image.isNull()
    big = theme.app_icon_image(256)
    corner = big.pixelColor(30, 30)
    assert corner.red() < 60 and corner.green() < 60
    warm = [big.pixelColor(x, y) for x in range(80, 180, 6) for y in range(60, 200, 6)]
    assert any(c.red() > 220 and c.green() > 120 and c.blue() < 120 for c in warm)


def test_avatar_fits_centered_in_a_rect(qapp):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    import theme
    for av in theme.AVATARS:
        image = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        p = QPainter(image)
        theme.paint_avatar_in(p, QRectF(0, 0, 80, 80), t=0.0, avatar_id=av.id, scale=1.5)
        p.end()
        lit = [(x, y) for x in range(0, 80, 2) for y in range(0, 80, 2) if image.pixelColor(x, y).alpha()]
        xs, ys = [x for x, _ in lit], [y for _, y in lit]
        assert lit, av.id
        # Centred by its framing box over all frames, so one frame can sit a
        # little off-centre (a leaf swaying, a tail flicking).
        assert abs((min(xs) + max(xs)) / 2 - 40) < 10 and abs((min(ys) + max(ys)) / 2 - 40) < 10, av.id


def test_avatar_icons_render_for_every_avatar(qapp):
    import theme
    for av in theme.AVATARS:
        image = theme.avatar_image(av.id, 32)
        assert image.width() == 32
        opaque = sum(image.pixelColor(x, y).alpha() > 0 for x in range(32) for y in range(32))
        assert opaque > 100, av.id


def test_edge_glow_is_empty_when_off_and_lit_at_the_edge_when_on(qapp):
    import theme
    off = theme.edge_glow_image(600, 400, t=0.0, level=0.0)
    assert off.pixelColor(0, off.height() // 2).alpha() == 0
    on = theme.edge_glow_image(600, 400, t=0.0, level=1.0)
    assert on.pixelColor(0, on.height() // 2).alpha() > 0
    assert on.pixelColor(on.width() // 2, on.height() // 2).alpha() == 0
