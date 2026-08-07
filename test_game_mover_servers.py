import os
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
        self.headers = {"X-Timekpr-Token": "test-session"}
        self.client = backend.app.test_client()
        self.forge_mods = Path(self.temp_dir.name) / "forge-mods"
        self.pixelmon_mods = Path(self.temp_dir.name) / "pixelmon-mods"
        self.forge_mods.mkdir()
        self.pixelmon_mods.mkdir()
        self.servers = [
            {"id": "forge", "name": "Forge", "service": "forge-srv.service", "kind": "minecraft", "mods_dir": str(self.forge_mods)},
            {"id": "pixelmon", "name": "Pixelmon", "service": "pixelmon-srv", "kind": "minecraft", "mods_dir": str(self.pixelmon_mods)},
            {"id": "satisfactory", "name": "Satisfactory", "service": "satisfactory.service", "kind": "generic"},
        ]

    def tearDown(self):
        backend.GAME_SERVERS_CONFIG_PATH = self.original_config_path
        backend.TIMEKPRA_TOKENS.pop("test-session", None)
        self.temp_dir.cleanup()

    def request_options(self):
        return {"headers": self.headers, "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}

    def save_servers(self):
        response = self.client.put("/servers/config", json={"servers": self.servers}, **self.request_options())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_config_supports_multiple_minecraft_servers(self):
        self.save_servers()
        response = self.client.get("/servers/config", **self.request_options())
        self.assertEqual([item["id"] for item in response.json["servers"]], ["forge", "pixelmon", "satisfactory"])
        response = self.client.get("/servers/minecraft/mods?server_id=pixelmon", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["path"], str(self.pixelmon_mods.resolve()))

    def test_stop_is_local_and_requires_pam_session(self):
        self.save_servers()
        with patch.object(backend, "systemctl_stop", return_value=(0, "", "")) as stop:
            response = self.client.post("/servers/stop", json={"id": "pixelmon"}, **self.request_options())
        self.assertEqual(response.status_code, 200)
        stop.assert_called_once_with("pixelmon-srv")
        response = self.client.post(
            "/servers/stop", json={"id": "pixelmon"}, headers=self.headers,
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
