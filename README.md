# Faderly

One mixer for the sound on all my machines (formerly Audio Hub). Windows only for now; the Mac agent comes later.

## The mixer

Each machine gets a card with the **output device** (click it to switch to another one),
the **master volume**, and three sections:

- **Pinned**: apps you pinned, in a fixed place whether they're playing or not. Chrome and
  Spotify are pinned to start with. Hover a row and click the pin to pin or unpin it. A
  pinned app that's closed isn't listed until it runs again.
- **Now playing**: apps making sound and players that say they're playing.
- **Recent**: paused or stopped apps, newest first.
  - A paused player still registered with Windows stays, and ▶ resumes it.
  - An app that stopped but is still running drops after 10 minutes.
  - A closed app is removed at once.
  - A muted app stays listed (as "muted"), so it can always be unmuted from here.
  - Windows' own processes (anything in `C:\Windows`) show only while they make sound.

Each row has the app's own icon and name (both from Windows), and:

- **⏮ ⏯ ⏭** where the player supports them, paused or playing.
- **A progress bar** with times; drag it to seek.
- **A speaker button** that opens a small floating volume slider. It shows the level when
  it isn't 100%, and turns red when muted. The mouse wheel works over the slider.
- **Click the name** to bring that app (or tab) to the front.
- **✕** on hover: a tab closes at once (Ctrl+Shift+T brings it back). An app asks
  "Close?" first, then gets the same close request as its own ✕, so it can still ask
  about unsaved work, and tray apps just hide. Faderly never force-quits anything.

In the header: **Pause all** (then **Resume all**, which resumes only what it paused) and
**Reset volumes** (every app and tab back to 100% and unmuted; shown only when needed).

## Tray

Faderly lives in the tray. **Left click** the icon for a compact mixer above the
taskbar; it hides when you click elsewhere or press Esc. **Right click** for the menu:
Open Faderly (the full window), Start with Windows, Quit. Closing the window keeps it in
the tray; Quit ends it.

Only one Faderly runs at a time: starting it again brings the running one to the front.

## Browser extension

The extension shows each browser tab that plays sound as its own row, nested under its
browser, with the same controls (play/pause, previous/next, seek, volume, close).
Without it, Windows only sees one sound stream for the whole browser.

Chrome, Edge, Brave and other Chromium browsers: open `chrome://extensions`, turn on
Developer mode, click **Load unpacked** and pick the extension folder:

- from source: the `extension` folder of this repo;
- with the built exe: `%LOCALAPPDATA%\Faderly\extension` (the exe keeps it up to date).

The app tells you when a browser is open without the extension (with these steps and a
button to its extensions page), and when the extension is older than the app's copy
(**Update now** reloads it).

It also works on its own, without the desktop app:

- **Toolbar popup**: Now playing and Recent tabs with the same controls as the app.
- **Badge** with the number of tabs making sound.
- **Only one tab plays at a time** (option): starting a tab pauses the others.
- **Shortcuts**: Alt+Shift+P plays/pauses the current or last paused tab; "Pause all"
  can be given a key at `chrome://extensions/shortcuts`.

While the desktop app runs, the extension sends it the tabs over `127.0.0.1:8765`; nothing
leaves the PC. Only Faderly's own windows and browser extensions can connect; websites
are refused.

- On YouTube, tab volume is YouTube's own volume. Elsewhere it sets the page's
  video/audio level, so the site's slider won't move.
- Previous/next use the page's own media controls (most music and video sites have them);
  on YouTube, next falls back to its Next button. Previous restarts the track otherwise.
- Tabs that play without a video/audio element (games, Web Audio) can't be paused; they
  are muted instead.
- Tabs have no level meter; browsers don't give per-tab levels.

## Run

```bat
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat
```

`pip install -e .` also works; it adds a `faderly` command.

Options (`python -m audiohub --help`): `--tray` runs in the background with the tray icon,
`--autostart` registers it to start with Windows in tray mode, `--browser` opens the UI in
the browser instead of a window, `--headless` runs only the server, `--recent-minutes N`
changes the Recent timeout, `--port` (a second copy can run on another port).

Settings (pins) are kept in `%LOCALAPPDATA%\Faderly\settings.json`.

**Upgrading from Audio Hub** (the old name): on its first start `Faderly.exe` moves the
settings over from `%LOCALAPPDATA%\AudioHub` and replaces the old "Audio Hub" Startup
shortcut with a Faderly one. Repoint any other shortcut you made at `Faderly.exe`, and
update the extension (the app offers **Update now**). The old folder can then be deleted.

## Tests

```bat
.venv\Scripts\python -m unittest discover -s tests
```

## Build a standalone exe

```bat
build.bat
```

makes `dist\Faderly.exe` (single windowed exe, no Python needed). Stop a running
`Faderly.exe` first; Windows locks the file while it runs. To make the built exe start
with Windows: `dist\Faderly.exe --autostart --tray`; the Startup shortcut then points at
the exe, not at Python. The icon is drawn by `tools\make_icons.py`.

## How it works

`audiohub/hub.py` polls every 0.2 s and pushes state over a WebSocket to `web/index.html`
(the full window and the tray mixer are the same page). Blocking COM calls run in a worker
thread so the server never stalls.

- `win_audio.py`: per-app audio sessions (the Volume Mixer data) and output devices, via pycaw.
- `win_media.py`: media players (the Quick Settings media pop-up) via WinRT: track, position, controls.
- `app_names.py`, `icons.py`: names and icons from Windows (Start menu entries, exe details).
- `tracker.py`: the Pinned / Now playing / Recent rules; no Windows code, so the Mac agent can reuse it.
- `flyout.py`, `tray.py`, `single_instance.py`: the tray mixer, tray icon and the one-copy rule.
- `extension_info.py`: what the app knows about the browser extension in each browser.

The state is a list of machines, so a Mac agent can join later by sending the same shape.
