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
        for value in ("../escape", "nested/game", "..", "bad\x00name"):
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
            patch.object(privileged.subprocess, "run", return_value=completed) as run,
        ):
            result = privileged._broker_dispatch(
                "move-game", {"platform": "steam", "user": "alice", "game_name": "Game"},
            )

        self.assertEqual(result, {"message": "ok"})
        self.assertEqual(run.call_args.kwargs["user"], 1001)
        self.assertEqual(run.call_args.kwargs["group"], 1001)
        self.assertEqual(run.call_args.kwargs["extra_groups"], [1234])
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_ea_user_worker_has_extended_move_timeout(self):
        account = Mock(pw_uid=1001, pw_gid=1001, pw_dir="/home/player")
        completed = Mock(returncode=0, stdout='{"message":"ok"}', stderr="")
        with (
            patch.object(privileged.pwd, "getpwnam", return_value=account),
            patch.object(privileged.grp, "getgrnam", return_value=Mock(gr_gid=1234)),
            patch.object(
                privileged.subprocess, "run", return_value=completed,
            ) as run,
        ):
            privileged._broker_dispatch(
                "move-game",
                {"platform": "ea", "user": "player", "game_name": "Game"},
            )

        self.assertEqual(run.call_args.kwargs["timeout"], 900)

    def test_ea_move_keeps_prefix_and_links_only_game_payload(self):
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
            proxy = links / "player/ea/Example Game II"
            self.assertTrue(prefix.is_dir())
            self.assertTrue(launcher.is_dir())
            self.assertEqual((target / "game.bin").read_bytes(), b"payload")
            self.assertEqual(os.readlink(proxy), str(target))
            self.assertEqual(os.readlink(game), str(proxy))
            self.assertIn("přesunuta", result["message"])

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
