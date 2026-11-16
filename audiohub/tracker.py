"""Now Playing / Recent bookkeeping. Platform-neutral, so a Mac agent can reuse it.

Rules:
- Playing: the player says it's playing, or the app made sound in the last PLAY_HOLD s.
- Recent: stopped/paused things, newest play first.
  * still registered as a player (paused)  -> stays while the player exists, can resume
  * no player, app still running (e.g. closed browser tab) -> drops after `timeout`
- App closed (no process, no player, no stream) -> removed at once.
- Muted app with a live stream -> stays in Recent ("muted"), however long ago it played.
- Dismissed (x) -> hidden until it plays again.

Browser tabs (reported by the browser extension) are child rows of their browser:
they follow the same rules and are removed when the tab closes. In the view, a
browser and its tabs form one group (see Tracker.view), so a paused tab stays
with its browser instead of dropping into the machine-wide Recent.
"""

PLAY_HOLD = 2.0     # seconds of silence before something counts as stopped (gaps between songs)
SOUND_FLOOR = 0.003  # peak level treated as silence


class Row:
    def __init__(self, key):
        self.key = key
        self.name = key
        self.audio = None
        self.media = None
        self.playing = False
        self.last_sound = None
        self.last_played = None
        self.play_started = None  # when the current play began; keeps Now playing in a steady order
        self.dismissed_at = None
        self.volume = None
        self.muted = False
        self.parent = None       # browser key, for tab rows
        self.parent_name = None
        self.site = ""


class Tracker:
    def __init__(self, recent_timeout=600, recent_cap=10):
        self.recent_timeout = recent_timeout
        self.recent_cap = recent_cap
        self.rows = {}

    def update(self, audio, media, running, now, tabs=None):
        """audio/media/tabs: key -> dict; running: set of app keys with a live process.

        tabs[key] = {parent, parent_name, title, site, playing, has_media, volume, muted}
        """
        tabs = tabs or {}
        for key in set(audio) | set(media) | set(tabs) | set(self.rows):
            r = self.rows.get(key)
            t = tabs.get(key)
            if t is not None:
                if r is None:
                    r = self.rows[key] = Row(key)
                self._update_tab(r, t, now)
                continue
            if r is not None and r.parent is not None:
                self.rows.pop(key)  # tab closed, or the extension went away
                continue
            a, m = audio.get(key), media.get(key)
            if a is None and m is None and key not in running:
                self.rows.pop(key, None)  # app closed
                continue
            if r is None:
                r = self.rows[key] = Row(key)
            r.audio, r.media = a, m
            if a:
                r.name, r.volume, r.muted = a["name"], a["volume"], a["muted"]
                if a["peak"] > SOUND_FLOOR and not a["muted"]:
                    r.last_sound = now
            elif m:
                r.name = m["name"]
            self._set_playing(r, bool((m and m["status"] == "playing")
                                      or (r.last_sound is not None and now - r.last_sound < PLAY_HOLD)), now)

    @staticmethod
    def _set_playing(r, playing, now):
        if playing and not r.playing:
            r.play_started = now
        r.playing = playing
        if playing:
            r.last_played = now

    def _update_tab(self, r, t, now):
        r.parent, r.parent_name = t["parent"], t["parent_name"]
        r.name = t["title"] or t["site"] or "Tab"
        r.site = t["site"]
        r.audio = None
        self._set_playing(r, bool(t["playing"]), now)
        r.media = ({"status": "playing" if r.playing else "paused", "title": "", "artist": "",
                    "position": t.get("position"), "duration": t.get("duration"),
                    "seekable": t.get("duration") is not None, "position_at": t.get("position_at"),
                    "can_next": t.get("can_next", False), "can_prev": t.get("can_prev", False)}
                   if t["has_media"] else None)
        r.volume, r.muted = t["volume"], t["muted"]

    def dismiss(self, key, now):
        r = self.rows.get(key)
        if r:
            r.dismissed_at = now

    def remember_volume(self, key, volume=None, muted=None):
        r = self.rows.get(key)
        if r:
            if volume is not None:
                r.volume = volume
            if muted is not None:
                r.muted = muted

    def view(self, now, keep=frozenset(), hide=frozenset()):
        """(playing, recent) lists of rows for the UI.

        keep: keys that stay listed whatever their age (pinned apps); the Recent
        cap never drops them. hide: keys that never enter Recent (Windows' own
        processes), though they still show while playing.

        Each browser with tabs is one group row: {"group": True, "tabs": [...],
        "recentTabs": [...]}. The group sits in Now playing when the browser or
        any of its tabs plays, and then carries its paused tabs in "recentTabs";
        otherwise it sits in Recent with its recent tabs in "tabs".
        """
        playing, recent = [], []
        for r in self.rows.values():
            if r.playing:
                playing.append(r)
                continue
            if r.key in keep:
                recent.append(r)
                continue
            if r.key in hide:
                continue
            if r.dismissed_at is not None and (r.last_played or 0) <= r.dismissed_at:
                continue
            if r.media is not None:
                recent.append(r)
            elif r.audio is not None and r.muted:
                # Muted, it can never make sound to count as playing: keep it in
                # sight, or there's no way to unmute it from here.
                recent.append(r)
            elif r.last_played is not None and now - r.last_played < self.recent_timeout:
                recent.append(r)
        # Now playing keeps the order things started in, so rows don't jump under the mouse.
        by_start = lambda r: (r.play_started or 0, r.key)  # noqa: E731
        by_recent = lambda r: -(r.last_played or float("-inf"))  # noqa: E731

        tabs = {}  # browser key -> ([playing tabs], [recent tabs])
        for i, lst in enumerate((playing, recent)):
            for r in lst:
                if r.parent is not None:
                    tabs.setdefault(r.parent, ([], []))[i].append(r)
        top_playing = [(by_start(r), self._row(r, now)) for r in playing
                       if r.parent is None and r.key not in tabs]
        top_recent = [(by_recent(r), self._row(r, now)) for r in recent
                      if r.parent is None and r.key not in tabs]
        for browser, (p_tabs, r_tabs) in tabs.items():
            b = self.rows.get(browser)
            p_tabs.sort(key=by_start)
            r_tabs.sort(key=by_recent)
            r_tabs = r_tabs[:self.recent_cap]
            head = self._row(b, now) if b else self._head(browser, (p_tabs or r_tabs)[0], now)
            head["group"] = True
            if p_tabs or (b and b.playing):
                head["tabs"] = [self._row(t, now) for t in p_tabs]
                head["recentTabs"] = [self._row(t, now) for t in r_tabs]
                starts = [by_start(t) for t in p_tabs] + ([by_start(b)] if b and b.playing else [])
                top_playing.append((min(starts), head))
            else:
                head["tabs"] = [self._row(t, now) for t in r_tabs]
                head["recentTabs"] = []
                top_recent.append((min(by_recent(t) for t in r_tabs), head))
        top_playing.sort(key=lambda x: x[0])
        top_recent.sort(key=lambda x: x[0])
        kept = [d for _, d in top_recent if d["key"] in keep]
        others = [d for _, d in top_recent if d["key"] not in keep][:self.recent_cap]
        return [d for _, d in top_playing], kept + others

    @staticmethod
    def _head(browser, tab, now):
        """A group header for a browser that has no row of its own (no stream, no player)."""
        return {"key": browser, "name": tab.parent_name or browser, "status": "stopped",
                "title": "", "artist": "", "volume": None, "muted": False, "peak": 0.0,
                "media": False, "ago": None, "parent": None}

    @staticmethod
    def _row(r, now):
        m = r.media or {}
        status = "playing" if r.playing else m.get("status", "stopped")
        if status == "playing" and not r.playing:
            status = "paused"
        if status == "stopped" and r.muted and r.audio is not None:
            status = "muted"
        return {
            "key": r.key,
            "name": r.name,
            "status": status,
            "title": m.get("title", ""),
            "artist": m.get("artist", "") or r.site,
            "volume": r.volume,
            "muted": r.muted,
            "peak": r.audio["peak"] if r.audio else 0.0,
            "media": r.media is not None,
            "ago": None if r.last_played is None else now - r.last_played,
            "parent": r.parent,
            "canNext": bool(m.get("can_next")),
            "canPrev": bool(m.get("can_prev")),
            **Tracker._progress(r, m, now),
        }

    @staticmethod
    def _progress(r, m, now):
        """Playback position in seconds as of `now`, for the progress bar; None when unknown."""
        pos, dur = m.get("position"), m.get("duration")
        if pos is None or not dur:
            return {"position": None, "duration": None, "seekable": False}
        if r.playing and m.get("position_at") is not None:
            pos += max(0.0, now - m["position_at"])
        return {"position": min(pos, dur), "duration": dur, "seekable": bool(m.get("seekable"))}
