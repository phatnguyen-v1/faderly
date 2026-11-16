"""One Faderly per port: a second launch finds the lock taken and signals the first."""
import threading
import unittest

from audiohub import single_instance as si

PORT = 48765  # a port no real copy uses, so the tests never touch a running app


class TestSingleInstance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.first = si.acquire(PORT)  # this test process plays the running copy

    def test_first_copy_gets_the_lock(self):
        self.assertTrue(self.first)

    def test_second_copy_is_refused(self):
        self.assertFalse(si.acquire(PORT))

    def test_other_port_is_independent(self):
        self.assertTrue(si.acquire(PORT + 1))

    def test_second_launch_reaches_the_first(self):
        shown = threading.Event()
        si.listen(PORT, shown.set)
        self.assertTrue(si.show_existing(PORT))
        self.assertTrue(shown.wait(2))

    def test_no_running_copy_means_no_answer(self):
        self.assertFalse(si.show_existing(PORT + 2, timeout=0.3))


if __name__ == "__main__":
    unittest.main()
