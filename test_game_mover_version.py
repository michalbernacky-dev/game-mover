import re
import unittest
from pathlib import Path

from game_mover_version import __version__


class VersionTest(unittest.TestCase):
    def test_version_is_semver_and_matches_rpm(self):
        self.assertRegex(__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        specification = Path("game-mover.spec").read_text(encoding="utf-8")
        match = re.search(r"^Version:\s*(\S+)$", specification, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), __version__)


if __name__ == "__main__":
    unittest.main()
