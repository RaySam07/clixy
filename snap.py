"""Snap a mark to the real thing under it, using Windows UI Automation.

The model guesses a box from a shrunken screenshot, so its circles and
boxes come out loose: too big, a little off. Windows knows the exact
rectangle of the button, tab, field or menu item at that spot. ``refine``
asks for it and, when the answer looks like what the model meant (it
contains the model's centre and is about the same size), swaps it in, so
the buddy's loop hugs the real element.

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


def element_rect_at(x: int, y: int) -> tuple[Rect, int] | None:
    """The rectangle and control type of the UI element at physical screen
    point (x, y), or None. Never raises, never waits long."""
    global _jobs
    if sys.platform != "win32" or _broken:
        return None
    with _lock:
        if _jobs is None:
            _jobs = queue.Queue()
            threading.Thread(target=_serve, args=(_jobs,), name="clixy-uia", daemon=True).start()
    reply: queue.Queue = queue.Queue(maxsize=1)
    _jobs.put((x, y, reply))
    try:
        return reply.get(timeout=_TIMEOUT_S)
    except queue.Empty:
        return None


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
    me = os.getpid()
    while True:
        x, y, reply = jobs.get()
        result = None
        try:
            element = uia.ElementFromPoint(ctypes.wintypes.POINT(x, y))
            if element is not None and element.CurrentProcessId != me:
                r = element.CurrentBoundingRectangle
                child = ((r.left, r.top, r.right, r.bottom), element.CurrentControlType)
                parent = None
                if child[1] in (_TEXT, _IMAGE):
                    up = walker.GetParentElement(element)
                    if up is not None:
                        pr = up.CurrentBoundingRectangle
                        parent = ((pr.left, pr.top, pr.right, pr.bottom), up.CurrentControlType)
                result = pick_rect(child, parent)
        except Exception:
            result = None
        try:
            reply.put_nowait(result)
        except queue.Full:
            pass
