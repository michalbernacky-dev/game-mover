import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import game_mover_rockstar_epic as rockstar_epic


class RockstarEpicLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home/player"
        self.config = self.home / ".config/heroic"
        self.legendary = self.config / "legendaryConfig/legendary"
        self.app_name = "8769e24080ea413b8ebca3f1b8c50951"
        self.game_root = self.root / "Games/Heroic/GTAVEnhanced"
        self.prefix = self.home / "Games/Heroic/Prefixes/GTA"
        self.proton = self.home / ".config/heroic/tools/proton/GE-Proton-test"
        self.umu = self.home / ".config/heroic/tools/runtimes/umu/umu-run"

        (self.config / "GamesConfig").mkdir(parents=True)
        self.legendary.mkdir(parents=True)
        self.game_root.mkdir(parents=True)
        self.prefix.mkdir(parents=True)
        (self.prefix / "pfx").symlink_to(".", target_is_directory=True)
        (self.game_root / "PlayGTAV.exe").write_bytes(b"MZ")
        (self.game_root / "EpicGamesLauncher.exe").write_bytes(b"MZ")
        launcher = (
            self.prefix
            / "drive_c/Program Files/Rockstar Games/Launcher/Launcher.exe"
        )
        launcher.parent.mkdir(parents=True)
        launcher.write_bytes(b"MZ")
        self.proton.mkdir(parents=True)
        (self.proton / "proton").write_text("runner")
        self.umu.parent.mkdir(parents=True)
        self.umu.write_text("#!/bin/sh\n")
        self.umu.chmod(0o755)
        (self.legendary / "installed.json").write_text(json.dumps({
            self.app_name: {
                "app_name": self.app_name,
                "title": "Grand Theft Auto V Enhanced",
                "install_path": str(self.game_root),
                "executable": "PlayGTAV.exe",
            },
        }))
        (self.legendary / "user.json").write_text(json.dumps({
            "account_id": "fixture-account", "refresh_token": "fixture-token",
        }))
        (self.config / f"GamesConfig/{self.app_name}.json").write_text(json.dumps({
            self.app_name: {
                "winePrefix": str(self.prefix),
                "wineVersion": {
                    "type": "proton", "name": "GE-Proton-test",
                    "bin": str(self.proton / "proton"),
                },
            },
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def report(self):
        with patch.object(
            rockstar_epic, "_legendary_executable", return_value="/usr/bin/legendary",
        ):
            return rockstar_epic.prerequisite_report(
                self.app_name, home=str(self.home),
                shared_root=str(self.root / "Games"),
            )

    def test_reports_verified_heroic_gta_environment(self):
        report = self.report()
        self.assertTrue(report["ready"])
        self.assertEqual(report["game"]["launcher_type"], "rockstar_epic")
        self.assertNotIn("fixture-token", json.dumps(report))

    def test_launch_quarantines_titles_and_injects_installed_helper(self):
        titles = (
            self.prefix
            / "drive_c/ProgramData/Rockstar Games/Launcher/titles.dat"
        )
        titles.parent.mkdir(parents=True)
        titles.write_bytes(b"scanned titles")
        captured = {}

        class Process:
            pid = 4321

        def fake_popen(arguments, **options):
            captured.update(arguments=arguments, options=options)
            return Process()

        with (
            patch.object(
                rockstar_epic, "_legendary_executable",
                return_value="/usr/bin/legendary",
            ),
            patch.object(rockstar_epic, "_rockstar_running", return_value=False),
            patch.object(
                rockstar_epic, "_helper_path",
                return_value="/usr/bin/game-mover-rockstar-epic",
            ),
        ):
            pid = rockstar_epic.launch(
                self.app_name, home=str(self.home),
                shared_root=str(self.root / "Games"), popen=fake_popen,
            )

        self.assertEqual(pid, 4321)
        self.assertFalse(titles.exists())
        self.assertEqual(
            titles.with_name("titles.dat.game-mover-disabled").read_bytes(),
            b"scanned titles",
        )
        self.assertEqual(captured["arguments"][:3], [
            "/usr/bin/legendary", "launch", self.app_name,
        ])
        self.assertIn("/usr/bin/game-mover-rockstar-epic", captured["arguments"])
        self.assertEqual(
            captured["options"]["env"][rockstar_epic.ENV_GAME_ROOT],
            str(self.game_root),
        )
        self.assertEqual(captured["options"]["stdout"], subprocess.DEVNULL)

    def test_wrapper_injects_epic_launcher_before_playgtav(self):
        captured = {}

        def fake_exec(path, arguments, environment):
            captured.update(path=path, arguments=arguments, environment=environment)

        environment = {
            rockstar_epic.ENV_APP: self.app_name,
            rockstar_epic.ENV_GAME_ROOT: str(self.game_root),
            rockstar_epic.ENV_PREFIX: str(self.prefix),
            rockstar_epic.ENV_PROTON: str(self.proton),
            rockstar_epic.ENV_UMU: str(self.umu),
        }
        changes = []
        auth_arguments = ["-AUTH_LOGIN=unused", "-EpicPortal"]
        with patch.dict(os.environ, environment, clear=True):
            rockstar_epic.run_wrapper(
                [str(self.game_root / "PlayGTAV.exe"), *auth_arguments],
                execve=fake_exec, chdir=changes.append,
            )

        self.assertEqual(changes, [str(self.game_root)])
        self.assertEqual(captured["path"], str(self.umu))
        self.assertEqual(captured["arguments"][:3], [
            str(self.umu), str(self.game_root / "EpicGamesLauncher.exe"),
            str(self.game_root / "PlayGTAV.exe"),
        ])
        self.assertEqual(captured["arguments"][3:], auth_arguments)
        self.assertEqual(captured["environment"]["STORE"], "egs")

    def test_running_launcher_blocks_metadata_change(self):
        titles = (
            self.prefix
            / "drive_c/ProgramData/Rockstar Games/Launcher/titles.dat"
        )
        titles.parent.mkdir(parents=True)
        titles.write_bytes(b"keep")
        with (
            patch.object(
                rockstar_epic, "_legendary_executable",
                return_value="/usr/bin/legendary",
            ),
            patch.object(rockstar_epic, "_rockstar_running", return_value=True),
            self.assertRaisesRegex(RuntimeError, "Close GTA"),
        ):
            rockstar_epic.launch(
                self.app_name, home=str(self.home),
                shared_root=str(self.root / "Games"),
            )
        self.assertEqual(titles.read_bytes(), b"keep")

    def test_prefix_pfx_symlink_cannot_escape_the_player_prefix(self):
        (self.prefix / "pfx").unlink()
        (self.prefix / "pfx").symlink_to(self.root, target_is_directory=True)
        with (
            patch.object(
                rockstar_epic, "_legendary_executable",
                return_value="/usr/bin/legendary",
            ),
            self.assertRaisesRegex(RuntimeError, "outside the Heroic prefix"),
        ):
            rockstar_epic.prerequisite_report(
                self.app_name, home=str(self.home),
                shared_root=str(self.root / "Games"),
            )


if __name__ == "__main__":
    unittest.main()
