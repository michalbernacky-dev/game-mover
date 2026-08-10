import tempfile
import unittest
from pathlib import Path

from game_mover_gate import (
    GateConfigError,
    default_gate_config,
    normalize_gate_config,
    render_gate_yaml,
    upsert_gate_route,
    write_gate_layout,
)


class GateConfigTest(unittest.TestCase):
    def test_default_is_tested_transparent_route_to_current_forge(self):
        config = normalize_gate_config(default_gate_config())

        self.assertEqual(config["listen"], {"host": "0.0.0.0", "port": 25581})
        self.assertEqual(config["container_name"], "gate")
        self.assertEqual(config["network"], "game-platform")
        self.assertIn("@sha256:", config["image"])
        self.assertEqual(config["routes"], [{
            "host": "*",
            "backend": {"host": "host.containers.internal", "port": 25565},
        }])

    def test_render_is_lite_and_contains_only_validated_routes(self):
        config = default_gate_config()
        config["routes"] = [
            {"host": "forge.mc.example", "backend": {"host": "forge", "port": 25565}},
            {"host": "*", "backend": {"host": "host.containers.internal", "port": 25565}},
        ]

        rendered = render_gate_yaml(config)

        self.assertIn("enabled: true", rendered)
        self.assertIn('host: "forge.mc.example"', rendered)
        self.assertIn('backend: "forge:25565"', rendered)
        self.assertNotIn("forwarding", rendered.lower())
        self.assertNotIn("secret", rendered.lower())

    def test_injection_duplicate_and_misordered_default_are_rejected(self):
        config = default_gate_config()
        config["routes"][0]["backend"]["host"] = 'forge"\nmalicious: true'
        with self.assertRaisesRegex(GateConfigError, "platný hostitel"):
            normalize_gate_config(config)

        config = default_gate_config()
        config["routes"].append(config["routes"][0].copy())
        with self.assertRaisesRegex(GateConfigError, "Duplicitní"):
            normalize_gate_config(config)

        config = default_gate_config()
        config["routes"].append({
            "host": "forge.mc.example", "backend": {"host": "forge", "port": 25565},
        })
        with self.assertRaisesRegex(GateConfigError, "poslední"):
            normalize_gate_config(config)

    def test_layout_is_atomic_and_contains_no_secret(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = write_gate_layout(default_gate_config(), temporary_directory)
            second = write_gate_layout(default_gate_config(), temporary_directory)

            self.assertEqual(first, second)
            config_path = Path(first["config_path"])
            self.assertTrue(config_path.is_file())
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("secret", config_path.read_text(encoding="utf-8").lower())

    def test_upsert_route_keeps_wildcard_last_and_replaces_existing(self):
        config = upsert_gate_route(
            default_gate_config(), "forge.mc.example", "forge-podman", 25565,
        )
        self.assertEqual([route["host"] for route in config["routes"]], ["forge.mc.example", "*"])
        config = upsert_gate_route(config, "forge.mc.example", "forge-new", 25566)
        self.assertEqual(len(config["routes"]), 2)
        self.assertEqual(config["routes"][0]["backend"], {"host": "forge-new", "port": 25566})


if __name__ == "__main__":
    unittest.main()
