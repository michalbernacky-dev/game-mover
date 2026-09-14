import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import game_mover_privileged as privileged
from game_mover_ea import EaInstallation


class PrivilegedBrokerTest(unittest.TestCase):
    def test_unknown_action_and_non_object_parameters_are_rejected(self):
        with self.assertRaisesRegex(privileged.PrivilegedError, "Neznámá"):
            privileged.dispatch("shell", {})
        with self.assertRaisesRegex(privileged.PrivilegedError, "objekt"):
            privileged.dispatch("systemd", [])

    def test_systemd_cannot_control_security_boundary_services(self):
        for unit in ("game_mover.service", "game-mover-privileged.service"):
            with self.subTest(unit=unit):
                with self.assertRaisesRegex(privileged.PrivilegedError, "není povolena"):
                    privileged.dispatch(
                        "systemd", {"verb": "restart", "unit": unit},
                    )

    def test_systemd_builds_fixed_argument_array(self):
        completed = Mock(returncode=0, stdout="active\n", stderr="")
        with patch.object(privileged.subprocess, "run", return_value=completed) as run:
            result = privileged.dispatch(
                "systemd", {"verb": "is-active", "unit": "forge-srv.service"},
            )
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "active\n")
        command = run.call_args.args[0]
        self.assertEqual(command, ["/usr/bin/systemctl", "is-active", "forge-srv.service"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_enable_is_limited_to_fixed_infrastructure_units(self):
        with self.assertRaises(privileged.PrivilegedError):
            privileged.dispatch(
                "systemd", {"verb": "enable-now", "unit": "example.service"},
            )

    def test_systemd_rejects_valid_but_unlisted_service(self):
        with patch.object(privileged, "ALLOWED_UNITS_PATH", "/missing/allowlist.json"):
            with self.assertRaisesRegex(privileged.PrivilegedError, "root allowlistu"):
                privileged.dispatch(
                    "systemd", {"verb": "restart", "unit": "sshd.service"},
                )

    def test_timekpr_rejects_unknown_flags_and_unbounded_values(self):
        account = Mock(pw_uid=1000, pw_dir="/home/alice")
        with patch.object(privileged.pwd, "getpwnam", return_value=account):
            for args in (
                ["--arbitrary", "alice"],
                ["--settimeleft", "alice", "=", "86401"],
                ["--userinfo", "alice", "extra"],
            ):
                with self.subTest(args=args):
                    with self.assertRaises(privileged.PrivilegedError):
                        privileged._timekpr_args(args)

    def test_timekpr_preserves_case_of_existing_interactive_account(self):
        account = Mock(pw_uid=1001, pw_dir="/home/Alice")
        with patch.object(privileged.pwd, "getpwnam", return_value=account) as lookup:
            for args in (
                ["--userinfo", "Alice"],
                ["--settimeleft", "Alice", "+", "1800"],
                ["--setallowedhours", "Alice", "3", "13:00-22:00"],
            ):
                with self.subTest(args=args):
                    self.assertEqual(privileged._timekpr_args(args), args)
            self.assertEqual(privileged._safe_username("Alice"), "Alice")
        self.assertTrue(lookup.call_args_list)
        for invocation in lookup.call_args_list:
            self.assertEqual(invocation.args, ("Alice",))

    def test_timekpr_still_rejects_unsafe_missing_and_system_accounts(self):
        with patch.object(privileged.pwd, "getpwnam") as lookup:
            for username in ("../Alice", "-Alice", "Alice;id", "Alice Smith", "Alice\n"):
                with self.subTest(username=username):
                    with self.assertRaises(privileged.PrivilegedError):
                        privileged._timekpr_args(["--userinfo", username])
            lookup.assert_not_called()
        with patch.object(privileged.pwd, "getpwnam", side_effect=KeyError):
            with self.assertRaises(privileged.PrivilegedError):
                privileged._timekpr_args(["--userinfo", "MissingUser"])
        with patch.object(privileged.pwd, "getpwnam", return_value=Mock(pw_uid=99, pw_dir="/var/lib/Service")):
            with self.assertRaises(privileged.PrivilegedError):
                privileged._timekpr_args(["--userinfo", "Service"])

    def test_pihole_rejects_non_dns_configuration(self):
        with self.assertRaises(privileged.PrivilegedError):
            privileged.dispatch("pihole", {"records": ["127.0.0.1 bad/name"]})

    def test_pam_rejects_oversized_password_before_loading_module(self):
        account = Mock(pw_uid=1000, pw_dir="/home/alice")
        with patch.object(privileged.pwd, "getpwnam", return_value=account):
            with self.assertRaisesRegex(privileged.PrivilegedError, "údaje"):
                privileged.dispatch(
                    "pam-auth", {"username": "alice", "password": "x" * 4097},
                )

    def test_game_names_cannot_escape_managed_roots(self):
        for value in (
            "../escape", "nested/game", "..", "bad\x00name", "bad\nname",
        ):
            with self.subTest(value=value):
                with self.assertRaises(privileged.PrivilegedError):
                    privileged._safe_game_name(value)

    def test_fix_permissions_uses_only_fixed_steam_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            games = Path(temporary) / "games"
            steam = games / "steam"
            steam.mkdir(parents=True)
            with (
                patch.object(privileged, "GAMES_ROOT", str(games)),
                patch.object(privileged, "_set_shared_permissions") as setter,
            ):
                result = privileged.dispatch("fix-permissions", {"path": "/etc"})
        setter.assert_called_once_with(str(steam))
        self.assertIn("oprávnění", result["message"].lower())

    def test_setfacl_inherits_the_validated_directory_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            games = Path(temporary) / "games"
            steam = games / "steam"
            steam.mkdir(parents=True)
            with (
                patch.object(privileged, "GAMES_ROOT", str(games)),
                patch.object(
                    privileged.grp, "getgrnam", return_value=Mock(gr_gid=os.getgid()),
                ),
                patch.object(privileged.subprocess, "run") as setfacl,
            ):
                privileged._set_shared_permissions(str(steam))

        self.assertEqual(setfacl.call_count, 2)
        for invocation in setfacl.call_args_list:
            descriptor = invocation.kwargs["pass_fds"]
            self.assertEqual(len(descriptor), 1)
            self.assertEqual(invocation.args[0][-1], f"/proc/self/fd/{descriptor[0]}")

    def test_beneath_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = os.path.join(temporary, "root")
            os.mkdir(root)
            with self.assertRaisesRegex(privileged.PrivilegedError, "opouští"):
                privileged._beneath(root, "..", "outside", allow_missing=True)

    def test_user_filesystem_actions_drop_root_before_traversal(self):
        account = Mock(pw_uid=1001, pw_gid=1001, pw_dir="/home/alice")
        completed = Mock(returncode=0, stdout='{"message":"ok"}', stderr="")
        with (
            patch.object(privileged.pwd, "getpwnam", return_value=account),
            patch.object(privileged.grp, "getgrnam", return_value=Mock(gr_gid=1234)),
            patch.object(privileged, "_prepare_user_proxy_base") as prepare,
            patch.object(privileged.subprocess, "run", return_value=completed) as run,
        ):
            result = privileged._broker_dispatch(
                "move-game", {"platform": "steam", "user": "alice", "game_name": "Game"},
            )

        self.assertEqual(result, {"message": "ok"})
        prepare.assert_called_once_with("alice", "steam")
        self.assertEqual(run.call_args.kwargs["user"], 1001)
        self.assertEqual(run.call_args.kwargs["group"], 1001)
        self.assertEqual(run.call_args.kwargs["extra_groups"], [1234])
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_ea_user_worker_has_extended_move_timeout(self):
        account = Mock(pw_uid=1001, pw_gid=1001, pw_dir="/home/player")
        completed = Mock(
            returncode=0,
            stdout='{"message":"ok","ea_mountpoint":"/home/player/Game"}',
            stderr="",
        )
        with (
            patch.object(privileged.pwd, "getpwnam", return_value=account),
            patch.object(privileged.grp, "getgrnam", return_value=Mock(gr_gid=1234)),
            patch.object(privileged, "_prepare_user_proxy_base"),
            patch.object(
                privileged.subprocess, "run", return_value=completed,
            ) as run,
            patch.object(
                privileged, "_ensure_ea_bind_mount",
                return_value={"message": "mounted"},
            ) as ensure_mount,
        ):
            privileged._broker_dispatch(
                "move-game",
                {"platform": "ea", "user": "player", "game_name": "Game"},
            )

        self.assertEqual(run.call_args.kwargs["timeout"], 900)
        ensure_mount.assert_called_once_with(
            {"platform": "ea", "user": "player", "game_name": "Game"},
            "/home/player/Game",
        )

    def test_ea_broker_does_not_prepare_a_user_proxy(self):
        account = Mock(pw_uid=1001, pw_gid=1001, pw_dir="/home/player")
        completed = Mock(
            returncode=0,
            stdout='{"ea_mountpoint":"/home/player/Game"}', stderr="",
        )
        with (
            patch.object(privileged.pwd, "getpwnam", return_value=account),
            patch.object(privileged.grp, "getgrnam", return_value=Mock(gr_gid=1234)),
            patch.object(privileged, "_prepare_user_proxy_base") as prepare,
            patch.object(privileged.subprocess, "run", return_value=completed),
            patch.object(
                privileged, "_ensure_ea_bind_mount",
                return_value={"message": "mounted"},
            ),
        ):
            privileged._broker_dispatch(
                "create-symlink",
                {"platform": "ea", "user": "player", "game_name": "Game"},
            )
        prepare.assert_not_called()

    def test_proxy_preparation_repairs_legacy_player_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            links = Path(temporary) / "Games_links"
            player = links / "Luky"
            player.mkdir(parents=True)
            player.chmod(0o555)
            account = Mock(
                pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir="/home/Luky",
            )
            with (
                patch.object(privileged, "GAMES_LINKS_ROOT", str(links)),
                patch.object(privileged.pwd, "getpwnam", return_value=account),
            ):
                result = privileged._prepare_user_proxy_base("Luky", "ea")

            self.assertEqual(result, str(player / "ea"))
            self.assertEqual(player.stat().st_mode & 0o777, 0o700)
            self.assertEqual((player / "ea").stat().st_mode & 0o777, 0o700)

    def test_proxy_preparation_rejects_symlinked_player_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            links = root / "Games_links"
            outside = root / "outside"
            links.mkdir()
            outside.mkdir()
            (links / "Luky").symlink_to(outside, target_is_directory=True)
            account = Mock(
                pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir="/home/Luky",
            )
            with (
                patch.object(privileged, "GAMES_LINKS_ROOT", str(links)),
                patch.object(privileged.pwd, "getpwnam", return_value=account),
            ):
                with self.assertRaisesRegex(
                    privileged.PrivilegedError, "bezpečný adresář",
                ):
                    privileged._prepare_user_proxy_base("Luky", "ea")

    def test_ea_move_keeps_prefix_and_prepares_only_game_mountpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home/player"
            prefix = home / "Games/Heroic/Prefixes/default"
            games_root = prefix / "drive_c/Program Files/EA Games"
            game = games_root / "Example Game II"
            launcher = prefix / "drive_c/Program Files/Electronic Arts/EA Desktop"
            game.mkdir(parents=True)
            launcher.mkdir(parents=True)
            (game / "game.bin").write_bytes(b"payload")
            shared = root / "Games"
            links = root / "Games_links"
            shared.mkdir()
            links.mkdir()
            installation = EaInstallation(
                app_id="ea-app-local",
                prefix=str(prefix),
                games_root=str(games_root),
                install_data=str(
                    prefix / "drive_c/ProgramData/EA Desktop/InstallData"
                ),
            )
            account = Mock(pw_uid=os.getuid(), pw_dir=str(home))

            with (
                patch.object(privileged.pwd, "getpwnam", return_value=account),
                patch.object(privileged, "_safe_username", return_value="player"),
                patch.object(privileged, "GAMES_ROOT", str(shared)),
                patch.object(privileged, "GAMES_LINKS_ROOT", str(links)),
                patch.object(
                    privileged, "heroic_ea_installations",
                    return_value=[installation],
                ),
                patch.object(
                    privileged, "heroic_ea_shared_default_detected",
                    return_value=False,
                ),
                patch.object(
                    privileged, "ea_game_install_in_progress", return_value=False,
                ),
                patch.object(privileged, "ea_runtime_active", return_value=False),
                patch.object(privileged, "_set_shared_permissions"),
            ):
                result = privileged._move_game({
                    "platform": "ea", "user": "player",
                    "game_name": "Example Game II",
                })

            target = shared / "EA/Example Game II"
            self.assertTrue(prefix.is_dir())
            self.assertTrue(launcher.is_dir())
            self.assertEqual((target / "game.bin").read_bytes(), b"payload")
            self.assertTrue(game.is_dir())
            self.assertEqual(list(game.iterdir()), [])
            self.assertEqual(result["ea_mountpoint"], str(game))
            self.assertIn("přesunuta", result["message"])

    def test_ea_link_migrates_only_the_expected_legacy_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home/player"
            games_root = home / "prefix/drive_c/Program Files/EA Games"
            shared = root / "Games/EA/Game"
            games_root.mkdir(parents=True)
            shared.mkdir(parents=True)
            source = games_root / "Game"
            source.symlink_to(shared, target_is_directory=True)
            installation = EaInstallation(
                app_id="ea", prefix=str(home / "prefix"),
                games_root=str(games_root), install_data="",
            )
            account = Mock(pw_uid=os.getuid(), pw_dir=str(home))
            with (
                patch.object(privileged.pwd, "getpwnam", return_value=account),
                patch.object(privileged, "_safe_username", return_value="player"),
                patch.object(privileged, "GAMES_ROOT", str(root / "Games")),
                patch.object(privileged, "heroic_ea_installations", return_value=[installation]),
                patch.object(privileged, "heroic_ea_shared_default_detected", return_value=False),
                patch.object(privileged, "ea_runtime_active", return_value=False),
            ):
                result = privileged._create_symlink({
                    "platform": "ea", "user": "player", "game_name": "Game",
                })
            self.assertTrue(source.is_dir())
            self.assertFalse(source.is_symlink())
            self.assertEqual(result["ea_mountpoint"], str(source))

    def test_ea_link_rejects_a_legacy_symlink_to_other_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home/player"
            games_root = home / "prefix/drive_c/Program Files/EA Games"
            shared = root / "Games/EA/Game"
            other = root / "other"
            games_root.mkdir(parents=True)
            shared.mkdir(parents=True)
            other.mkdir()
            (games_root / "Game").symlink_to(other, target_is_directory=True)
            installation = EaInstallation(
                app_id="ea", prefix=str(home / "prefix"),
                games_root=str(games_root), install_data="",
            )
            account = Mock(pw_uid=os.getuid(), pw_dir=str(home))
            with (
                patch.object(privileged.pwd, "getpwnam", return_value=account),
                patch.object(privileged, "_safe_username", return_value="player"),
                patch.object(privileged, "GAMES_ROOT", str(root / "Games")),
                patch.object(privileged, "heroic_ea_installations", return_value=[installation]),
                patch.object(privileged, "heroic_ea_shared_default_detected", return_value=False),
                patch.object(privileged, "ea_runtime_active", return_value=False),
            ):
                with self.assertRaisesRegex(privileged.PrivilegedError, "jiná data"):
                    privileged._create_symlink({
                        "platform": "ea", "user": "player", "game_name": "Game",
                    })

    def test_ea_mount_writes_a_bounded_persistent_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home/player"
            mountpoint = home / "prefix/drive_c/Program Files/EA Games/Game"
            target = root / "Games/EA/Game"
            units = root / "units"
            state = root / "state"
            mountpoint.mkdir(parents=True)
            target.mkdir(parents=True)
            account = Mock(pw_uid=os.getuid(), pw_dir=str(home))
            calls = []

            def fake_run(command, _timeout):
                calls.append(command)
                if command[0].endswith("systemd-escape"):
                    return {"returncode": 0, "stdout": "home-player-game.mount\n", "stderr": ""}
                return {"returncode": 0, "stdout": "", "stderr": ""}

            with (
                patch.object(privileged, "_safe_username", return_value="player"),
                patch.object(privileged.pwd, "getpwnam", return_value=account),
                patch.object(privileged, "GAMES_ROOT", str(root / "Games")),
                patch.object(privileged, "SYSTEMD_UNIT_ROOT", str(units)),
                patch.object(privileged, "EA_MOUNT_STATE_ROOT", str(state)),
                patch.object(privileged, "_run", side_effect=fake_run),
            ):
                result = privileged._ensure_ea_bind_mount(
                    {"user": "player", "game_name": "Game"}, str(mountpoint),
                )

            unit = (units / "home-player-game.mount").read_text()
            escaped_target = str(target).replace(" ", r"\x20")
            escaped_mountpoint = str(mountpoint).replace(" ", r"\x20")
            self.assertIn(f"What={escaped_target}", unit)
            self.assertIn(f"Where={escaped_mountpoint}", unit)
            self.assertNotIn('What="', unit)
            self.assertIn("Options=bind,nosuid,nodev", unit)
            self.assertEqual(calls[-1], [
                "/usr/bin/systemctl", "restart", "home-player-game.mount",
            ])
            self.assertEqual(result["mount_unit"], "home-player-game.mount")

    def test_ea_mount_rejects_worker_path_outside_player_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home/player"
            outside = root / "outside/Game"
            target = root / "Games/EA/Game"
            home.mkdir(parents=True)
            outside.mkdir(parents=True)
            target.mkdir(parents=True)
            account = Mock(pw_uid=os.getuid(), pw_dir=str(home))
            with (
                patch.object(privileged, "_safe_username", return_value="player"),
                patch.object(privileged.pwd, "getpwnam", return_value=account),
                patch.object(privileged, "GAMES_ROOT", str(root / "Games")),
                patch.object(privileged, "_run") as run,
            ):
                with self.assertRaisesRegex(privileged.PrivilegedError, "profilu"):
                    privileged._ensure_ea_bind_mount(
                        {"user": "player", "game_name": "Game"}, str(outside),
                    )
            run.assert_not_called()

    def test_ea_mount_refuses_to_replace_a_foreign_systemd_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home/player"
            mountpoint = home / "prefix/EA Games/Game"
            target = root / "Games/EA/Game"
            units = root / "units"
            mountpoint.mkdir(parents=True)
            target.mkdir(parents=True)
            units.mkdir()
            (units / "home-player-game.mount").write_text("foreign")
            account = Mock(pw_uid=os.getuid(), pw_dir=str(home))

            def fake_run(command, _timeout):
                if command[0].endswith("systemd-escape"):
                    return {
                        "returncode": 0,
                        "stdout": "home-player-game.mount\n",
                        "stderr": "",
                    }
                self.fail("systemctl must not run for a foreign unit")

            with (
                patch.object(privileged, "_safe_username", return_value="player"),
                patch.object(privileged.pwd, "getpwnam", return_value=account),
                patch.object(privileged, "GAMES_ROOT", str(root / "Games")),
                patch.object(privileged, "SYSTEMD_UNIT_ROOT", str(units)),
                patch.object(
                    privileged, "EA_MOUNT_STATE_ROOT", str(root / "state"),
                ),
                patch.object(privileged, "_run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(privileged.PrivilegedError, "cizí"):
                    privileged._ensure_ea_bind_mount(
                        {"user": "player", "game_name": "Game"},
                        str(mountpoint),
                    )

            self.assertEqual(
                (units / "home-player-game.mount").read_text(), "foreign",
            )

    def test_systemd_path_value_uses_hex_escapes_without_quotes(self):
        self.assertEqual(
            privileged._systemd_path_value("/var/Games/EA/Example Game"),
            r"/var/Games/EA/Example\x20Game",
        )

    def test_ea_move_rejects_incomplete_download_before_mutation(self):
        installation = Mock(
            games_root="/home/player/prefix/drive_c/Program Files/EA Games",
        )
        account = Mock(pw_uid=1001, pw_dir="/home/player")
        with (
            patch.object(privileged.pwd, "getpwnam", return_value=account),
            patch.object(
                privileged, "heroic_ea_installations", return_value=[installation],
            ),
            patch.object(
                privileged, "heroic_ea_shared_default_detected", return_value=False,
            ),
            patch.object(privileged.os.path, "isdir", return_value=True),
            patch.object(privileged.os.path, "islink", return_value=False),
            patch.object(
                privileged, "ea_game_install_in_progress", return_value=True,
            ),
            patch.object(privileged.shutil, "move") as move,
        ):
            with self.assertRaisesRegex(privileged.PrivilegedError, "dokončená"):
                privileged._move_game({
                    "platform": "ea", "user": "player", "game_name": "Game",
                })
        move.assert_not_called()

    def test_ea_move_rejects_heroic_shared_default_before_discovery(self):
        account = Mock(pw_uid=1001, pw_dir="/home/player")
        with (
            patch.object(privileged.pwd, "getpwnam", return_value=account),
            patch.object(privileged, "_safe_username", return_value="player"),
            patch.object(
                privileged, "heroic_ea_shared_default_detected",
                return_value=True,
            ),
            patch.object(privileged, "heroic_ea_installations") as discover,
            patch.object(privileged.shutil, "move") as move,
        ):
            with self.assertRaisesRegex(privileged.PrivilegedError, "samostatný"):
                privileged._move_game({
                    "platform": "ea", "user": "player", "game_name": "Game",
                })
        discover.assert_not_called()
        move.assert_not_called()


if __name__ == "__main__":
    unittest.main()
