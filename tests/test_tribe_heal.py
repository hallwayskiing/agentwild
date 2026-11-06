"""The owner's fixes of 2026-11-05: a healing visit at the tribe leaves for the hunt the moment health passes
rules.HEALED (it used to go on training to the day's quota), and a hero under rules.COIN_KEEP coins is never topped
up from the treasury (only the chief may take coins out)."""
import unittest
from unittest import mock

from wild import tribe


def hero(**kw):
    m = {"x": 340, "y": 265, "ring": "forest", "hp": 40, "max_hp": 100, "ap": 100, "level": 10, "bag": {}, "coins": 0}
    m.update(kw)
    return m


class HealVisitTests(unittest.TestCase):
    def run_visit(self, state, max_minutes=25, quota=True):
        acts, clock = [], {"t": 0.0}

        def me(_unit):
            return state["m"], None

        def act(_unit, payload):
            acts.append(payload)
            return 200, {"today": {"trained": 1, "most": 99}}

        with mock.patch.object(tribe, "go", return_value=True), \
                mock.patch.object(tribe, "deposit_all", return_value=None), \
                mock.patch.object(tribe.nav, "walk", return_value=state["m"]), \
                mock.patch.object(tribe.api, "me", side_effect=me), \
                mock.patch.object(tribe.api, "wait_busy", return_value=None), \
                mock.patch.object(tribe.api, "act", side_effect=act), \
                mock.patch.object(tribe, "has_quota", return_value=quota), \
                mock.patch.object(tribe.care, "eat", return_value=False), \
                mock.patch.object(tribe.care, "dust_repair", return_value=None), \
                mock.patch.object(tribe.time, "time", side_effect=lambda: clock["t"]), \
                mock.patch.object(tribe.time, "sleep", side_effect=lambda s: clock.__setitem__("t", clock["t"] + s)):
            tribe.visit(925, heal=True, train=99, max_minutes=max_minutes)
        return acts

    def test_healed_on_arrival_leaves_at_once_without_training(self):
        acts = self.run_visit({"m": hero(hp=95)})
        self.assertEqual([a["action"] for a in acts], ["home"])

    def test_training_stops_the_moment_health_passes_the_line(self):
        state = {"m": hero(hp=85), "reads": 0}

        def me(_unit):
            state["reads"] += 1
            if state["reads"] > 2:          # the land has healed it while the first train ran
                state["m"]["hp"] = 95
            return state["m"], None

        acts, clock = [], {"t": 0.0}
        with mock.patch.object(tribe, "go", return_value=True), \
                mock.patch.object(tribe, "deposit_all", return_value=None), \
                mock.patch.object(tribe.nav, "walk", return_value=state["m"]), \
                mock.patch.object(tribe.api, "me", side_effect=me), \
                mock.patch.object(tribe.api, "wait_busy", return_value=None), \
                mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {"today": {"trained": 1, "most": 99}})), \
                mock.patch.object(tribe, "has_quota", return_value=True), \
                mock.patch.object(tribe.care, "eat", return_value=False), \
                mock.patch.object(tribe.care, "dust_repair", return_value=None), \
                mock.patch.object(tribe.time, "time", side_effect=lambda: clock["t"]), \
                mock.patch.object(tribe.time, "sleep", side_effect=lambda s: clock.__setitem__("t", clock["t"] + s)):
            tribe.visit(925, heal=True, train=99)
        self.assertEqual([a["action"] for a in acts], ["train", "home"])   # one train, then straight out

    def test_still_hurt_with_the_quota_gone_rests_on_the_land(self):
        acts = self.run_visit({"m": hero(hp=60)}, max_minutes=2, quota=False)
        self.assertEqual([a["action"] for a in acts], ["home"])            # no train: it rests to the end, then home


class CoinTopUpTests(unittest.TestCase):
    def run_balance(self, coins, treasury):
        acts = []
        m = {"bag": {}, "coins": coins}
        with mock.patch.object(tribe.api, "act", side_effect=lambda u, p: acts.append(p) or (200, {})), \
                mock.patch.object(tribe, "info", return_value={"store": {}, "coins": treasury}):
            tribe.balance_coins(925, m)
        return acts

    def test_a_hero_is_never_topped_up(self):
        self.assertEqual(self.run_balance(10, 100), [])

    def test_the_excess_still_goes_in(self):
        self.assertEqual(self.run_balance(45, 100), [{"action": "deposit", "coins": 15}])


if __name__ == "__main__":
    unittest.main()
