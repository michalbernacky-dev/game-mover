import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from game_mover_tip_checks import evaluate_check, steam_library_roots


class TipChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_file_and_nested_json_checks_do_not_return_contents(self):
        wrapper = self.root / "fix.bat"
        wrapper.write_text("EpicGamesLauncher.exe PlayGTAV.exe secret-value")
        result = evaluate_check({
            "type": "file_contains", "label": "Wrapper", "category": "configuration",
            "path": str(wrapper), "all": ["EpicGamesLauncher.exe", "PlayGTAV.exe"],
        })
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("secret-value", result["detail"])

        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"minecraft": json.dumps({"java": {"17": "/java"}})}))
        result = evaluate_check({
            "type": "json_value", "label": "Java", "category": "configuration",
            "path": str(settings), "keys": ["minecraft", "java", "17"], "equals": "/java",
        })
        self.assertEqual(result["status"], "ok")

    def test_steam_app_uses_additional_libraryfolders(self):
        steam_home = self.root / "Steam"
        extra = self.root / "Extra Library"
        (steam_home / "steamapps").mkdir(parents=True)
        (extra / "steamapps").mkdir(parents=True)
        (steam_home / "steamapps" / "libraryfolders.vdf").write_text(
            f'"libraryfolders" {{ "1" {{ "path" "{extra}" }} }}'
        )
        (extra / "steamapps" / "appmanifest_271590.acf").write_text(
            '"AppState" { "appid" "271590" "name" "Grand Theft Auto V" }'
        )
        with patch("game_mover_tip_checks._expand") as expand:
            expand.side_effect = lambda value: (
                str(steam_home) if value in (
                    "~/.local/share/Steam", "~/.steam/steam",
                    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
                )
                else os.path.abspath(os.path.expanduser(str(value)))
            )
            self.assertIn(str(extra), steam_library_roots())
            result = evaluate_check({
                "type": "steam_app", "label": "Steam GTA", "category": "installation",
                "app_id": "271590",
            })
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
