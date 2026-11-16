"""System tray icon and the start-with-Windows toggle.

Autostart is a shortcut in the user's Startup folder, not a Run key: the
shell quotes target and arguments for us, sets the working directory, and
pythonw starts without a console window. pystray and the WScript.Shell COM
object are needed only in this mode, so they are imported lazily.
"""
import ctypes
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger("audiohub")


def startup_link():
    folder = (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows"
              / "Start Menu" / "Programs" / "Startup")
    return folder / "Audio Hub.lnk"


def autostart_registered():
    return startup_link().exists()


def _autostart_target():
    """Executable autostart should launch: this exe when built, else None (pythonw -m)."""
    return sys.executable if getattr(sys, "frozen", False) else None


def set_autostart(on, extra_args=(), target=None):
    """Create/remove the Startup shortcut that runs the app in tray mode.

    `target` is the executable to launch; None means this Python install
    (`pythonw -m audiohub`), a path means a built exe.
    """
    link = startup_link()
    if not on:
        link.unlink(missing_ok=True)
        return
    args = [a for a in extra_args if a not in ("--browser", "--autostart")]
    if "--tray" not in args:
        args.insert(0, "--tray")
    quoted = [f'"{a}"' if " " in a else a for a in args]
    if target is None:
        target = _pythonw()
        arguments = " ".join(["-m audiohub"] + quoted)
        workdir = str(Path(__file__).resolve().parent.parent)
    else:
        arguments = " ".join(quoted)
        workdir = str(Path(target).parent)
    ctypes.windll.ole32.CoInitializeEx(None, 2)  # STA for WScript.Shell; refcounted
    import comtypes.client
    from comtypes.client.dynamic import Dispatch
    shell = comtypes.client.CreateObject("WScript.Shell")
    shortcut = Dispatch(shell.CreateShortcut(str(link)))  # late-bound IWshShortcut
    shortcut.TargetPath = target
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = workdir
    shortcut.save()


def _pythonw():
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[: -len("python.exe")] + "pythonw.exe"
    return exe


def _icon_image():
    from PIL import Image, ImageDraw
    for png in (Path(__file__).parent / "icons" / "128.png",  # bundled by PyInstaller
                Path(__file__).parent.parent / "extension" / "icons" / "128.png"):
        if png.exists():
            return Image.open(png)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(47, 111, 237, 255))
    return img


def run_tray(url, extra_args=(), on_open=None, on_quit=None, on_click=None):
    """Show the tray icon; blocks until the user picks Quit.

    Left click calls on_click (the flyout mixer), the menu's Open calls
    on_open (the full window). The windows live on the main thread; the tray
    only asks them to show/hide themselves.
    """
    import pystray

    def call(fn, what):
        if fn:
            try:
                fn()
            except Exception:
                log.exception("tray %s failed", what)

    def open_app(icon, item):
        call(on_open, "open")

    def click(icon, item):
        call(on_click or on_open, "click")

    def toggle_autostart(icon, item):
        set_autostart(not autostart_registered(), extra_args,
                      target=_autostart_target())

    def quit_app(icon, item):
        icon.stop()
        if on_quit:
            try:
                on_quit()
            except Exception:
                log.exception("tray on_quit failed")

    menu = pystray.Menu(
        # pystray runs the default item on a left click; hidden, it's only that.
        pystray.MenuItem("Mixer", click, default=True, visible=False),
        pystray.MenuItem("Open Audio Hub", open_app),
        pystray.MenuItem("Start with Windows", toggle_autostart,
                         checked=lambda item: autostart_registered()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("AudioHub", _icon_image(), "Audio Hub", menu).run()


def start_tray_thread(url, extra_args=(), on_open=None, on_quit=None, on_click=None):
    """Tray icon in a daemon thread; False when pystray is unavailable."""
    try:
        import pystray  # noqa: F401
    except Exception as e:
        log.error("No system tray: %r", e)
        return False
    threading.Thread(target=run_tray, args=(url, extra_args, on_open, on_quit, on_click),
                     daemon=True, name="audiohub-tray").start()
    return True
