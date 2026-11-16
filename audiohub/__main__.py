"""Start the hub in a worker thread; show a window, run a tray icon, or go headless."""
import argparse
import asyncio
import logging
import os
import sys
import threading
import webbrowser

log = logging.getLogger("audiohub")

# Set by tray -> Quit. The windows' close-to-tray handlers must let the close
# through then: pywebview's destroy() is an ordinary close, so a handler that
# cancels it leaves the app running, invisible, with no tray icon.
QUITTING = threading.Event()
QUIT_GRACE = 3.0  # s for the windows to close before the process is ended anyway


def serve(args, ready, failed):
    # Imported here so COM is set up in this thread, not the UI thread.
    try:
        from .hub import Hub
        hub = Hub(recent_timeout=args.recent_minutes * 60)
        asyncio.run(hub.run(args.host, args.port, ready=ready.set))
    except Exception as e:
        failed.append(e)
        ready.set()
        raise


def _quit():
    """Tray -> Quit: close every window, then end the process."""
    QUITTING.set()
    # Whatever happens below, don't linger in the background.
    threading.Timer(QUIT_GRACE, lambda: os._exit(0)).start()
    try:
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                log.exception("closing a window failed")
    except Exception:
        log.exception("quit failed")


def _bring_forward(window):
    """A second launch asked for us: show the full window and give it focus."""
    window.show()
    try:
        window.restore()  # if it was minimized
    except Exception:
        pass
    from .flyout import _own_window, take_focus
    hwnd = _own_window(window.title)
    if hwnd:
        take_focus(hwnd)


def _hide_to_tray(window):
    """Close hides the window back to the tray instead of quitting (unless quitting)."""
    if QUITTING.is_set():
        return True
    window.hide()
    return False  # pywebview: a False return cancels the close


def main():
    ap = argparse.ArgumentParser(prog="faderly")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--recent-minutes", type=float, default=10,
                    help="how long a stopped app with no player stays under Recent")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    ap.add_argument("--headless", action="store_true", help="only run the server")
    ap.add_argument("--tray", action="store_true",
                    help="run in the background with a tray icon: left click for the mixer, right click to open the app")
    ap.add_argument("--autostart", action="store_true",
                    help="register Faderly to start with Windows (in tray mode), then exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.autostart:
        from .tray import _autostart_target, set_autostart
        set_autostart(True, sys.argv[1:], target=_autostart_target())
        print("Faderly will start with Windows (in the tray).")
        return

    from . import single_instance
    if not single_instance.acquire(args.port):
        # Already running: bring that copy's window forward instead of starting another.
        if not single_instance.show_existing(args.port):
            log.warning("Faderly is already running but didn't answer")
        return

    if getattr(sys, "frozen", False):
        # The built exe used to be AudioHub.exe: carry its Startup shortcut over. Only the
        # exe does this, so running from source never repoints autostart at Python.
        from .tray import migrate_autostart
        migrate_autostart()

    ready, failed = threading.Event(), []
    worker = threading.Thread(target=serve, args=(args, ready, failed), daemon=True)
    worker.start()
    ready.wait()
    if failed:
        raise SystemExit(f"Faderly failed to start: {failed[0]!r}")

    url = f"http://{args.host}:{args.port}/"
    if args.browser:
        webbrowser.open(url)
        single_instance.listen(args.port, lambda: webbrowser.open(url))
    elif not args.headless:
        try:
            import webview
            from . import tray
            from .flyout import TITLE, WIDTH, Flyout, FlyoutApi
            window = webview.create_window("Faderly", url, width=440, height=680,
                                           min_size=(340, 400), hidden=args.tray)
            if args.tray:
                window.events.closing += _hide_to_tray
            fly = Flyout(open_full=window.show)
            fly.window = webview.create_window(
                TITLE, url + "?flyout=1", width=WIDTH, height=fly.height,
                # easy_drag off: a frameless window is otherwise dragged from anywhere,
                # which steals every slider drag. The flyout stays pinned by the tray.
                frameless=True, easy_drag=False, on_top=True, resizable=False, hidden=True,
                background_color="#222226", js_api=FlyoutApi(fly))
            # Alt+F4 hides it too, except when quitting.
            fly.window.events.closing += lambda: QUITTING.is_set() or (fly.hide(), False)[1]
            single_instance.listen(args.port, lambda: _bring_forward(window))
            tray.start_tray_thread(url, sys.argv[1:], on_open=window.show,
                                   on_quit=_quit, on_click=fly.toggle)
            webview.start()
            # Windows closed: end now. The server, COM and icon threads have nothing to
            # save, and one left waiting would keep a windowless process alive.
            logging.shutdown()
            os._exit(0)
        except Exception as e:
            log.error("No app window: %r", e)
    try:
        worker.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
