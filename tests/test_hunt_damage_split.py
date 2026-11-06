import unittest
from wild import ambush, rules, combat


class HuntDamageSplitTests(unittest.TestCase):
    def test_rules_max_level_diff(self):
        self.assertEqual(rules.MAX_LEVEL_DIFF, 5)

    def test_ambush_ignores_creatures_more_than_5_levels_below(self):
        m = {"level": 15, "hp": 300, "max_hp": 300, "attack": 50, "crit": 0.1, "defense": 20}
        # Creature level 9 is 6 levels below 15 (> 5 levels below): no threats to fight back
        low_creature = [{"kind": "wolf", "level": 9, "hp": 100, "attacks": True}]
        # threats list is empty, can_fight_back returns True, 1.0 (no threat left)
        ok, left = ambush.can_fight_back(m, low_creature)
        self.assertTrue(ok)
        self.assertEqual(left, 1.0)

        # Creature level 10 is within 5 levels: evaluated as real fight
        ok_creature = [{"kind": "wolf", "level": 10, "hp": 100, "attacks": True}]
        ok, left = ambush.can_fight_back(m, ok_creature)
        self.assertTrue(ok)

    def test_ambush_fight_back_skips_low_level(self):
        m = {"level": 15, "hp": 300, "max_hp": 300}
        # If fight_back is called with only low level creatures, it skips them and returns True
        res = ambush.fight_back(0, m, [{"kind": "wolf", "level": 8, "hp": 50, "id": 999}])
        self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
