"""Focused checks for the Abyss hand-off: a live run is owned by wild.abyss, never held by the queue."""
import unittest
from unittest import mock

from wild import abyss, queue


LIVE = {"you": {"run": {"status": "live", "run": 197, "floor": 1, "stage": 0, "seconds_left": 900}}}


class LiveRunTests(unittest.TestCase):
    def test_live_run_reads_you_run(self):
        with mock.patch.object(abyss, "info", return_value=LIVE):
            run = abyss.live_run()
        self.assertEqual(run["run"], 197)
        self.assertEqual(run["seconds_left"], 900)

    def test_live_run_none_when_not_live(self):
        with mock.patch.object(abyss, "info", return_value={"you": {"run": {"status": "done"}}}):
            self.assertIsNone(abyss.live_run())
        with mock.patch.object(abyss, "info", return_value={}):
            self.assertIsNone(abyss.live_run())

    def test_resume_fights_live_run_with_seconds_left(self):
        fought, left, detached = [], [], []
        with mock.patch.object(abyss, "info", return_value=LIVE), \
                mock.patch.object(abyss, "fight_loop", side_effect=lambda s=None: fought.append(s)), \
                mock.patch.object(abyss.api, "act", side_effect=lambda u, p: (left.append(p) or (200, {}))):
            abyss.resume()
        self.assertEqual(fought, [900])
        self.assertIn({"action": "abyss", "do": "leave"}, left)
        self.assertFalse(abyss.PAUSE.is_set())


class QueueHandOffTests(unittest.TestCase):
    def test_abyss_enter_returns_at_once_when_run_live(self):
        # abyss.watch owns a live run: the queue task must end, not re-enter and not hold the main character.
        enter = queue._runner(None, "abyss-enter", None)
        with mock.patch.object(abyss, "live_run", return_value=LIVE["you"]["run"]), \
                mock.patch.object(queue.api, "me", side_effect=AssertionError("must not read/enter again")):
            self.assertTrue(enter())

    def test_abyss_enter_returns_after_successful_entry(self):
        # After a fresh entry the queue hands over to abyss.watch instead of holding the main character still.
        enter = queue._runner(None, "abyss-enter", None)
        main = {"ring": "camp", "ap": 100, "coins": 100, "hero": {"squad": [1, 2, 3, 4]}}
        with mock.patch.object(abyss, "live_run", return_value=None), \
                mock.patch.object(queue.api, "me", return_value=(main, None)), \
                mock.patch.object(queue.api, "wait_busy", return_value=None), \
                mock.patch.object(queue.api, "call", return_value=(200, {"success": True})) as call:
            self.assertTrue(enter())
        self.assertEqual(call.call_args[0][2], {"action": "abyss", "do": "enter", "wait": False})


if __name__ == "__main__":
    unittest.main()
