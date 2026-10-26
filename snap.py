"""Snap a mark to the real thing under it, and list what's clickable on
screen, using Windows UI Automation.

The model guesses a box from a shrunken screenshot, so its circles and
boxes come out loose: too big, a little off. Windows knows the exact
rectangle of the button, tab, field or menu item at that spot. ``refine``
asks for it and, when the answer looks like what the model meant (it
contains the model's centre and is about the same size), swaps it in, so
the buddy's loop hugs the real element.

``elements`` lists the clickable things on screen (in the foreground window,
any menu or flyout above it, and the taskbar) with their exact rectangles,
so guide mode can point at a real element by id instead of guessing pixels.

Nothing is sent anywhere: UI Automation is a local Windows API. Off
Windows, or when an app is slow to answer (each lookup gets a quarter of a
second) or the answer looks wrong, the marks keep the model's coordinates.

Threading: ``refine`` runs on the pipeline's worker thread. UI Automation
lives on one helper thread of its own with COM set up there.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from dataclasses import dataclass

# UI Automation control types (UIA_*ControlTypeId).
_TEXT, _IMAGE = 50020, 50006
_CLICKABLE = {
    50000,  # button
    50002,  # check box
    50003,  # combo box
    50005,  # hyperlink
    50007,  # list item
    50011,  # menu item
    50013,  # radio button
    50019,  # tab item
    50024,  # tree item
    50029,  # data item
    50031,  # split button
    50035,  # header item
}
_CONTAINERS = {
    50008,  # list
    50009,  # menu
    50010,  # menu bar
    50017,  # status bar
    50018,  # tab
    50021,  # tool bar
    50023,  # tree
    50026,  # group
    50028,  # data grid
    50030,  # document
    50032,  # window
    50033,  # pane
    50036,  # table
    50037,  # title bar
}

Rect = tuple[int, int, int, int]   # left, top, right, bottom in physical px

# What elements() lists, and the word the model sees for each.
_LISTED = {
    50000: "button", 50002: "checkbox", 50003: "dropdown", 50004: "field", 50005: "link",
    50007: "list item", 50011: "menu item", 50013: "option", 50015: "slider", 50019: "tab",
    50024: "tree item", 50029: "item", 50031: "button", 50035: "header",
}
_P_RECT, _P_TYPE, _P_NAME, _P_ENABLED, _P_OFFSCREEN = 30001, 30003, 30005, 30010, 30022
_SCOPE_DESCENDANTS = 4


@dataclass(frozen=True)
class Element:
    """Something clickable on screen: its kind ("button"), name and exact rectangle."""
    kind: str
    name: str
    rect: Rect


def pick_rect(element: tuple[Rect, int], parent: tuple[Rect, int] | None) -> tuple[Rect, int]:
    """The text or icon inside a button stands for the button: use the
    parent when it is something clickable not much bigger than its child."""
    (rect, kind) = element
    if parent is not None and kind in (_TEXT, _IMAGE):
        prect, pkind = parent
        if pkind in _CLICKABLE and _area(prect) <= 8 * max(1, _area(rect)):
            return prect, pkind
    return rect, kind


def _area(r: Rect) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def accept(model: Rect, element: Rect, kind: int, monitor: dict) -> bool:
    """Is the element's rectangle what the model meant? It must contain the
    model's centre, be a real control (not a window or panel), fit the
    screen sensibly and be about the model's size."""
    if kind in _CONTAINERS:
        return False
    l, t, r, b = element
    w, h = r - l, b - t
    if w < 6 or h < 6:
        return False
    if w > 0.5 * monitor.get("width", 1 << 16) or h > 0.4 * monitor.get("height", 1 << 16):
        return False
    cx, cy = (model[0] + model[2]) / 2, (model[1] + model[3]) / 2
    if not (l - 3 <= cx <= r + 3 and t - 3 <= cy <= b + 3):
        return False
    ratio = _area(element) / max(1, _area(model))
    return 0.12 <= ratio <= 6.0


def refine(annotations: list, monitor: dict, *, rect_at=None, dbg=None) -> list:
    """Snap boxes and circles (PHYSICAL px) to the elements under them.
    Other shapes, and any that don't snap, stay as they are."""
    from annotations import Box, Circle

    if rect_at is None:
        rect_at = element_rect_at
    out = []
    for a in annotations:
        if isinstance(a, Box):
            model, label = (a.x1, a.y1, a.x2, a.y2), a.label
        elif isinstance(a, Circle):
            model, label = (a.x - a.r, a.y - a.r, a.x + a.r, a.y + a.r), a.label
        else:
            out.append(a)
            continue
        found = rect_at((model[0] + model[2]) // 2, (model[1] + model[3]) // 2)
        if found is not None and accept(model, found[0], found[1], monitor):
            l, t, r, b = found[0]
            if dbg is not None:
                dbg.log(f"SNAP: {model} -> {found[0]} (type {found[1]})")
            out.append(Box(l, t, r, b, label))
        else:
            if dbg is not None:
                dbg.log(f"SNAP: kept {model} ({'no element' if found is None else found})")
            out.append(a)
    return out


# --- UI Automation on its own thread ------------------------------------------------

_TIMEOUT_S = 0.25
_jobs: queue.Queue | None = None
_broken = False
_lock = threading.Lock()


def _run(job, timeout_s: float):
    """Run ``job(uia, walker)`` on the UI Automation thread and return what it
    returns, or None if it fails or takes longer than ``timeout_s``."""
    global _jobs
    if sys.platform != "win32" or _broken:
        return None
    with _lock:
        if _jobs is None:
            _jobs = queue.Queue()
            threading.Thread(target=_serve, args=(_jobs,), name="clixy-uia", daemon=True).start()
    reply: queue.Queue = queue.Queue(maxsize=1)
    _jobs.put((job, reply))
    try:
        return reply.get(timeout=timeout_s)
    except queue.Empty:
        return None


def element_rect_at(x: int, y: int) -> tuple[Rect, int] | None:
    """The rectangle and control type of the UI element at physical screen
    point (x, y), or None. Never raises, never waits long."""
    return _run(lambda uia, walker: _rect_at(uia, walker, x, y), _TIMEOUT_S)


def elements(monitor: dict | None = None, *, limit: int = 120, timeout_s: float = 1.5) -> list[Element]:
    """The clickable things on ``monitor`` (physical px; None for all), top
    window first: the foreground window, any menu or flyout open above it,
    then the taskbar. Empty off Windows, or when Windows is too slow."""
    return _run(lambda uia, _walker: _list_elements(uia, monitor, limit), timeout_s) or []


def keep_element(kind_id: int, name: str, rect: Rect, monitor: dict | None) -> Element | None:
    """The element as listed, or None when it's not worth listing: an
    unlisted kind, too small, off the monitor, or unnamed (fields may be)."""
    kind = _LISTED.get(kind_id)
    if kind is None:
        return None
    l, t, r, b = rect
    if r - l < 4 or b - t < 4 or not _on_monitor(rect, monitor):
        return None
    name = " ".join((name or "").split())
    if not name and kind != "field":
        return None
    return Element(kind, name[:80], rect)


def _on_monitor(rect: Rect, monitor: dict | None) -> bool:
    if monitor is None:
        return True
    ml, mt = monitor.get("left", 0), monitor.get("top", 0)
    mr, mb = ml + monitor.get("width", 0), mt + monitor.get("height", 0)
    l, t, r, b = rect
    return r > ml and l < mr and b > mt and t < mb


def foreground() -> tuple[int, str]:
    """The foreground window's handle and title; (0, "") off Windows."""
    if sys.platform != "win32":
        return 0, ""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return int(hwnd or 0), buf.value
    except Exception:
        return 0, ""


def _rect_at(uia, walker, x: int, y: int):
    import ctypes.wintypes
    element = uia.ElementFromPoint(ctypes.wintypes.POINT(x, y))
    if element is None or element.CurrentProcessId == os.getpid():
        return None
    r = element.CurrentBoundingRectangle
    child = ((r.left, r.top, r.right, r.bottom), element.CurrentControlType)
    parent = None
    if child[1] in (_TEXT, _IMAGE):
        up = walker.GetParentElement(element)
        if up is not None:
            pr = up.CurrentBoundingRectangle
            parent = ((pr.left, pr.top, pr.right, pr.bottom), up.CurrentControlType)
    return pick_rect(child, parent)


def _top_windows(monitor: dict | None) -> list[int]:
    """Windows to list, top first: everything visible above the foreground
    window on this monitor (menus, flyouts, the Win+P panel), the foreground
    window itself, then the taskbar. Never Clixy's own windows."""
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    me = os.getpid()
    fg = user32.GetForegroundWindow()
    order: list[int] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(lambda h, _l: order.append(h) or True)
    user32.EnumWindows(proc, 0)
    picked: list[int] = []
    for hwnd in order:
        if not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
            continue
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            continue
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < 8 or rect.bottom - rect.top < 8:
            continue
        if not _on_monitor((rect.left, rect.top, rect.right, rect.bottom), monitor):
            continue
        picked.append(hwnd)
        if hwnd == fg or len(picked) >= 6:
            break
    if fg and fg not in picked:
        picked.append(fg)
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    if tray and tray not in picked:
        picked.append(tray)
    return picked


def _cloaked(hwnd) -> bool:
    """Hidden-but-"visible" windows (suspended Store apps, other desktops)."""
    try:
        import ctypes
        value = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
        return value.value != 0
    except Exception:
        return False


def _list_elements(uia, monitor: dict | None, limit: int) -> list[Element]:
    cache = uia.CreateCacheRequest()
    for prop in (_P_RECT, _P_TYPE, _P_NAME, _P_ENABLED, _P_OFFSCREEN):
        cache.AddProperty(prop)
    condition = None
    for kind_id in _LISTED:
        one = uia.CreatePropertyCondition(_P_TYPE, kind_id)
        condition = one if condition is None else uia.CreateOrCondition(condition, one)
    out: list[Element] = []
    seen: set[tuple[Rect, str]] = set()
    for hwnd in _top_windows(monitor):
        try:
            found = uia.ElementFromHandle(hwnd).FindAllBuildCache(_SCOPE_DESCENDANTS, condition, cache)
        except Exception:
            continue
        for i in range(found.Length if found is not None else 0):
            try:
                e = found.GetElement(i)
                if e.CachedIsOffscreen or not e.CachedIsEnabled:
                    continue
                r = e.CachedBoundingRectangle
                item = keep_element(e.CachedControlType, e.CachedName, (r.left, r.top, r.right, r.bottom), monitor)
            except Exception:
                continue
            if item is None or (item.rect, item.name) in seen:
                continue
            seen.add((item.rect, item.name))
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def _serve(jobs: queue.Queue) -> None:
    global _broken
    try:
        import ctypes.wintypes
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialised on this thread
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        walker = uia.ControlViewWalker
    except Exception:
        _broken = True
        return
    while True:
        job, reply = jobs.get()
        try:
            result = job(uia, walker)
        except Exception:
            result = None
        try:
            reply.put_nowait(result)
        except queue.Full:
            pass
