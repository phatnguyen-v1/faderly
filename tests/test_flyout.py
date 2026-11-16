"""Where the tray flyout goes, for each taskbar edge."""
import unittest

from audiohub.flyout import MARGIN, MAX_H, max_height, place

WORK_BOTTOM = (0, 0, 1920, 1032)  # 1080p screen, 48 px taskbar at the bottom


class TestPlace(unittest.TestCase):
    def test_bottom_taskbar_sits_above_it_near_the_icon(self):
        x, y = place(380, 500, work=WORK_BOTTOM, cursor=(1800, 1060), scale=1)
        self.assertEqual(x, 1920 - 380 - MARGIN)  # clamped to the right edge
        self.assertEqual(y, 1032 - 500 - MARGIN)

    def test_centered_on_the_icon_when_there_is_room(self):
        x, _ = place(380, 500, work=WORK_BOTTOM, cursor=(1000, 1060), scale=1)
        self.assertEqual(x, 1000 - 190)

    def test_top_taskbar(self):
        _, y = place(380, 500, work=(0, 48, 1920, 1080), cursor=(1800, 20), scale=1)
        self.assertEqual(y, 48 + MARGIN)

    def test_left_taskbar(self):
        x, _ = place(380, 500, work=(60, 0, 1920, 1080), cursor=(30, 1000), scale=1)
        self.assertEqual(x, 60 + MARGIN)

    def test_high_dpi_converts_to_logical_pixels(self):
        x, y = place(380, 500, work=(0, 0, 3840, 2064), cursor=(3700, 2100), scale=2)
        self.assertEqual((x, y), (1920 - 380 - MARGIN, 1032 - 500 - MARGIN))


class FakeWindow:
    def __init__(self):
        self.moves = []

    def resize(self, w, h):
        pass

    def move(self, x, y):
        self.moves.append((x, y))


class TestFitKeepsPlace(unittest.TestCase):
    def test_resize_while_open_does_not_follow_the_pointer(self):
        from unittest import mock
        from audiohub import flyout as fl

        f = fl.Flyout(open_full=lambda: None)
        f.window, f.visible, f.anchor = FakeWindow(), True, (1800, 1060)
        api = fl.FlyoutApi(f)
        with mock.patch.object(fl, "_work_area", return_value=WORK_BOTTOM),                 mock.patch.object(fl, "_scale", return_value=1),                 mock.patch.object(fl, "_cursor", return_value=(1500, 700)):  # pointer inside it
            api.fit(400)
            api.fit(520)
        xs = {x for x, _ in f.window.moves}
        self.assertEqual(xs, {1920 - 380 - MARGIN})  # same x as at the tray click
        self.assertEqual([y + h for (_, y), h in zip(f.window.moves, (400, 520))],
                         [1032 - MARGIN] * 2)  # bottom edge stays on the taskbar


class TestMaxHeight(unittest.TestCase):
    def test_fits_the_work_area(self):
        self.assertEqual(max_height(work=(0, 0, 1920, 800), scale=1), 800 - 2 * MARGIN)

    def test_capped_on_tall_screens(self):
        self.assertEqual(max_height(work=(0, 0, 1440, 2500), scale=1), MAX_H)

    def test_high_dpi(self):
        self.assertEqual(max_height(work=(0, 0, 2880, 1600), scale=2), 800 - 2 * MARGIN)


if __name__ == "__main__":
    unittest.main()
