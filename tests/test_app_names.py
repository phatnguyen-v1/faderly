"""App names come from Windows, with a tidy fallback; no per-app list."""
import unittest
from unittest import mock

from audiohub import app_names
from audiohub.app_names import for_exe, for_player, tidy


class TestTidy(unittest.TestCase):
    def test_reverse_domain_id_keeps_the_last_piece(self):
        self.assertEqual(tidy("com.riotgames.riotclient"), "Riotclient")

    def test_separators_become_spaces(self):
        self.assertEqual(tidy("my_cool-app"), "My cool app")

    def test_plain_key_is_capitalised(self):
        self.assertEqual(tidy("zalo"), "Zalo")


class TestForPlayer(unittest.TestCase):
    def test_short_name_wins(self):
        self.assertEqual(for_player("chrome", "Chrome.UserData.Profile6", "Chrome"), "Chrome")

    def test_windows_name_for_the_id(self):
        with mock.patch.object(app_names, "from_app_id", return_value="Riot Client"):
            self.assertEqual(for_player("com.riotgames.riotclient", "com.riotgames.riotclient"),
                             "Riot Client")

    def test_falls_back_to_a_matching_running_exe(self):
        with mock.patch.object(app_names, "from_app_id", return_value=None), \
                mock.patch.object(app_names, "from_running_process",
                                  side_effect=lambda t: "Riot Client" if t == "riotclient" else None):
            self.assertEqual(for_player("x", "com.riotgames.riotclient"), "Riot Client")

    def test_never_shows_the_raw_id(self):
        with mock.patch.object(app_names, "from_app_id", return_value=None), \
                mock.patch.object(app_names, "from_running_process", return_value=None):
            self.assertEqual(for_player("x", "com.example.someplayer"), "Someplayer")


class TestForExe(unittest.TestCase):
    def test_exe_description(self):
        with mock.patch.object(app_names, "from_exe", return_value="Riot Client"):
            self.assertEqual(for_exe("riotclientservices", r"C:\Riot\RiotClientServices.exe"),
                             "Riot Client")

    def test_no_description(self):
        with mock.patch.object(app_names, "from_exe", return_value=None):
            self.assertEqual(for_exe("zalocall", None), "Zalocall")


class TestRealLookups(unittest.TestCase):
    """Against this Windows install: ids and exes every Windows has."""

    def test_known_app_id(self):
        self.assertTrue(app_names.from_app_id("MSEdge"))

    def test_unknown_app_id(self):
        self.assertIsNone(app_names.from_app_id("not.a.real.app"))

    def test_exe_description_of_windows_explorer(self):
        self.assertTrue(app_names.from_exe(r"C:\Windows\explorer.exe"))


if __name__ == "__main__":
    unittest.main()
