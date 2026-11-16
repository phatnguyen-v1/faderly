"""Settings the app keeps across restarts: %LOCALAPPDATA%\\AudioHub\\settings.json.

pinned: [{key, name}] in pin order.
"""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("audiohub")

DEFAULT_PINS = [{"key": "chrome", "name": "Chrome"}, {"key": "spotify", "name": "Spotify"}]


def default_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AudioHub" / "settings.json"


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()
        self.data = {"pinned": [dict(p) for p in DEFAULT_PINS]}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded.get("pinned"), list):
                self.data["pinned"] = [p for p in loaded["pinned"]
                                       if isinstance(p, dict) and p.get("key")]
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("couldn't read %s; using defaults", self.path)

    def save(self):
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                tmp.replace(self.path)  # never a half-written file
            except Exception:
                log.exception("couldn't save %s", self.path)

    # ---- pins -------------------------------------------------------------
    def pins(self):
        return list(self.data["pinned"])

    def pinned_keys(self):
        return {p["key"] for p in self.data["pinned"]}

    def pin(self, key, name=None):
        if key in self.pinned_keys():
            return
        self.data["pinned"].append({"key": key, "name": name or key})
        self.save()

    def unpin(self, key):
        before = len(self.data["pinned"])
        self.data["pinned"] = [p for p in self.data["pinned"] if p["key"] != key]
        if len(self.data["pinned"]) != before:
            self.save()
