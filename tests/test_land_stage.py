"""Focused checks for the corrupted-land wait: like recovery, the main character and the lord use the
wait to go to the tribe when that is worthwhile (reusing the tribe trip), and rest in the camp otherwise."""
import time
import unittest
from unittest import mock

from wild import landwatch, nav, queue, tribe


def hero(**kw):
    m = {"x": 200, "y": 200, "ring": "forest", "hp": 100, "max_hp": 100, "ap": 100, "level": 15, "bag": {}}
    m.update(kw)
    return m


class StageRestTests(unittest.TestCase):
    def run_stage(self, reads, dues, go_result=True):
        with mock.patch.dict(landwatch.STATE, {"stage": True, "on": False}), \
                mock.patch.object(landwatch, "due_in", side_effect=dues), \
                mock.patch.object(queue, "waiting", return_value=False), \
                mock.patch.object(tribe.api, "me", side_effect=reads), \
                mock.patch.object(tribe, "go", return_value=go_result) as go, \
                mock.patch.object(tribe, "deposit_all") as deposit, \
                mock.patch.object(tribe.state, "note", return_value=None), \
                mock.patch.object(tribe.time, "sleep", return_value=None), \
                mock.patch.object(queue, "_go_home", side_effect=AssertionError("already safe or at the tribe")):
            done = tribe.stage_rest(928)
        return done, go, deposit

    def test_goods_make_the_wait_a_tribe_trip(self):
        # The user removed goods from tribe triggers: hurt heroes recover at tribe
        field = hero(hp=50, max_hp=100)
        at_tribe = hero(x=nav.TOTEM[0], y=nav.TOTEM[1], ring="forest", hp=50, max_hp=100)
        done, go, deposit = self.run_stage([(field, None), (at_tribe, None)], [100, 100, 0])
        self.assertTrue(done)
        go.assert_called_once()
        deposit.assert_called_once_with(928)

    def test_nothing_worthwhile_rests_in_the_camp(self):
        camp = hero(ring="camp", x=256, y=256)
        done, go, deposit = self.run_stage([(camp, None)], [100, 0])
        self.assertTrue(done)
        go.assert_not_called()
        deposit.assert_not_called()


class StageQueueingTests(unittest.TestCase):
    def test_stage_start_queues_the_tribe_wait_for_hunting_units(self):
        saved_next, saved_stage = landwatch._next[0], landwatch.STATE["stage"]
        try:
            landwatch._next[0] = time.time() + 600
            landwatch.STATE["stage"] = False
            with mock.patch.object(landwatch, "_next_is_ours", return_value=True), \
                    mock.patch.object(queue, "hunting", return_value=True), \
                    mock.patch.object(queue, "add") as add:
                landwatch._update_stage()
            self.assertTrue(landwatch.STATE["stage"])
            self.assertEqual([c.args[1] for c in add.call_args_list], ["land-stage", "land-stage"])
        finally:
            landwatch._next[0], landwatch.STATE["stage"] = saved_next, saved_stage


if __name__ == "__main__":
    unittest.main()
