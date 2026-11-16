"""Tray -> Quit must really close the windows (close-to-tray handlers stand aside)."""
import unittest
from unittest import mock

from audiohub import __main__ as app


class FakeWindow:
    def __init__(self):
        self.hidden = False

    def hide(self):
        self.hidden = True


class TestCloseHandlers(unittest.TestCase):
    def tearDown(self):
        app.QUITTING.clear()

    def test_close_hides_to_tray_normally(self):
        w = FakeWindow()
        self.assertFalse(app._hide_to_tray(w))  # False cancels the close
        self.assertTrue(w.hidden)

    def test_close_goes_through_when_quitting(self):
        app.QUITTING.set()
        w = FakeWindow()
        self.assertTrue(app._hide_to_tray(w))
        self.assertFalse(w.hidden)

    def test_quit_destroys_every_window_and_arms_the_exit(self):
        destroyed = []

        class W:
            def __init__(self, name):
                self.name = name

            def destroy(self):
                # pywebview runs the closing handlers inside destroy(): they must allow it.
                self.allowed = app._hide_to_tray(FakeWindow())
                destroyed.append(self)

        fake_webview = mock.Mock(windows=[W("main"), W("flyout")])
        with mock.patch.dict("sys.modules", {"webview": fake_webview}), \
                mock.patch.object(app.threading, "Timer") as timer:
            app._quit()
        self.assertEqual([w.name for w in destroyed], ["main", "flyout"])
        self.assertTrue(all(w.allowed for w in destroyed))
        timer.assert_called_once()
        self.assertEqual(timer.call_args[0][0], app.QUIT_GRACE)


if __name__ == "__main__":
    unittest.main()


class TestRenameMigration(unittest.TestCase):
    """Audio Hub was renamed Faderly: settings and autostart carry over."""

    def test_settings_move_from_the_old_folder(self):
        import os
        import tempfile
        from pathlib import Path
        from audiohub import settings
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / "AudioHub" / "settings.json"
            old.parent.mkdir()
            old.write_text('{"pinned": [{"key": "vlc", "name": "VLC"}]}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": d}):
                path = settings.default_path()
                self.assertEqual(path, Path(d) / "Faderly" / "settings.json")
                self.assertEqual(settings.Settings(path).pins(), [{"key": "vlc", "name": "VLC"}])

    def test_old_startup_shortcut_is_replaced(self):
        from audiohub import tray
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            new = Path(d) / "Faderly.lnk"
            (Path(d) / "Audio Hub.lnk").write_text("old")
            with mock.patch.object(tray, "startup_link", return_value=new),                     mock.patch.object(tray, "set_autostart") as make:
                tray.migrate_autostart()
            make.assert_called_once()
            self.assertFalse((Path(d) / "Audio Hub.lnk").exists())

    def test_nothing_to_migrate(self):
        from audiohub import tray
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d,                 mock.patch.object(tray, "startup_link", return_value=Path(d) / "Faderly.lnk"),                 mock.patch.object(tray, "set_autostart") as make:
            tray.migrate_autostart()
        make.assert_not_called()
