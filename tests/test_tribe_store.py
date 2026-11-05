"""Focused checks for a tribe visit's store routine: materials go in first, bread and meat are topped
up to 5 each, any food over 5 goes in afterwards, and bandages are never deposited."""
import unittest
from unittest import mock

from wild import tribe


class DepositAllTests(unittest.TestCase):
    def test_materials_then_top_up_then_surplus_and_never_bandages(self):
        arrival = {"bag": {"wood": 10, "hide": 3, "bread": 8, "meat": 2, "roast": 9, "bandage": 7}, "coins": 30}
        after_top_up = {"bag": {"bread": 8, "meat": 5, "roast": 9, "bandage": 7}, "coins": 30}
        acts = []
        with mock.patch.object(tribe.api, "me", side_effect=[(arrival, None), (arrival, None), (after_top_up, None)]), \
                mock.patch.object(tribe.api, "wait_busy", return_value=None), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value={"store": {"meat": 10, "bread": 0}}):
            tribe.deposit_all(925)
        self.assertEqual([a["action"] for a in acts], ["deposit", "withdraw", "deposit"])
        self.assertEqual(acts[0]["items"], {"wood": 10, "hide": 3})          # materials first, no food yet
        self.assertEqual(acts[1]["items"], {"meat": 3})                       # topped up to 5 meat (bread already over)
        self.assertEqual(acts[2]["items"], {"bread": 3, "roast": 4})          # anything over 5 goes in afterwards
        for a in acts:
            self.assertNotIn("bandage", a.get("items") or {})


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

    def test_coins_under_30_are_topped_up_from_the_treasury(self):
        self.assertEqual(self.run_deposit(10, 100), [{"action": "withdraw", "coins": 20}])

    def test_top_up_is_limited_by_the_treasury(self):
        self.assertEqual(self.run_deposit(10, 5), [{"action": "withdraw", "coins": 5}])

    def test_exactly_30_coins_stay_untouched(self):
        self.assertEqual(self.run_deposit(30, 100), [])


if __name__ == "__main__":
    unittest.main()
