import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import game_mover_ea_epic as ea_epic
from game_mover_ea import heroic_ea_installations


class EaEpicLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home/player"
        self.config = self.home / ".config/heroic"
        self.legendary = self.config / "legendaryConfig/legendary"
        self.prefix = self.home / "Games/Heroic/Prefixes/EA_app"
        self.wine_root = self.prefix / "pfx"
        self.games = self.wine_root / "drive_c/Program Files/EA Games"
        self.shared = self.root / "Games/EA/STAR WARS Battlefront II"
        self.proton = self.home / ".local/share/Steam/compatibilitytools.d/GE-Proton-test"
        self.umu = self.home / ".config/heroic/tools/runtimes/umu/umu-run"
        (self.config / "sideload_apps").mkdir(parents=True)
        (self.config / "GamesConfig").mkdir()
        self.legendary.mkdir(parents=True)
        (self.wine_root / "drive_c/Program Files/Electronic Arts/EA Desktop").mkdir(
            parents=True,
        )
        self.games.mkdir(parents=True)
        start = self.wine_root / "drive_c/windows/system32/start.exe"
        start.parent.mkdir(parents=True)
        start.write_bytes(b"MZ")
        self.proton.mkdir(parents=True)
        (self.proton / "proton").write_text("runner")
        self.umu.parent.mkdir(parents=True)
        self.umu.write_text("#!/bin/sh\n")
        self.umu.chmod(0o755)
        self.shared.mkdir(parents=True)
        installer = self.shared / "__Installer/Touchup.exe"
        installer.parent.mkdir()
        installer.write_bytes(b"MZ")
        (self.shared / "starwarsbattlefrontii.exe").write_bytes(b"MZ")
        (self.games / "STAR WARS Battlefront II").symlink_to(
            self.shared, target_is_directory=True,
        )
        (self.wine_root / "system.reg").write_text(
            "[Software\\\\EA Games\\\\STAR WARS Battlefront II]\n"
            '"Install Dir"="C:\\\\Program Files\\\\EA Games\\\\'
            'STAR WARS Battlefront II\\\\"\n',
        )
        (self.config / "sideload_apps/library.json").write_text(json.dumps({
            "games": [{"title": "EA App", "app_name": "ea-app-local"}],
        }))
        (self.config / "GamesConfig/ea-app-local.json").write_text(json.dumps({
            "ea-app-local": {
                "winePrefix": str(self.prefix),
                "wineVersion": {
                    "type": "proton", "name": "Proton - GE-Proton-test",
                    "bin": str(self.proton / "proton"),
                },
            },
        }))
        (self.legendary / "user.json").write_text(json.dumps({
            "account_id": "fixture-account", "refresh_token": "fixture-token",
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def test_discovers_proton_pfx_layout_and_reports_ready(self):
        installation = heroic_ea_installations(str(self.home))[0]
        self.assertEqual(installation.wine_root, str(self.wine_root))
        with (
            patch.object(ea_epic, "_heroic_available", return_value=True),
            patch.object(ea_epic, "_legendary_executable", return_value="/usr/bin/legendary"),
        ):
            report = ea_epic.prerequisite_report(
                "MtMassive", home=str(self.home), shared_root=str(self.root / "Games"),
            )
        self.assertTrue(report["ready"])
        self.assertEqual(report["runtime"]["proton"], str(self.proton))
        self.assertEqual(report["game"]["launcher_type"], "ea_epic")
        self.assertEqual(report["game"]["shared_data"], str(self.shared))
        serialized = json.dumps(report)
        self.assertNotIn("fixture-token", serialized)

    def test_reports_a_bind_mount_as_connected_shared_data(self):
        with (
            patch.object(ea_epic, "_heroic_available", return_value=True),
            patch.object(ea_epic, "_legendary_executable", return_value="/usr/bin/legendary"),
            patch.object(ea_epic.os.path, "samefile", return_value=True),
        ):
            report = ea_epic.prerequisite_report(
                "MtMassive", home=str(self.home), shared_root=str(self.root / "Games"),
            )
        check = {item["id"]: item for item in report["checks"]}["shared_link"]
        self.assertTrue(check["ok"])

    def test_missing_login_has_specific_non_secret_message(self):
        (self.legendary / "user.json").write_text("{}")
        with patch.object(ea_epic, "_heroic_available", return_value=True):
            report = ea_epic.prerequisite_report(
                "MtMassive", home=str(self.home), shared_root=str(self.root / "Games"),
            )
        check = {item["id"]: item for item in report["checks"]}["epic_login"]
        self.assertFalse(check["ok"])
        self.assertIn("Sign in to Epic", check["message"])

    def test_wrapper_strips_start_and_executes_umu_with_required_environment(self):
        captured = {}

        def fake_exec(path, arguments, environment):
            captured.update(path=path, arguments=arguments, environment=environment)

        environment = {
            ea_epic.ENV_APP: "MtMassive",
            ea_epic.ENV_PREFIX: str(self.prefix),
            ea_epic.ENV_PROTON: str(self.proton),
            ea_epic.ENV_UMU: str(self.umu),
        }
        url = "link2ea://launchgame/MtMassive?platform=epic&AUTH_PASSWORD=short-secret"
        with patch.dict(os.environ, environment, clear=True):
            ea_epic.run_wrapper(["start", url], execve=fake_exec)

        self.assertEqual(captured["path"], str(self.umu))
        self.assertEqual(captured["arguments"][1], str(
            self.wine_root / "drive_c/windows/system32/start.exe",
        ))
        self.assertEqual(captured["arguments"][2], url)
        self.assertEqual(captured["environment"]["WINEPREFIX"], str(self.prefix))
        self.assertEqual(captured["environment"]["PROTONPATH"], str(self.proton))
        self.assertEqual(captured["environment"]["GAMEID"], "umu-MtMassive")
        self.assertEqual(captured["environment"]["STORE"], "egs")

    def test_rejects_unrelated_protocol_and_redacts_exchange_code(self):
        self.assertNotIn(
            "short-secret",
            ea_epic.redact_sensitive(
                "link2ea://launchgame/MtMassive?AUTH_PASSWORD=short-secret&x=1",
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "Invalid"):
            with patch.dict(os.environ, {ea_epic.ENV_APP: "MtMassive"}, clear=True):
                ea_epic.run_wrapper(["start", "https://example.invalid/"])

    def test_launch_passes_current_user_context_without_capturing_output(self):
        captured = {}

        class Process:
            pid = 4321

        def fake_popen(arguments, **options):
            captured.update(arguments=arguments, options=options)
            return Process()

        with (
            patch.object(ea_epic, "_heroic_available", return_value=True),
            patch.object(ea_epic, "_legendary_executable", return_value="/usr/bin/legendary"),
        ):
            pid = ea_epic.launch(
                "MtMassive", home=str(self.home),
                shared_root=str(self.root / "Games"), popen=fake_popen,
            )

        self.assertEqual(pid, 4321)
        self.assertEqual(captured["arguments"][:4], [
            "/usr/bin/legendary", "launch", "MtMassive", "--origin",
        ])
        self.assertIn("--wine-prefix", captured["arguments"])
        self.assertEqual(
            captured["options"]["env"]["LEGENDARY_CONFIG_PATH"],
            str(self.legendary),
        )
        self.assertEqual(captured["options"]["stdout"], ea_epic.subprocess.DEVNULL)
        self.assertEqual(captured["options"]["stderr"], ea_epic.subprocess.DEVNULL)

    def test_missing_install_key_runs_game_touchup_before_launch(self):
        (self.wine_root / "system.reg").write_text("WINE REGISTRY Version 2\n")
        touchup_call = {}

        def fake_run(arguments, **options):
            touchup_call.update(arguments=arguments, options=options)

        class Process:
            pid = 4321

        with (
            patch.object(ea_epic, "_heroic_available", return_value=True),
            patch.object(ea_epic, "_legendary_executable", return_value="/usr/bin/legendary"),
            patch.object(ea_epic.subprocess, "run", side_effect=fake_run),
        ):
            ea_epic.launch(
                "MtMassive", home=str(self.home),
                shared_root=str(self.root / "Games"),
                popen=lambda *args, **kwargs: Process(),
            )

        self.assertEqual(touchup_call["arguments"], [
            str(self.umu),
            str(self.shared / "__Installer/Touchup.exe"),
            "install", "-locale", "en_US", "-installPath",
            r"C:\Program Files\EA Games\STAR WARS Battlefront II",
            "-autologging",
        ])
        self.assertEqual(touchup_call["options"]["env"]["WINEPREFIX"], str(self.prefix))
        self.assertEqual(touchup_call["options"]["env"]["PROTONPATH"], str(self.proton))
        self.assertTrue(touchup_call["options"]["check"])

    def test_existing_install_key_skips_touchup(self):
        installation = heroic_ea_installations(str(self.home))[0]
        game = ea_epic.game_by_app_name("MtMassive")
        with patch.object(ea_epic.subprocess, "run") as run:
            changed = ea_epic._ensure_ea_registration(
                installation, game, str(self.proton), str(self.umu),
            )
        self.assertFalse(changed)
        run.assert_not_called()

    def test_windows_game_path_accepts_symlinked_drive_c(self):
        real_drive = self.root / "real-drive-c"
        real_games = real_drive / "Program Files/EA Games"
        real_games.mkdir(parents=True)
        linked_root = self.root / "linked-wine-root"
        linked_root.mkdir()
        (linked_root / "drive_c").symlink_to(real_drive, target_is_directory=True)
        installation = ea_epic.EaInstallation(
            app_id="ea-app-local", prefix=str(self.prefix),
            games_root=str(real_games), install_data="",
            wine_root=str(linked_root),
        )

        self.assertEqual(
            ea_epic._windows_game_path(
                installation, ea_epic.game_by_app_name("MtMassive"),
            ),
            r"C:\Program Files\EA Games\STAR WARS Battlefront II",
        )


if __name__ == "__main__":
    unittest.main()
