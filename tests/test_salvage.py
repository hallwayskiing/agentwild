"""Unit tests for auto salvage rule (2026-11-05):
Auto salvage all plain gear when returning to camp or tribe,
preserving at least one pickaxe and one axe/hatchet in bag besides worn gear.
"""
import unittest
from unittest import mock

from wild import care, nav, rules


def piece(gid, base, rarity="plain", level=1, slot="tool", dur=100, unidentified=False):
    return {
        "id": gid,
        "base": base,
        "rarity": rarity,
        "level": level,
        "slot": slot,
        "durability": dur,
        "durability_max": 100,
        "unidentified": unidentified,
    }


class AutoSalvageCandidateTests(unittest.TestCase):
    def test_preserves_single_plain_axe_and_single_plain_pickaxe(self):
        m = {
            "worn": {"tool": piece(1, "hoe", "plain")},
            "gear_bag": [
                piece(10, "pickaxe", "plain", level=5),
                piece(11, "hatchet", "plain", level=4),
                piece(12, "blade", "plain", level=8),
            ]
        }
        cands = care.plain_salvage_candidates(m)
        cand_ids = [c["id"] for c in cands]
        self.assertEqual(cand_ids, [12])  # Only the blade is salvaged; pickaxe 10 and hatchet 11 are preserved!

    def test_preserves_best_pickaxe_when_multiple_plain_exist(self):
        m = {
            "worn": {},
            "gear_bag": [
                piece(21, "pickaxe", "plain", level=4),
                piece(22, "pickaxe", "plain", level=8),
                piece(23, "hatchet", "plain", level=3),
            ]
        }
        cands = care.plain_salvage_candidates(m)
        cand_ids = [c["id"] for c in cands]
        self.assertIn(21, cand_ids)  # Weaker pickaxe 21 is salvaged
        self.assertNotIn(22, cand_ids)  # Stronger pickaxe 22 is preserved
        self.assertNotIn(23, cand_ids)  # Hatchet 23 is preserved

    def test_preserves_runed_pickaxe_and_salvages_plain_pickaxe(self):
        m = {
            "worn": {},
            "gear_bag": [
                piece(31, "pickaxe", "runed", level=5),
                piece(32, "pickaxe", "plain", level=4),
                piece(33, "hatchet", "runed", level=2),
                piece(34, "hatchet", "plain", level=1),
            ]
        }
        cands = care.plain_salvage_candidates(m)
        cand_ids = [c["id"] for c in cands]
        self.assertEqual(sorted(cand_ids), [32, 34])  # Plain tools salvaged, runed tools preserved

    def test_does_not_salvage_unidentified(self):
        m = {
            "worn": {},
            "gear_bag": [
                piece(41, "blade", "plain", unidentified=True),
                piece(42, "blade", "plain", unidentified=False),
            ]
        }
        cands = care.plain_salvage_candidates(m)
        cand_ids = [c["id"] for c in cands]
        self.assertEqual(cand_ids, [42])

    def test_salvages_runed_gear_below_level_10_and_keeps_level_10_plus(self):
        m = {
            "worn": {},
            "gear_bag": [
                piece(51, "blade", "runed", level=8),       # <10: salvage
                piece(52, "helm", "runed", level=9),        # <10: salvage
                piece(53, "chest", "runed", level=10),      # >=10: keep!
                piece(54, "greaves", "runed", level=12),    # >=10: keep!
                piece(55, "boots", "treasured", level=5),   # treasured: keep!
                piece(56, "dagger", "plain", level=12),     # plain: salvage regardless of level
            ]
        }
        cands = care.plain_salvage_candidates(m)
        cand_ids = sorted([c["id"] for c in cands])
        self.assertEqual(cand_ids, [51, 52, 56])


    def test_does_not_salvage_upgrades_or_empty_slot_gear(self):
        # 1. Weapon slot is empty in worn: the best weapon in bag (L5 maul) MUST be preserved!
        # 2. Chest slot is worn (L2 chest, armor 10): bag has L6 chest (armor 25, upgrade) and L1 chest (armor 5, worse).
        #    The L6 chest MUST be preserved; the L1 chest CAN be salvaged!
        m = {
            "worn": {
                "chest": {"id": 101, "slot": "chest", "rarity": "plain", "level": 2, "armor": 10, "durability": 50, "durability_max": 50},
                "weapon": None,
            },
            "gear_bag": [
                {"id": 201, "slot": "weapon", "base": "maul", "rarity": "plain", "level": 5, "damage": 20, "durability": 50, "durability_max": 50},
                {"id": 202, "slot": "weapon", "base": "blade", "rarity": "plain", "level": 1, "damage": 5, "durability": 50, "durability_max": 50},
                {"id": 203, "slot": "chest", "base": "cuirass", "rarity": "plain", "level": 6, "armor": 25, "durability": 50, "durability_max": 50},
                {"id": 204, "slot": "chest", "base": "tunic", "rarity": "plain", "level": 1, "armor": 5, "durability": 50, "durability_max": 50},
            ]
        }
        cands = care.plain_salvage_candidates(m)
        cand_ids = sorted([c["id"] for c in cands])
        # 201 (best weapon for empty slot) must be preserved -> not in cands
        # 203 (L6 chest upgrade over worn L2) must be preserved -> not in cands
        # 202 (weaker weapon for empty slot) and 204 (worse chest than worn) are candidates
        self.assertNotIn(201, cand_ids)
        self.assertNotIn(203, cand_ids)
        self.assertIn(202, cand_ids)
        self.assertIn(204, cand_ids)


if __name__ == "__main__":
    unittest.main()
