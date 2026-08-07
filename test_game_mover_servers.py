import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import game_mover_flask as backend


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
        self.forge_mods = Path(self.temp_dir.name) / "forge-mods"
        self.pixelmon_mods = Path(self.temp_dir.name) / "pixelmon-mods"
        self.forge_mods.mkdir()
        self.pixelmon_mods.mkdir()
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
        self.save_servers()
        response = self.client.get("/servers/config", **self.local_options(self.pam_headers))
        saved = {item["id"]: item for item in response.json["servers"]}
        self.assertEqual(saved["forge"]["mods_dir"], str(self.forge_mods))
        self.assertEqual(saved["pixelmon"]["mods_dir"], str(self.pixelmon_mods))
        response = self.client.get(
            "/servers/minecraft/mods?server_id=pixelmon",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["path"], str(self.pixelmon_mods.resolve()))
        with patch.object(backend, "systemctl_is_active", return_value=(0, "inactive", "")):
            response = self.client.get("/servers/status", **self.local_options())
        statuses = {item["id"]: item for item in response.json["servers"]}
        self.assertTrue(statuses["forge"]["has_mods"])
        self.assertTrue(statuses["pixelmon"]["has_mods"])
        self.assertFalse(statuses["satisfactory"]["has_mods"])

    def test_silent_control_starts_stops_and_resets_failed_state(self):
        self.save_servers()
        with (
            patch.object(backend, "load_local_admin_token", return_value="local-secret"),
            patch.object(backend, "systemctl_start", return_value=(0, "", "")) as start,
            patch.object(backend, "systemctl_stop", return_value=(0, "", "")) as stop,
            patch.object(backend, "systemctl_reset_failed", return_value=(0, "", "")) as reset,
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
        start.assert_called_once_with("pixelmon-srv")
        stop.assert_called_once_with("pixelmon-srv")
        self.assertEqual(reset.call_count, 2)

    def test_pam_policy_and_remote_control_are_enforced(self):
        self.save_servers()
        with (
            patch.object(backend, "load_local_admin_token", return_value="local-secret"),
            patch.object(backend, "systemctl_stop", return_value=(0, "", "")) as stop,
            patch.object(backend, "systemctl_reset_failed", return_value=(0, "", "")),
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
        stop.assert_called_once_with("forge-srv.service")


if __name__ == "__main__":
    unittest.main()
