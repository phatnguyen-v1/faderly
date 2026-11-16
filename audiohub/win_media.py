"""Windows media players (the ones in the Quick Settings media pop-up)."""
import datetime
import time

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as Manager,
)

from . import app_names
from .win_audio import NICE_NAMES

PLAYING, PAUSED = 4, 5

# Chromium browsers register non-default profiles as e.g. 'Chrome.UserData.Profile6'.
BROWSERS = {"chrome", "msedge", "brave", "vivaldi", "opera", "chromium"}


def media_key(aumid: str) -> str:
    """Map an app id to the same key as its process name.

    'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify' -> 'spotify'
    'Chrome' -> 'chrome', 'MSEdge' -> 'msedge', r'C:\\...\\vlc.exe' -> 'vlc'
    'Chrome.UserData.Profile6' -> 'chrome' (a browser profile is still that browser)
    """
    part = aumid.split("!")[-1].replace("/", "\\").rsplit("\\", 1)[-1]
    if part.lower().endswith(".exe"):
        part = part[:-4]
    part = part.lower()
    head = part.split(".", 1)[0]
    return head if head in BROWSERS else part


class WinMedia:
    def __init__(self):
        self._mgr = None
        self._sessions = {}
        self.app_ids = {}  # key -> app id (AUMID), for the row icon

    async def start(self):
        self._mgr = await Manager.request_async()

    async def read(self):
        """key -> {name, title, artist, status} for every registered player."""
        out, sessions = {}, {}
        for s in self._mgr.get_sessions():
            try:
                aumid = s.source_app_user_model_id
                base = media_key(aumid)
                # An app with several players gets one row each: spotify, spotify~2, ...
                key, n = base, 1
                while key in out:
                    n += 1
                    key = f"{base}~{n}"
                st = int(s.get_playback_info().playback_status)
                status = "playing" if st == PLAYING else "paused" if st == PAUSED else "stopped"
                try:
                    p = await s.try_get_media_properties_async()
                    title, artist = p.title or "", p.artist or ""
                except Exception:
                    title = artist = ""
            except Exception:
                continue
            try:
                ctl = s.get_playback_info().controls
                can_next, can_prev = bool(ctl.is_next_enabled), bool(ctl.is_previous_enabled)
            except Exception:
                can_next = can_prev = True
            out[key] = {"name": app_names.for_player(base, aumid, NICE_NAMES.get(base)), "title": title, "artist": artist, "status": status,
                        "can_next": can_next, "can_prev": can_prev,
                        **self._timeline(s, status)}
            sessions[key] = s
            self.app_ids.setdefault(key, aumid)
        self._sessions = sessions
        return out

    @staticmethod
    def _timeline(s, status):
        """position/duration in seconds (None if the player gives none), as of position_at.

        Players update the position only now and then, so while playing add the
        time since their last update.
        """
        try:
            t = s.get_timeline_properties()
            duration = (t.end_time - t.start_time).total_seconds()
            if duration <= 0:
                return {"position": None, "duration": None, "seekable": False}
            pos = (t.position - t.start_time).total_seconds()
            if status == "playing":
                pos += (datetime.datetime.now(datetime.timezone.utc) - t.last_updated_time).total_seconds()
            seekable = bool(s.get_playback_info().controls.is_playback_position_enabled)
            return {"position": max(0.0, min(pos, duration)), "duration": duration,
                    "seekable": seekable, "position_at": time.monotonic()}
        except Exception:
            return {"position": None, "duration": None, "seekable": False}

    async def seek(self, key, seconds):
        s = self._sessions.get(key)
        if s is None:
            return
        t = s.get_timeline_properties()
        ticks = int((t.start_time.total_seconds() + max(0.0, float(seconds))) * 10_000_000)
        await s.try_change_playback_position_async(ticks)

    async def command(self, key, cmd):
        s = self._sessions.get(key)
        if s is None:
            return
        if cmd == "playpause":
            await s.try_toggle_play_pause_async()
        elif cmd == "pause":  # never a toggle: "pause all" must not start anything
            await s.try_pause_async()
        elif cmd == "play":  # never a toggle: "resume all" must not pause anything
            await s.try_play_async()
        elif cmd == "next":
            await s.try_skip_next_async()
        elif cmd == "prev":
            await s.try_skip_previous_async()
