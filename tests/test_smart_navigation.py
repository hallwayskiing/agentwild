import unittest
from wild import nav, rules, queue

class SmartNavigationTests(unittest.TestCase):
    def test_waypoints_recorded(self):
        for name in ("camp", "meadow", "forest", "highlands", "ashlands", "ruins"):
            self.assertIn(name, rules.WAYPOINTS)
            pos = rules.WAYPOINTS[name]
            self.assertEqual(len(pos), 2)

    def test_parse_destination(self):
        self.assertEqual(nav.parse_destination("340 265"), ("coord (340,265)", (340, 265)))
        self.assertEqual(nav.parse_destination("340, 265"), ("coord (340,265)", (340, 265)))
        self.assertEqual(nav.parse_destination("[340, 265]"), ("coord (340,265)", (340, 265)))
        self.assertEqual(nav.parse_destination((340, 265)), ("coord (340,265)", (340, 265)))
        self.assertEqual(nav.parse_destination("home"), ("home", rules.WAYPOINTS["camp"]))
        self.assertEqual(nav.parse_destination("camp"), ("home", rules.WAYPOINTS["camp"]))
        self.assertEqual(nav.parse_destination("tribe"), ("tribe", rules.TRIBE_TOTEM))
        self.assertEqual(nav.parse_destination("forest"), ("forest", rules.WAYPOINTS["forest"]))
        self.assertEqual(nav.parse_destination("highlands"), ("highlands", rules.WAYPOINTS["highlands"]))

    def test_plan_from_camp_to_tribe_chooses_forest_waypoint(self):
        m = {
            "x": 256, "y": 256, "ring": "camp", "coins": 50,
            "waypoints": [
                {"ring": "meadow", "x": 307, "y": 256, "open": True},
                {"ring": "forest", "x": 356, "y": 256, "open": True},
            ]
        }
        plan = nav.plan_route(m, rules.TRIBE_TOTEM, "tribe")
        self.assertEqual(plan["name"], "via_waypoint_forest")
        self.assertLess(plan["ap"], 40.0)

    def test_plan_near_tribe_chooses_direct_walk(self):
        m = {
            "x": 342, "y": 260, "ring": "forest",
            "waypoints": [
                {"ring": "forest", "x": 356, "y": 256, "open": True},
            ]
        }
        plan = nav.plan_route(m, rules.TRIBE_TOTEM, "tribe")
        self.assertEqual(plan["name"], "direct_walk")
        self.assertLess(plan["ap"], 15.0)

    def test_plan_far_to_home_chooses_home_action(self):
        m = {
            "x": 342, "y": 260, "ring": "forest",
            "waypoints": [{"ring": "forest", "x": 356, "y": 256, "open": True}]
        }
        plan = nav.plan_route(m, rules.WAYPOINTS["camp"], "home")
        self.assertEqual(plan["name"], "via_home")
        self.assertEqual(plan["ap"], 8.0)

    def test_plan_from_camp_to_highlands_node(self):
        m = {
            "x": 256, "y": 256, "ring": "camp",
            "waypoints": [
                {"ring": "meadow", "x": 307, "y": 256, "open": True},
                {"ring": "forest", "x": 356, "y": 256, "open": True},
                {"ring": "highlands", "x": 410, "y": 256, "open": True},
            ]
        }
        # Copper node at (420, 260) in highlands
        plan = nav.plan_route(m, (420, 260))
        self.assertEqual(plan["name"], "via_waypoint_highlands")
        self.assertLess(plan["ap"], 35.0)  # ~28 AP vs 240+ AP walk

    def test_queue_supports_goto_and_build(self):
        self.assertIn("build", queue.TRIBE_ACTIONS)
        self.assertNotIn("back", queue.TRIBE_ACTIONS)
        self.assertIsNone(queue._runner(None, "back", "home", False))
        self.assertIsNotNone(queue._runner(None, "goto", "340 265", False))
        self.assertIsNotNone(queue._runner(None, "goto", "forest", False))
        self.assertIsNotNone(queue._runner(None, "goto", "home", False))
        self.assertIsNotNone(queue._runner(None, "build", "market", False))

if __name__ == "__main__":
    unittest.main()
