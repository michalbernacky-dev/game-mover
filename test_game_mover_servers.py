import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import game_mover_flask as backend
from game_mover_version import __version__
from game_mover_workloads import BackendResult, WorkloadState


class ServerRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = str(Path(self.temp_dir.name) / "servers.json")
        self.original_config_path = backend.GAME_SERVERS_CONFIG_PATH
        backend.GAME_SERVERS_CONFIG_PATH = self.config_path
        backend.TIMEKPRA_TOKENS["test-session"] = ("tester", time.time() + 60)
        self.pam_headers = {"X-Timekpr-Token": "test-session"}
        self.admin_headers = {backend.LOCAL_ADMIN_TOKEN_HEADER: "local-secret"}
        self.client = backend.app.test_client()
        self.forge_data = Path(self.temp_dir.name) / "forge-data"
        self.pixelmon_data = Path(self.temp_dir.name) / "pixelmon-data"
        self.forge_mods = self.forge_data / "mods"
        self.pixelmon_mods = self.pixelmon_data / "mods"
        self.forge_mods.mkdir(parents=True)
        self.pixelmon_mods.mkdir(parents=True)
        with backend.MINECRAFT_STATUS_LOCK:
            backend.MINECRAFT_STATUS_CACHE.clear()
            backend.MINECRAFT_STATUS_INFLIGHT.clear()
        self.servers = [
            {
                "id": "forge", "name": "Forge", "service": "forge-srv.service",
                "kind": "minecraft", "mods_dir": str(self.forge_mods), "control_auth": "pam",
            },
            {
                "id": "pixelmon", "name": "Pixelmon", "service": "pixelmon-srv",
                "kind": "minecraft", "mods_dir": str(self.pixelmon_mods), "control_auth": "silent",
            },
            {
                "id": "satisfactory", "name": "Satisfactory",
                "service": "satisfactory.service", "kind": "generic", "control_auth": "silent",
            },
        ]

    def tearDown(self):
        backend.GAME_SERVERS_CONFIG_PATH = self.original_config_path
        backend.TIMEKPRA_TOKENS.pop("test-session", None)
        with backend.MINECRAFT_STATUS_LOCK:
            backend.MINECRAFT_STATUS_CACHE.clear()
            backend.MINECRAFT_STATUS_INFLIGHT.clear()
        self.temp_dir.cleanup()

    def local_options(self, headers=None):
        return {"headers": headers or {}, "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

    def save_servers(self):
        response = self.client.put(
            "/servers/config", json={"servers": self.servers},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_config_preserves_each_minecraft_mods_path(self):
        (self.forge_data / "server.properties").write_text(
            "server-port=25565\n", encoding="utf-8",
        )
        self.save_servers()
        response = self.client.get("/servers/config", **self.local_options(self.pam_headers))
        saved = {item["id"]: item for item in response.json["servers"]}
        self.assertNotIn("control_auth", saved["forge"])
        self.assertEqual(saved["forge"]["permissions"], {
            "start": "pam", "stop": "pam", "restart": "pam", "backup": "pam",
        })
        self.assertEqual(saved["pixelmon"]["permissions"], {
            "start": "silent", "stop": "silent", "restart": "silent", "backup": "pam",
        })
        self.assertEqual(saved["forge"]["mods_dir"], str(self.forge_mods))
        self.assertEqual(saved["forge"]["data"]["directory"], str(self.forge_data))
        self.assertNotIn("connection", saved["forge"])
        self.assertEqual(saved["pixelmon"]["mods_dir"], str(self.pixelmon_mods))
        self.assertEqual(saved["pixelmon"]["data"]["directory"], str(self.pixelmon_data))
        response = self.client.get(
            "/servers/minecraft/mods?server_id=pixelmon",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["path"], str(self.pixelmon_mods.resolve()))
        fake_backend = Mock()
        fake_backend.status.return_value = WorkloadState(
            "inactive", "inactive", "Neběží",
        )
        with patch.object(backend, "backend_for", return_value=fake_backend):
            response = self.client.get("/servers/status", **self.local_options())
        statuses = {item["id"]: item for item in response.json["servers"]}
        self.assertEqual(response.json["version"], __version__)
        self.assertTrue(statuses["forge"]["has_mods"])
        self.assertTrue(statuses["pixelmon"]["has_mods"])
        self.assertFalse(statuses["satisfactory"]["has_mods"])
        self.assertEqual(statuses["forge"]["connection"], {
            "direct_port": 25565, "source": "server.properties",
        })
        self.assertEqual(statuses["forge"]["permissions"]["start"], "pam")
        self.assertEqual(statuses["pixelmon"]["permissions"]["backup"], "pam")

    def test_minecraft_status_exposes_read_only_player_statistics(self):
        data_directory = Path(self.temp_dir.name) / "managed-servers" / "mc-test" / "data"
        player_data = data_directory / "world" / "playerdata"
        player_data.mkdir(parents=True)
        (player_data / "17aeaf09-24d4-47b4-a1dd-2aa945960095.dat").touch()
        server = {
            "id": "mc-test", "name": "Minecraft Test", "backend": "podman",
            "kind": "minecraft", "runtime": {"container_name": "mc-test"},
            "data": {"directory": str(data_directory)},
        }
        fake_backend = Mock()
        fake_backend.status.return_value = WorkloadState("active", "running", "Běží")
        fake_backend.published_port.return_value = 25570
        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(
                backend, "local_server_addresses",
                return_value=["192.0.2.66", "127.0.0.1", "::1"],
            ),
            patch.object(
                backend, "cached_minecraft_player_status",
                return_value=({"online": 1, "max": 20}, False, None),
            ) as status_query,
        ):
            status = backend.game_server_status(server)
        self.assertEqual(status["connection"], {"direct_port": 25570, "source": "podman"})
        self.assertEqual(status["players"], {"online": 1, "max": 20, "known": 1})
        status_query.assert_called_once_with("mc-test", "192.0.2.66", 25570)

    def test_minecraft_status_refresh_is_non_blocking_and_deduplicated(self):
        fake_executor = Mock()
        with patch.object(backend, "MINECRAFT_STATUS_EXECUTOR", fake_executor):
            first = backend.cached_minecraft_player_status(
                "forge", "192.0.2.66", 25565,
            )
            second = backend.cached_minecraft_player_status(
                "forge", "192.0.2.66", 25565,
            )

        self.assertEqual(first, (None, True, None))
        self.assertEqual(second, (None, True, None))
        fake_executor.submit.assert_called_once()

        cache_key = ("forge", "192.0.2.66", 25565)
        with patch.object(
            backend, "query_server_status", return_value={"online": 2, "max": 20},
        ) as query:
            backend._refresh_minecraft_player_status(
                cache_key, "192.0.2.66", 25565,
            )

        self.assertEqual(
            backend.cached_minecraft_player_status("forge", "192.0.2.66", 25565),
            ({"online": 2, "max": 20}, False, None),
        )
        query.assert_called_once_with("192.0.2.66", 25565, timeout=8.0)

    def test_silent_control_starts_stops_and_resets_failed_state(self):
        self.save_servers()
        fake_backend = Mock()
        fake_backend.start.return_value = BackendResult(0)
        fake_backend.stop.return_value = BackendResult(0)
        with (
            patch.object(backend, "load_local_admin_token", return_value="local-secret"),
            patch.object(backend, "backend_for", return_value=fake_backend),
        ):
            response = self.client.post(
                "/servers/start", json={"id": "pixelmon"},
                **self.local_options(self.admin_headers),
            )
            self.assertEqual(response.status_code, 200)
            response = self.client.post(
                "/servers/stop", json={"id": "pixelmon"},
                **self.local_options(self.admin_headers),
            )
            self.assertEqual(response.status_code, 200)
        fake_backend.start.assert_called_once()
        fake_backend.stop.assert_called_once()
        self.assertEqual(fake_backend.start.call_args.args[0]["backend"], "systemd")

    def test_pam_policy_and_remote_control_are_enforced(self):
        self.save_servers()
        fake_backend = Mock()
        fake_backend.stop.return_value = BackendResult(0)
        with (
            patch.object(backend, "load_local_admin_token", return_value="local-secret"),
            patch.object(backend, "backend_for", return_value=fake_backend),
        ):
            response = self.client.post(
                "/servers/stop", json={"id": "forge"},
                **self.local_options(self.admin_headers),
            )
            self.assertEqual(response.status_code, 403)
            response = self.client.post(
                "/servers/stop", json={"id": "forge"},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 200)
            response = self.client.post(
                "/servers/stop", json={"id": "pixelmon"}, headers=self.admin_headers,
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
            self.assertEqual(response.status_code, 403)
        fake_backend.stop.assert_called_once()


    def test_podman_registry_restart_and_remote_rejection(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        data_directory = data_root / "mc-test" / "data"
        mods_directory = data_directory / "mods"
        mods_directory.mkdir(parents=True)
        podman_server = {
            "id": "mc-test",
            "name": "Minecraft Test",
            "backend": "podman",
            "kind": "minecraft",
            "control_auth": "pam",
            "runtime": {"container_name": "mc-test"},
            "management_mode": "adopted",
            "connection": {"direct_port": 25570},
            "data": {"directory": str(data_directory), "mods_relative_path": "mods"},
            "mods_dir": str(mods_directory),
        }
        fake_backend = Mock()
        fake_backend.restart.return_value = BackendResult(0)
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root.resolve())),
            patch.object(backend, "backend_for", return_value=fake_backend),
        ):
            invalid_container = {
                **podman_server,
                "runtime": {"container_name": "mc-test;systemctl"},
            }
            response = self.client.put(
                "/servers/config", json={"servers": [invalid_container]},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 400)

            invalid_permissions = {
                **podman_server,
                "permissions": {"start": "everyone"},
            }
            response = self.client.put(
                "/servers/config", json={"servers": [invalid_permissions]},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 400)

            wrong_data = {
                **podman_server,
                "data": {"directory": str(data_root / "other" / "data")},
            }
            response = self.client.put(
                "/servers/config", json={"servers": [wrong_data]},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 400)

            response = self.client.put(
                "/servers/config", json={"servers": [podman_server]},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            saved = response.json["servers"][0]
            self.assertEqual(saved["backend"], "podman")
            self.assertEqual(saved["runtime"]["container_name"], "mc-test")
            self.assertEqual(saved["connection"]["direct_port"], 25570)

            response = self.client.post(
                "/servers/restart", json={"id": "mc-test"},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 200)
            response = self.client.post(
                "/servers/restart", json={"id": "mc-test"},
                headers=self.pam_headers, environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
            self.assertEqual(response.status_code, 403)
        fake_backend.restart.assert_called_once()

    def test_podman_backup_requires_local_pam_and_rejects_systemd(self):
        self.servers[0]["backend"] = "systemd"
        self.save_servers()
        response = self.client.post(
            "/servers/backup", json={"id": "forge"},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 400)

        podman_server = {
            "id": "mc-test", "name": "Minecraft Test", "backend": "podman",
            "kind": "minecraft", "control_auth": "silent",
            "runtime": {"container_name": "mc-test"},
            "data": {"directory": "/var/lib/game-platform/servers/mc-test/data"},
            "mods_dir": "/var/lib/game-platform/servers/mc-test/data/mods",
        }
        with (
            patch.object(backend, "find_game_server", return_value=podman_server),
            patch.object(backend, "backend_for", return_value=Mock()),
            patch.object(backend, "create_workload_backup", return_value={"id": "backup-1"}) as create,
        ):
            response = self.client.post(
                "/servers/backup", json={"id": "mc-test"},
                **self.local_options(self.admin_headers),
            )
            self.assertEqual(response.status_code, 403)
            response = self.client.post(
                "/servers/backup", json={"id": "mc-test"},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 200)
            response = self.client.post(
                "/servers/backup", json={"id": "mc-test"}, headers=self.pam_headers,
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
            self.assertEqual(response.status_code, 403)
        create.assert_called_once()

    def test_each_server_action_has_an_independent_policy(self):
        server = {
            "id": "mc-test", "name": "Minecraft Test", "backend": "podman",
            "kind": "minecraft", "runtime": {"container_name": "mc-test"},
            "permissions": {
                "start": "silent", "stop": "pam", "restart": "disabled", "backup": "silent",
            },
            "data": {"directory": "/var/lib/game-platform/servers/mc-test/data"},
            "mods_dir": "/var/lib/game-platform/servers/mc-test/data/mods",
        }
        fake_backend = Mock()
        fake_backend.start.return_value = BackendResult(0)
        fake_backend.stop.return_value = BackendResult(0)
        with (
            patch.object(backend, "find_game_server", return_value=server),
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "load_local_admin_token", return_value="local-secret"),
            patch.object(backend, "create_workload_backup", return_value={"id": "backup-1"}),
        ):
            self.assertEqual(self.client.post(
                "/servers/start", json={"id": "mc-test"},
                **self.local_options(self.admin_headers),
            ).status_code, 200)
            self.assertEqual(self.client.post(
                "/servers/stop", json={"id": "mc-test"},
                **self.local_options(self.admin_headers),
            ).status_code, 403)
            self.assertEqual(self.client.post(
                "/servers/stop", json={"id": "mc-test"},
                **self.local_options(self.pam_headers),
            ).status_code, 200)
            self.assertEqual(self.client.post(
                "/servers/restart", json={"id": "mc-test"},
                **self.local_options(self.pam_headers),
            ).status_code, 403)
            self.assertEqual(self.client.post(
                "/servers/backup", json={"id": "mc-test"},
                **self.local_options(self.admin_headers),
            ).status_code, 200)

if __name__ == "__main__":
    unittest.main()
