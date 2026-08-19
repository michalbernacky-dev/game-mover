import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import game_mover_flask as backend
from game_mover_version import __version__
from game_mover_workloads import BackendResult, WorkloadState


class ServerRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = str(Path(self.temp_dir.name) / "servers.json")
        self.original_config_path = backend.GAME_SERVERS_CONFIG_PATH
        self.original_gate_config_path = backend.GATE_CONFIG_PATH
        self.original_security_config_path = backend.SECURITY_CONFIG_PATH
        self.original_dns_config_path = backend.DNS_CONFIG_PATH
        self.original_dns_runtime_config_path = backend.DNS_RUNTIME_CONFIG_PATH
        self.original_dns_pihole_state_path = backend.DNS_PIHOLE_STATE_PATH
        self.original_operations = backend.OPERATIONS
        backend.OPERATIONS = type(backend.OPERATIONS)()
        backend.GAME_SERVERS_CONFIG_PATH = self.config_path
        backend.GATE_CONFIG_PATH = str(Path(self.temp_dir.name) / "gate.json")
        backend.SECURITY_CONFIG_PATH = str(Path(self.temp_dir.name) / "security.json")
        backend.DNS_CONFIG_PATH = str(Path(self.temp_dir.name) / "dns.json")
        backend.DNS_RUNTIME_CONFIG_PATH = str(Path(self.temp_dir.name) / "dns-runtime.json")
        backend.DNS_PIHOLE_STATE_PATH = str(Path(self.temp_dir.name) / "dns-pihole-state.json")
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
        backend.GATE_CONFIG_PATH = self.original_gate_config_path
        backend.SECURITY_CONFIG_PATH = self.original_security_config_path
        backend.DNS_CONFIG_PATH = self.original_dns_config_path
        backend.DNS_RUNTIME_CONFIG_PATH = self.original_dns_runtime_config_path
        backend.DNS_PIHOLE_STATE_PATH = self.original_dns_pihole_state_path
        backend.OPERATIONS = self.original_operations
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
        self.assertTrue(statuses["forge"]["backup_supported"])
        self.assertTrue(statuses["pixelmon"]["backup_supported"])
        self.assertFalse(statuses["satisfactory"]["backup_supported"])
        self.assertEqual(statuses["forge"]["connection"], {
            "direct_port": 25565, "source": "server.properties",
        })
        self.assertEqual(statuses["forge"]["permissions"]["start"], "pam")
        self.assertEqual(statuses["pixelmon"]["permissions"]["backup"], "pam")

    def test_security_registry_requires_local_pam_and_applies_immediately(self):
        self.save_servers()
        response = self.client.get("/security/policies", **self.local_options())
        self.assertEqual(response.status_code, 403)
        response = self.client.get(
            "/security/policies", headers=self.pam_headers,
            environ_base={"REMOTE_ADDR": "100.64.0.20"},
        )
        self.assertEqual(response.status_code, 403)

        response = self.client.get(
            "/security/policies", **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200)
        fixed = {item["id"]: item for item in response.json["catalog"]["fixed"]}
        self.assertEqual(fixed["timekpr.manage"]["policy"], "pam")
        policies = response.json["policies"]
        policies["servers"]["pixelmon"]["start"] = "disabled"
        response = self.client.put(
            "/security/policies", json={"policies": policies},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Path(backend.SECURITY_CONFIG_PATH).is_file())

        fake_backend = Mock()
        fake_backend.start.return_value = BackendResult(0)
        with (
            patch.object(backend, "load_local_admin_token", return_value="local-secret"),
            patch.object(backend, "backend_for", return_value=fake_backend),
        ):
            response = self.client.post(
                "/servers/start", json={"id": "pixelmon"},
                **self.local_options(self.admin_headers),
            )
        self.assertEqual(response.status_code, 403)
        fake_backend.start.assert_not_called()

    def test_health_probe_is_fast_local_only(self):
        response = self.client.get("/health", **self.local_options())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "version": __version__})
        response = self.client.get(
            "/health", environ_base={"REMOTE_ADDR": "100.64.0.20"},
        )
        self.assertEqual(response.status_code, 403)

    def test_global_silent_policy_uses_host_local_token(self):
        self.save_servers()
        response = self.client.get(
            "/security/policies", **self.local_options(self.pam_headers),
        )
        policies = response.json["policies"]
        policies["global"]["server.registry"] = "silent"
        response = self.client.put(
            "/security/policies", json={"policies": policies},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200)
        with patch.object(backend, "load_local_admin_token", return_value="local-secret"):
            response = self.client.get(
                "/servers/config", **self.local_options(self.admin_headers),
            )
        self.assertEqual(response.status_code, 200)

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
                return_value=({
                    "online": 1, "max": 20,
                    "version": {"name": "1.21.8", "protocol": 772},
                }, False, None),
            ) as status_query,
        ):
            status = backend.game_server_status(server)
        self.assertEqual(status["connection"], {"direct_port": 25570, "source": "podman"})
        self.assertEqual(status["players"], {"online": 1, "max": 20, "known": 1})
        self.assertEqual(
            status["minecraft_version"], {"name": "1.21.8", "protocol": 772},
        )
        status_query.assert_called_once_with(
            "mc-test", ["192.0.2.66", "127.0.0.1", "::1"], 25570, rcon=None,
        )

    def test_minecraft_properties_require_policy_and_preserve_unknown_values(self):
        (self.forge_data / "server.properties").write_text(
            "motd=Old MOTD\ncustom-mod-option=keep\nserver-port=25565\n",
            encoding="utf-8",
        )
        self.save_servers()
        response = self.client.get(
            "/servers/minecraft/properties?server_id=forge", **self.local_options(),
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.get(
            "/servers/minecraft/properties?server_id=forge", **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["settings"]["motd"], "Old MOTD")
        self.assertEqual(response.json["effective"]["server-port"], "25565")

        settings = dict(response.json["settings"])
        settings.update({"motd": "New MOTD", "max-players": 12, "white-list": True})
        response = self.client.put(
            "/servers/minecraft/properties",
            json={"server_id": "forge", "settings": settings},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        content = (self.forge_data / "server.properties").read_text(encoding="utf-8")
        self.assertIn("motd=New MOTD", content)
        self.assertIn("max-players=12", content)
        self.assertIn("white-list=true", content)
        self.assertIn("custom-mod-option=keep", content)
        self.assertIn("server-port=25565", content)

        response = self.client.put(
            "/servers/minecraft/properties",
            json={"server_id": "forge", "settings": {"motd": "incomplete"}},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 400)

    def test_server_logs_require_policy_and_use_registered_runtime(self):
        self.save_servers()
        response = self.client.get(
            "/servers/logs?server_id=forge&tail=100", **self.local_options(),
        )
        self.assertEqual(response.status_code, 403)
        fake_backend = Mock()
        fake_backend.logs.return_value = BackendResult(0, "first\nlast")
        with patch.object(backend, "backend_for", return_value=fake_backend):
            response = self.client.get(
                "/servers/logs?server_id=forge&tail=100",
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["output"], "first\nlast")
        fake_backend.logs.assert_called_once()
        self.assertEqual(fake_backend.logs.call_args.args[0]["id"], "forge")
        self.assertEqual(fake_backend.logs.call_args.args[1], 100)
        response = self.client.get(
            "/servers/logs?server_id=forge&tail=9999",
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 400)

    def test_minecraft_operators_require_policy_and_execute_validated_rcon(self):
        (self.forge_data / "ops.json").write_text(
            '[{"uuid":"one","name":"Alex","level":4,"bypassesPlayerLimit":false}]',
            encoding="utf-8",
        )
        (self.forge_data / "server.properties").write_text(
            "enable-rcon=true\nrcon.port=25575\nrcon.password=secret\n",
            encoding="utf-8",
        )
        self.save_servers()

        denied = self.client.get(
            "/servers/minecraft/operators?server_id=forge", **self.local_options(),
        )
        self.assertEqual(denied.status_code, 403)
        catalog = self.client.get(
            "/servers/minecraft/operators?server_id=forge",
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json["operators"][0]["name"], "Alex")

        with patch.object(
            backend, "execute_rcon_command", return_value="Made Bernye a server operator",
        ) as execute:
            response = self.client.post(
                "/servers/minecraft/operators",
                json={"server_id": "forge", "action": "op", "player": "Bernye"},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        execute.assert_called_once_with(
            "127.0.0.1", 25575, "secret", "op Bernye", timeout=5.0,
        )

        invalid = self.client.post(
            "/servers/minecraft/operators",
            json={"server_id": "forge", "action": "op", "player": "Alex; stop"},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(invalid.status_code, 400)

    def test_minecraft_whitelist_requires_policy_and_uses_validated_rcon(self):
        (self.forge_data / "whitelist.json").write_text(
            '[{"uuid":"one","name":"Alex"}]', encoding="utf-8",
        )
        (self.forge_data / "server.properties").write_text(
            "white-list=false\nenable-rcon=true\nrcon.port=25575\nrcon.password=secret\n",
            encoding="utf-8",
        )
        self.save_servers()

        denied = self.client.get(
            "/servers/minecraft/whitelist?server_id=forge", **self.local_options(),
        )
        self.assertEqual(denied.status_code, 403)
        catalog = self.client.get(
            "/servers/minecraft/whitelist?server_id=forge",
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(catalog.status_code, 200)
        self.assertFalse(catalog.json["enabled"])
        self.assertEqual(catalog.json["players"][0]["name"], "Alex")

        with patch.object(
            backend, "execute_rcon_command", return_value="Added Bernye to the whitelist",
        ) as execute:
            response = self.client.post(
                "/servers/minecraft/whitelist",
                json={"server_id": "forge", "action": "add", "player": "Bernye"},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        execute.assert_called_once_with(
            "127.0.0.1", 25575, "secret", "whitelist add Bernye", timeout=5.0,
        )
        invalid = self.client.post(
            "/servers/minecraft/whitelist",
            json={"server_id": "forge", "action": "add", "player": "Alex; stop"},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(invalid.status_code, 400)

    def test_minecraft_logs_prefer_persistent_latest_log_for_podman_and_systemd(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        data_directory = data_root / "forge" / "data"
        logs_directory = data_directory / "logs"
        logs_directory.mkdir(parents=True)
        (logs_directory / "latest.log").write_text(
            "old line\nvanilla or forge server line\nlatest line\n", encoding="utf-8",
        )
        self.servers[0].update({
            "backend": "podman",
            "runtime": {"container_name": "mc-test"},
            "mods_dir": str(data_directory / "mods"),
        })
        (data_directory / "mods").mkdir()

        fake_backend = Mock()
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root)),
            patch.object(backend, "backend_for", return_value=fake_backend),
        ):
            self.save_servers()
            response = self.client.get(
                "/servers/logs?server_id=forge&tail=10",
                **self.local_options(self.pam_headers),
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["source"], "minecraft-file")
        self.assertEqual(
            response.json["output"],
            "old line\nvanilla or forge server line\nlatest line",
        )
        fake_backend.logs.assert_not_called()

    def test_gate_config_requires_pam_and_status_is_read_only(self):
        config = backend.default_gate_config()
        self.assertEqual(self.client.get(
            "/proxy/config", **self.local_options(),
        ).status_code, 403)
        response = self.client.put(
            "/proxy/config", json={"proxy": config},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["proxy"]["listen"]["port"], 25581)

        fake_backend = Mock()
        fake_backend.status.return_value = WorkloadState("inactive", "exited", "Neběží")
        fake_backend.container_exists.return_value = False
        with patch.object(backend, "backend_for", return_value=fake_backend):
            response = self.client.get(
                "/proxy/status",
                headers={backend.READ_TOKEN_HEADER: "read-secret"},
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
        self.assertEqual(response.status_code, 403)

        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "require_read_access", return_value=True),
        ):
            response = self.client.get(
                "/proxy/status", environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["proxy"]["type"], "gate-lite")
        self.assertFalse(response.json["proxy"]["deployed"])
        self.assertNotIn("backends", response.json["proxy"])

    def test_gate_routes_use_registered_server_ids_and_restart_deployed_gate(self):
        targets = [
            {
                "id": "forge", "name": "Forge", "backend": "systemd",
                "endpoint": {"host": "host.containers.internal", "port": 25565},
            },
            {
                "id": "forge-podman", "name": "Forge Podman", "backend": "podman",
                "endpoint": {"host": "forge-podman", "port": 25565},
            },
        ]
        fake_backend = Mock()
        fake_backend.container_exists.return_value = True
        fake_backend.restart.return_value = BackendResult(0)
        with (
            patch.object(backend, "minecraft_route_targets", return_value=targets),
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "write_gate_layout", return_value={
                "data_directory": "/managed/gate", "config_path": "/managed/gate/config.yml",
            }),
            patch.object(backend, "chown_gate_layout"),
        ):
            response = self.client.put(
                "/proxy/routes", json={"routes": [
                    {"host": "forge.mc.example", "target_id": "forge-podman"},
                    {"host": "*", "target_id": "forge"},
                ]},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.json["restarted"])
        self.assertEqual(response.json["routes"][0]["target_id"], "forge-podman")
        self.assertEqual(response.json["routes"][1]["target_id"], "forge")
        saved = backend.load_gate_config()
        self.assertEqual(saved["routes"], [
            {"host": "forge.mc.example", "backend": {"host": "forge-podman", "port": 25565}},
            {"host": "*", "backend": {"host": "host.containers.internal", "port": 25565}},
        ])
        fake_backend.restart.assert_called_once()

    def test_gate_connection_catalog_prefers_named_route_and_supports_default(self):
        servers = [{"id": "forge"}, {"id": "mc-test"}]
        config = backend.default_gate_config()
        config["listen"]["port"] = 25581
        config["routes"] = [
            {"host": "*", "backend": {"host": "forge", "port": 25565}},
            {"host": "*", "backend": {"host": "vanilla", "port": 25565}},
            {"host": "forge.mc.example", "backend": {"host": "forge", "port": 25565}},
        ]
        targets = [
            {"id": "forge", "backend": "podman", "container_name": "forge",
             "endpoint": {"host": "forge", "port": 25565}},
            {"id": "mc-test", "backend": "podman", "container_name": "vanilla",
             "endpoint": {"host": "vanilla", "port": 25565}},
        ]
        with (
            patch.object(backend.os.path, "isfile", return_value=True),
            patch.object(backend, "load_gate_config", return_value=config),
            patch.object(backend, "minecraft_route_targets", return_value=targets),
        ):
            connections = backend.gate_connections_by_server(servers)

        self.assertEqual(connections["forge"], {
            "host": "forge.mc.example", "port": 25581, "route_host": "forge.mc.example",
        })
        self.assertEqual(connections["mc-test"], {
            "port": 25581, "route_host": "*",
        })

    def test_gate_connection_catalog_is_empty_without_persisted_config(self):
        with patch.object(backend.os.path, "isfile", return_value=False):
            self.assertEqual(backend.gate_connections_by_server(self.servers), {})

    def test_gate_routes_reject_unknown_target_and_missing_default(self):
        targets = [{
            "id": "forge", "name": "Forge", "backend": "systemd",
            "endpoint": {"host": "host.containers.internal", "port": 25565},
        }]
        with patch.object(backend, "minecraft_route_targets", return_value=targets):
            response = self.client.put(
                "/proxy/routes", json={"routes": [{"host": "*", "target_id": "missing"}]},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 400)
            response = self.client.put(
                "/proxy/routes", json={"routes": [{"host": "forge.mc.example", "target_id": "forge"}]},
                **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 400)

    def test_gate_route_targets_use_host_port_for_adopted_podman(self):
        targets = backend.minecraft_route_targets([{
            "id": "mc-test", "name": "Minecraft Test", "kind": "minecraft",
            "backend": "podman", "management_mode": "adopted",
            "runtime": {"container_name": "mc-test"},
            "connection": {"direct_port": 25570},
        }, {
            "id": "forge-podman", "name": "Forge Podman", "kind": "minecraft",
            "backend": "podman", "management_mode": "managed",
            "runtime": {"container_name": "forge-podman"},
            "connection": {"direct_port": 25571},
        }])
        by_id = {target["id"]: target for target in targets}
        self.assertEqual(by_id["mc-test"]["endpoint"], {
            "host": "host.containers.internal", "port": 25570,
        })
        self.assertEqual(by_id["forge-podman"]["endpoint"], {
            "host": "forge-podman", "port": 25565,
        })
        old_config = backend.default_gate_config()
        old_config["routes"] = [{
            "host": "test.mc.example", "backend": {"host": "mc-test", "port": 25565},
        }]
        self.assertEqual(
            backend.abstract_gate_routes(old_config, targets)[0]["target_id"], "mc-test",
        )

    def test_gate_deploy_is_pam_protected_and_uses_staging_port(self):
        fake_backend = Mock()
        fake_backend.container_exists.return_value = False
        fake_backend.network_exists.return_value = False
        fake_backend.create_network.return_value = BackendResult(0, "game-platform")
        fake_backend.pull_image.return_value = BackendResult(0, "sha256:image")
        fake_backend.create_container.return_value = BackendResult(0, "gate")
        fake_backend.start.return_value = BackendResult(0)
        layout = {
            "data_directory": "/managed/gate",
            "config_path": "/managed/gate/config.yml",
        }
        self.assertEqual(self.client.post(
            "/proxy/deploy", **self.local_options(self.admin_headers),
        ).status_code, 403)
        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "write_gate_layout", return_value=layout),
            patch.object(backend, "chown_gate_layout") as chown,
            patch.object(
                backend, "wait_for_gate_ready",
                return_value={"online": 0, "max": 20},
            ) as readiness,
        ):
            response = self.client.post(
                "/proxy/deploy", **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["listen"]["port"], 25581)
        chown.assert_called_once_with(layout, backend.PODMAN_USER)
        create_kwargs = fake_backend.create_container.call_args.kwargs
        self.assertNotIn("environment", create_kwargs)
        self.assertEqual(create_kwargs["mounts"], [{
            "source": "/managed/gate/config.yml", "target": "/config.yml",
        }])
        self.assertEqual(create_kwargs["ports"][0]["host_port"], 25581)
        self.assertEqual(create_kwargs["ports"][0]["container_port"], 25565)
        self.assertEqual(create_kwargs["restart_policy"], "unless-stopped")
        self.assertEqual(create_kwargs["networks"], ["game-platform"])
        fake_backend.create_network.assert_called_once_with(
            "game-platform", labels={"io.game-platform.managed": "true"},
        )
        operation = backend.OPERATIONS.snapshot(backend.GATE_DEPLOY_OPERATION_ID)
        self.assertEqual(operation["phase"], "complete")
        self.assertEqual(operation["progress"], 100)
        readiness.assert_called_once_with("127.0.0.1", 25581)

    def test_gate_running_container_is_not_ready_until_tcp_listener_works(self):
        fake_backend = Mock()
        fake_backend.status.return_value = WorkloadState("active", "running", "Běží")
        fake_backend.container_exists.return_value = True
        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(
                backend, "check_gate_tcp_ready",
                side_effect=ConnectionRefusedError("refused"),
            ),
        ):
            response = self.client.get("/proxy/status", **self.local_options())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["proxy"]["status"], "activating")
        self.assertFalse(response.json["proxy"]["ready"])

    def test_gate_lifecycle_is_pam_protected(self):
        fake_backend = Mock()
        fake_backend.container_exists.return_value = True
        fake_backend.restart.return_value = BackendResult(0)
        with patch.object(backend, "backend_for", return_value=fake_backend):
            response = self.client.post(
                "/proxy/restart", **self.local_options(self.admin_headers),
            )
            self.assertEqual(response.status_code, 403)
            response = self.client.post(
                "/proxy/restart", **self.local_options(self.pam_headers),
            )
            self.assertEqual(response.status_code, 200)
            response = self.client.post(
                "/proxy/restart", headers=self.pam_headers,
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
            self.assertEqual(response.status_code, 403)
        fake_backend.restart.assert_called_once()

    def test_failed_gate_readiness_removes_only_disposable_container(self):
        fake_backend = Mock()
        fake_backend.container_exists.return_value = False
        fake_backend.network_exists.return_value = True
        fake_backend.pull_image.return_value = BackendResult(0)
        fake_backend.create_container.return_value = BackendResult(0)
        fake_backend.start.return_value = BackendResult(0)
        layout = {
            "data_directory": "/managed/gate",
            "config_path": "/managed/gate/config.yml",
        }
        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "write_gate_layout", return_value=layout),
            patch.object(backend, "chown_gate_layout"),
            patch.object(
                backend, "wait_for_gate_ready",
                side_effect=RuntimeError("handshake selhal"),
            ),
        ):
            response = self.client.post(
                "/proxy/deploy", **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 500)
        fake_backend.remove_container.assert_called_once_with(
            {"backend": "podman", "runtime": {"container_name": "gate"}},
            force=True,
        )
        self.assertEqual(
            backend.OPERATIONS.snapshot(backend.GATE_DEPLOY_OPERATION_ID)["phase"],
            "failed",
        )

    def test_gate_lifecycle_requires_deployed_container(self):
        fake_backend = Mock()
        fake_backend.container_exists.return_value = False
        with patch.object(backend, "backend_for", return_value=fake_backend):
            response = self.client.post(
                "/proxy/start", **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 404)
        fake_backend.start.assert_not_called()

    def test_systemd_minecraft_prefers_configured_rcon(self):
        (self.forge_data / "server.properties").write_text(
            "server-port=25565\nenable-rcon=true\n"
            "rcon.port=25575\nrcon.password=secret\n",
            encoding="utf-8",
        )
        server = self.servers[0]
        server["data"] = {"directory": str(self.forge_data)}
        fake_backend = Mock()
        fake_backend.status.return_value = WorkloadState("active", "active", "Běží")
        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "local_server_addresses", return_value=["127.0.0.1"]),
            patch.object(
                backend, "cached_minecraft_player_status",
                return_value=({"online": 2, "max": 20}, False, None),
            ) as status_query,
        ):
            status = backend.game_server_status(server)
        self.assertEqual(status["players"]["online"], 2)
        status_query.assert_called_once_with(
            "forge", ["127.0.0.1"], 25565,
            rcon={"port": 25575, "password": "secret"},
        )

    def test_minecraft_refresh_uses_rcon_before_status_ping(self):
        cache_key = ("forge", 25565)
        with patch.object(
            backend, "query_server_rcon", return_value={"online": 2, "max": 20},
        ) as rcon_query, patch.object(
            backend, "query_server_status",
            return_value={
                "online": 1, "max": 10,
                "version": {"name": "1.20.1", "protocol": 763},
            },
        ) as status_query:
            backend._refresh_minecraft_player_status(
                cache_key, ["127.0.0.1"], 25565,
                {"port": 25575, "password": "secret"},
            )
        self.assertEqual(
            backend.MINECRAFT_STATUS_CACHE[cache_key]["counts"],
            {"online": 2, "max": 20},
        )
        rcon_query.assert_called_once_with(
            "127.0.0.1", 25575, "secret", timeout=3.0,
        )
        status_query.assert_called_once_with("127.0.0.1", 25565, timeout=3.0)
        self.assertEqual(
            backend.MINECRAFT_STATUS_CACHE[cache_key]["version"],
            {"name": "1.20.1", "protocol": 763},
        )

    def test_minecraft_status_refresh_is_non_blocking_and_deduplicated(self):
        fake_executor = Mock()
        with patch.object(backend, "MINECRAFT_STATUS_EXECUTOR", fake_executor):
            first = backend.cached_minecraft_player_status(
                "forge", ["192.0.2.66", "100.64.0.10"], 25565,
            )
            second = backend.cached_minecraft_player_status(
                "forge", ["192.0.2.66", "100.64.0.10"], 25565,
            )

        self.assertEqual(first, (None, True, None))
        self.assertEqual(second, (None, True, None))
        fake_executor.submit.assert_called_once()

        cache_key = ("forge", 25565)
        with patch.object(
            backend, "query_server_status",
            return_value={"online": 2, "max": 20},
        ) as query:
            backend._refresh_minecraft_player_status(
                cache_key, ["192.0.2.66", "100.64.0.10"], 25565,
            )

        self.assertEqual(
            backend.cached_minecraft_player_status(
                "forge", ["192.0.2.66", "100.64.0.10"], 25565,
            ),
            ({"online": 2, "max": 20}, False, None),
        )
        self.assertEqual(query.call_args_list, [
            call("100.64.0.10", 25565, timeout=3.0),
        ])
        self.assertEqual(
            backend.MINECRAFT_STATUS_CACHE[cache_key]["preferred_host"],
            "100.64.0.10",
        )

    def test_minecraft_probe_skips_unscoped_link_local_ipv6(self):
        self.assertEqual(backend._minecraft_probe_hosts([
            "192.0.2.66", "fe80::1234", "fd7a:115c:a1e0::1",
            "100.64.0.10", "127.0.0.1",
        ]), [
            "100.64.0.10", "192.0.2.66", "fd7a:115c:a1e0::1", "127.0.0.1",
        ])

    def test_minecraft_worker_always_releases_inflight_state(self):
        cache_key = ("forge", 25565)
        with backend.MINECRAFT_STATUS_LOCK:
            backend.MINECRAFT_STATUS_INFLIGHT.add(cache_key)
        with patch.object(
            backend, "_minecraft_probe_hosts", side_effect=RuntimeError("broken discovery"),
        ):
            backend._refresh_minecraft_player_status(
                cache_key, ["100.64.0.10"], 25565,
            )
        self.assertNotIn(cache_key, backend.MINECRAFT_STATUS_INFLIGHT)
        self.assertIn(
            "Interní chyba discovery: RuntimeError: broken discovery",
            backend.MINECRAFT_STATUS_CACHE[cache_key]["error"],
        )

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

    def test_local_pam_session_can_authorize_silent_action_without_admin_token(self):
        self.save_servers()
        fake_backend = Mock()
        fake_backend.start.return_value = BackendResult(0)
        with (
            patch.object(backend, "load_local_admin_token", return_value="host-only-secret"),
            patch.object(backend, "backend_for", return_value=fake_backend),
        ):
            response = self.client.post(
                "/servers/start", json={"id": "pixelmon"},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200)
        fake_backend.start.assert_called_once()

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

    def test_generic_network_endpoints_are_persisted_and_conflicts_rejected(self):
        satisfactory = {
            "id": "satisfactory", "name": "Satisfactory",
            "backend": "systemd", "kind": "generic",
            "service": "satisfactory.service",
            "endpoints": [
                {"name": "Game/API", "protocol": "tcp", "port": 7778},
                {"name": "Game/Query", "protocol": "udp", "port": 7778},
                {"name": "Reliable", "protocol": "tcp", "port": 8888},
            ],
        }
        response = self.client.put(
            "/servers/config", json={"servers": [satisfactory]},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["servers"][0]["endpoints"], satisfactory["endpoints"])
        self.assertEqual(response.json["servers"][0]["adapter"], "satisfactory")

        conflicting = {
            "id": "other", "name": "Other", "backend": "systemd",
            "kind": "generic", "service": "other.service",
            "endpoints": [{"name": "Game", "protocol": "tcp", "port": 7778}],
        }
        response = self.client.put(
            "/servers/config", json={"servers": [satisfactory, conflicting]},
            **self.local_options(self.pam_headers),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tcp:7778", response.json["message"])

    def test_satisfactory_adapter_supplies_effective_endpoints_with_sources(self):
        server = backend.normalize_game_server({
            "id": "satisfactory", "name": "Satisfactory",
            "backend": "systemd", "kind": "generic",
            "service": "satisfactory.service",
        })
        fake_backend = Mock()
        fake_backend.status.return_value = WorkloadState("active", "active", "Běží")
        discovered = [
            {
                "name": "Game/API", "protocol": "tcp", "port": 7778,
                "source": "systemd ExecStart",
            },
            {
                "name": "Game/Query", "protocol": "udp", "port": 7778,
                "source": "systemd ExecStart",
            },
            {
                "name": "Reliable messaging", "protocol": "tcp", "port": 8888,
                "source": "výchozí Satisfactory",
            },
        ]
        with (
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(
                backend, "discover_satisfactory_endpoints", return_value=discovered,
            ) as discover,
        ):
            status = backend.game_server_status(server)

        discover.assert_called_once_with("satisfactory.service")
        self.assertEqual(status["adapter"], "satisfactory")
        self.assertEqual(status["endpoints"], discovered)

    def test_minecraft_port_suggestion_reserves_generic_tcp_endpoints(self):
        servers = [{
            "id": "other", "kind": "generic", "backend": "systemd",
            "endpoints": [
                {"name": "TCP", "protocol": "tcp", "port": 25570},
                {"name": "UDP", "protocol": "udp", "port": 25571},
            ],
        }]
        gate_config = backend.default_gate_config()
        gate_config["listen"]["port"] = 25572
        with patch.object(backend, "host_port_available", return_value=True):
            port = backend.next_available_minecraft_port(
                servers=servers, gate_config=gate_config,
            )
        self.assertEqual(port, 25571)

    def test_systemd_and_podman_backups_require_local_pam(self):
        self.servers[0]["backend"] = "systemd"
        self.save_servers()
        with (
            patch.object(backend, "backend_for", return_value=Mock()),
            patch.object(
                backend, "create_workload_backup", return_value={"id": "forge-backup"},
            ) as create_systemd,
        ):
            response = self.client.post(
                "/servers/backup", json={"id": "forge"},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200)
        create_systemd.assert_called_once()

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

    def test_minecraft_install_restores_creates_registers_and_routes(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        backup_root = Path(self.temp_dir.name) / "backups"
        fake_backend = Mock()
        fake_backend.container_exists.side_effect = [False, True]
        fake_backend.network_exists.return_value = True
        fake_backend.pull_image.return_value = BackendResult(0, "sha256:image")
        fake_backend.create_container.return_value = BackendResult(0, "forge-podman")
        fake_backend.start.return_value = BackendResult(0)
        fake_backend.restart.return_value = BackendResult(0)
        restored_data = data_root / "forge-podman" / "data"
        restored_data.mkdir(parents=True)
        gate_config = backend.default_gate_config()
        gate_config["routes"] = [{
            "host": "*", "backend": {"host": "host.containers.internal", "port": 25565},
        }]
        request = {
            "id": "forge-podman", "name": "Forge Podman", "loader": "FORGE",
            "version": "1.20.1", "loader_version": "47.4.4", "memory_mb": 8192,
            "port": 25571, "hostname": "forge.mc.example",
            "accept_eula": True,
            "backup": {"source_id": "forge", "id": "backup-1"},
        }
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root)),
            patch.object(backend, "BACKUP_ROOT", str(backup_root)),
            patch.object(backend, "load_game_servers", return_value=[]),
            patch.object(backend, "save_game_servers") as save_servers,
            patch.object(backend, "check_host_port_available"),
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "restore_backup", return_value={
                "data_directory": str(restored_data), "sha256": "abc",
            }) as restore,
            patch.object(backend, "wait_for_minecraft_install_ready", return_value={
                "online": 0, "max": 20,
            }),
            patch.object(backend, "load_gate_config", return_value=gate_config),
            patch.object(backend, "save_gate_config") as save_gate,
            patch.object(backend, "write_gate_layout", return_value={
                "data_directory": "/managed/gate", "config_path": "/managed/gate/config.yml",
            }),
            patch.object(backend, "chown_gate_layout"),
        ):
            response = self.client.post(
                "/servers/minecraft/install", json=request,
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["server"]["management_mode"], "managed")
        self.assertEqual(response.json["server"]["connection"]["direct_port"], 25571)
        restore.assert_called_once()
        create_kwargs = fake_backend.create_container.call_args.kwargs
        self.assertEqual(create_kwargs["environment"]["TYPE"], "FORGE")
        self.assertEqual(create_kwargs["environment"]["FORGE_VERSION"], "47.4.4")
        self.assertEqual(create_kwargs["mounts"][0]["source"], str(restored_data))
        self.assertEqual(create_kwargs["ports"][0]["host_port"], 25571)
        saved_server = save_servers.call_args.args[0][0]
        self.assertEqual(saved_server["runtime"]["container_name"], "forge-podman")
        routed = save_gate.call_args.args[0]["routes"]
        self.assertEqual(routed[0], {
            "host": "forge.mc.example", "backend": {"host": "forge-podman", "port": 25565},
        })
        self.assertEqual(routed[-1]["host"], "*")

    def test_minecraft_install_suggests_first_unreserved_available_port(self):
        systemd_data = Path(self.temp_dir.name) / "systemd-minecraft"
        systemd_data.mkdir()
        (systemd_data / "server.properties").write_text(
            "server-port=25571\nenable-rcon=true\nrcon.port=25573\nrcon.password=secret\n",
            encoding="utf-8",
        )
        servers = [
            {
                "id": "managed", "kind": "minecraft", "backend": "podman",
                "connection": {"direct_port": 25570},
            },
            {
                "id": "systemd-mc", "kind": "minecraft", "backend": "systemd",
                "data": {"directory": str(systemd_data)},
            },
        ]
        gate_config = backend.default_gate_config()
        gate_config["listen"]["port"] = 25572
        with (
            patch.object(backend, "load_game_servers", return_value=servers),
            patch.object(backend, "load_gate_config", return_value=gate_config),
            patch.object(
                backend, "host_port_available", side_effect=lambda port: port != 25574,
            ) as available,
        ):
            response = self.client.get(
                "/servers/minecraft/install",
                **self.local_options(self.pam_headers),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["suggested_port"], 25575)
        self.assertEqual(response.json["range"]["start"], 25570)
        self.assertEqual(
            [item.args[0] for item in available.call_args_list], [25574, 25575],
        )

    def test_minecraft_install_resolves_and_installs_curseforge_server_pack(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        installed_data = data_root / "family-pack" / "data"
        fake_backend = Mock()
        fake_backend.container_exists.return_value = False
        fake_backend.network_exists.return_value = True
        fake_backend.pull_image.return_value = BackendResult(0, "sha256:image")
        fake_backend.create_container.return_value = BackendResult(0, "family-pack")
        fake_backend.start.return_value = BackendResult(0)
        provider = Mock()
        descriptor = {
            "project_id": 123, "file_id": 790,
            "download_url": "https://edge.forgecdn.net/files/1/server.zip",
        }
        provider.resolve_server_pack.return_value = descriptor
        request = {
            "id": "family-pack", "name": "Family Pack", "loader": "FABRIC",
            "version": "1.20.1", "memory_mb": 8192, "port": 25576,
            "hostname": "", "accept_eula": True,
            "curseforge": {"project_id": 123, "file_id": 789},
        }
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root)),
            patch.object(backend, "load_game_servers", return_value=[]),
            patch.object(backend, "save_game_servers"),
            patch.object(backend, "check_host_port_available"),
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "curseforge_catalog_provider", return_value=provider),
            patch.object(backend, "install_curseforge_server_pack", return_value={
                "data_directory": str(installed_data), "sha1": "abc",
            }) as install_pack,
            patch.object(backend, "wait_for_minecraft_install_ready", return_value={
                "online": 0, "max": 20,
            }),
            patch.object(backend, "load_gate_config", return_value=backend.default_gate_config()),
        ):
            response = self.client.post(
                "/servers/minecraft/install", json=request,
                **self.local_options(self.pam_headers),
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        provider.resolve_server_pack.assert_called_once_with(123, 789)
        install_pack.assert_called_once_with(
            descriptor, data_root=str(data_root), target_id="family-pack",
            owner_user=backend.PODMAN_USER,
        )
        self.assertEqual(
            fake_backend.create_container.call_args.kwargs["mounts"][0]["source"],
            str(installed_data),
        )

    def test_minecraft_install_port_suggestion_requires_local_pam(self):
        response = self.client.get(
            "/servers/minecraft/install",
            **self.local_options(self.admin_headers),
        )
        self.assertEqual(response.status_code, 403)

    def test_minecraft_install_requires_local_pam(self):
        payload = {
            "id": "new-server", "name": "New Server", "loader": "VANILLA",
            "version": "1.21.1", "port": 25572,
            "accept_eula": True,
        }
        self.assertEqual(self.client.post(
            "/servers/minecraft/install", json=payload,
            **self.local_options(self.admin_headers),
        ).status_code, 403)
        self.assertEqual(self.client.post(
            "/servers/minecraft/install", json=payload,
            headers=self.pam_headers, environ_base={"REMOTE_ADDR": "192.0.2.10"},
        ).status_code, 403)

    def test_managed_minecraft_delete_removes_runtime_routes_data_and_backups(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        backup_root = Path(self.temp_dir.name) / "backups"
        kcd_root = data_root / "kcd"
        kcd_data = kcd_root / "data"
        kcd_data.mkdir(parents=True)
        (kcd_data / "server.properties").write_text("server-port=25565\n")
        kcd_backups = backup_root / "kcd"
        kcd_backups.mkdir(parents=True)
        (kcd_backups / "old.tar.gz").write_bytes(b"backup")
        servers = [
            {
                "id": "kcd", "name": "KCD", "backend": "podman",
                "kind": "minecraft", "management_mode": "managed",
                "runtime": {"container_name": "kcd"},
                "data": {"directory": str(kcd_data)},
                "mods_dir": str(kcd_data / "mods"),
            },
            {
                "id": "vanilla", "name": "Vanilla", "backend": "podman",
                "kind": "minecraft", "management_mode": "managed",
                "runtime": {"container_name": "vanilla"},
                "data": {"directory": str(data_root / "vanilla" / "data")},
                "mods_dir": str(data_root / "vanilla" / "data" / "mods"),
            },
        ]
        backend.save_game_servers(servers)
        gate_config = backend.default_gate_config()
        gate_config["routes"] = [
            {"host": "kcd.mc.loc", "backend": {"host": "kcd", "port": 25565}},
            {"host": "*", "backend": {"host": "vanilla", "port": 25565}},
        ]
        backend.save_gate_config(gate_config)
        fake_backend = Mock()
        fake_backend.container_exists.side_effect = [True, True]
        fake_backend.restart.return_value = BackendResult(0)
        fake_backend.remove_container.return_value = BackendResult(0)
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root.resolve())),
            patch.object(backend, "BACKUP_ROOT", str(backup_root.resolve())),
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "write_gate_layout", return_value={
                "data_directory": "/managed/gate", "config_path": "/managed/gate/config.yml",
            }),
            patch.object(backend, "chown_gate_layout"),
        ):
            response = self.client.delete(
                "/servers/minecraft/delete",
                json={
                    "id": "kcd", "confirmation": "kcd",
                    "delete_data": True, "delete_backups": True,
                },
                **self.local_options(self.pam_headers),
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        fake_backend.remove_container.assert_called_once()
        removed_server = fake_backend.remove_container.call_args.args[0]
        self.assertEqual(removed_server["id"], "kcd")
        self.assertEqual(removed_server["runtime"]["container_name"], "kcd")
        self.assertTrue(fake_backend.remove_container.call_args.kwargs["force"])
        self.assertFalse(kcd_root.exists())
        self.assertFalse(kcd_backups.exists())
        self.assertEqual([item["id"] for item in backend.load_game_servers()], ["vanilla"])
        self.assertEqual(
            [route["host"] for route in backend.load_gate_config()["routes"]], ["*"],
        )
        operation = backend.OPERATIONS.snapshot("minecraft-delete-kcd")
        self.assertFalse(operation["running"])
        self.assertEqual(operation["phase"], "complete")
        self.assertEqual(operation["progress"], 100)

    def test_managed_minecraft_delete_refuses_default_gate_target(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        data_directory = data_root / "kcd" / "data"
        data_directory.mkdir(parents=True)
        server = {
            "id": "kcd", "name": "KCD", "backend": "podman",
            "kind": "minecraft", "management_mode": "managed",
            "runtime": {"container_name": "kcd"},
            "data": {"directory": str(data_directory)},
            "mods_dir": str(data_directory / "mods"),
        }
        backend.save_game_servers([server])
        gate_config = backend.default_gate_config()
        gate_config["routes"] = [{
            "host": "*", "backend": {"host": "kcd", "port": 25565},
        }]
        backend.save_gate_config(gate_config)
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root.resolve())),
            patch.object(backend, "backend_for") as backend_factory,
        ):
            response = self.client.delete(
                "/servers/minecraft/delete",
                json={"id": "kcd", "confirmation": "kcd", "delete_data": True},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("výchozí Gate trasy", response.json["message"])
        backend_factory.assert_not_called()
        self.assertTrue(data_directory.exists())

    def test_managed_minecraft_delete_rolls_back_gate_if_container_removal_fails(self):
        data_root = Path(self.temp_dir.name) / "managed-servers"
        data_directory = data_root / "kcd" / "data"
        data_directory.mkdir(parents=True)
        servers = [
            {
                "id": "kcd", "name": "KCD", "backend": "podman",
                "kind": "minecraft", "management_mode": "managed",
                "runtime": {"container_name": "kcd"},
                "data": {"directory": str(data_directory)},
                "mods_dir": str(data_directory / "mods"),
            },
            {
                "id": "vanilla", "name": "Vanilla", "backend": "podman",
                "kind": "minecraft", "management_mode": "managed",
                "runtime": {"container_name": "vanilla"},
                "data": {"directory": str(data_root / "vanilla" / "data")},
                "mods_dir": str(data_root / "vanilla" / "data" / "mods"),
            },
        ]
        backend.save_game_servers(servers)
        gate_config = backend.default_gate_config()
        gate_config["routes"] = [
            {"host": "kcd.mc.loc", "backend": {"host": "kcd", "port": 25565}},
            {"host": "*", "backend": {"host": "vanilla", "port": 25565}},
        ]
        backend.save_gate_config(gate_config)
        fake_backend = Mock()
        fake_backend.container_exists.side_effect = [True, True, True]
        fake_backend.restart.return_value = BackendResult(0)
        fake_backend.remove_container.return_value = BackendResult(1, error="rm failed")
        with (
            patch.object(backend, "PODMAN_DATA_ROOT", str(data_root.resolve())),
            patch.object(backend, "backend_for", return_value=fake_backend),
            patch.object(backend, "write_gate_layout", return_value={
                "data_directory": "/managed/gate", "config_path": "/managed/gate/config.yml",
            }),
            patch.object(backend, "chown_gate_layout"),
        ):
            response = self.client.delete(
                "/servers/minecraft/delete",
                json={"id": "kcd", "confirmation": "kcd", "delete_data": True},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            [route["host"] for route in backend.load_gate_config()["routes"]],
            ["kcd.mc.loc", "*"],
        )
        self.assertTrue(data_directory.exists())
        self.assertEqual([item["id"] for item in backend.load_game_servers()], ["kcd", "vanilla"])
        operation = backend.OPERATIONS.snapshot("minecraft-delete-kcd")
        self.assertFalse(operation["running"])
        self.assertEqual(operation["phase"], "failed")

    def test_managed_dns_config_is_pam_protected_and_derived_from_gate(self):
        gate_config = backend.default_gate_config()
        gate_config["routes"] = [
            {"host": "forge.mc.home.arpa", "backend": {"host": "forge", "port": 25565}},
            {"host": "*", "backend": {"host": "forge", "port": 25565}},
        ]
        backend.save_gate_config(gate_config)
        dns = {
            "provider": "builtin", "zone": "mc.home.arpa", "ttl": 60,
            "listen_addresses": ["127.0.0.1"],
            "answer_addresses": ["192.0.2.66"],
        }
        denied = self.client.put(
            "/dns/config", json={"dns": dns}, **self.local_options(),
        )
        self.assertEqual(denied.status_code, 403)

        with patch.object(backend, "systemctl_enable_now", return_value=(0, "", "")):
            saved = self.client.put(
                "/dns/config", json={"dns": dns}, **self.local_options(self.pam_headers),
            )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        with patch.object(backend, "systemctl_is_active", return_value=(0, "active", "")):
            status = self.client.get("/dns/status", **self.local_options())
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json["dns"]["records"], [{
            "name": "forge.mc.home.arpa", "address": "192.0.2.66",
        }])

    def test_local_pihole_provider_is_explicit_and_optional(self):
        gate_config = backend.default_gate_config()
        gate_config["routes"] = [{
            "host": "forge.mc.example", "backend": {"host": "forge", "port": 25565},
        }]
        backend.save_gate_config(gate_config)
        dns = {
            "provider": "pihole_local", "zone": "mc.example", "ttl": 60,
            "listen_addresses": [], "answer_addresses": ["192.0.2.66"],
        }
        with (
            patch.object(backend, "sync_pihole_records", return_value={"changed": True}) as sync,
            patch.object(backend, "systemctl_disable_now", return_value=(0, "", "")),
        ):
            response = self.client.put(
                "/dns/config", json={"dns": dns},
                **self.local_options(self.pam_headers),
            )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        sync.assert_called_once_with(
            [{"name": "forge.mc.example", "address": "192.0.2.66"}],
            backend.DNS_PIHOLE_STATE_PATH,
            enabled=True,
        )
        config = self.client.get(
            "/dns/config", **self.local_options(self.pam_headers),
        ).json
        self.assertIn(
            {"id": "pihole_local", "name": "Pi-hole na tomto hostiteli"},
            config["provider_catalog"],
        )


if __name__ == "__main__":
    unittest.main()
