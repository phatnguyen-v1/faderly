# Faderly on macOS: research notes

Status: **parked**. First release is Windows only (the exe + the browser extension).
These notes record what we found (November 2026) so the Mac work can start from here.

## The core problem

Windows gives every app its own audio session with a volume (pycaw, `win_audio.py`).
macOS has no such API. Every per-app volume app on the Mac works around it, and that
workaround is the bulk of the Mac work. Everything else is ordinary porting.

## What carries over unchanged

- `hub.py`, `tracker.py`: plain Python (the tracker is kept platform-neutral for this).
- `web/index.html`: pywebview has a Cocoa/WebKit backend.
- The browser extension: same code on Chrome for Mac; only the profile paths in
  `extension_info.py` differ.
- The tray: pystray supports the macOS menu bar.

## What needs a Mac version

| Windows piece | Mac equivalent | Difficulty |
|---|---|---|
| `win_audio` (per-app volume) | No OS API. See "Per-app volume" below. | Hard |
| `win_media` (play/pause, track, seek) | MediaRemote, a private framework, locked down since macOS 15.4. Workarounds exist (e.g. the open-source mediaremote-adapter). | Medium, fragile |
| Device switching (IPolicyConfig) | CoreAudio's default-output-device property (public API) | Easy |
| `win_window` (raise / close an app) | `NSRunningApplication.activate()` / `terminate()` via pyobjc | Easy |
| `icons.py`, `app_names.py` | `NSWorkspace.iconForFile`, the bundle's display name | Easy |
| `single_instance` (named mutex) | A lock file with `fcntl.flock` | Easy |
| Autostart (Startup `.lnk`) | `SMAppService` login item, or a LaunchAgent plist | Easy |
| `build.bat` (PyInstaller exe) | PyInstaller or py2app `.app`, signed + notarized `.dmg` | Medium |

## Per-app volume: the options

### 1. Core Audio process taps (macOS 14.2+)
Tap one app's audio, mute its original output, play the tapped audio back at the
slider's level. Created through Core Audio's C API (`AudioHardwareCreateProcessTap`)
with a `CATapDescription` object; easiest to call from Swift.

- No driver, no admin install, no Apple approval.
- The user grants audio-recording permission once, and macOS shows a recording
  indicator in the menu bar while a tap runs.
- Real-time audio can't be done reliably in Python (a GC pause is an audible glitch),
  so this would be a small Swift helper talking JSON over stdin/stdout to a
  `mac_audio.py` with the same interface as `win_audio.py`.

### 2. A HAL plugin (AudioServerPlugIn), i.e. a virtual output device
The plugin creates a virtual output device that every app plays into. It is told which
app each stream comes from, scales each one, and the mix goes on to the real speakers.

- Installed into `/Library/Audio/Plug-Ins/HAL` (admin password once; coreaudiod restarts).
- Needs **no** Apple-granted entitlement: only Developer ID signing + notarization.
  (AudioDriverKit, a different, newer driver kind, does need one.)
- Real-time-safe C/C++. A bug in it can cut sound for every app.
- Start from Apple's **NullAudio** sample (Apple's permissive sample-code licence),
  not from BackgroundMusic's code (GPLv2).
- **Passing the mix on to the speakers is the hard part.** The simple way (the app
  records from the virtual device and plays it out, as BackgroundMusic does) counts as
  recording, so macOS shows the microphone indicator while audio plays. SoundSource and
  AppVolume avoid it with their own, unpublished, methods. Doing this cleanly is
  research, not a weekend.

### 3. Drive BackgroundMusic (https://github.com/kyleneideck/BackgroundMusic)
Free, open source (GPLv2, C++), ~19k stars. Its BGMDriver is a HAL plugin as in option 2.
Faderly would talk to it as a separate program (no GPL issue at arm's length).

- Still maintained, slowly: v0.5.0 in June 2026 after two years without a release
  (Rosetta no longer needed to install, per-app mute, better browser/Discord support,
  crash fixes). ~530 open issues.
- Shows the microphone indicator while audio plays (its own release notes say so).
- Open in Aug 2026: display won't wake from sleep while it's installed (several reports,
  especially with IINA), FaceTime volume issues, YouTube in Safari not responding.
- **Unchecked:** whether its AppleScript support covers per-app volume or only the
  output device. That decides whether this option works at all.
- Users would install two apps.

### Comparison

| | Effort | For users |
|---|---|---|
| Process taps (Swift helper) | Medium | Recording indicator while active |
| Drive BackgroundMusic | Smallest, if its scripting allows it | Two installs, its indicator and bugs |
| Own HAL plugin (from NullAudio) | Largest, plus the pass-through research | One app; clean only if pass-through is solved |

**Recommendation:** prototype with process taps. It has the same indicator cost as
BackgroundMusic without the driver install or its bugs. Consider an own HAL plugin only
if Faderly grows and the indicator matters.

## Reference: AppVolume

A Mac per-app volume app (r/macapps, 2025): per-app sliders, mute, output picker, menu
bar. Said to use a HAL plugin; its author mentioned waiting for Apple to approve
entitlements, which suggests AudioDriverKit or the Mac App Store (a plain HAL plugin
needs no approval). No track info or media controls: Faderly's browser tabs, media
controls/seek and the multi-machine mixer would set it apart.

## Order of work (when we pick this up)

0. On Windows, now-ish: move the platform code in `hub.py` behind an interface, so
   the Mac version adds files instead of editing shared ones.
1. Faderly running on a Mac without per-app volume: window, tray, Chrome tabs.
2. Easy native pieces: device switching, icons/names, raise/close, single instance,
   autostart.
3. Per-app volume (process-tap helper).
4. Media controls last: most likely to break with macOS updates.

Requirements: a real Mac to build and test on (GitHub's macOS runners can build, not
test audio); a $99/year Apple Developer account to sign and notarize for other people.
