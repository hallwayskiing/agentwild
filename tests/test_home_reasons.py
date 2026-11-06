"""Every act that sends a character `home` must say why: api.act logs it at WARNING ("no reason given" is a bug, the
owner reads those lines to see what pulled a hunter off the field)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class HomeReasonTests(unittest.TestCase):
    def test_every_home_act_is_explained(self):
        missing = []
        for path in sorted((ROOT / "wild").glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if '"action": "home"' not in line:
                    continue
                # the payload may carry the reason literally or by the `why=why` keyword
                if '"why"' in line or "why=why" in line:
                    continue
                missing.append("%s:%d %s" % (path.name, number, line.strip()))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
