"""The owner's repair rule (2026-11-05): in the field a piece is repaired only once it is broken, and in coins; at the camp
or on the tribe's land every worn piece under rules.DUST_BELOW (20%) is repaired with dust; a tribe visit keeps the hero's
dust at rules.DUST_KEEP (about three repair rounds)."""
import unittest
from unittest import mock

from wild import care, nav, rules, tribe


def hero(**kw):
    m = {"x": 340, "y": 265, "ring": "camp", "bag": {"dust": 10}, "coins": 40, "worn": {}}
    m.update(kw)
    return m


def piece(gid, base, dur, top):
    return {"id": gid, "base": base, "durability": dur, "durability_max": top}


class DustRepairTests(unittest.TestCase):
    def check(self, m, at_tribe=False, cost=4):
        acts = []
        with mock.patch.object(nav, "at_tribe", return_value=at_tribe), \
                mock.patch.object(care.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {"cost": cost})):
            care.auto_repair(925, m)
        return acts

    def test_camp_repairs_a_piece_under_20_percent_with_dust(self):
        m = hero(worn={"head": piece(1, "helm", 15, 100)})
        self.assertEqual(self.check(m), [{"action": "repair", "item": 1, "with": "dust"}])

    def test_camp_leaves_a_piece_over_20_percent_alone(self):
        m = hero(worn={"head": piece(1, "helm", 25, 100)})
        self.assertEqual(self.check(m), [])

    def test_tribe_land_counts_as_well(self):
        m = hero(ring="forest", worn={"chest": piece(2, "cuirass", 10, 100)})
        self.assertEqual(self.check(m, at_tribe=True), [{"action": "repair", "item": 2, "with": "dust"}])

    def test_field_repairs_a_broken_piece_in_coins_only(self):
        m = hero(ring="forest", worn={"head": piece(1, "helm", 0, 100)})
        self.assertEqual(self.check(m), [{"action": "repair", "item": 1}])

    def test_field_does_not_touch_a_piece_that_still_holds(self):
        m = hero(ring="forest", worn={"head": piece(1, "helm", 15, 100)})
        self.assertEqual(self.check(m), [])

    def test_no_dust_no_repair(self):
        m = hero(bag={}, worn={"head": piece(1, "helm", 15, 100)})
        self.assertEqual(self.check(m), [])


class DustRestockTests(unittest.TestCase):
    def run_restock(self, have, store_dust, unit=925):
        m = {"bag": {"dust": have} if have else {}, "coins": 0}
        acts = []
        with mock.patch.object(tribe.api, "me", return_value=(m, None)), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value={"store": {"dust": store_dust}}):
            tribe.restock_dust(unit)
        return acts

    def test_tops_up_to_the_keep(self):
        self.assertEqual(self.run_restock(4, 100), [{"action": "withdraw", "items": {"dust": rules.DUST_KEEP - 4}}])

    def test_takes_only_what_the_store_has(self):
        self.assertEqual(self.run_restock(4, 5), [{"action": "withdraw", "items": {"dust": 5}}])

    def test_nothing_when_the_store_is_empty(self):
        self.assertEqual(self.run_restock(0, 0), [])

    def test_nothing_when_the_hero_already_has_enough(self):
        self.assertEqual(self.run_restock(rules.DUST_KEEP + 5, 100), [])


class DustInTheStoreTests(unittest.TestCase):
    def test_the_working_stock_stays_in_the_bag(self):
        out = tribe.currencies(933, {"bag": {"dust": 50, "gem_ruby_1": 2}})
        self.assertEqual(out, {"dust": 50 - rules.DUST_KEEP, "gem_ruby_1": 2})

    def test_a_small_stock_is_not_deposited(self):
        self.assertEqual(tribe.currencies(933, {"bag": {"dust": 12}}), {})

    def test_the_main_character_puts_its_surplus_in(self):
        acts = []
        slots = {"slots": 40, "slots_used": 30}   # 10 free, but one visit shares at most rules.DUST_SHARE_SLOTS of them
        with mock.patch.object(tribe.api, "me", return_value=({"bag": {"dust": 407}}, None)), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value={"store": {}, "store_slots": slots}):
            tribe.share_dust()
        self.assertEqual(acts, [{"action": "deposit", "items": {"dust": rules.DUST_SHARE_SLOTS * 20}}])

    def test_the_share_stops_at_the_stores_free_slots(self):
        acts = []
        slots = {"slots": 40, "slots_used": 39}   # one slot free
        with mock.patch.object(tribe.api, "me", return_value=({"bag": {"dust": 407}}, None)), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value={"store": {}, "store_slots": slots}):
            tribe.share_dust()
        self.assertEqual(acts, [{"action": "deposit", "items": {"dust": 20}}])


if __name__ == "__main__":
    unittest.main()

