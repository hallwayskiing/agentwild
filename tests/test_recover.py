"""Focused checks for recovery: go to the tribe when depositable goods exceed 20 or training quota remains
(either one is enough), and head there directly via the forest waypoint instead of resting in camp first."""
import unittest
from unittest import mock

from wild import care, hunt, nav, queue, tribe


def hero(**kw):
    m = {"x": 200, "y": 200, "ring": "forest", "hp": 30, "max_hp": 100, "ap": 100, "level": 10, "bag": {}}
    m.update(kw)
    return m


class GoodsAndQuotaTests(unittest.TestCase):
    def test_depositable_goods_counts_materials_only(self):
        m = hero(bag={"wood": 10, "stone": 5, "hide": 3, "ore": 2, "iron_ore": 1, "bread": 50, "coins": 9})
        self.assertEqual(tribe.depositable_goods(m), 21)

    def test_either_condition_is_enough(self):
        rich = hero(bag={"wood": 21})
        poor = hero(bag={"wood": 20})
        with mock.patch.object(tribe, "has_quota", return_value=False):
            self.assertTrue(tribe.recover_goes_to_tribe(925, rich))    # goods over 20 alone
            self.assertFalse(tribe.recover_goes_to_tribe(925, poor))   # exactly 20 is not over 20
        with mock.patch.object(tribe, "has_quota", return_value=True):
            self.assertTrue(tribe.recover_goes_to_tribe(925, poor))    # quota alone


class RecoverRouteTests(unittest.TestCase):
    def test_worthwhile_recovery_goes_straight_to_tribe_without_camp_rest(self):
        m = hero(bag={"wood": 21})
        with mock.patch.object(tribe, "has_quota", return_value=False), \
                mock.patch.object(tribe.api, "me", return_value=(m, None)), \
                mock.patch.object(tribe, "visit", return_value=True) as visit, \
                mock.patch.object(queue, "_go_home", side_effect=AssertionError("must not go home first")):
            self.assertTrue(tribe.recover(925))
        self.assertTrue(visit.call_args.kwargs.get("direct"))

    def test_no_goods_no_quota_rests_in_camp_until_healed(self):
        low = hero(ring="camp", bag={"wood": 20})
        healed = hero(ring="camp", hp=90, bag={"wood": 20})
        with mock.patch.object(tribe, "has_quota", return_value=False), \
                mock.patch.object(tribe.api, "me", side_effect=[(low, None), (healed, None)]), \
                mock.patch.object(tribe, "visit", side_effect=AssertionError("must not visit the tribe")) as visit, \
                mock.patch.object(tribe.state, "note", return_value=None), \
                mock.patch.object(tribe.time, "sleep", return_value=None):
            self.assertTrue(tribe.recover(925))


class DirectTravelTests(unittest.TestCase):
    def test_walk_direct_travels_to_forest_waypoint_before_any_home(self):
        far = hero()
        there = hero(x=nav.TRIBE_SPOT[0], y=nav.TRIBE_SPOT[1], ring="forest")
        acts = []
        with mock.patch.object(nav.api, "me", side_effect=[(far, None), (there, None)]), \
                mock.patch.object(nav.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(nav.api, "wait_busy", return_value=None), \
                mock.patch.object(nav.danger, "observe", return_value=None), \
                mock.patch.object(nav.care, "eat", return_value=False), \
                mock.patch.object(nav.time, "sleep", return_value=None):
            nav.walk(925, nav.TRIBE_SPOT, 2, direct=True)
        self.assertEqual(acts[0].get("action"), "travel")
        self.assertEqual(acts[0].get("to"), "forest")
        self.assertNotIn("home", [a.get("action") for a in acts])


class FoodBeforeHomeTests(unittest.TestCase):
    def squad(self):
        sq = hunt.Squad.__new__(hunt.Squad)
        sq.bread = True
        sq.heal_items = care.HEAL_ITEMS
        return sq

    def test_food_healing_that_lifts_health_over_threshold_skips_recovery(self):
        m = hero(hp=30, bag={"bandage": 2})
        def heal(u, mm, items=None):
            mm["hp"] = 85
            return 2
        with mock.patch.object(hunt.care, "eat", return_value=False), \
                mock.patch.object(hunt.care, "heal_with_items", side_effect=heal), \
                mock.patch.object(hunt.queue, "add") as add:
            self.squad().food_heal(925, m)
            self.assertFalse(self.squad().queue_recover_if_hurt(925, m))
        add.assert_not_called()

    def test_still_hurt_after_food_queues_recovery(self):
        m = hero(hp=30, bag={})
        with mock.patch.object(hunt.care, "eat", return_value=False), \
                mock.patch.object(hunt.care, "heal_with_items", return_value=0), \
                mock.patch.object(hunt.queue, "add", return_value=True) as add:
            self.squad().food_heal(925, m)
            self.assertTrue(self.squad().queue_recover_if_hurt(925, m))
        add.assert_called_once()


class BandageRestockOffTests(unittest.TestCase):
    def test_restock_bandage_does_nothing_when_off(self):
        m = hero(ring="camp", bag={"bandage": 0})
        with mock.patch.object(care.api, "call", side_effect=AssertionError("must not ask the quartermaster")), \
                mock.patch.object(care.api, "act", side_effect=AssertionError("must not requisition")):
            self.assertFalse(care.restock_bandage(928, m))
            self.assertFalse(care._bandages_short({"bag": {"bandage": 0}}))


if __name__ == "__main__":
    unittest.main()
