"""One Audio Hub at a time, like other Windows apps.

The first copy holds a named mutex. A later launch finds it taken, asks the
first copy to show its window (a named event), and exits. Names include the
port: two copies on one port can't both serve, while a run on another port is
deliberate (development, tests).
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes

log = logging.getLogger("audiohub")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
INFINITE = 0xFFFFFFFF
ASFW_ANY = 0xFFFFFFFF

_held = []  # handles kept open for the life of the process
_show_events = {}  # port -> the event a later launch sets


def names(port):
    base = f"Local\\AudioHub-{port}"  # Local\\: per user session
    return base + "-instance", base + "-show"


def acquire(port):
    """True if this is the only Audio Hub on `port`; it then owns the lock until exit."""
    mutex_name, event_name = names(port)
    h = kernel32.CreateMutexW(None, False, mutex_name)
    first = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    if not h:
        log.warning("single-instance lock unavailable; continuing")
        return True
    if not first:
        kernel32.CloseHandle(h)
        return False
    _held.append(h)
    # Create the show event now, so a launch right after this one can find it.
    ev = kernel32.CreateEventW(None, False, False, event_name)  # auto-reset
    if ev:
        _held.append(ev)
        _show_events[port] = ev
    return True


def show_existing(port, timeout=3.0):
    """Ask the running copy to show its window. True if it got the message."""
    _, event_name = names(port)
    # We were just launched by the user, so we may bring a window forward; pass that on.
    user32.AllowSetForegroundWindow(ASFW_ANY)
    end = time.monotonic() + timeout
    while True:
        ev = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, event_name)
        if ev:
            try:
                return bool(kernel32.SetEvent(ev))
            finally:
                kernel32.CloseHandle(ev)
        if time.monotonic() > end:  # the first copy is still starting up, or stuck
            return False
        time.sleep(0.1)


def listen(port, on_show):
    """Call on_show() each time a later launch asks for the window (daemon thread)."""
    ev = _show_events.get(port)
    if not ev:
        return

    def run():
        while True:
            if kernel32.WaitForSingleObject(ev, INFINITE) != 0:
                return
            try:
                on_show()
            except Exception:
                log.exception("showing the window for a second launch failed")

    threading.Thread(target=run, daemon=True, name="audiohub-single-instance").start()
