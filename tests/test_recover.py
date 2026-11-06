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

    def test_goods_alone_do_not_trigger_tribe(self):
        rich = hero(bag={"wood": 100})
        with mock.patch.object(tribe, "has_quota", return_value=False):
            self.assertFalse(tribe.recover_goes_to_tribe(925, rich))   # goods alone no longer go to tribe
        with mock.patch.object(tribe, "has_quota", return_value=True):
            self.assertTrue(tribe.recover_goes_to_tribe(925, rich))    # quota triggers tribe


class RecoverRouteTests(unittest.TestCase):
    def test_worthwhile_recovery_goes_straight_to_tribe_without_camp_rest(self):
        m = hero(bag={"wood": 21})
        with mock.patch.object(tribe, "has_quota", return_value=True), \
                mock.patch.object(tribe.api, "me", return_value=(m, None)), \
                mock.patch.object(tribe, "path_safe", return_value=True), \
                mock.patch.object(tribe, "visit", return_value=True) as visit, \
                mock.patch.object(queue, "_go_home", side_effect=AssertionError("must not go home first")):
            self.assertTrue(tribe.recover(925))
        self.assertTrue(visit.call_args.kwargs.get("heal"))

    def test_no_goods_no_quota_rests_in_camp_until_healed(self):
        low = hero(ring="camp", bag={"wood": 20})
        healed = hero(ring="camp", hp=90, bag={"wood": 20})
        with mock.patch.object(tribe, "has_quota", return_value=False), \
                mock.patch.object(tribe.api, "me", side_effect=[(low, None), (healed, None)]), \
                mock.patch.object(tribe, "visit", side_effect=AssertionError("must not visit the tribe")) as visit, \
                mock.patch.object(tribe.state, "note", return_value=None), \
                mock.patch.object(tribe.time, "sleep", return_value=None):
            self.assertTrue(tribe.recover(925))


class RoadShutTests(unittest.TestCase):
    """The owner's report (2026-11-05): the way to the tribe stayed shut while the heroes were already whole, and the
    recovery order went on waiting at the camp, freezing every squad."""

    def test_healed_at_the_camp_ends_the_order_when_the_road_is_shut(self):
        m = hero(ring="camp", hp=95, max_hp=100)
        with mock.patch.object(tribe, "has_quota", return_value=True), \
                mock.patch.object(tribe.api, "me", return_value=(m, None)), \
                mock.patch.object(tribe, "path_safe", return_value=False), \
                mock.patch.object(tribe, "visit", side_effect=AssertionError("no trip with the road shut")), \
                mock.patch.object(queue, "_go_home", side_effect=AssertionError("already at the camp")):
            self.assertTrue(tribe.recover(925))

    def test_hurt_at_the_camp_waits_and_ends_once_healed(self):
        hurt = hero(ring="camp", hp=60, max_hp=100)
        whole = hero(ring="camp", hp=95, max_hp=100)
        with mock.patch.object(tribe, "has_quota", return_value=True), \
                mock.patch.object(tribe.api, "me", side_effect=[(hurt, None), (whole, None)]), \
                mock.patch.object(tribe, "path_safe", return_value=False), \
                mock.patch.object(tribe, "visit", side_effect=AssertionError("no trip with the road shut")), \
                mock.patch.object(tribe.care, "eat", return_value=False), \
                mock.patch.object(tribe.state, "note", return_value=None), \
                mock.patch.object(tribe.time, "sleep", return_value=None):
            self.assertTrue(tribe.recover(925))

    def test_the_queue_lets_a_healed_camp_hero_finish_the_order(self):
        whole = hero(ring="camp", hp=95, max_hp=100)
        with mock.patch.object(queue.api, "me", return_value=(whole, None)), \
                mock.patch.object(tribe, "path_safe", return_value=False):
            self.assertTrue(queue.can_run(925, {"action": "recover", "arg": "resume-hunt", "prio": 9}))

    def test_the_queue_holds_a_hurt_camp_hero_while_the_road_is_shut(self):
        hurt = hero(ring="camp", hp=60, max_hp=100)
        with mock.patch.object(queue.api, "me", return_value=(hurt, None)), \
                mock.patch.object(tribe, "path_safe", return_value=False), \
                mock.patch.object(tribe, "recover_goes_to_tribe", return_value=True):
            self.assertFalse(queue.can_run(925, {"action": "recover", "arg": "resume-hunt", "prio": 9}))


class WaypointTravelTests(unittest.TestCase):
    # (the `direct` shortcut of the walk was dropped in the 10:46 refactor: a walk from the camp still goes by `travel`,
    # one from the field goes home first)
    def test_walk_from_the_camp_travels_to_a_waypoint(self):
        camp = hero(x=256, y=256, ring="camp")
        there = hero(x=nav.TRIBE_SPOT[0], y=nav.TRIBE_SPOT[1], ring="forest")
        acts = []
        with mock.patch.object(nav.api, "me", side_effect=[(camp, None), (there, None)]), \
                mock.patch.object(nav.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(nav.api, "wait_busy", return_value=None), \
                mock.patch.object(nav.danger, "observe", return_value=None), \
                mock.patch.object(nav.care, "eat", return_value=False), \
                mock.patch.object(nav.time, "sleep", return_value=None):
            nav.walk(925, nav.TRIBE_SPOT, 2)
        self.assertEqual(acts[0].get("action"), "travel")
        self.assertEqual(acts[0].get("to"), "forest")


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


    def test_recovery_heals_with_items_in_the_field(self):
        m = hero(hp=30, max_hp=100, ap=10, ring="forest", bag={"roast": 2})
        calls = []
        def fake_heal(u, mm, **kw):
            calls.append("heal")
            mm["hp"] = 95
            return 2
        with mock.patch.object(tribe.api, "me", return_value=(m, None)), \
                mock.patch.object(tribe.care, "heal_with_items", side_effect=fake_heal), \
                mock.patch.object(tribe.care, "eat", return_value=False), \
                mock.patch.object(tribe, "recover_goes_to_tribe", return_value=False), \
                mock.patch.object(queue, "_go_home", side_effect=AssertionError("should not go home if healed")):
            self.assertTrue(tribe.recover(925))
        self.assertIn("heal", calls)

    def test_go_home_heals_when_hurt_in_field(self):
        m = hero(hp=30, max_hp=100, ap=10, ring="forest", bag={"roast": 2})
        calls = []
        def fake_heal(u, mm, **kw):
            calls.append("heal")
            mm["hp"] = 60
            return 1
        with mock.patch.object(queue.api, "me", side_effect=[(m, None), (m, None), (dict(m, ring="camp"), None)]), \
                mock.patch.object(queue.care, "heal_with_items", side_effect=fake_heal), \
                mock.patch.object(queue.care, "eat", return_value=False), \
                mock.patch.object(queue.api, "act", return_value=(200, {})), \
                mock.patch.object(queue.api, "wait_busy", return_value=None), \
                mock.patch.object(queue.time, "sleep", return_value=None):
            self.assertTrue(queue._go_home(925, "test"))
        self.assertIn("heal", calls)


if __name__ == "__main__":
    unittest.main()
