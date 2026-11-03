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


# --- The open windows, and bringing one forward ---------------------------------------
#
# Guide mode tells the model which windows are open (front first), so a window
# that opened behind another gets brought forward instead of opened again, and
# it can put two windows side by side. Plain Win32 calls through private DLL
# handles (argtypes set here never leak into other modules); no UI Automation,
# so this is quick and works on any thread.

# Friendly names for common apps, by .exe (what the model and the know-how packs call them).
FRIENDLY = {
    "systemsettings.exe": "settings", "taskmgr.exe": "task manager", "explorer.exe": "file explorer",
    "chrome.exe": "chrome", "msedge.exe": "edge", "firefox.exe": "firefox", "brave.exe": "brave",
    "winword.exe": "word", "excel.exe": "excel", "powerpnt.exe": "powerpoint", "outlook.exe": "outlook",
    "olk.exe": "outlook", "ms-teams.exe": "teams", "teams.exe": "teams", "code.exe": "vs code",
    "spotify.exe": "spotify", "whatsapp.exe": "whatsapp", "snippingtool.exe": "snipping tool",
    "screenclippinghost.exe": "snipping tool", "notepad.exe": "notepad", "windowsterminal.exe": "terminal",
    "mmc.exe": "management console", "control.exe": "control panel", "msinfo32.exe": "system information",
    "calculatorapp.exe": "calculator", "photos.exe": "photos", "onenote.exe": "onenote",
}
_SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}
_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


@dataclass(frozen=True)
class Window:
    """An open top-level window, as Alt+Tab would list it."""
    hwnd: int
    title: str
    exe: str             # "Taskmgr.exe" (for a Store app, the app's own .exe, not its frame host)
    app: str             # friendly: "task manager"
    minimized: bool = False
    front: bool = False
    admin: bool = False  # runs as administrator: its buttons and your clicks in it are hidden from Clixy
    rect: Rect = (0, 0, 0, 0)

    def matches(self, name: str) -> bool:
        """Is this the window someone calls ``name`` ("task manager", "settings", "spotify")?"""
        want = " ".join((name or "").lower().split())
        if not want:
            return False
        exe = self.exe.lower()
        # A browser's title is whatever page is open ("Task Manager - Google Search"): never match on it.
        by_title = exe not in _BROWSERS and want in self.title.lower()
        return (by_title or want in (self.app, exe, exe.removesuffix(".exe"))
                or (len(self.app) > 2 and self.app in want))


def windows(limit: int = 10) -> list[Window]:
    """The open windows, front first (z-order), like Alt+Tab: titled, not
    tool windows, not cloaked, never Clixy's own. Empty off Windows."""
    if sys.platform != "win32":
        return []
    try:
        return _list_windows(limit)
    except Exception:
        return []


def focus(hwnd: int) -> bool:
    """Bring a window to the front (restoring it if minimised). True when it's
    in front afterwards. Clixy only ever brings windows forward; it never
    clicks or types into them."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        return _focus(int(hwnd))
    except Exception:
        return False


def side_by_side(first: int, second: int, monitor: dict | None = None) -> str:
    """Put two windows side by side on ``monitor``'s work area, ``first`` on
    the left and in front. Returns what happened in a few words for the model
    ("" when nothing could be moved). A window running as administrator can't
    be moved from here; the other one then takes the half away from it."""
    if sys.platform != "win32" or not first or not second:
        return ""
    try:
        return _side_by_side(int(first), int(second), monitor)
    except Exception:
        return ""


def halves(area: Rect) -> tuple[Rect, Rect]:
    """The left and right halves of a work area."""
    l, t, r, b = area
    mid = l + (r - l) // 2
    return (l, t, mid, b), (mid, t, r, b)


_W32: dict = {}


def _win32() -> dict:
    """user32, kernel32, advapi32 and dwmapi with argtypes set, made once."""
    if _W32:
        return _W32
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    p_dword = ctypes.POINTER(wt.DWORD)
    sig = {
        (user32, "EnumWindows"): (wt.BOOL, [enum_proc, wt.LPARAM]),
        (user32, "EnumChildWindows"): (wt.BOOL, [wt.HWND, enum_proc, wt.LPARAM]),
        (user32, "GetForegroundWindow"): (wt.HWND, []),
        (user32, "IsWindowVisible"): (wt.BOOL, [wt.HWND]),
        (user32, "IsIconic"): (wt.BOOL, [wt.HWND]),
        (user32, "IsZoomed"): (wt.BOOL, [wt.HWND]),
        (user32, "IsWindow"): (wt.BOOL, [wt.HWND]),
        (user32, "GetWindow"): (wt.HWND, [wt.HWND, wt.UINT]),
        (user32, "GetAncestor"): (wt.HWND, [wt.HWND, wt.UINT]),
        (user32, "GetWindowLongW"): (wt.LONG, [wt.HWND, ctypes.c_int]),
        (user32, "GetWindowTextW"): (ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
        (user32, "GetClassNameW"): (ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
        (user32, "GetWindowThreadProcessId"): (wt.DWORD, [wt.HWND, p_dword]),
        (user32, "GetWindowRect"): (wt.BOOL, [wt.HWND, ctypes.POINTER(wt.RECT)]),
        (user32, "ShowWindow"): (wt.BOOL, [wt.HWND, ctypes.c_int]),
        (user32, "BringWindowToTop"): (wt.BOOL, [wt.HWND]),
        (user32, "SetForegroundWindow"): (wt.BOOL, [wt.HWND]),
        (user32, "SwitchToThisWindow"): (None, [wt.HWND, wt.BOOL]),
        (user32, "AttachThreadInput"): (wt.BOOL, [wt.DWORD, wt.DWORD, wt.BOOL]),
        (user32, "PeekMessageW"): (wt.BOOL, [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]),
        (user32, "SetWindowPos"): (wt.BOOL, [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, wt.UINT]),
        (user32, "MonitorFromPoint"): (wt.HANDLE, [wt.POINT, wt.DWORD]),
        (user32, "GetMonitorInfoW"): (wt.BOOL, [wt.HANDLE, ctypes.c_void_p]),
        (kernel32, "GetCurrentThreadId"): (wt.DWORD, []),
        (kernel32, "GetCurrentProcess"): (wt.HANDLE, []),
        (kernel32, "OpenProcess"): (wt.HANDLE, [wt.DWORD, wt.BOOL, wt.DWORD]),
        (kernel32, "CloseHandle"): (wt.BOOL, [wt.HANDLE]),
        (kernel32, "QueryFullProcessImageNameW"): (wt.BOOL, [wt.HANDLE, wt.DWORD, wt.LPWSTR, p_dword]),
        (advapi32, "OpenProcessToken"): (wt.BOOL, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]),
        (advapi32, "GetTokenInformation"): (wt.BOOL, [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                                      p_dword]),
        (dwmapi, "DwmGetWindowAttribute"): (ctypes.c_long, [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]),
    }
    for (dll, name), (restype, argtypes) in sig.items():
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = restype, argtypes
    _W32.update(user32=user32, kernel32=kernel32, advapi32=advapi32, dwmapi=dwmapi, enum_proc=enum_proc,
                ctypes=ctypes, wt=wt)
    return _W32


def _text(fn, hwnd) -> str:
    buf = _win32()["ctypes"].create_unicode_buffer(512)
    fn(hwnd, buf, 512)
    return buf.value


def _pid_of(hwnd) -> int:
    w = _win32()
    pid = w["wt"].DWORD()
    w["user32"].GetWindowThreadProcessId(hwnd, w["ctypes"].byref(pid))
    return int(pid.value)


def _exe_of(pid: int) -> str:
    w = _win32()
    handle = w["kernel32"].OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = w["ctypes"].create_unicode_buffer(520)
        size = w["wt"].DWORD(520)
        if w["kernel32"].QueryFullProcessImageNameW(handle, 0, buf, w["ctypes"].byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        w["kernel32"].CloseHandle(handle)


def _elevated(handle) -> bool | None:
    """Does this process run elevated? None when Windows won't say."""
    w = _win32()
    token = w["wt"].HANDLE()
    if not w["advapi32"].OpenProcessToken(handle, 0x0008, w["ctypes"].byref(token)):   # TOKEN_QUERY
        return None
    try:
        value, size = w["wt"].DWORD(0), w["wt"].DWORD(0)
        if w["advapi32"].GetTokenInformation(token, 20, w["ctypes"].byref(value), 4, w["ctypes"].byref(size)):
            return bool(value.value)                                                    # TokenElevation
        return None
    finally:
        w["kernel32"].CloseHandle(token)


_SELF_ELEVATED: list = []


def _is_admin(pid: int) -> bool:
    """Does ``pid`` run as administrator while Clixy doesn't? Windows then
    hides its controls, and your clicks in it, from Clixy."""
    w = _win32()
    if not _SELF_ELEVATED:
        _SELF_ELEVATED.append(bool(_elevated(w["kernel32"].GetCurrentProcess())))
    if _SELF_ELEVATED[0]:
        return False
    handle = w["kernel32"].OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        state = _elevated(handle)
        return True if state is None else state   # a token we can't even read belongs to someone above us
    finally:
        w["kernel32"].CloseHandle(handle)


def _frame_app_pid(hwnd, frame_pid: int) -> int:
    """A Store app's frame (ApplicationFrameHost) holds the app's own window
    as a child; that child's process is the real app (SystemSettings.exe)."""
    w = _win32()
    found: list[int] = []

    def visit(child, _l):
        pid = _pid_of(child)
        if pid and pid != frame_pid:
            found.append(pid)
            return False
        return True

    w["user32"].EnumChildWindows(hwnd, w["enum_proc"](visit), 0)
    return found[0] if found else 0


def _list_windows(limit: int) -> list[Window]:
    w = _win32()
    user32, ctypes, wt = w["user32"], w["ctypes"], w["wt"]
    me = os.getpid()
    fg = int(user32.GetForegroundWindow() or 0)
    if fg:
        fg = int(user32.GetAncestor(fg, 3) or fg)   # GA_ROOTOWNER: a dialog or popup in front stands for its window
    order: list[int] = []
    user32.EnumWindows(w["enum_proc"](lambda h, _l: order.append(int(h or 0)) or True), 0)
    out: list[Window] = []
    exes: dict[int, str] = {}
    admins: dict[int, bool] = {}
    for hwnd in order:
        if not hwnd or not user32.IsWindowVisible(hwnd):
            continue
        ex = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF                    # GWL_EXSTYLE
        app_window = bool(ex & 0x00040000)                                       # WS_EX_APPWINDOW
        if (ex & 0x00000080 and not app_window) or ex & 0x08000000:              # TOOLWINDOW, NOACTIVATE
            continue
        if user32.GetWindow(hwnd, 4) and not app_window:                         # owned (a dialog's owner lists it)
            continue
        title = " ".join(_text(user32.GetWindowTextW, hwnd).split())
        if not title or _text(user32.GetClassNameW, hwnd) in _SKIP_CLASSES or _cloaked(hwnd):
            continue
        pid = _pid_of(hwnd)
        if pid == me:
            continue
        if pid not in exes:
            exes[pid] = _exe_of(pid)
        exe, real = exes[pid], pid
        if exe.lower() == "applicationframehost.exe":
            real = _frame_app_pid(hwnd, pid) or pid
            if real not in exes:
                exes[real] = _exe_of(real)
            exe = exes[real] or exe
        if real not in admins:
            admins[real] = _is_admin(real)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        friendly = FRIENDLY.get(exe.lower(), "")
        if not friendly and exe.lower() == "applicationframehost.exe":
            friendly = title.lower()   # a Store app whose own window is away (minimised): its title names it
        out.append(Window(hwnd=hwnd, title=title[:90], exe=exe or "?",
                          app=friendly or exe.lower().removesuffix(".exe"),
                          minimized=bool(user32.IsIconic(hwnd)), front=hwnd == fg,
                          admin=admins[real], rect=(rect.left, rect.top, rect.right, rect.bottom)))
        if len(out) >= limit:
            break
    if out and not fg:
        # Windows names no front window while focus is changing hands: the top one is it.
        out[0] = Window(**{**out[0].__dict__, "front": True})
    return out


def _focus(hwnd: int) -> bool:
    w = _win32()
    user32, kernel32, ctypes, wt = w["user32"], w["kernel32"], w["ctypes"], w["wt"]
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                               # SW_RESTORE
    if int(user32.GetForegroundWindow() or 0) == hwnd:
        return True
    # Windows only lets the app you're using hand over the front. Sharing its
    # input state for a moment (AttachThreadInput) lets Clixy pass it on.
    msg = wt.MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)                       # gives this thread a queue
    mine = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)
    if int(user32.GetForegroundWindow() or 0) != hwnd:
        user32.SwitchToThisWindow(hwnd, True)                                   # what Alt+Tab does
    return int(user32.GetForegroundWindow() or 0) == hwnd


def _work_area(monitor: dict | None, hwnd: int) -> Rect | None:
    w = _win32()
    user32, ctypes, wt = w["user32"], w["ctypes"], w["wt"]
    if monitor:
        cx = monitor.get("left", 0) + monitor.get("width", 0) // 2
        cy = monitor.get("top", 0) + monitor.get("height", 0) // 2
    else:
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    handle = user32.MonitorFromPoint(wt.POINT(cx, cy), 2)                        # MONITOR_DEFAULTTONEAREST
    if not handle or not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    a = info.rcWork
    return a.left, a.top, a.right, a.bottom


def _place(hwnd: int, rect: Rect) -> bool:
    """Move a window so its visible frame fills ``rect`` (Windows 10 and 11
    put an invisible resize border around it). True when it really moved."""
    w = _win32()
    user32, ctypes, wt = w["user32"], w["ctypes"], w["wt"]
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                               # SW_RESTORE
    outer, frame = wt.RECT(), wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    dl = dt = dr = db = 0
    if w["dwmapi"].DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame)) == 0:
        dl, dt = frame.left - outer.left, frame.top - outer.top                  # DWMWA_EXTENDED_FRAME_BOUNDS
        dr, db = outer.right - frame.right, outer.bottom - frame.bottom
    l, t, r, b = rect
    x, y, cx, cy = l - dl, t - dt, (r - l) + dl + dr, (b - t) + dt + db
    if not user32.SetWindowPos(hwnd, None, x, y, cx, cy, 0x0004 | 0x0010):      # NOZORDER | NOACTIVATE
        return False
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    return abs(outer.left - x) <= 4 and abs(outer.top - y) <= 4


def _side_by_side(first: int, second: int, monitor: dict | None) -> str:
    area = _work_area(monitor, first)
    if area is None:
        return ""
    left, right = halves(area)
    listed = {win.hwnd: win for win in windows(30)}
    a = listed[first].app if first in listed else "the first"
    b = listed[second].app if second in listed else "the second"
    first_admin = first in listed and listed[first].admin
    second_admin = second in listed and listed[second].admin
    moved_first = _place(first, left)
    if moved_first:
        moved_second = _place(second, right)
    else:
        # The first can't move (it runs as administrator): the second takes the half away from it.
        w = _win32()
        r = w["wt"].RECT()
        w["user32"].GetWindowRect(first, w["ctypes"].byref(r))
        on_left = (r.left + r.right) // 2 < (area[0] + area[2]) // 2
        moved_second = _place(second, right if on_left else left)
    # The first ends in front, unless only it runs as administrator: while an
    # admin window has the keyboard, Windows hides your keys (Clixy's hotkey
    # too), so the other one stays in front until you click into it.
    if first_admin and not second_admin:
        _focus(first)
        _focus(second)
    else:
        _focus(second)
        _focus(first)
    if moved_first and moved_second:
        return f"{a} on the left, {b} on the right"
    if moved_second:
        return f"{b} beside {a} ({a} couldn't be moved: it runs as administrator)"
    if moved_first:
        return f"{a} on the left; {b} couldn't be moved"
    return ""
