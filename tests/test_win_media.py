"""Tests for mapping Windows media app ids to app keys."""
import unittest

from audiohub.win_media import media_key


class TestMediaKey(unittest.TestCase):
    def test_store_app(self):
        self.assertEqual(media_key("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"), "spotify")

    def test_exe_path(self):
        self.assertEqual(media_key(r"C:\Program Files\VideoLAN\VLC\vlc.exe"), "vlc")

    def test_browser(self):
        self.assertEqual(media_key("Chrome"), "chrome")
        self.assertEqual(media_key("MSEdge"), "msedge")

    def test_browser_profile_is_the_browser(self):
        # Without this the extension can't hide the profile's own player: duplicate row.
        self.assertEqual(media_key("Chrome.UserData.Profile6"), "chrome")
        self.assertEqual(media_key("MSEdge.UserData.Profile1"), "msedge")

    def test_dotted_non_browser_kept(self):
        self.assertEqual(media_key("Foo.Bar"), "foo.bar")


if __name__ == "__main__":
    unittest.main()
