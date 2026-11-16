"""Tests for hub Pause All / Mute All and tab commands.

The hub's Windows objects are replaced with fakes, so no COM is touched.
"""
import asyncio
import json
import time
import unittest

from audiohub.hub import Hub

T0 = 1_000.0


def audio(peak=0.5):
    return {"name": "App", "peak": peak, "volume": 0.8, "muted": False}


def media(status="playing"):
    return {"name": "App", "title": "Song", "artist": "Artist", "status": status}


def tab(playing=True, has_media=True):
    return {"parent": "chrome", "parent_name": "Chrome", "title": "Video",
            "site": "site.com", "playing": playing, "has_media": has_media,
            "volume": 1.0, "muted": False}


class FakeAudio:
    def __init__(self):
        self.muted = []
        self.volumes = []

    def set_mute(self, key, muted):
        self.muted.append((key, muted))

    def set_volume(self, key, value):
        self.volumes.append((key, value))

    def set_output(self, device_id):
        self.output = device_id

    def master(self):
        return {"device": "Speakers", "deviceId": "x", "outputs": [], "volume": 0.5, "muted": False}


class FakeMedia:
    def __init__(self, fail=()):
        self.commands = []
        self.fail = set(fail)

    async def seek(self, key, seconds):
        self.commands.append((key, "seek", seconds))

    async def command(self, key, cmd):
        if key in self.fail:
            raise RuntimeError("player gone")
        self.commands.append((key, cmd))


class FakeExtWs:
    def __init__(self):
        self.sent = []

    async def send_str(self, s):
        self.sent.append(json.loads(s))


def make_hub(ext_ws=None, tabs=None, pins=None):
    import tempfile
    from pathlib import Path
    from audiohub.settings import Settings
    hub = Hub()
    # Never the user's real settings file: a fresh one per test hub.
    hub.settings = Settings(Path(tempfile.mkdtemp()) / "settings.json")
    if pins is not None:
        hub.settings.data["pinned"] = [dict(p) for p in pins]
    hub.audio = FakeAudio()
    hub.media = FakeMedia()
    if ext_ws is not None:
        hub.ext = {"chrome": {"ws": ext_ws, "tabs": tabs or []}}
    return hub


def run(coro):
    return asyncio.run(coro)


class TestPauseAll(unittest.TestCase):
    def test_pause_is_explicit_and_skips_rows_without_transport(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        # spotify's player says paused, but its stream still peaks inside
        # PLAY_HOLD: the row is in Now playing and a toggle would start it.
        hub.tracker.update(
            {"chrome": audio(), "vlc": audio(), "spotify": audio()},
            {"spotify": media("paused")},
            {"chrome", "vlc", "spotify"}, T0,
            {"chrome#11": tab()})
        self.assertTrue(hub.tracker.rows["spotify"].playing)

        run(hub._pause_mute_all(mute=False))

        self.assertIn(("spotify", "pause"), hub.media.commands)
        self.assertNotIn(("spotify", "playpause"), hub.media.commands)
        # vlc has no media player: nothing to send it; chrome's hidden player isn't touched.
        self.assertNotIn("vlc", [k for k, _ in hub.media.commands])
        self.assertNotIn("chrome", [k for k, _ in hub.media.commands])
        self.assertEqual(ext_ws.sent,
                         [{"cmd": "pause", "tabId": 11, "value": None}])
        self.assertFalse(hub.tracker.rows["chrome#11"].muted)
        # Non-playing rows (nothing here) and browsers themselves are left alone.
        self.assertEqual(hub.audio.muted, [])

    def test_pause_all_records_mute_for_tabs_without_media_element(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        hub.tracker.update({}, {}, set(), T0, {"chrome#11": tab(has_media=False)})

        run(hub._pause_mute_all(mute=False))

        # No video/audio element: the extension mutes the tab, so the row
        # must show muted right away instead of waiting for the next report.
        self.assertTrue(hub.tracker.rows["chrome#11"].muted)
        self.assertEqual(ext_ws.sent,
                         [{"cmd": "pause", "tabId": 11, "value": None}])

    def test_one_failing_row_does_not_stop_the_others(self):
        hub = make_hub(ext_ws=FakeExtWs())
        hub.media = FakeMedia(fail={"spotify"})
        hub.tracker.update({}, {"spotify": media(), "applemusic": media("playing")},
                           {"spotify", "applemusic"}, T0)

        with self.assertLogs("audiohub", level="ERROR"):
            run(hub._pause_mute_all(mute=False))

        self.assertIn(("applemusic", "pause"), hub.media.commands)

    def test_tab_mute_updates_the_row_at_once(self):
        hub = make_hub(ext_ws=FakeExtWs())
        hub.tracker.update({}, {}, set(), T0, {"chrome#11": tab()})
        run(hub.command({"cmd": "mute", "key": "chrome#11", "value": True}))
        self.assertTrue(hub.tracker.rows["chrome#11"].muted)
        self.assertEqual(hub.ext["chrome"]["ws"].sent,
                         [{"cmd": "mute", "tabId": 11, "value": True}])


class TestMuteAll(unittest.TestCase):
    def test_only_live_streams_are_muted(self):
        hub = make_hub(ext_ws=FakeExtWs())
        hub.tracker.update(
            {"vlc": audio()},                             # live stream: mute it
            {"nox": media("playing")},                    # no stream yet: leave it
            {"vlc", "nox"}, T0)

        run(hub._pause_mute_all(mute=True))

        self.assertEqual(hub.audio.muted, [("vlc", True)])
        self.assertFalse(hub.tracker.rows["nox"].muted)   # not shown muted either

    def test_extension_browser_row_is_not_muted_in_windows(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        hub.tracker.update({"chrome": audio()}, {}, {"chrome"}, T0,
                           {"chrome#11": tab()})

        run(hub._pause_mute_all(mute=True))

        self.assertEqual(hub.audio.muted, [])             # Chrome itself stays unmuted
        self.assertEqual(ext_ws.sent,
                         [{"cmd": "mute", "tabId": 11, "value": True}])
        self.assertTrue(hub.tracker.rows["chrome#11"].muted)


class TestUndoAll(unittest.TestCase):
    def test_resume_all_plays_only_what_pause_all_paused(self):
        hub = make_hub(ext_ws=FakeExtWs())
        hub.tracker.update({}, {"spotify": media("playing"), "vlc": media("paused")},
                           {"spotify", "vlc"}, T0)
        run(hub._pause_mute_all(mute=False))
        hub.tracker.update({}, {"spotify": media("paused"), "vlc": media("paused")},
                           {"spotify", "vlc"}, T0 + 1)
        self.assertTrue(hub._can_undo("pause"))

        run(hub.command({"cmd": "resume_all"}))

        self.assertEqual(hub.media.commands, [("spotify", "pause"), ("spotify", "play")])
        self.assertFalse(hub._can_undo("pause"))

    def test_entry_survives_a_late_player_report(self):
        hub = make_hub()
        hub.tracker.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        run(hub._pause_mute_all(mute=False))
        # The player still says "playing" right after the pause: keep the entry.
        self.assertIn("spotify", hub._prune_held("pause"))

    def test_user_resuming_forgets_the_entry(self):
        hub = make_hub()
        hub.tracker.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        run(hub._pause_mute_all(mute=False))
        later = time.monotonic() + 10
        self.assertNotIn("spotify", hub._prune_held("pause", now=later))

    def test_something_new_playing_means_pause_all_again(self):
        hub = make_hub()
        hub.tracker.update({}, {"spotify": media("playing")}, {"spotify"}, T0)
        run(hub._pause_mute_all(mute=False))
        hub.tracker.update({}, {"spotify": media("paused"), "vlc": media("playing")},
                           {"spotify", "vlc"}, T0 + 1)
        self.assertFalse(hub._can_undo("pause"))

    def test_unmute_all_leaves_mutes_the_user_set(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        user_muted = dict(tab(), muted=True)
        hub.tracker.update({"vlc": audio()}, {}, {"vlc"}, T0,
                           {"chrome#11": tab(), "chrome#12": user_muted})
        run(hub._pause_mute_all(mute=True))
        self.assertTrue(hub._can_undo("mute"))
        ext_ws.sent.clear()

        run(hub.command({"cmd": "unmute_all"}))

        self.assertEqual(ext_ws.sent, [{"cmd": "mute", "tabId": 11, "value": False}])
        self.assertEqual(hub.audio.muted[-1], ("vlc", False))
        self.assertTrue(hub.tracker.rows["chrome#12"].muted)


class TestResetVolumes(unittest.TestCase):
    def test_apps_and_tabs_back_to_full_and_unmuted(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        quiet_tab = dict(tab(playing=False), volume=0.5, muted=True)
        hub.tracker.update(
            {"zalo": dict(audio(peak=0), muted=True), "vlc": dict(audio(), volume=0.3),
             "spotify": dict(audio(), volume=1.0)},
            {}, {"zalo", "vlc", "spotify", "nox"}, T0, {"chrome#11": quiet_tab})

        run(hub.command({"cmd": "reset_volumes"}))

        self.assertEqual(sorted(hub.audio.muted), [("zalo", False)])
        self.assertEqual(sorted(hub.audio.volumes), [("vlc", 1.0), ("zalo", 1.0)])
        self.assertIn({"cmd": "volume", "tabId": 11, "value": 1.0}, ext_ws.sent)
        self.assertIn({"cmd": "mute", "tabId": 11, "value": False}, ext_ws.sent)
        self.assertFalse(hub.tracker.rows["zalo"].muted)
        self.assertEqual(hub.tracker.rows["chrome#11"].volume, 1.0)


class TestSeek(unittest.TestCase):
    def test_seek_goes_to_the_player_or_the_tab(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        run(hub.command({"cmd": "seek", "key": "spotify", "value": 42}))
        run(hub.command({"cmd": "seek", "key": "chrome#11", "value": 7}))
        self.assertEqual(hub.media.commands, [("spotify", "seek", 42)])
        self.assertEqual(ext_ws.sent, [{"cmd": "seek", "tabId": 11, "value": 7}])

    def test_tab_next_and_prev_go_to_the_extension(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        run(hub.command({"cmd": "next", "key": "chrome#11"}))
        run(hub.command({"cmd": "prev", "key": "chrome#11"}))
        self.assertEqual([m["cmd"] for m in ext_ws.sent], ["next", "prev"])
        self.assertEqual(hub.media.commands, [])


class TestOutputDevice(unittest.TestCase):
    def test_set_output_goes_to_windows_audio(self):
        hub = make_hub()
        run(hub.command({"cmd": "set_output", "value": "{0.0.0.00000000}.{abc}"}))
        self.assertEqual(hub.audio.output, "{0.0.0.00000000}.{abc}")


class TestIcons(unittest.TestCase):
    def test_rows_get_app_icons_and_tab_favicons(self):
        hub = make_hub(ext_ws=FakeExtWs(), tabs=[
            {"id": 11, "favIconUrl": "https://www.youtube.com/favicon.ico"},
            {"id": 12, "favIconUrl": "chrome://theme/IDR_EXTENSIONS_FAVICON"}])
        hub.audio.exes = {"zalo": r"C:\Zalo\Zalo.exe"}
        hub.media.app_ids = {"spotify": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"}
        rows = [{"key": "zalo"}, {"key": "spotify"}, {"key": "nothing"},
                {"key": "chrome", "tabs": [{"key": "chrome#11"}], "recentTabs": [{"key": "chrome#12"}]}]
        hub._add_icons(rows)
        self.assertEqual([r["icon"] for r in rows[:3]], ["/icon/zalo", "/icon/spotify", None])
        self.assertEqual(rows[3]["tabs"][0]["icon"], "https://www.youtube.com/favicon.ico")
        self.assertIsNone(rows[3]["recentTabs"][0]["icon"])  # chrome:// can't load in the page

    def test_icon_source_prefers_the_exe(self):
        hub = make_hub()
        hub.audio.exes = {"spotify": r"C:\Spotify\Spotify.exe"}
        hub.media.app_ids = {"spotify": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"}
        self.assertEqual(hub.icon_source("spotify"), r"C:\Spotify\Spotify.exe")
        hub.audio.exes = {}
        self.assertEqual(hub.icon_source("spotify"),
                         r"shell:AppsFolder\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")


class TestExtensionUpdate(unittest.TestCase):
    def test_connected_browser_replaces_its_windows_player(self):
        hub = make_hub(ext_ws=FakeExtWs())
        self.assertEqual(hub.sharing(), {"chrome"})

    def test_update_now_asks_the_extension_to_reload(self):
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        hub.ext["chrome"]["accepts"] = ["reload"]
        run(hub.command({"cmd": "ext_update", "value": "chrome"}))
        self.assertEqual(ext_ws.sent, [{"cmd": "reload"}])

    def test_connected_extension_opens_the_page_in_its_own_window(self):
        from unittest import mock
        from audiohub import extension_info, hub as hub_mod
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        hub.ext["chrome"]["accepts"] = ["reload", "openExtensionsPage"]
        with mock.patch.object(extension_info, "open_extensions_page") as launch,                 mock.patch.object(hub_mod, "raise_app") as raise_app,                 mock.patch.object(hub_mod.asyncio, "sleep", new=mock.AsyncMock()):
            run(hub.command({"cmd": "ext_open_page", "value": "chrome"}))
        self.assertEqual(ext_ws.sent, [{"cmd": "openExtensionsPage"}])
        launch.assert_not_called()  # no command-line launch, so no stray window
        raise_app.assert_called_once_with("chrome")

    def test_too_old_to_reload_opens_the_extensions_page(self):
        from unittest import mock
        from audiohub import extension_info
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)  # no "accepts": from before 0.6
        from audiohub import hub as hub_mod
        with mock.patch.object(extension_info, "open_extensions_page") as page,                 mock.patch.object(hub_mod, "raise_app"),                 mock.patch.object(hub_mod.asyncio, "sleep", new=mock.AsyncMock()):
            run(hub.command({"cmd": "ext_update", "value": "chrome"}))
        page.assert_called_once_with("chrome")
        self.assertEqual(ext_ws.sent, [])


class TestClose(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from audiohub import hub as hub_mod
        self.ext_ws = FakeExtWs()
        self.hub = make_hub(ext_ws=self.ext_ws)
        self.patch = mock.patch.object(hub_mod, "close_app", return_value=1)
        self.close_app = self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_tab_closes_through_the_extension(self):
        run(self.hub.command({"cmd": "close", "key": "chrome#11"}))
        self.assertEqual(self.ext_ws.sent, [{"cmd": "close", "tabId": 11, "value": None}])
        self.close_app.assert_not_called()

    def test_app_is_asked_to_close(self):
        run(self.hub.command({"cmd": "close", "key": "vlc~2"}))
        self.close_app.assert_called_once_with("vlc")

    def test_never_the_whole_browser(self):
        run(self.hub.command({"cmd": "close", "key": "chrome"}))
        self.close_app.assert_not_called()

    def test_rows_say_whether_they_can_close(self):
        rows = [{"key": "vlc"}, {"key": "explorer"}, {"key": "chrome", "tabs": [{"key": "chrome#11"}]}]
        self.hub._add_icons(rows)
        self.assertEqual([rows[0]["canClose"], rows[1]["canClose"], rows[2]["canClose"]],
                         [True, False, False])
        self.assertTrue(rows[2]["tabs"][0]["canClose"])


class TestNeverClose(unittest.TestCase):
    def test_windows_itself_is_refused_without_looking_for_windows(self):
        from unittest import mock
        from audiohub import win_window
        with mock.patch.object(win_window, "app_windows") as find:
            self.assertEqual(win_window.close_app("explorer"), 0)
        find.assert_not_called()


class TestPinned(unittest.TestCase):
    def snap(self, hub, now=T0):
        from unittest import mock
        from audiohub import hub as hub_mod

        async def direct(fn, *args):
            return fn(*args)
        with mock.patch.object(hub, "_audio_call", side_effect=direct),                 mock.patch.object(hub_mod.extension_info, "statuses", return_value=[]):
            return run(hub.snapshot(now))["machines"][0]

    def test_pinned_rows_have_their_own_section_in_pin_order(self):
        hub = make_hub(pins=[{"key": "spotify", "name": "Spotify"}, {"key": "vlc", "name": "VLC"}])
        hub.tracker.update({"vlc": audio(peak=0.5)}, {"spotify": media("paused")},
                           {"vlc", "spotify"}, T0)
        m = self.snap(hub)
        self.assertEqual([r["key"] for r in m["pinned"]], ["spotify", "vlc"])
        self.assertEqual([r["live"] for r in m["pinned"]], ["recent", "playing"])
        self.assertTrue(all(r["pinned"] for r in m["pinned"]))
        self.assertEqual(m["playing"] + m["recent"], [])  # not listed twice

    def test_closed_pinned_app_is_not_listed_but_a_silent_one_is(self):
        hub = make_hub(pins=[{"key": "spotify", "name": "Spotify"}, {"key": "zalo", "name": "Zalo"}])
        hub._running = {"zalo"}  # Zalo running with no sound yet; Spotify closed
        pinned = self.snap(hub)["pinned"]
        self.assertEqual([r["key"] for r in pinned], ["zalo"])
        self.assertEqual(pinned[0]["status"], "running")
        hub._running = {"zalo", "spotify"}  # Spotify opened: back in its pinned place
        self.assertEqual([r["key"] for r in self.snap(hub)["pinned"]], ["spotify", "zalo"])

    def test_pin_and_unpin(self):
        hub = make_hub(pins=[])
        run(hub.command({"cmd": "pin", "key": "vlc", "value": "VLC"}))
        self.assertEqual(hub.settings.pins(), [{"key": "vlc", "name": "VLC"}])
        run(hub.command({"cmd": "pin", "key": "chrome#11"}))  # tabs aren't pinned
        run(hub.command({"cmd": "unpin", "key": "vlc"}))
        self.assertEqual(hub.settings.pins(), [])

    def test_windows_processes_stay_out_of_recent_and_cannot_close(self):
        hub = make_hub(pins=[])
        hub.audio.exes = {"taskhostw": r"C:\Windows\System32\taskhostw.exe",
                          "vlc": r"C:\VLC\vlc.exe"}
        hub.tracker.update({"taskhostw": audio(peak=0.5)}, {}, {"taskhostw"}, T0)
        m = self.snap(hub)
        self.assertEqual([r["key"] for r in m["playing"]], ["taskhostw"])  # visible while sounding
        self.assertFalse(m["playing"][0]["canClose"])
        hub.tracker.update({"taskhostw": audio(peak=0.0)}, {}, {"taskhostw"}, T0 + 5)
        self.assertEqual(self.snap(hub, T0 + 5)["recent"], [])
        self.assertEqual(hub.system_keys(), {"taskhostw"})


class TestTabFocus(unittest.TestCase):
    def test_browser_comes_forward_before_the_tab_switch(self):
        from unittest import mock
        from audiohub import hub as hub_mod
        ext_ws = FakeExtWs()
        hub = make_hub(ext_ws=ext_ws)
        order = []
        with mock.patch.object(hub_mod, "raise_app",
                               side_effect=lambda key: order.append(("raise", key, len(ext_ws.sent)))):
            run(hub.command({"cmd": "focus", "key": "chrome#11"}))
        # Raised while nothing had been sent yet; then the switch went to the extension.
        self.assertEqual(order, [("raise", "chrome", 0)])
        self.assertEqual(ext_ws.sent, [{"cmd": "focus", "tabId": 11, "value": None}])


class TestAudioWorker(unittest.TestCase):
    def test_cancelled_call_does_not_kill_the_worker(self):
        """A call cancelled while queued must not poison the audio thread."""
        from audiohub.hub import AUDIO_WORKER

        slow = AUDIO_WORKER.submit(time.sleep, 0.4)  # keep the worker busy

        async def cancel_while_queued():
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(AUDIO_WORKER, lambda: None)
            fut.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await fut

        asyncio.run(cancel_while_queued())
        slow.result(timeout=5)
        self.assertEqual(AUDIO_WORKER.submit(lambda: "alive").result(timeout=5), "alive")


if __name__ == "__main__":
    unittest.main()
