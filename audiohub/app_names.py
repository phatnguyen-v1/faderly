"""Human names for apps, from what Windows already knows (no per-app list).

- A media player's app id (AUMID, e.g. 'com.riotgames.riotclient') resolves
  through the shell's Apps folder, as the Start menu does.
- An app's .exe carries a FileDescription (what Task Manager shows).
- Otherwise: tidy the id itself.
"""
import ctypes
import functools
import re
import time
from ctypes import wintypes

shell32 = ctypes.windll.shell32
ole32 = ctypes.windll.ole32
version = ctypes.windll.version


class GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                ("d4", ctypes.c_ubyte * 8)]


def _guid(s):
    g = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


IID_IShellItem = _guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
SIGDN_NORMALDISPLAY = 0
shell32.SHCreateItemFromParsingName.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
GetDisplayName = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_wchar_p))
Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


@functools.lru_cache(maxsize=256)
def from_app_id(aumid):
    """The Start menu name for an app id, or None if Windows doesn't know it."""
    if not aumid:
        return None
    ole32.CoInitializeEx(None, 0)  # harmless if this thread already has COM
    item = ctypes.c_void_p()
    try:
        shell32.SHCreateItemFromParsingName("shell:AppsFolder\\" + aumid, None,
                                            ctypes.byref(IID_IShellItem), ctypes.byref(item))
    except OSError:
        return None
    if not item.value:
        return None
    vtbl = ctypes.cast(ctypes.cast(item, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    try:
        out = ctypes.c_wchar_p()
        GetDisplayName(vtbl[5])(item, SIGDN_NORMALDISPLAY, ctypes.byref(out))  # IShellItem::GetDisplayName
        name = out.value
        ole32.CoTaskMemFree(out)
    except OSError:
        name = None
    finally:
        Release(vtbl[2])(item)
    # Unknown ids come back as the id itself (or a path): not a name.
    if not name or name == aumid or "\\" in name or name.lower().endswith(".exe"):
        return None
    return name


@functools.lru_cache(maxsize=256)
def from_exe(path):
    """FileDescription from an exe's version info, or None."""
    if not path:
        return None
    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buf):
        return None
    trans, n = ctypes.c_void_p(), wintypes.UINT()
    langs = []
    if version.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(trans), ctypes.byref(n)) and n.value:
        words = ctypes.cast(trans, ctypes.POINTER(wintypes.WORD))
        langs = [(words[i], words[i + 1]) for i in range(0, n.value // 2, 2)]
    for lang, cp in langs + [(0x0409, 0x04B0), (0x0409, 0x04E4)]:
        val = ctypes.c_wchar_p()
        if version.VerQueryValueW(buf, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription",
                                  ctypes.byref(val), ctypes.byref(n)) and n.value:
            desc = (val.value or "").strip()
            if desc:
                return desc
    return None


_by_process = {}
_misses = {}  # token -> when the search found nothing; retried after MISS_TTL
MISS_TTL = 60.0


def from_running_process(token):
    """Description of a running exe whose name contains `token`
    ('riotclient' -> RiotClientServices.exe -> 'Riot Client'). Hits are cached."""
    token = re.sub(r"[^a-z0-9]", "", token.lower())
    if len(token) < 4:
        return None
    if token in _by_process:
        return _by_process[token]
    if time.monotonic() - _misses.get(token, -MISS_TTL) < MISS_TTL:
        return None  # searched recently: don't walk the process list every poll
    import psutil
    for p in psutil.process_iter(["name", "exe"]):
        name = re.sub(r"[^a-z0-9]", "", (p.info["name"] or "").lower().removesuffix(".exe"))
        if token in name:
            desc = from_exe(p.info["exe"])
            if desc:
                _by_process[token] = desc
                return desc
    _misses[token] = time.monotonic()
    return None


def for_player(key, aumid, preferred=None):
    """Name for a media player row."""
    if preferred:
        return preferred
    name = from_app_id(aumid)
    if name:
        return name
    last = aumid.split("!")[-1].rsplit(".", 1)[-1]
    return from_running_process(last) or tidy(aumid)


def for_exe(key, exe, preferred=None):
    """Name for an app playing sound, from its process."""
    return preferred or from_exe(exe) or tidy(key)


def tidy(key):
    """Last resort: 'com.riotgames.riotclient' -> 'Riotclient', 'my_app' -> 'My app'."""
    part = key.split("!")[-1]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+", part):  # reverse-domain id: keep the last piece
        part = part.rsplit(".", 1)[-1]
    part = re.sub(r"[_\-]+", " ", part).strip() or key
    return part[:1].upper() + part[1:]
