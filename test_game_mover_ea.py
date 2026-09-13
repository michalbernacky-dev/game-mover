import json
import os
from pathlib import Path
import tempfile
import unittest

from game_mover_ea import (
    ea_game_install_in_progress,
    ea_runtime_active,
    heroic_ea_installations,
    heroic_ea_shared_default_detected,
)


class HeroicEaDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "home" / "player"
        self.config = self.home / ".config/heroic"
        self.prefix = self.home / "Games/Heroic/Prefixes/default"
        self.games = self.prefix / "drive_c/Program Files/EA Games"
        launcher = self.prefix / "drive_c/Program Files/Electronic Arts/EA Desktop"
        launcher.mkdir(parents=True)
        self.games.mkdir(parents=True)
        (self.config / "sideload_apps").mkdir(parents=True)
        (self.config / "GamesConfig").mkdir()
        (self.config / "sideload_apps/library.json").write_text(json.dumps({
            "games": [{"title": "EA App", "app_name": "ea-app-local"}],
        }), encoding="utf-8")
        (self.config / "GamesConfig/ea-app-local.json").write_text(json.dumps({
            "ea-app-local": {"winePrefix": str(self.prefix)},
        }), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_discovers_only_game_payload_root_from_heroic_configuration(self):
        installations = heroic_ea_installations(str(self.home))

        self.assertEqual(len(installations), 1)
        self.assertEqual(installations[0].prefix, str(self.prefix))
        self.assertEqual(installations[0].games_root, str(self.games))
        self.assertNotEqual(installations[0].games_root, installations[0].prefix)

    def test_rejects_prefix_outside_home_and_symlinked_game_root(self):
        outside = Path(self.temporary.name) / "outside"
        (outside / "drive_c/Program Files/Electronic Arts/EA Desktop").mkdir(
            parents=True,
        )
        (outside / "drive_c/Program Files/EA Games").mkdir(parents=True)
        config = self.config / "GamesConfig/ea-app-local.json"
        config.write_text(json.dumps({
            "ea-app-local": {"winePrefix": str(outside)},
        }), encoding="utf-8")
        self.assertEqual(heroic_ea_installations(str(self.home)), [])

        config.write_text(json.dumps({
            "ea-app-local": {"winePrefix": str(self.prefix)},
        }), encoding="utf-8")
        self.games.rmdir()
        self.games.symlink_to(
            outside / "drive_c/Program Files/EA Games", target_is_directory=True,
        )
        self.assertEqual(heroic_ea_installations(str(self.home)), [])

    def test_marks_staged_ea_download_as_incomplete(self):
        game = self.games / "Example Game II"
        game.mkdir()
        staged = (
            self.prefix
            / "drive_c/ProgramData/EA Desktop/InstallData/Example Game II"
        )
        staged.mkdir(parents=True)
        (staged / "example_DiP_Staged.eajrn").write_text("pending")
        installation = heroic_ea_installations(str(self.home))[0]

        self.assertTrue(ea_game_install_in_progress(installation, game.name))
        (staged / "example_DiP_Staged.eajrn").unlink()
        self.assertFalse(ea_game_install_in_progress(installation, game.name))

    def test_shared_heroic_default_is_inventory_only_and_not_mutable(self):
        (self.config / "config.json").write_text(json.dumps({
            "defaultSettings": {
                "winePrefix": str(self.prefix),
                "defaultWinePrefix": str(self.prefix),
            },
        }), encoding="utf-8")

        self.assertTrue(heroic_ea_shared_default_detected(str(self.home)))
        self.assertEqual(heroic_ea_installations(str(self.home)), [])
        self.assertEqual(len(heroic_ea_installations(
            str(self.home), allow_shared_default=True,
        )), 1)

    def test_runtime_check_reads_only_processes_owned_by_caller(self):
        proc = Path(self.temporary.name) / "proc"
        process = proc / "123"
        process.mkdir(parents=True)
        (process / "cmdline").write_bytes(b"wine\0EADesktop.exe\0")
        self.assertTrue(ea_runtime_active(str(proc)))
        (process / "cmdline").write_bytes(b"python\0worker.py\0")
        self.assertFalse(ea_runtime_active(str(proc)))


if __name__ == "__main__":
    unittest.main()
