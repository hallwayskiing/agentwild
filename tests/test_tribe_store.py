"""Focused checks for a tribe visit's store routine:
Strict whitelist:
- 全存: wood, stone, ore, copper_ore, iron_ore, hide, bone, herb, wheat_seed
- 多存少补: bread (5), meat (5), dust (30)
- 留在身上不存: roast, bandage, runes, gems, gear, currencies
"""
import unittest
from unittest import mock

from wild import tribe, rules


class DepositAllTests(unittest.TestCase):
    def test_strict_whitelist_deposit_and_balance(self):
        # Arrival with whitelist materials, surplus bread and dust, deficit meat, and forbidden items (roast, bandage, gem)
        arrival = {
            "bag": {
                "wood": 10,
                "hide": 3,
                "bread": 8,       # 8 - 5 = 3 surplus
                "meat": 2,        # 5 - 2 = 3 deficit
                "dust": 45,       # 45 - 30 = 15 surplus
                "roast": 9,       # must NEVER be deposited
                "bandage": 7,     # must NEVER be deposited
                "gem_ruby_1": 1,  # must NEVER be deposited
            },
            "coins": 30
        }
        # After balancing bread and dust deposit, and meat withdraw:
        mid_bag = {
            "bag": {
                "wood": 10,
                "hide": 3,
                "bread": 5,
                "meat": 5,
                "dust": 30,
                "roast": 9,
                "bandage": 7,
                "gem_ruby_1": 1,
            },
            "coins": 30
        }
        acts = []
        store_info = {
            "store": {"meat": 10, "bread": 0, "dust": 0},
            "store_slots": {"slots": 40, "slots_used": 5}
        }
        with mock.patch.object(tribe.api, "me", side_effect=[
            (arrival, None),  # in balance_supplies
            (arrival, None),  # after dust deposit
            (arrival, None),  # after bread deposit
            (mid_bag, None),  # in deposit_all after balance_supplies
            (mid_bag, None),  # after materials deposit
        ]), \
                mock.patch.object(tribe.api, "wait_busy", return_value=None), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value=store_info):
            tribe.deposit_all(925)

        # Verify all deposit acts
        deposit_items = {}
        withdraw_items = {}
        for a in acts:
            if a["action"] == "deposit" and "items" in a:
                deposit_items.update(a["items"])
            elif a["action"] == "withdraw" and "items" in a:
                withdraw_items.update(a["items"])

        # Check: materials 全存
        self.assertEqual(deposit_items.get("wood"), 10)
        self.assertEqual(deposit_items.get("hide"), 3)

        # Check: 多存少补
        self.assertEqual(deposit_items.get("bread"), 3)   # surplus deposited
        self.assertEqual(deposit_items.get("dust"), 15)   # surplus deposited
        self.assertEqual(withdraw_items.get("meat"), 3)   # deficit withdrawn

        # Check: forbidden items never deposited
        self.assertNotIn("roast", deposit_items)
        self.assertNotIn("bandage", deposit_items)
        self.assertNotIn("gem_ruby_1", deposit_items)


class CoinBalanceTests(unittest.TestCase):
    def run_deposit(self, coins, treasury):
        m = {"bag": {}, "coins": coins}
        acts = []
        with mock.patch.object(tribe.api, "me", return_value=(m, None)), \
                mock.patch.object(tribe.api, "wait_busy", return_value=None), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value={"store": {}, "coins": treasury}):
            tribe.deposit_all(925)
        return acts

    def test_coins_over_30_go_to_the_treasury(self):
        self.assertEqual(self.run_deposit(45, 100), [{"action": "deposit", "coins": 15}])

    def test_coins_under_30_are_left_alone(self):
        self.assertEqual(self.run_deposit(10, 100), [])

    def test_exactly_30_coins_stay_untouched(self):
        self.assertEqual(self.run_deposit(30, 100), [])


if __name__ == "__main__":
    unittest.main()
