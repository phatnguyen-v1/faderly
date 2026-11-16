"""Bring an app's main window to the front ("open the sound source")."""
import ctypes
import os
from ctypes import wintypes

import psutil

from .win_audio import app_key

user32 = ctypes.WinDLL("user32", use_last_error=True)

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 2

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND


def _pids(key):
    return {p.pid for p in psutil.process_iter(["name"])
            if p.info["name"] and app_key(p.info["name"]) == key}


WM_CLOSE = 0x0010
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]


def app_windows(key, first_only=False):
    """Visible, titled, top-level windows of any process named `key`, front first."""
    pids = _pids(key)
    found = []

    @EnumWindowsProc
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (pid.value in pids and user32.IsWindowVisible(hwnd)
                and not user32.GetWindow(hwnd, GW_OWNER)
                and not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW
                and user32.GetWindowTextLengthW(hwnd) > 0):
            found.append(hwnd)
            return not first_only  # EnumWindows goes top of the z-order first
        return True

    if pids:
        user32.EnumWindows(cb, 0)
    return found


def main_window(key):
    """The app window in use: the frontmost one."""
    found = app_windows(key, first_only=True)
    return found[0] if found else None


# Windows itself, and us: "close" there would shut every File Explorer window, the
# shell or Faderly. Their rows get no close.
NEVER_CLOSE = {"explorer", "audiohub", "faderly", "python", "pythonw", "dwm", "svchost", "audiodg",
               "shellexperiencehost", "startmenuexperiencehost", "searchhost",
               "systemsettings", "applicationframehost", "textinputhost"}


def can_close(key):
    return key not in NEVER_CLOSE


def close_app(key):
    """Ask the app to close, as its own ✕ / Alt+F4 would: WM_CLOSE to each of its
    windows. Never a force-quit, so an app can still ask about unsaved work, and a
    tray app (Discord, Spotify...) just hides as usual. Returns the windows asked."""
    if not can_close(key):
        return 0
    windows = app_windows(key)
    for hwnd in windows:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return len(windows)


def raise_app(key):
    """Restore and focus the app's window. False if it has none (e.g. only in the tray)."""
    hwnd = main_window(key)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if not user32.SetForegroundWindow(hwnd):
        # Windows only lets the foreground app hand focus over; a tapped Alt key
        # counts as user input and lifts that lock.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
    return True
