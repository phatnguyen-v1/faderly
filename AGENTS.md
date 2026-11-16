# AGENTS.md

Audio Hub: one mixer for the sound on all machines. Windows only today; a Mac
agent is planned, so the tracker stays platform-neutral and the wire state is a
list of machines. User-facing docs live in `README.md`.

## Layout

- `audiohub/hub.py` — aiohttp server: polling, WebSocket to the UI, extension link, commands. All pycaw COM calls go through `AUDIO_WORKER` (one daemon thread, `CoInitializeEx(MTA)`, queue of futures).
- `audiohub/tracker.py` — Now playing / Recent rules. Pure Python, no Windows code; unit-tested.
- `audiohub/win_audio.py` — per-app sessions via pycaw. Must be imported from the hub worker thread (it sets `sys.coinit_flags` before comtypes import).
- `audiohub/win_media.py` — media players via WinRT.
- `audiohub/win_window.py` — "Show" on a row: raise an app's main window (ctypes, no COM).
- `tools/make_icons.py` — draws the icon (extension PNGs + `tools/AudioHub.ico` for the exe). `build/` is gitignored, so tracked build inputs live in `tools/`.
- `audiohub/flyout.py` — tray left-click mixer: a frameless on-top window (`?flyout=1`) placed above the taskbar; the page reports `blur`/height through `js_api`.
- `audiohub/app_names.py` — row names from Windows: shell Apps folder for media app ids, exe FileDescription for audio sessions, a running-exe match, then `tidy()`. `NICE_NAMES` in `win_audio.py` is only for short names (Chrome, not Google Chrome); don't add per-app fixes there.
- `audiohub/single_instance.py` — one copy per port: a named mutex; a later launch sets a named event so the running copy shows its window, then exits. To run a second copy on purpose (dev, tests), use another `--port`.
- `audiohub/extension_info.py` — the extension from the app's side: per-browser status (ok / missing / off / outdated) for the UI notes, the shipped version, and the stable folder to load unpacked (`%LOCALAPPDATA%\AudioHub\extension`, copied from the exe on every launch; the repo's `extension/` when run from source).
- `audiohub/settings.py` — what persists: `%LOCALAPPDATA%\AudioHub\settings.json` (pins: key, name; default Chrome + Spotify). Tests must give hubs a temp `Settings(path)` (see `make_hub`), never the real file.
- `audiohub/tray.py` — tray icon, Startup-folder shortcut, autostart toggle.
- `audiohub/web/index.html` — the whole UI (HTML+CSS+JS in one file).
- `extension/` — Manifest V3 browser extension reporting per-tab audio. Reload it in the browser after any change.
- `tests/` — unittest only, no pytest dependency.

## Commands

- Tests: `.venv\Scripts\python -m unittest discover -s tests` — all green is the bar for done.
- Build the exe: `build.bat` → `dist\AudioHub.exe`. Requires: no `AudioHub.exe` process is running (the onefile bootloader locks the exe — `taskkill //PID <pid> //T //F` the whole tree first). Build is complete when PyInstaller prints `Building EXE ... completed successfully`.
- Deploy after a code change: rebuild, then kill and relaunch the running instance (`cmd //c start "" dist\AudioHub.exe --tray`). The Desktop and Startup shortcuts point at the exe, so a rebuild is mandatory for changes to reach the running app.

## Gotchas (cost time to rediscover)

- **Venv process pairs are normal.** Python 3.12 venvs on Windows launch the real interpreter (system Python) as a child of `.venv\Scripts\python[w].exe`, which waits as a shim. One app instance = two processes. Same for PyInstaller onefile: bootloader parent + real child. When in doubt, the process holding the port is the real one.
- **`webview.start()` must run on the main thread** (pywebview raises otherwise). Tray mode therefore runs the hidden window on the main thread and the tray icon in a daemon thread; tray → Open calls `window.show()`. Never fall back to opening a browser.
- **Close-to-tray**: a `window.events.closing` handler that returns `False` cancels the close (the event is lock-synchronous in pywebview); tray mode uses it to hide instead of quit. `window.destroy()` is an ordinary close too, so tray -> Quit sets `QUITTING` first or every handler cancels it and the app lingers invisibly; after `webview.start()` returns, `os._exit(0)` ends the process (plus a `QUIT_GRACE` timer in `_quit`). `tests/test_quit.py` covers it.
- **Cancelled COM futures**: the audio worker calls `future.set_running_or_notify_cancel()` before running each call. Without it, a client disconnecting mid-snapshot cancels the future, `set_result` raises `InvalidStateError`, and the exception kills the worker thread — the mixer then freezes forever. `tests/test_hub.py` has a regression test; keep it passing.
- **WScript.Shell shortcuts**: `shell.CreateShortcut()` returns a raw `IDispatch`; wrap it with `comtypes.client.dynamic.Dispatch(...)` before setting properties, and call `CoInitializeEx(None, 2)` (STA) on the calling thread first.
- **Autostart is a Startup-folder `.lnk`**, not a Run key — the shell handles quoting and working directory. Python entry (`pythonw -m audiohub`) and built exe (`--tray`) are both supported via `set_autostart(..., target=...)`.
- **PyInstaller**: the entry script lives in `build/`, so the build passes `--paths .` or the `audiohub` package is "missing". The bundled tray icon lands at `audiohub/icons/128.png` inside the archive; `_icon_image()` checks that location first.
- **Tray clicks**: pystray runs the menu's `default=True` item on left click; a hidden (`visible=False`) default item makes left click the flyout while the right-click menu keeps Open. pywebview has no focus-lost event, and the page's `window.blur` fires spuriously right after show (it hid the flyout the instant it opened), so `Flyout._watch` polls the foreground window instead. The flyout is a `WS_EX_TOOLWINDOW` (no taskbar button), or Windows flashes that button whenever it refuses focus. Don't test it with simulated tray clicks while the user is active: they steal focus from whatever they're doing.
- **Page worlds**: Next/Previous handlers come from `extension/mediasession-hook.js` (MAIN world, document_start) wrapping `navigator.mediaSession.setActionHandler`. Volume on YouTube must go through `#movie_player.setVolume()`: YouTube re-applies its own saved level and evens out loudness, so a raw `<video>.volume` drifts and reverts.
- **Extension handshake**: there is no "send tabs" switch (removed in 0.7): connected means sharing. `hello` carries browser, version and `accepts`; the app sends `reload` (`chrome.runtime.reload()` re-reads the unpacked folder) only to versions that list it. Bump `manifest.json`'s version on extension changes: the app compares it to offer "Update now".
- **Pinned and system rows**: `Tracker.view(keep=, hide=)` — pinned keys never expire and are never cut by the Recent cap; the hub lifts them into `pinned` (pin order, `live` = which section they'd be in). A closed pinned app isn't listed; a running but silent one gets a placeholder row. Exes under `%SystemRoot%` are Windows' own (`system_keys`): shown while sounding, never in Recent, no close.
- **Shell commands with backslashes**: the Bash tool collapses `\\` inside heredocs, turning `\t`/`\v` in Windows paths into control characters. Put such edits in a script file (Write tool) instead.
- Browser tabs without a media element (Web Audio, games) can't be paused; the extension mutes them instead, and the hub mirrors that (see `_tab_has_media` in `hub.py`).

## Security invariants

- The page WebSocket (`/ws`) refuses foreign `Origin`s; the extension endpoint (`/ext`) accepts extension origins only. Bind stays on `127.0.0.1`; anything beyond localhost needs an auth token first.
