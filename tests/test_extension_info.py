"""What the app suggests for each browser's extension."""
import unittest

import json
import tempfile
from pathlib import Path

from audiohub.extension_info import last_used_profile, open_command, shipped_version, statuses

LATEST = "0.7.0"


def state(open_browsers, ext):
    return {s["key"]: s["state"] for s in statuses(open_browsers, ext, LATEST)}


class TestStatuses(unittest.TestCase):
    def test_open_browser_without_extension_is_missing(self):
        self.assertEqual(state({"chrome"}, {}), {"chrome": "missing"})

    def test_connected_and_current_is_ok(self):
        self.assertEqual(state({"chrome"}, {"chrome": {"version": "0.7.0"}}),
                         {"chrome": "ok"})

    def test_old_extension_with_its_switch_off_is_just_outdated(self):
        # 0.6 had a "Send tabs" switch; updating removes it, so suggest the update.
        self.assertEqual(state({"chrome"}, {"chrome": {"sharing": False, "version": "0.6.0"}}),
                         {"chrome": "outdated"})

    def test_older_version_is_outdated(self):
        self.assertEqual(state({"chrome"}, {"chrome": {"sharing": True, "version": "0.5.1"}}),
                         {"chrome": "outdated"})

    def test_versions_compare_as_numbers(self):
        self.assertEqual(state(set(), {"chrome": {"sharing": True, "version": "0.10.0"}}),
                         {"chrome": "ok"})

    def test_extension_from_before_versions_counts_as_outdated(self):
        self.assertEqual(state(set(), {"chrome": {"sharing": True}}), {"chrome": "outdated"})

    def test_closed_browser_is_not_listed(self):
        self.assertEqual(state(set(), {}), {})

    def test_names(self):
        rows = statuses({"msedge", "chrome"}, {}, LATEST)
        self.assertEqual([(r["key"], r["name"]) for r in rows], [("chrome", "Chrome"), ("msedge", "Edge")])


class TestOpenInExistingWindow(unittest.TestCase):
    def test_last_used_profile_from_local_state(self):
        with tempfile.TemporaryDirectory() as d:
            ud = Path(d) / "Google" / "Chrome" / "User Data"
            ud.mkdir(parents=True)
            (ud / "Local State").write_text(json.dumps({"profile": {"last_used": "Profile 6"}}))
            self.assertEqual(last_used_profile("chrome", d), "Profile 6")
            self.assertIsNone(last_used_profile("msedge", d))  # no Local State: no guess

    def test_command_names_the_profile(self):
        self.assertEqual(open_command("chrome.exe", "chrome", "Profile 6"),
                         ["chrome.exe", "--profile-directory=Profile 6", "chrome://extensions"])
        self.assertEqual(open_command("msedge.exe", "msedge", None), ["msedge.exe", "edge://extensions"])


class TestShippedVersion(unittest.TestCase):
    def test_reads_the_manifest(self):
        self.assertRegex(shipped_version(), r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
