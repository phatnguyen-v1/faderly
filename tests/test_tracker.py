"""Tests for the Now playing / Recent rules in tracker.py.

Runnable with either `python -m unittest discover -s tests` or pytest.
`now` is a plain increasing number in these tests; the tracker only
compares and subtracts, so any monotonic clock works.
"""
import unittest

from audiohub.tracker import PLAY_HOLD, SOUND_FLOOR, Tracker

T0 = 1_000.0


def audio(peak=0.0, volume=0.8, muted=False, name="App"):
    return {"name": name, "peak": peak, "volume": volume, "muted": muted}


def media(status="paused", title="Song", artist="Artist"):
    return {"name": "App", "title": title, "artist": artist, "status": status}


def tab(playing=False, has_media=True, volume=1.0, muted=False, title="Tab", site="site.com"):
    return {"parent": "chrome", "parent_name": "Chrome", "title": title, "site": site,
            "playing": playing, "has_media": has_media, "volume": volume, "muted": muted}


def keys(rows):
    return [r["key"] for r in rows]


class TestPlaying(unittest.TestCase):
    def test_media_playing_is_now_playing(self):
        tr = Tracker()
        tr.update({}, {"spotify": media("playing", title="X", artist="Y")}, {"spotify"}, T0)
        playing, recent = tr.view(T0)
        self.assertEqual(keys(playing), ["spotify"])
        self.assertEqual(playing[0]["title"], "X")
        self.assertEqual(playing[0]["artist"], "Y")
        self.assertEqual(recent, [])

    def test_sound_keeps_app_playing_for_play_hold(self):
        tr = Tracker()
        tr.update({"vlc": audio(peak=SOUND_FLOOR + 0.1)}, {}, {"vlc"}, T0)
        playing, _ = tr.view(T0)
        self.assertEqual(keys(playing), ["vlc"])

        # Silence shorter than PLAY_HOLD: still playing (gaps between songs).
        tr.update({"vlc": audio(peak=0.0)}, {}, {"vlc"}, T0 + PLAY_HOLD - 0.1)
        playing, _ = tr.view(T0 + PLAY_HOLD - 0.1)
        self.assertEqual(keys(playing), ["vlc"])

        # Silence longer than PLAY_HOLD: falls to Recent while still running.
        tr.update({"vlc": audio(peak=0.0)}, {}, {"vlc"}, T0 + PLAY_HOLD + 0.1)
        playing, recent = tr.view(T0 + PLAY_HOLD + 0.1)
        self.assertEqual(playing, [])
        self.assertEqual(keys(recent), ["vlc"])

    def test_muted_app_makes_no_sound(self):
        tr = Tracker()
        tr.update({"vlc": audio(peak=1.0, muted=True)}, {}, {"vlc"}, T0)
        playing, _ = tr.view(T0)
        self.assertEqual(playing, [])

    def test_playing_order_follows_start_time(self):
        tr = Tracker()
        tr.update({}, {"a": media("playing"), "b": media("playing")}, {"a", "b"}, T0)
        tr.update({}, {"a": media("playing"), "b": media("playing")}, {"a", "b"}, T0 + 1)
        playing, _ = tr.view(T0 + 1)
        self.assertEqual(keys(playing), ["a", "b"])


class TestRecent(unittest.TestCase):
    def test_paused_player_stays_while_registered(self):
        tr = Tracker()
        tr.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        tr.update({}, {"spotify": media("paused")}, {"spotify"}, T0 + 5)
        playing, recent = tr.view(T0 + 5)
        self.assertEqual(playing, [])
        self.assertEqual(keys(recent), ["spotify"])
        self.assertEqual(recent[0]["status"], "paused")

    def test_recent_timeout_measured_from_last_played(self):
        tr = Tracker(recent_timeout=600)
        tr.update({"vlc": audio(peak=0.5)}, {}, {"vlc"}, T0)
        # Sound stops; no media player; process still alive.
        tr.update({"vlc": audio()}, {}, {"vlc"}, T0 + 10)
        _, recent = tr.view(T0 + 599)  # 599s since it last played: still listed
        self.assertEqual(keys(recent), ["vlc"])
        _, recent = tr.view(T0 + 601)  # the timeout runs from last_played
        self.assertEqual(recent, [])

    def test_closed_app_removed_at_once(self):
        tr = Tracker()
        tr.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        # App gone: no player, no stream, no process.
        tr.update({}, {}, set(), T0 + 1)
        playing, recent = tr.view(T0 + 1)
        self.assertEqual(playing, [])
        self.assertEqual(recent, [])

    def test_recent_newest_first_and_capped(self):
        tr = Tracker(recent_cap=3)
        apps = {}
        for i in range(5):
            key = f"app{i}"
            apps[key] = audio(peak=0.5)
            tr.update(dict(apps), {}, set(apps), T0 + i * 10)
            apps[key] = audio(peak=0.0)
            tr.update(dict(apps), {}, set(apps), T0 + i * 10 + 5)
        _, recent = tr.view(T0 + 100)
        self.assertEqual(keys(recent), ["app4", "app3", "app2"])


class TestDismiss(unittest.TestCase):
    def test_dismiss_hides_until_it_plays_again(self):
        tr = Tracker()
        tr.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        tr.update({}, {"spotify": media("paused")}, {"spotify"}, T0 + 5)
        tr.dismiss("spotify", T0 + 6)
        _, recent = tr.view(T0 + 7)
        self.assertEqual(recent, [])
        # Plays again after being dismissed: visible again.
        tr.update({}, {"spotify": media("playing")}, {"spotify"}, T0 + 8)
        playing, _ = tr.view(T0 + 8)
        self.assertEqual(keys(playing), ["spotify"])

    def test_remember_volume(self):
        tr = Tracker()
        tr.update({"vlc": audio(peak=0.5)}, {}, {"vlc"}, T0)
        tr.update({"vlc": audio()}, {}, {"vlc"}, T0 + 5)
        tr.remember_volume("vlc", volume=0.25, muted=True)
        _, recent = tr.view(T0 + 5)
        self.assertEqual(recent[0]["volume"], 0.25)
        self.assertTrue(recent[0]["muted"])


class TestProgress(unittest.TestCase):
    def test_position_runs_on_while_playing(self):
        tr = Tracker()
        m = dict(media("playing"), position=10.0, duration=200.0, seekable=True, position_at=T0)
        tr.update({}, {"spotify": m}, {"spotify"}, T0)
        playing, _ = tr.view(T0 + 2.5)
        self.assertAlmostEqual(playing[0]["position"], 12.5)
        self.assertEqual(playing[0]["duration"], 200.0)
        self.assertTrue(playing[0]["seekable"])

    def test_paused_position_stands_still(self):
        tr = Tracker()
        m = dict(media("paused"), position=10.0, duration=200.0, seekable=True, position_at=T0)
        tr.update({}, {"spotify": m}, {"spotify"}, T0)
        _, recent = tr.view(T0 + 30)
        self.assertEqual(recent[0]["position"], 10.0)

    def test_no_timeline_no_progress(self):
        tr = Tracker()
        tr.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        playing, _ = tr.view(T0)
        self.assertIsNone(playing[0]["position"])

    def test_tab_progress(self):
        tr = Tracker()
        t = dict(tab(playing=True), position=5.0, duration=60.0, position_at=T0)
        tr.update({}, {}, set(), T0, {"chrome#1": t})
        playing, _ = tr.view(T0 + 1)
        row = playing[0]["tabs"][0]
        self.assertAlmostEqual(row["position"], 6.0)
        self.assertTrue(row["seekable"])


class TestTrackButtons(unittest.TestCase):
    def test_player_flags_reach_the_row(self):
        tr = Tracker()
        tr.update({}, {"spotify": dict(media("paused"), can_next=True, can_prev=False)},
                  {"spotify"}, T0)
        _, recent = tr.view(T0)
        self.assertTrue(recent[0]["canNext"])
        self.assertFalse(recent[0]["canPrev"])

    def test_tab_flags_reach_the_row(self):
        tr = Tracker()
        tr.update({}, {}, set(), T0, {"chrome#1": dict(tab(playing=True), can_next=True, can_prev=True)})
        playing, _ = tr.view(T0)
        row = playing[0]["tabs"][0]
        self.assertTrue(row["canNext"] and row["canPrev"])

    def test_no_player_no_buttons(self):
        tr = Tracker()
        tr.update({"vlc": audio(peak=0.5)}, {}, {"vlc"}, T0)
        playing, _ = tr.view(T0)
        self.assertFalse(playing[0]["canNext"] or playing[0]["canPrev"])


class TestKeepAndHide(unittest.TestCase):
    def test_kept_row_stays_long_after_it_played(self):
        tr = Tracker(recent_timeout=600)
        tr.update({"vlc": audio(peak=0.5)}, {}, {"vlc"}, T0)
        tr.update({"vlc": audio(peak=0.0)}, {}, {"vlc"}, T0 + 5000)
        self.assertEqual(tr.view(T0 + 5000)[1], [])
        self.assertEqual(keys(tr.view(T0 + 5000, keep={"vlc"})[1]), ["vlc"])

    def test_cap_never_drops_kept_rows(self):
        tr = Tracker(recent_cap=2)
        for i, k in enumerate(["a", "b", "c", "d"]):
            tr.update({k: audio(peak=0.5)}, {}, {"a", "b", "c", "d"}, T0 + i)
        tr.update({}, {}, {"a", "b", "c", "d"}, T0 + 10)
        _, recent = tr.view(T0 + 10, keep={"a"})
        self.assertIn("a", keys(recent))  # oldest, but kept
        self.assertEqual(len(recent), 3)  # a + the 2 newest

    def test_hidden_row_shows_while_playing_but_not_in_recent(self):
        tr = Tracker()
        tr.update({"taskhostw": audio(peak=0.5)}, {}, {"taskhostw"}, T0)
        self.assertEqual(keys(tr.view(T0, hide={"taskhostw"})[0]), ["taskhostw"])
        tr.update({"taskhostw": audio(peak=0.0)}, {}, {"taskhostw"}, T0 + 5)
        self.assertEqual(tr.view(T0 + 5, hide={"taskhostw"})[1], [])


class TestMuted(unittest.TestCase):
    def test_muted_app_stays_in_recent_to_be_unmuted(self):
        tr = Tracker(recent_timeout=600)
        # Muted before it ever played: no sound, so never "playing", no last_played.
        tr.update({"zalo": audio(peak=0.5, muted=True)}, {}, {"zalo"}, T0)
        tr.update({"zalo": audio(peak=0.5, muted=True)}, {}, {"zalo"}, T0 + 3600)
        playing, recent = tr.view(T0 + 3600)
        self.assertEqual(playing, [])
        self.assertEqual(keys(recent), ["zalo"])
        self.assertEqual(recent[0]["status"], "muted")
        self.assertTrue(recent[0]["muted"])

    def test_muted_app_without_a_stream_is_not_listed(self):
        tr = Tracker()
        tr.update({}, {}, {"zalo"}, T0)
        tr.remember_volume("zalo", muted=True)
        self.assertEqual(tr.view(T0), ([], []))


class TestTabs(unittest.TestCase):
    def test_playing_tab_grouped_under_its_browser(self):
        tr = Tracker()
        tr.update({}, {"chrome": media("playing", title="")}, {"chrome"}, T0,
                  {"chrome#11": tab(playing=True, title="Video")})
        playing, recent = tr.view(T0)
        self.assertEqual(keys(playing), ["chrome"])
        self.assertTrue(playing[0]["group"])
        self.assertEqual(keys(playing[0]["tabs"]), ["chrome#11"])
        self.assertEqual(playing[0]["tabs"][0]["name"], "Video")
        self.assertEqual(recent, [])

    def test_browser_without_a_row_gets_a_header(self):
        tr = Tracker()
        tr.update({}, {}, set(), T0, {"chrome#11": tab(playing=True, title="Video")})
        playing, _ = tr.view(T0)
        self.assertEqual(keys(playing), ["chrome"])
        self.assertEqual(playing[0]["name"], "Chrome")
        self.assertEqual(keys(playing[0]["tabs"]), ["chrome#11"])

    def test_paused_tab_stays_in_its_browser_while_another_plays(self):
        tr = Tracker()
        tr.update({}, {}, set(), T0, {"chrome#1": tab(playing=True, title="Song")})
        tr.update({}, {}, set(), T0 + 5, {"chrome#1": tab(playing=False, title="Song"),
                                          "chrome#2": tab(playing=True, title="Feed")})
        playing, recent = tr.view(T0 + 5)
        self.assertEqual(keys(playing), ["chrome"])
        self.assertEqual(keys(playing[0]["tabs"]), ["chrome#2"])
        self.assertEqual(keys(playing[0]["recentTabs"]), ["chrome#1"])
        self.assertEqual(recent, [])  # not duplicated in the machine-wide Recent

    def test_browser_group_moves_to_recent_when_nothing_plays(self):
        tr = Tracker()
        tr.update({}, {}, {"vlc"}, T0, {"chrome#1": tab(playing=True)})
        tr.update({"vlc": audio(peak=0.5)}, {}, {"vlc"}, T0 + 3, {"chrome#1": tab(playing=False)})
        playing, recent = tr.view(T0 + 3)
        self.assertEqual(keys(playing), ["vlc"])
        self.assertEqual(keys(recent), ["chrome"])
        self.assertEqual(keys(recent[0]["tabs"]), ["chrome#1"])
        self.assertEqual(recent[0]["recentTabs"], [])

    def test_tab_removed_when_extension_stops_reporting_it(self):
        tr = Tracker()
        tr.update({}, {}, set(), T0, {"chrome#11": tab(playing=True)})
        tr.update({}, {}, set(), T0 + 1, {})
        playing, recent = tr.view(T0 + 1)
        self.assertEqual(playing, [])
        self.assertEqual(recent, [])

    def test_tab_with_media_has_status_and_volume(self):
        tr = Tracker()
        tr.update({}, {}, set(), T0,
                  {"chrome#11": tab(playing=False, has_media=True, volume=0.5)})
        _, recent = tr.view(T0)
        row = recent[0]["tabs"][0]
        self.assertEqual(row["status"], "paused")
        self.assertEqual(row["volume"], 0.5)


if __name__ == "__main__":
    unittest.main()
