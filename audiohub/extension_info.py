"""The browser extension, as seen from the app: where its folder is, which version
ships with the app, and what to suggest for each open browser.

Status per browser (only Chromium browsers with a visible window count, since
Edge often runs in the background with none):
  ok        connected, sharing tabs, current version
  outdated  connected, older than the version that ships with the app
  missing   open, but no extension connected: not installed, or switched off
"""
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("audiohub")

# Where each browser keeps its profiles ("Local State" names the last-used one).
USER_DATA = {
    "chrome": r"Google\Chrome\User Data",
    "msedge": r"Microsoft\Edge\User Data",
    "brave": r"BraveSoftware\Brave-Browser\User Data",
    "vivaldi": r"Vivaldi\User Data",
    "chromium": r"Chromium\User Data",
}

BROWSERS = {  # app key -> (name, extensions page)
    "chrome": ("Chrome", "chrome://extensions"),
    "msedge": ("Edge", "edge://extensions"),
    "brave": ("Brave", "brave://extensions"),
    "vivaldi": ("Vivaldi", "vivaldi://extensions"),
    "opera": ("Opera", "opera://extensions"),
    "chromium": ("Chromium", "chrome://extensions"),
}


def _source_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "extension"  # bundled by build.bat
    return Path(__file__).resolve().parent.parent / "extension"


def extension_dir():
    """The folder to load in the browser ("Load unpacked").

    From source that's the repo's extension/. The built exe carries a copy and
    keeps it at %LOCALAPPDATA%\\AudioHub\\extension, refreshed every launch, so the
    path stays put and "Update now" in the app picks up the new files.
    """
    src = _source_dir()
    if not getattr(sys, "frozen", False):
        return src
    dest = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AudioHub" / "extension"
    try:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    except Exception:
        log.exception("couldn't copy the extension to %s", dest)
    return dest


def shipped_version(folder=None):
    try:
        manifest = Path(folder or _source_dir()) / "manifest.json"
        return json.loads(manifest.read_text(encoding="utf-8")).get("version") or "0"
    except Exception:
        return "0"


def _v(version):
    try:
        return tuple(int(x) for x in str(version).split("."))
    except ValueError:
        return (0,)


def statuses(open_browsers, ext, latest):
    """[{key, name, state, version, latest, accepts}] for each open or connected browser.

    ext: browser key -> {"version": str, "accepts": [...]} for
    connected extensions (the hub's self.ext). Up to 0.6 the extension had a
    "Send tabs" switch; one connected with it off still counts as outdated, since
    updating removes the switch.
    """
    out = []
    for key in sorted(set(open_browsers) | set(ext)):
        name = BROWSERS.get(key, (key.title(), ""))[0]
        info = ext.get(key)
        if info is None:
            state, version, accepts = "missing", None, []
        else:
            version = info.get("version") or "0"  # before 0.6 the extension sent none
            accepts = info.get("accepts") or []
            if _v(version) < _v(latest):
                state = "outdated"
            else:
                state = "ok"
        out.append({"key": key, "name": name, "state": state, "version": version,
                    "latest": latest, "accepts": accepts})
    return out


def last_used_profile(browser, local_appdata=None):
    """The profile folder name the browser used last ('Profile 6'), or None."""
    sub = USER_DATA.get(browser)
    if not sub:
        return None
    base = Path(local_appdata or os.environ.get("LOCALAPPDATA", "")) / sub / "Local State"
    try:
        return json.loads(base.read_text(encoding="utf-8")).get("profile", {}).get("last_used") or None
    except Exception:
        return None


def open_command(exe, browser, profile):
    """Command line that hands the page to the running browser as a new tab.

    Naming the profile matters: with several profiles, a bare launch can open a
    window of a different one, which looks like a second browser.
    """
    url = BROWSERS.get(browser, ("", "chrome://extensions"))[1]
    return [exe] + ([f"--profile-directory={profile}"] if profile else []) + [url]


def open_extensions_page(browser):
    """Fallback when the extension can't open the tab itself (not connected, or too old)."""
    import psutil

    from .win_audio import app_key
    exe = next((p.info["exe"] for p in psutil.process_iter(["name", "exe"])
                if p.info["name"] and app_key(p.info["name"]) == browser and p.info["exe"]), None)
    if not exe:
        raise FileNotFoundError(f"{browser} isn't running")
    subprocess.Popen(open_command(exe, browser, last_used_profile(browser)), close_fds=True)


def open_folder(path):
    os.startfile(str(path))  # Explorer
