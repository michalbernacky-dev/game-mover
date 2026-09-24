import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from game_mover_version import __version__


class VersionTest(unittest.TestCase):
    def test_version_is_semver_and_matches_rpm(self):
        self.assertRegex(__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        specification = Path("game-mover.spec").read_text(encoding="utf-8")
        match = re.search(r"^Version:\s*(\S+)$", specification, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), __version__)

    def test_version_matches_latest_appstream_release(self):
        root = ET.parse("game-mover.metainfo.xml").getroot()
        releases = root.findall("./releases/release")
        self.assertTrue(releases)
        self.assertEqual(releases[0].get("version"), __version__)
        self.assertTrue("".join(releases[0].itertext()).strip())


if __name__ == "__main__":
    unittest.main()
