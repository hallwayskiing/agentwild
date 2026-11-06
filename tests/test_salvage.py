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


if __name__ == "__main__":
    unittest.main()
