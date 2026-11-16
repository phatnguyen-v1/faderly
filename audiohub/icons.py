"""App icons from the Windows shell, as PNG bytes (what Explorer / Start show).

A source is an exe path or 'shell:AppsFolder\\<app id>' for media players.
Shell image calls want a single-threaded apartment, so they run on one
dedicated STA thread (see IconCache).
"""
import ctypes
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

log = logging.getLogger("audiohub")

shell32 = ctypes.windll.shell32
ole32 = ctypes.windll.ole32
gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

SIZE_PX = 64
SIIGBF_BIGGERSIZEOK, SIIGBF_ICONONLY = 0x1, 0x4


# One GUID type and one SHCreateItemFromParsingName signature for the process:
# argtypes live on the shared shell32 function, so a second GUID class breaks it.
from .app_names import _guid  # noqa: E402

IID_IShellItemImageFactory = _guid("{BCC18B79-BA16-442F-80C4-8A59C30C463B}")


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p)]


gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
GetImage = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, SIZE, ctypes.c_int,
                              ctypes.POINTER(ctypes.c_void_p))
Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def shell_icon_png(source, size=SIZE_PX):
    """PNG bytes of the shell's icon for `source`, or None. Call on an STA thread."""
    from PIL import Image

    factory = ctypes.c_void_p()
    try:
        shell32.SHCreateItemFromParsingName(source, None, ctypes.byref(IID_IShellItemImageFactory),
                                            ctypes.byref(factory))
    except OSError:
        return None
    if not factory.value:
        return None
    vtbl = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    hbm = ctypes.c_void_p()
    try:
        GetImage(vtbl[3])(factory, SIZE(size, size), SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK,
                          ctypes.byref(hbm))  # IShellItemImageFactory::GetImage
    except OSError:
        return None
    finally:
        Release(vtbl[2])(factory)
    try:
        bm = BITMAP()
        gdi32.GetObjectW(hbm, ctypes.sizeof(BITMAP), ctypes.byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0)  # top-down BGRA
        buf = ctypes.create_string_buffer(w * h * 4)
        dc = user32.GetDC(None)
        try:
            if not gdi32.GetDIBits(dc, hbm, 0, h, buf, ctypes.byref(bih), 0):
                return None
        finally:
            user32.ReleaseDC(None, dc)
    finally:
        gdi32.DeleteObject(hbm)
    # The shell hands back premultiplied alpha; undo it so edges don't go dark.
    img = Image.frombuffer("RGBa", (w, h), buf.raw, "raw", "BGRa", 0, 1).convert("RGBA")
    if not img.getbbox():  # fully transparent: no icon really
        return None
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


class IconCache:
    """source -> PNG bytes (or None), extracted once on a dedicated STA thread."""

    def __init__(self):
        self._png = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audiohub-icons",
                                        initializer=lambda: ole32.CoInitializeEx(None, 2))

    async def get(self, source):
        import asyncio
        if source not in self._png:
            try:
                self._png[source] = await asyncio.get_running_loop().run_in_executor(
                    self._pool, shell_icon_png, source)
            except Exception:
                log.exception("icon for %s failed", source)
                self._png[source] = None
        return self._png[source]
