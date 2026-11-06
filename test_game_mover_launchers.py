import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import game_mover_flask as backend
import game_mover_launchers as launchers


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class FakeResponse:
    def __init__(self, *, payload=None, body=b""):
        self.payload = payload
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield self.body


def heroic_payload(version="2.22.1", *, digest=""):
    name = f"Heroic-{version}-linux-x86_64.rpm"
    return {
        "tag_name": f"v{version}",
        "published_at": "2026-08-09T11:42:00Z",
        "html_url": f"https://github.com/{launchers.HEROIC_REPOSITORY}/releases/tag/v{version}",
        "assets": [{
            "name": name,
            "browser_download_url": (
                f"https://github.com/{launchers.HEROIC_REPOSITORY}/releases/download/"
                f"v{version}/{name}"
            ),
            "digest": digest,
            "size": 123,
        }],
    }


class LauncherProviderTest(unittest.TestCase):
    @staticmethod
    def create_heroic_battle_net_profile(home):
        heroic = home / ".config/heroic"
        (heroic / "sideload_apps").mkdir(parents=True)
        (heroic / "GamesConfig").mkdir()
        umu = heroic / "tools/runtimes/umu/umu-run"
        umu.parent.mkdir(parents=True)
        umu.write_text("#!/bin/sh\n")
        umu.chmod(0o755)
        proton = heroic / "tools/proton/GE-Proton/proton"
        proton.parent.mkdir(parents=True)
        proton.write_text("#!/bin/sh\n")
        proton.chmod(0o755)
        prefix = home / "Games/Heroic/Prefixes/Battle.net"
        executable = prefix / "drive_c/Program Files (x86)/Battle.net/Battle.net.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"MZ")
        executable.chmod(0o755)
        app_id = "battle-local"
        (heroic / "sideload_apps/library.json").write_text(json.dumps({
            "games": [{
                "title": "Battle.net", "app_name": app_id,
                "is_installed": True,
                "install": {"executable": str(executable), "platform": "Windows"},
            }],
        }))
        (heroic / "GamesConfig" / f"{app_id}.json").write_text(json.dumps({
            app_id: {
                "winePrefix": str(prefix),
                "wineVersion": {"type": "proton", "bin": str(proton)},
            },
        }))
        return prefix, executable, proton, umu

    def test_semantic_versions_and_prereleases_are_compared(self):
        self.assertTrue(launchers.version_is_newer("2.22.1", "2.22.0"))
        self.assertTrue(launchers.version_is_newer("2.23.0", "2.23.0-beta.1"))
        self.assertFalse(launchers.version_is_newer("2.22.0", "2.22.0"))

    def test_catalog_marks_missing_and_outdated_launchers(self):
        def runner(command, timeout=30):
            if command[:4] == ["rpm", "-q", "--qf", "%{VERSION}"]:
                versions = {"heroic": "2.22.0", "steam": "1.0"}
                version = versions.get(command[-1], "")
                return completed(command, 0 if version else 1, version)
            if command[:5] == ["dnf", "--cacheonly", "--quiet", "repoquery", "--available"]:
                return completed(command, 0, "steam|1.0\n")
            return completed(command, 1)

        with patch.object(launchers.os.path, "isfile", return_value=False):
            result = launchers.launcher_statuses(
                runner=runner,
                request_get=Mock(return_value=FakeResponse(payload=heroic_payload())),
            )
        by_id = {item["id"]: item for item in result}
        self.assertTrue(by_id["heroic"]["installed"])
        self.assertTrue(by_id["heroic"]["update_available"])
        self.assertFalse(by_id["lutris"]["installed"])
        self.assertFalse(by_id["steam"]["update_available"])

    def test_managed_catalog_uses_only_explicit_heroic_and_lutris_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home/player"
            heroic = home / ".config/heroic"
            games_config = heroic / "GamesConfig"
            (heroic / "sideload_apps").mkdir(parents=True)
            games_config.mkdir()
            ea_prefix = home / "Games/EA App"
            ea_prefix.mkdir(parents=True)
            (ea_prefix / "system.reg").write_text(
                '[Software\\\\Vendor\\\\EA]\n'
                '"DisplayName"="EA app"\n'
                '"BundleVersion"="13.788.2.6298"\n',
            )
            game_prefix = home / "Games/Battlefront"
            (game_prefix / "drive_c/Program Files/Electronic Arts/EA Desktop").mkdir(
                parents=True,
            )
            (heroic / "sideload_apps/library.json").write_text(json.dumps({
                "games": [
                    {
                        "title": "EA app", "app_name": "ea-local",
                        "is_installed": True,
                    },
                    {
                        "title": "GOG Galaxy", "app_name": "gog-local",
                        "is_installed": True, "version": "2.0.80.33",
                    },
                    {
                        "title": "STAR WARS Battlefront II",
                        "app_name": "battlefront", "is_installed": True,
                    },
                    {
                        "title": "Origin", "app_name": "old-origin",
                        "is_installed": False,
                    },
                ],
            }))
            for app_id, prefix in (
                ("ea-local", ea_prefix), ("gog-local", home / "Games/GOG"),
                ("battlefront", game_prefix),
                ("old-origin", home / "Games/Old Origin"),
            ):
                prefix.mkdir(parents=True, exist_ok=True)
                (games_config / f"{app_id}.json").write_text(json.dumps({
                    app_id: {"winePrefix": str(prefix)},
                }))

            lutris = home / ".local/share/lutris"
            lutris.mkdir(parents=True)
            ubisoft = home / "Games/Ubisoft Connect"
            ubisoft.mkdir(parents=True)
            (ubisoft / "system.reg").write_text(
                '[Software\\\\Vendor\\\\Ubisoft]\n'
                '"DisplayName"="Ubisoft Connect"\n'
                '"DisplayVersion"="168.0.12921"\n',
            )
            connection = sqlite3.connect(lutris / "pga.db")
            connection.execute(
                "CREATE TABLE games (name, slug, directory, configpath, installed)",
            )
            connection.execute(
                "INSERT INTO games VALUES (?, ?, ?, ?, 1)",
                ("Ubisoft Connect", "ubisoft-connect", str(ubisoft), None),
            )
            connection.execute(
                "INSERT INTO games VALUES (?, ?, ?, ?, 1)",
                ("Grand Theft Auto V", "gta-v", str(home / "Games/GTA"), None),
            )
            connection.commit()
            connection.close()

            statuses = launchers.managed_launcher_statuses(str(home))

        by_id = {item["id"]: item for item in statuses}
        self.assertEqual(set(by_id), {
            "managed-ea-app", "managed-gog-galaxy", "managed-ubisoft-connect",
        })
        self.assertEqual(
            by_id["managed-ea-app"]["installed_version"], "13.788.2.6298",
        )
        self.assertEqual(
            by_id["managed-gog-galaxy"]["installed_version"], "2.0.80.33",
        )
        self.assertEqual(by_id["managed-ubisoft-connect"]["source"], "Lutris")
        self.assertEqual(
            by_id["managed-ubisoft-connect"]["installed_version"],
            "168.0.12921",
        )
        self.assertFalse(any(item["update_supported"] for item in statuses))

    def test_managed_catalog_merges_the_same_launcher_sources(self):
        definition = launchers.MANAGED_LAUNCHER_BY_ALIAS["ea-app"]
        with (
            patch.object(
                launchers, "_heroic_managed_launchers",
                return_value=[(definition, "Heroic", "13.1.0")],
            ),
            patch.object(
                launchers, "_lutris_managed_launchers",
                return_value=[(definition, "Lutris", "13.2.0")],
            ),
        ):
            statuses = launchers.managed_launcher_statuses("/home/player")
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0]["source"], "Heroic + Lutris")
        self.assertEqual(statuses[0]["installed_version"], "13.2.0")

    def test_heroic_managed_launcher_can_launch_with_local_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home/player"
            prefix, executable, proton, umu = self.create_heroic_battle_net_profile(home)
            status = next(
                item for item in launchers.managed_launcher_statuses(str(home))
                if item["id"] == "managed-battle-net"
            )
            process = Mock(pid=4321)
            popen = Mock(return_value=process)

            result = launchers.launch_managed_launcher(
                "managed-battle-net", '--no-sandbox "two words"',
                home=str(home), popen=popen,
            )

        self.assertTrue(status["launch_supported"])
        self.assertTrue(status["library_mount_supported"])
        self.assertFalse(status["library_mounted"])
        self.assertEqual(result, {"pid": 4321, "name": "Battle.net"})
        command = popen.call_args.args[0]
        self.assertEqual(command, [
            str(umu.resolve()), str(executable.resolve()),
            "--no-sandbox", "two words",
        ])
        options = popen.call_args.kwargs
        self.assertEqual(options["env"]["WINEPREFIX"], str(prefix.resolve()))
        self.assertEqual(options["env"]["PROTONPATH"], str(proton.parent.resolve()))
        self.assertEqual(options["env"]["GAMEID"], "umu-default")
        self.assertTrue(options["start_new_session"])
        self.assertNotIn("shell", options)

    def test_managed_launch_rejects_executable_outside_its_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home/player"
            self.create_heroic_battle_net_profile(home)
            heroic = home / ".config/heroic"
            library_path = heroic / "sideload_apps/library.json"
            library = json.loads(library_path.read_text())
            outside = home / "Downloads/Battle.net.exe"
            outside.parent.mkdir()
            outside.write_bytes(b"MZ")
            library["games"][0]["install"]["executable"] = str(outside)
            library_path.write_text(json.dumps(library))

            with self.assertRaisesRegex(launchers.LauncherError, "Proton profil"):
                launchers.launch_managed_launcher(
                    "managed-battle-net", home=str(home), popen=Mock(),
                )

    def test_local_launcher_arguments_are_bounded_and_never_a_shell_command(self):
        self.assertEqual(
            launchers.parse_launcher_arguments('--flag "value with spaces"'),
            ["--flag", "value with spaces"],
        )
        with self.assertRaisesRegex(launchers.LauncherError, "uvozovky"):
            launchers.parse_launcher_arguments("'unfinished")
        with self.assertRaisesRegex(launchers.LauncherError, "neplatné"):
            launchers.parse_launcher_arguments("line one\nline two")

    def test_repo_versions_from_multiple_architectures_stay_separate(self):
        runner = Mock(return_value=completed(
            [], 0, "steam|1.0.0.85\nsteam|1.0.0.87\n",
        ))
        self.assertEqual(launchers.repo_version("steam", runner=runner), "1.0.0.87")
        self.assertTrue(any(value.endswith("\\n") for value in runner.call_args.args[0]))

    def test_release_rejects_asset_outside_exact_official_path(self):
        payload = heroic_payload()
        payload["assets"][0]["browser_download_url"] = "https://example.invalid/heroic.rpm"
        with self.assertRaisesRegex(launchers.LauncherError, "Oficiální RPM"):
            launchers.heroic_release(
                request_get=Mock(return_value=FakeResponse(payload=payload)),
            )

    def test_update_validates_digest_identity_and_uses_dnf(self):
        body = b"test heroic rpm"
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        calls = []

        def runner(command, timeout=30):
            calls.append(list(command))
            if command[:4] == ["rpm", "-q", "--qf", "%{VERSION}"]:
                return completed(command, 0, "2.22.0")
            if command[:2] == ["pgrep", "-f"]:
                return completed(command, 1)
            if command[:2] == ["rpm", "-qp"]:
                return completed(command, 0, "heroic\n2.22.1\nx86_64\n")
            if command[:3] == ["dnf", "install", "-y"]:
                self.assertTrue(os.path.isfile(command[-1]))
                return completed(command, 0)
            return completed(command, 1)

        requester = Mock(side_effect=[
            FakeResponse(payload=heroic_payload(digest=digest)),
            FakeResponse(body=body),
        ])
        result = launchers.update_heroic(runner=runner, request_get=requester)
        self.assertTrue(result["changed"])
        install = next(command for command in calls if command[:3] == ["dnf", "install", "-y"])
        self.assertFalse(os.path.exists(install[-1]))

    def test_running_heroic_must_be_closed(self):
        def runner(command, timeout=30):
            if command[0] == "rpm":
                return completed(command, 0, "2.22.0")
            return completed(command, 0, "1234\n")

        with self.assertRaisesRegex(launchers.LauncherError, "ukonči Heroic"):
            launchers.update_heroic(runner=runner, request_get=Mock())

    def test_verified_rpm_can_be_delegated_to_client_installer(self):
        body = b"verified heroic rpm"
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        runner = Mock(side_effect=[
            completed([], 0, "2.22.0"), completed([], 1),
            completed([], 0, "heroic\n2.22.1\nx86_64\n"),
        ])
        installed_paths = []

        def installer(path):
            self.assertTrue(os.path.isfile(path))
            installed_paths.append(path)
            return completed([], 0)

        result = launchers.update_heroic(
            runner=runner,
            request_get=Mock(side_effect=[
                FakeResponse(payload=heroic_payload(digest=digest)),
                FakeResponse(body=body),
            ]),
            installer=installer,
        )
        self.assertTrue(result["changed"])
        self.assertFalse(os.path.exists(installed_paths[0]))


class LauncherApiTest(unittest.TestCase):
    def setUp(self):
        self.client = backend.app.test_client()
        self.local = {"environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

    def test_status_exposes_catalog_and_policy(self):
        with (
            patch.object(backend, "launcher_statuses", return_value=[{"id": "heroic"}]),
            patch.object(backend, "operation_policy", return_value="pam"),
        ):
            response = self.client.get("/launchers/status", **self.local)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["launchers"], [{"id": "heroic"}])
        self.assertEqual(response.json["update_policy"], "pam")

    def test_update_requires_policy_and_dispatches_allowlisted_provider(self):
        with patch.object(backend, "require_local_operation", return_value=False):
            denied = self.client.post("/launchers/heroic/update", **self.local)
        self.assertEqual(denied.status_code, 403)
        with (
            patch.object(backend, "require_local_operation", return_value=True),
            patch.object(backend, "update_launcher", return_value={
                "changed": True, "version": "2.22.1", "message": "Hotovo",
            }) as update,
        ):
            response = self.client.post("/launchers/heroic/update", **self.local)
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with("heroic")

    def test_split_service_authorizes_client_packagekit_without_installing(self):
        with (
            patch.object(backend, "PRIVILEGED_HELPER_ENABLED", True),
            patch.object(backend, "require_local_operation", return_value=True),
            patch.object(backend, "update_launcher") as update,
        ):
            response = self.client.post("/launchers/heroic/update", **self.local)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["install_via_client"])
        update.assert_not_called()

    def test_battle_net_library_mount_uses_fixed_privileged_action(self):
        with (
            patch.object(backend, "PRIVILEGED_HELPER_ENABLED", True),
            patch.object(backend, "require_local_operation", return_value=True),
            patch.object(
                backend, "privileged_call", return_value={"message": "mounted"},
            ) as privileged_call,
        ):
            response = self.client.post(
                "/launchers/managed-battle-net/library",
                json={"user": "player", "source": "/tmp/evil"}, **self.local,
            )
        self.assertEqual(response.status_code, 200)
        privileged_call.assert_called_once_with(
            "ensure-launcher-bind",
            {"launcher_id": "managed-battle-net", "user": "player"},
            timeout=120,
        )

    def test_other_launchers_cannot_request_library_mount(self):
        with patch.object(backend, "require_local_operation", return_value=True):
            response = self.client.post(
                "/launchers/managed-ea-app/library",
                json={"user": "player"}, **self.local,
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
