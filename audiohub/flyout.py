"""The small mixer that pops up next to the tray icon (left click), like Windows' own
volume flyout. The full window stays one right-click away (tray menu -> Open).

Both are pywebview windows on the main thread; the tray thread only calls
show/hide here. pywebview has no focus-lost event, and the page's own `blur`
fires spuriously while Windows hands focus over on show (it hid the flyout
the instant it opened). So a small watcher thread hides it once another
window takes the foreground.
"""
import threading
import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger("audiohub")

WIDTH = 380
MIN_H, MAX_H = 160, 900  # MAX_H is also capped by the screen, see max_height()
MARGIN = 12
REOPEN_GUARD = 0.4  # s: a tray click that also took focus away must not reopen it
WATCH_EVERY = 0.1  # s

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

TITLE = "Audio Hub mixer"
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80, 0x40000


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]


def _own_window(title):
    """Our process's top-level window with this title (no cross-thread WinForms calls)."""
    pid, found = kernel32.GetCurrentProcessId(), []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, buf, 256)
            if buf.value == title:
                found.append(h)
                return False
        return True

    user32.EnumWindows(proc(cb), 0)
    return found[0] if found else None


def make_tool_window(hwnd):
    """No taskbar button: nothing to flash when Windows refuses focus."""
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if want != ex:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, want)


def take_focus(hwnd):
    """Bring the flyout to the foreground, as Windows' own tray flyouts do.

    Windows only lets the foreground thread hand focus over, so join its input
    queue for the call. A tapped Alt key is the last resort (it counts as input).
    """
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    our_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(fg_thread and fg_thread != our_thread
                    and user32.AttachThreadInput(fg_thread, our_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        ok = user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, our_thread, False)
    if not ok:
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        ok = user32.SetForegroundWindow(hwnd)
    # A refused request leaves the window flashing; stop it either way.
    fw = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0, 0, 0)  # FLASHW_STOP
    user32.FlashWindowEx(ctypes.byref(fw))
    return bool(ok)


def _work_area():
    """Primary monitor work area (screen minus taskbar), physical pixels."""
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def _cursor():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _scale():
    try:
        return user32.GetDpiForSystem() / 96
    except Exception:
        return 1.0


def place(width, height, work=None, cursor=None, scale=None):
    """Top-left corner (logical px) for a flyout of this size, in the corner of the
    work area nearest the taskbar: bottom-right for the usual bottom taskbar."""
    left, top, right, bottom = work or _work_area()
    cx, cy = cursor or _cursor()
    k = scale or _scale()
    left, top, right, bottom, cx, cy = (v / k for v in (left, top, right, bottom, cx, cy))
    x = min(max(cx - width / 2, left + MARGIN), right - width - MARGIN)
    y = top + MARGIN if cy < top else bottom - height - MARGIN  # taskbar at the top
    if cx < left:   # taskbar on the left
        x = left + MARGIN
    elif cx > right:  # taskbar on the right
        x = right - width - MARGIN
    return int(x), int(y)


def max_height(work=None, scale=None):
    """Tallest flyout that fits the work area with a margin above and below (logical px)."""
    left, top, right, bottom = work or _work_area()
    return int(min(MAX_H, (bottom - top) / (scale or _scale()) - 2 * MARGIN))


class Flyout:
    def __init__(self, open_full):
        self.window = None
        self.height = 480
        self.visible = False
        self.hidden_at = 0.0
        self._open_full = open_full
        self._hwnd = None
        self._shown_seq = 0
        self.anchor = None  # cursor at the tray click; later resizes keep to it

    # ---- called from the tray thread -----------------------------------
    def toggle(self):
        if self.visible:
            self.hide()
        elif time.monotonic() - self.hidden_at > REOPEN_GUARD:
            self.show()

    def show(self):
        w = self.window
        if not self._hwnd:
            self._hwnd = _own_window(TITLE)
        if self._hwnd:
            make_tool_window(self._hwnd)  # before it shows, so no taskbar button appears
        # Size too, not just position: the page may have reported a new height
        # while hidden, and a stale size would leave the bottom short of the taskbar.
        # The pointer is on the tray icon now; remember it, because later resizes
        # (a row opening its volume slider) happen with the pointer inside the flyout.
        self.anchor = _cursor()
        x, y = place(WIDTH, self.height, cursor=self.anchor)
        w.resize(WIDTH, self.height)
        w.move(x, y)
        w.show()
        self.visible = True
        # Without focus there is no blur, and it wouldn't hide when you click elsewhere.
        try:
            if self._hwnd and not take_focus(self._hwnd):
                log.info("flyout could not take focus")
        except Exception:
            log.exception("flyout focus failed")
        self._shown_seq += 1
        threading.Thread(target=self._watch, args=(self._shown_seq,), daemon=True,
                         name="audiohub-flyout-watch").start()
        try:
            w.evaluate_js("window.flyoutShown && flyoutShown()")
        except Exception:
            pass

    def _watch(self, seq):
        """Hide when the user moves on to another window.

        The window that was in front at show time (the taskbar, after a tray
        click) doesn't count until it changes, so a refused focus request
        doesn't close the flyout at once either.
        """
        start = user32.GetForegroundWindow()
        while self.visible and seq == self._shown_seq:
            time.sleep(WATCH_EVERY)
            fg = user32.GetForegroundWindow()
            if fg == self._hwnd:
                start = None  # it has had focus: any other window now means "gone"
            elif fg and fg != start:
                self.hide()
                return

    def hide(self):
        if self.visible:
            self.visible = False
            self.hidden_at = time.monotonic()
            self.window.hide()


class FlyoutApi:
    """window.pywebview.api.* for the flyout page."""

    def __init__(self, flyout):
        self._f = flyout

    def blur(self):
        """Esc in the page."""
        self._f.hide()

    def open_full(self):
        self._f.hide()
        self._f._open_full()

    def fit(self, height):
        """The page asks for the height of its content; the bottom edge stays put."""
        h = int(min(max_height(), max(MIN_H, float(height))))
        f = self._f
        if abs(h - f.height) < 2:
            return
        f.height = h
        if f.visible:
            x, y = place(WIDTH, h, cursor=f.anchor)
            f.window.resize(WIDTH, h)
            f.window.move(x, y)
