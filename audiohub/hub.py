"""Polls this machine's audio and serves the mixer page + a WebSocket.

The state is shaped as a list of machines so a Mac agent can join later.

pycaw's COM calls block, so they run on a dedicated worker thread (see
AUDIO_WORKER) rather than the event loop; that thread initializes COM once
and the single worker keeps the calls serialized.
"""
import asyncio
import ctypes
import json
import logging
import os
import platform
import queue
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import psutil
from aiohttp import WSMsgType, web

from . import extension_info
from .icons import IconCache
from .settings import Settings
from .tracker import Tracker
from .win_audio import WinAudio, app_key, nice_name
from .win_media import WinMedia
from .win_window import can_close, close_app, raise_app

log = logging.getLogger("audiohub")

EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "extension://")

WEB = Path(__file__).parent / "web"
HELD_GRACE = 3.0  # seconds a Pause all / Mute all entry survives before rows must confirm it
TICK = 0.2  # seconds; level meters update at this rate


def _init_com():
    # Every thread that calls COM must initialize it (MTA, like win_audio).
    ctypes.windll.ole32.CoInitializeEx(None, 0)


class _AudioWorker:
    """Single daemon thread that runs the blocking pycaw calls.

    COM is initialized once on that thread (MTA, like win_audio) and the
    queue keeps the calls serialized. The thread is a daemon so a stuck
    audio driver can never hang app exit (e.g. Quit from the tray).
    """

    def __init__(self):
        self._queue = queue.Queue()
        thread = threading.Thread(target=self._run, name="audiohub-audio", daemon=True)
        thread.start()

    def _run(self):
        _init_com()
        while True:
            fn, args, future = self._queue.get()
            if future is None:
                return
            # If the caller was cancelled while the call waited (e.g. a page
            # closed mid-snapshot), skip it; set_result on a cancelled future
            # would raise InvalidStateError and kill this thread forever.
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args))
            except BaseException as exc:
                future.set_exception(exc)

    def submit(self, fn, *args):
        future = Future()
        self._queue.put((fn, args, future))
        return future


AUDIO_WORKER = _AudioWorker()


class Hub:
    def __init__(self, recent_timeout=600):
        self.audio = WinAudio()
        self.media = WinMedia()
        self.tracker = Tracker(recent_timeout=recent_timeout)
        self.clients = set()
        # browser key ("chrome", "msedge", ...) -> {"ws", "tabs", "version", "accepts"}
        self.ext = {}
        self.ext_dir = extension_info.extension_dir()
        self.ext_latest = extension_info.shipped_version(self.ext_dir)
        self._open_browsers = set()  # Chromium browsers with a visible window
        # What Pause all / Mute all changed, so the same button can undo just that:
        # {"pause": {key: ("pause" | "mute", when)}, "mute": {key: ("mute", when)}}
        self.held = {"pause": {}, "mute": {}}
        self.icons = IconCache()
        self.settings = Settings()
        self.port = None
        self.machine = {"id": "local", "name": platform.node(), "os": "windows"}
        self._audio_state, self._media_state, self._running = {}, {}, set()

    async def _audio_call(self, fn, *args):
        """Run a blocking COM call on the dedicated audio thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(AUDIO_WORKER, fn, *args)

    # ---- polling ---------------------------------------------------------
    def sharing(self):
        """Browsers whose extension is connected (connected always means sharing tabs)."""
        return set(self.ext)

    def _open_browser_keys(self, running):
        from .win_window import main_window
        return {k for k in extension_info.BROWSERS if k in running and main_window(k)}

    def _running_keys(self):
        keys = set()
        for p in psutil.process_iter(["name"]):
            if p.info["name"]:
                keys.add(app_key(p.info["name"]))
        return keys

    async def poll(self):
        n = 0
        while True:
            try:
                if n % 5 == 0:
                    await self._audio_call(self.audio.refresh_sessions)
                    self._media_state = await self.media.read()
                if n % 10 == 0:
                    self._running = await asyncio.to_thread(self._running_keys)
                    self._open_browsers = await asyncio.to_thread(self._open_browser_keys, self._running)
                self._audio_state = await self._audio_call(self.audio.read)
                now = time.monotonic()
                # With the extension connected, tabs replace the browser's single Windows player.
                media = {k: v for k, v in self._media_state.items()
                         if k.split("~")[0] not in self.sharing()}
                self.tracker.update(self._audio_state, media, self._running, now, self._tab_rows())
                if self.clients:
                    await self.broadcast(await self.snapshot(now))
            except Exception:
                log.exception("poll error")  # keep the loop alive
            n += 1
            await asyncio.sleep(TICK)

    def _tab_rows(self):
        out = {}
        for browser, info in self.ext.items():
            for t in info["tabs"]:
                has_media = bool(t.get("hasMedia"))
                out[f"{browser}#{t['id']}"] = {
                    "parent": browser,
                    "parent_name": nice_name(browser),
                    "title": t.get("title") or "",
                    "site": t.get("site") or "",
                    # The page's own video/audio state is exact; Chrome's speaker flag lags
                    # a few seconds, so only use it for tabs without a media element.
                    "playing": (not t.get("paused")) if has_media else bool(t.get("audible")),
                    "has_media": has_media,
                    "volume": t.get("volume") if has_media else None,
                    "position": t.get("position") if has_media and t.get("duration") else None,
                    "duration": (t.get("duration") or None) if has_media else None,
                    "position_at": info.get("at", 0.0),
                    "can_next": bool(t.get("canNext")) if has_media else False,
                    "can_prev": bool(t.get("canPrev")) if has_media else False,
                    "muted": bool(t.get("muted")),
                }
        return out

    async def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        pins = self.settings.pins()
        keep = {p["key"] for p in pins}
        playing, recent = self.tracker.view(now, keep=keep, hide=self.system_keys())
        # Pinned rows keep one place in their own section, playing or not.
        found = {}
        for rows, live in ((playing, "playing"), (recent, "recent")):
            for r in [r for r in rows if r["key"] in keep]:
                rows.remove(r)
                found[r["key"]] = dict(r, live=live)
        # Closed pinned apps aren't listed; they take their place again once running.
        pinned = [found.get(p["key"]) or self._pinned_placeholder(p) for p in pins
                  if p["key"] in found or p["key"] in self._running]
        for r in pinned:
            r["pinned"] = True
        self._add_icons(pinned + playing + recent)
        master = await self._audio_call(self.audio.master)
        return {"machines": [{**self.machine, "master": master,
                              "pinned": pinned, "playing": playing, "recent": recent,
                              "canResume": self._can_undo("pause"),
                              "canUnmute": self._can_undo("mute"),
                              "canReset": self._can_reset(),
                              "browsers": extension_info.statuses(
                                  self._open_browsers, self.ext, self.ext_latest),
                              "extensionDir": str(self.ext_dir)}],
                "recentTimeout": self.tracker.recent_timeout}

    def system_keys(self):
        """Apps whose exe lives in the Windows folder: Windows' own processes. They
        show while making sound, never in Recent, and never get a close button."""
        windir = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")) + os.sep
        return {k for k, exe in getattr(self.audio, "exes", {}).items()
                if exe and os.path.normcase(exe).startswith(windir)}

    def _pinned_placeholder(self, pin):
        """A pinned app that's running but silent (no sound session or player yet)."""
        return {"key": pin["key"], "name": pin.get("name") or pin["key"], "status": "running",
                "title": "", "artist": "", "volume": None, "muted": False, "peak": 0.0,
                "media": False, "ago": None, "parent": None, "position": None, "duration": None,
                "seekable": False, "canNext": False, "canPrev": False, "live": "recent"}

    def icon_source(self, key):
        """What the shell draws this app's icon from: its exe, else its media app id."""
        base = key.split("~")[0]
        exe = getattr(self.audio, "exes", {}).get(base)
        if exe:
            return exe
        aumid = getattr(self.media, "app_ids", {}).get(key) or getattr(self.media, "app_ids", {}).get(base)
        return "shell:AppsFolder\\" + aumid if aumid else None

    def _add_icons(self, rows):
        """Each row gets an image URL: the tab's favicon, or /icon/<key> for apps."""
        favicons = {f"{b}#{t['id']}": t.get("favIconUrl") or ""
                    for b, info in self.ext.items() for t in info["tabs"]}
        system = self.system_keys()
        for r in rows:
            if "#" in r["key"]:
                fav = favicons.get(r["key"], "")
                r["icon"] = fav if fav.startswith(("https://", "http://", "data:image/")) else None
            else:
                r["icon"] = f"/icon/{r['key']}" if self.icon_source(r["key"]) else None
            base = r["key"].split("~")[0]
            r["canClose"] = ("#" in r["key"] or (base not in extension_info.BROWSERS
                                                 and can_close(base) and base not in system))
            self._add_icons(r.get("tabs") or [])
            self._add_icons(r.get("recentTabs") or [])

    async def icon(self, request):
        source = self.icon_source(request.match_info["key"])
        png = await self.icons.get(source) if source else None
        if png is None:
            raise web.HTTPNotFound()
        return web.Response(body=png, content_type="image/png",
                            headers={"Cache-Control": "max-age=86400"})

    async def broadcast(self, state):
        msg = json.dumps(state)
        for ws in list(self.clients):
            try:
                await ws.send_str(msg)
            except Exception:
                self.clients.discard(ws)

    # ---- commands from the page -------------------------------------------
    async def command(self, c):
        cmd, key = c.get("cmd"), c.get("key")
        if cmd in ("pause_all", "mute_all"):
            await self._pause_mute_all(mute=(cmd == "mute_all"))
            return
        if cmd in ("ext_open_page", "ext_open_folder", "ext_update"):
            await self._extension_cmd(cmd, str(c.get("value") or ""))
            return
        if cmd in ("pin", "unpin"):
            await self._pin_cmd(cmd, str(c.get("key") or ""), c.get("value"))
            return
        if cmd == "reset_volumes":
            await self._reset_volumes()
            return
        if cmd in ("resume_all", "unmute_all"):
            await self._undo_all("pause" if cmd == "resume_all" else "mute")
            return
        if key and "#" in key:  # a browser tab: the extension does it
            if cmd == "dismiss":
                self.tracker.dismiss(key, time.monotonic())
                return
            if cmd == "focus":
                # Browser to the front first, then the tab switch. Chrome stops painting a
                # covered or minimized window, so switching tabs there and raising it
                # afterwards showed a stale frame (old address, blank page) for a moment.
                # Raising first also hands Chrome the foreground (Windows won't let it take
                # it from us), so it can then focus whichever of its windows holds the tab.
                await asyncio.to_thread(raise_app, key.split("#", 1)[0])
                await self._tab_cmd(key, cmd, c.get("value"))
                return
            if cmd in ("playpause", "mute", "volume", "seek", "next", "prev", "close"):
                await self._tab_cmd(key, cmd, c.get("value"))
                if cmd == "volume":
                    self.tracker.remember_volume(key, volume=float(c["value"]))
                elif cmd == "mute":
                    self.tracker.remember_volume(key, muted=bool(c["value"]))
            return
        if cmd == "volume":
            await self._audio_call(self.audio.set_volume, key, c["value"])
            self.tracker.remember_volume(key, volume=float(c["value"]))
        elif cmd == "mute":
            await self._audio_call(self.audio.set_mute, key, c["value"])
            self.tracker.remember_volume(key, muted=bool(c["value"]))
        elif cmd == "master_volume":
            await self._audio_call(self.audio.set_master_volume, c["value"])
        elif cmd == "set_output":
            await self._audio_call(self.audio.set_output, c["value"])
        elif cmd == "master_mute":
            await self._audio_call(self.audio.set_master_mute, c["value"])
        elif cmd in ("playpause", "pause", "next", "prev"):
            await self.media.command(key, cmd)
        elif cmd == "dismiss":
            self.tracker.dismiss(key, time.monotonic())
        elif cmd == "seek":
            await self.media.seek(key, c["value"])
        elif cmd == "close" and key:
            base = key.split("~")[0]
            if base in extension_info.BROWSERS:
                return  # never the whole browser from a mixer row; its tabs close one by one
            await asyncio.to_thread(close_app, base)
        elif cmd == "focus" and key:
            await asyncio.to_thread(raise_app, key.split("~")[0])

    async def _pause_mute_all(self, mute):
        """Pause or mute every row in Now playing: players, apps and browser tabs.

        Pausing must never start playback, and one row failing must not stop
        the rest, so each row is handled on its own.
        """
        held = self.held["mute" if mute else "pause"]
        now = time.monotonic()
        for r in list(self.tracker.rows.values()):
            if not r.playing:
                continue
            try:
                if r.parent is not None:
                    await self._tab_cmd(r.key, "mute" if mute else "pause",
                                        True if mute else None)
                    if mute or r.media is None:
                        if not r.muted:  # a mute the user set stays theirs
                            held[r.key] = ("mute", now)
                        # The extension mutes tabs that have no media element
                        # to pause, so record that instead of a pause. It
                        # re-checks the page itself and sends a fresh report
                        # ~150 ms after acting, which corrects this guess if
                        # the page changed since the last report.
                        self.tracker.remember_volume(r.key, muted=True)
                    else:
                        held[r.key] = ("pause", now)
                elif mute:
                    # Without a live stream there is nothing to mute — a pending
                    # mute would hit later, out of the blue. And with the
                    # extension connected, the browser's own Windows session
                    # must stay untouched: muted there, a tab makes no sound
                    # even when the tab itself is unmuted.
                    if r.audio is not None and r.key not in self.sharing():
                        if not r.muted:
                            held[r.key] = ("mute", now)
                        await self._audio_call(self.audio.set_mute, r.key, True)
                        self.tracker.remember_volume(r.key, muted=True)
                elif r.media is not None:
                    await self.media.command(r.key, "pause")
                    held[r.key] = ("pause", now)
            except Exception:
                log.exception("%s all: %s failed", "mute" if mute else "pause", r.key)

    async def _extension_cmd(self, cmd, browser):
        """The extension notes in the UI: open its page or folder, or ask it to act."""
        try:
            if cmd == "ext_open_page":
                await self._open_extensions_page(browser)
            elif cmd == "ext_open_folder":
                await asyncio.to_thread(extension_info.open_folder, self.ext_dir)
            else:
                ext = self.ext.get(browser)
                if ext and "reload" in ext.get("accepts", []):
                    await ext["ws"].send_str(json.dumps({"cmd": "reload"}))
                else:  # too old to reload itself: its page has the button
                    await self._open_extensions_page(browser)
        except Exception:
            log.exception("%s for %s failed", cmd, browser)

    async def _open_extensions_page(self, browser):
        """A new tab in the browser window already open; then bring that window forward."""
        ext = self.ext.get(browser)
        if ext and "openExtensionsPage" in ext.get("accepts", []):
            await ext["ws"].send_str(json.dumps({"cmd": "openExtensionsPage"}))
        else:
            await asyncio.to_thread(extension_info.open_extensions_page, browser)
        # The browser can't take the foreground from us; we can give it, as for "Show".
        await asyncio.sleep(0.4)
        await asyncio.to_thread(raise_app, browser)

    async def _pin_cmd(self, cmd, key, name=None):
        base = key.split("~")[0]
        if not base or "#" in base:
            return  # tabs come and go; they aren't pinned
        if cmd == "pin":
            self.settings.pin(base, name=str(name or base))
        else:
            self.settings.unpin(base)

    def _can_reset(self):
        return any((r.parent is not None or r.audio is not None)
                   and (r.muted or (r.volume is not None and r.volume < 1))
                   for r in self.tracker.rows.values())

    async def _reset_volumes(self):
        """Every app and tab back to 100% and unmuted. The master volume is left alone."""
        for r in list(self.tracker.rows.values()):
            try:
                if r.parent is not None:
                    if r.volume is not None and r.volume < 1:
                        await self._tab_cmd(r.key, "volume", 1.0)
                        self.tracker.remember_volume(r.key, volume=1.0)
                    if r.muted:
                        await self._tab_cmd(r.key, "mute", False)
                        self.tracker.remember_volume(r.key, muted=False)
                elif r.audio is not None:  # only live streams: no pending change later
                    if r.volume is not None and r.volume < 1:
                        await self._audio_call(self.audio.set_volume, r.key, 1.0)
                        self.tracker.remember_volume(r.key, volume=1.0)
                    if r.muted:
                        await self._audio_call(self.audio.set_mute, r.key, False)
                        self.tracker.remember_volume(r.key, muted=False)
            except Exception:
                log.exception("reset volumes: %s failed", r.key)
        self.held["mute"].clear()

    def _prune_held(self, kind, now=None):
        """Forget rows that are gone or that the user already changed back.

        Players report a pause a poll or two late, so a fresh entry is kept
        for HELD_GRACE seconds whatever the row says.
        """
        now = time.monotonic() if now is None else now
        held = self.held[kind]
        for key, (how, when) in list(held.items()):
            r = self.tracker.rows.get(key)
            if r is None:
                del held[key]
            elif now - when > HELD_GRACE and (r.playing if how == "pause" else not r.muted):
                del held[key]
        return held

    def _can_undo(self, kind):
        """Offer Resume all / Unmute all only while nothing else is playing out loud:
        otherwise the button means Pause all / Mute all again."""
        if not self._prune_held(kind):
            return False
        loud = (r for r in self.tracker.rows.values() if r.playing and not r.muted)
        if kind == "pause":
            loud = (r for r in loud if r.key not in self.held["pause"])
        return not any(loud)

    async def _undo_all(self, kind):
        """Resume what Pause all paused, or unmute what Mute all muted. Never more."""
        held = self._prune_held(kind)
        for key, (how, _) in list(held.items()):
            r = self.tracker.rows.get(key)
            try:
                if how == "pause":
                    if r.parent is not None:
                        await self._tab_cmd(key, "play")
                    else:
                        await self.media.command(key, "play")
                elif r.parent is not None:
                    await self._tab_cmd(key, "mute", False)
                    self.tracker.remember_volume(key, muted=False)
                else:
                    await self._audio_call(self.audio.set_mute, key, False)
                    self.tracker.remember_volume(key, muted=False)
            except Exception:
                log.exception("undo %s all: %s failed", kind, key)
        held.clear()

    async def _tab_cmd(self, key, cmd, value=None):
        """Send a command for a browser tab row to that browser's extension."""
        browser, tab_id = key.split("#", 1)
        ext = self.ext.get(browser)
        if ext:
            await ext["ws"].send_str(json.dumps(
                {"cmd": cmd, "tabId": int(tab_id), "value": value}))

    # ---- web ---------------------------------------------------------------
    async def index(self, request):
        return web.FileResponse(WEB / "index.html")

    async def ws(self, request):
        # Only our own page may drive the mixer, not any website open in a browser.
        origin = request.headers.get("Origin", "")
        if origin and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_str(json.dumps(await self.snapshot()))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        await self.command(json.loads(msg.data))
                    except Exception:
                        log.exception("command error")
        finally:
            self.clients.discard(ws)
        return ws

    async def ext_ws(self, request):
        """The browser extension: reports its tabs, receives tab commands."""
        if not request.headers.get("Origin", "").startswith(EXTENSION_ORIGINS):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        browser = None
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("type") == "hello":
                    browser = str(data.get("browser") or "chrome").lower()
                    self.ext[browser] = {
                        "ws": ws, "tabs": [],
                        "version": str(data.get("version") or "0"),
                        "accepts": [str(a) for a in data.get("accepts") or []]}
                    log.info("extension connected: %s %s", browser, self.ext[browser]["version"])
                elif data.get("type") == "tabs" and browser in self.ext:
                    self.ext[browser]["tabs"] = data.get("tabs") or []
                    self.ext[browser]["at"] = time.monotonic()  # positions are as of now
        finally:
            if browser and self.ext.get(browser, {}).get("ws") is ws:
                del self.ext[browser]
                log.info("extension disconnected: %s", browser)
        return ws

    async def run(self, host, port, ready=None):
        self.port = port
        await self.media.start()
        app = web.Application()
        app.add_routes([web.get("/", self.index), web.get("/ws", self.ws),
                        web.get("/ext", self.ext_ws), web.get("/icon/{key}", self.icon)])
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        log.info("Audio Hub on http://%s:%s", host, port)
        if ready:
            ready()
        await self.poll()
