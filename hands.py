"""Clixy's hands, for "do it for me": a click, a line of typing, a shortcut.

Used only by guide mode when you've asked Clixy to do the steps itself
(``guide.Guide.drive``). Every action is a real Windows input event
(``SendInput``), the same as your own mouse and keyboard, so it works in any
app; the guide decides what is safe to do (it never clicks send, buy, delete
and the like without your yes, and never types into a password field).

- ``click(x, y)``: the pointer glides to (x, y), in physical pixels like the
  rectangles Windows reports for each control, and clicks there.
- ``type_text(text)``: each character as typed Unicode, so any language works.
- ``press("ctrl+shift+esc")``: the keys together, released in reverse.

Windows marks these events as injected; guide.InputWatch skips them, so
Clixy's own clicks are never taken for yours (yours mean "I'll take over").

Off Windows every action does nothing and returns False.

Threading: called on the guide's worker thread.
"""
from __future__ import annotations

import ctypes
import sys
import time

_MOUSE, _KEYBOARD = 0, 1
_LEFTDOWN, _LEFTUP = 0x0002, 0x0004
_MOVE, _ABSOLUTE, _VIRTUALDESK = 0x0001, 0x8000, 0x4000
_WHEEL, _NOTCH = 0x0800, 120
_KEYUP, _UNICODE, _EXTENDED = 0x0002, 0x0004, 0x0001

_VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "prtsc": 0x2C, "capslock": 0x14, "menu": 0x5D,
    "plus": 0xBB, "=": 0xBB, "minus": 0xBD, "-": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA,
    "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0,
}
_VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
_EXTENDED_KEYS = {0x5B, 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5D}


def vk_codes(combo: str) -> list[int] | None:
    """The virtual-key codes for "ctrl+shift+esc" (letters and digits included); None if
    any key is unknown."""
    codes: list[int] = []
    for raw in (combo or "").lower().replace(" ", "").split("+"):
        if not raw:
            continue
        if raw in _VK:
            codes.append(_VK[raw])
        elif len(raw) == 1 and raw.isalnum():
            codes.append(ord(raw.upper()))
        else:
            return None
    return codes or None


if sys.platform == "win32":
    from ctypes import wintypes

    _ULONG_PTR = ctypes.c_size_t

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    def _send(inputs: list) -> bool:
        if not inputs:
            return True
        array = (_INPUT * len(inputs))(*inputs)
        return ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT)) == len(inputs)

    def _mouse(flags: int) -> "_INPUT":
        return _INPUT(type=_MOUSE, u=_UNION(mi=_MOUSEINPUT(0, 0, 0, flags, 0, 0)))

    def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> "_INPUT":
        if vk in _EXTENDED_KEYS:
            flags |= _EXTENDED
        return _INPUT(type=_KEYBOARD, u=_UNION(ki=_KEYBDINPUT(vk, scan, flags, 0, 0)))


def _cursor() -> tuple[int, int] | None:
    point = ctypes.wintypes.POINT() if sys.platform == "win32" else None
    if point is None or not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


def _move_to(x: float, y: float) -> bool:
    """Move the pointer as an injected event (so it never reads as you moving it)."""
    user32 = ctypes.windll.user32
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)      # the whole desktop
    width, height = max(2, user32.GetSystemMetrics(78)), max(2, user32.GetSystemMetrics(79))
    dx = round((x - left) * 65535 / (width - 1))
    dy = round((y - top) * 65535 / (height - 1))
    move = _INPUT(type=_MOUSE, u=_UNION(mi=_MOUSEINPUT(dx, dy, 0, _MOVE | _ABSOLUTE | _VIRTUALDESK, 0, 0)))
    return _send([move])


def click(x: int, y: int, *, glide_s: float = 0.18) -> bool:
    """Glide the pointer to (x, y), physical px, and left-click there."""
    if sys.platform != "win32":
        return False
    start = _cursor()
    if start is not None and glide_s > 0:
        steps = 9
        for i in range(1, steps + 1):
            k = i / steps
            k = k * k * (3 - 2 * k)                      # ease in and out
            _move_to(start[0] + (x - start[0]) * k, start[1] + (y - start[1]) * k)
            time.sleep(glide_s / steps)
    _move_to(x, y)
    time.sleep(0.03)
    if not _send([_mouse(_LEFTDOWN)]):
        return False
    time.sleep(0.04)
    return _send([_mouse(_LEFTUP)])


def scroll(x: int, y: int, notches: int) -> bool:
    """Scroll the wheel over (x, y), physical px: ``notches`` down (positive) or up
    (negative), a few at a time like a hand would, so pages keep up."""
    if sys.platform != "win32" or not notches:
        return False
    _move_to(x, y)
    time.sleep(0.03)
    ok = True
    step = -1 if notches > 0 else 1                       # the wheel turns towards you to go down
    for _ in range(min(abs(int(notches)), 25)):
        wheel = _INPUT(type=_MOUSE, u=_UNION(mi=_MOUSEINPUT(0, 0, ctypes.c_uint32(step * _NOTCH).value,
                                                            _WHEEL, 0, 0)))
        ok = _send([wheel]) and ok
        time.sleep(0.025)
    return ok


def type_text(text: str, *, per_char_s: float = 0.008) -> bool:
    """Type ``text`` as Unicode characters (any language, emoji included)."""
    if sys.platform != "win32":
        return False
    ok = True
    for unit in _utf16_units(text or ""):
        if unit == 0x0A:                                  # a new line is Enter
            ok = _send([_key(0x0D), _key(0x0D, flags=_KEYUP)]) and ok
        else:
            ok = _send([_key(0, unit, _UNICODE), _key(0, unit, _UNICODE | _KEYUP)]) and ok
        if per_char_s:
            time.sleep(per_char_s)
    return ok


def press(combo: str) -> bool:
    """Press the keys together ("win+i"), then let go in reverse order."""
    if sys.platform != "win32":
        return False
    codes = vk_codes(combo)
    if not codes:
        return False
    if not _send([_key(vk) for vk in codes]):
        return False
    time.sleep(0.05)
    return _send([_key(vk, flags=_KEYUP) for vk in reversed(codes)])


def _utf16_units(text: str) -> list[int]:
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
