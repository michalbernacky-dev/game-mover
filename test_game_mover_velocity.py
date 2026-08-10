import tempfile
import unittest
from pathlib import Path

from game_mover_velocity import (
    VelocityConfigError,
    default_velocity_config,
    normalize_velocity_config,
    render_velocity_toml,
    write_velocity_layout,
)


class VelocityConfigTest(unittest.TestCase):
    def test_default_is_safe_staging_route_to_current_forge(self):
        config = normalize_velocity_config(default_velocity_config())

        self.assertEqual(config["listen"], {"host": "0.0.0.0", "port": 25580})
        self.assertEqual(config["forwarding_mode"], "none")
        self.assertEqual(config["network"], "game-platform")
        self.assertEqual(config["velocity_version"], "3.5.1")
        self.assertEqual(config["velocity_build_id"], "615")
        self.assertEqual(config["backends"], [{
            "id": "forge", "host": "host.containers.internal", "port": 25565,
        }])
        self.assertEqual(config["plugins"][0]["project"], "ambassador")

    def test_render_contains_only_validated_routes_and_forge_settings(self):
        config = default_velocity_config()
        config["forced_hosts"] = {"forge.example": ["forge"]}

        rendered = render_velocity_toml(config)

        self.assertIn('bind = "0.0.0.0:25580"', rendered)
        self.assertIn('forge = "host.containers.internal:25565"', rendered)
        self.assertIn('"forge.example" = ["forge"]', rendered)
        self.assertIn('announce-forge = true', rendered)
        self.assertIn('ping-passthrough = "mods"', rendered)
        self.assertIn('read-timeout = 120000', rendered)

    def test_unknown_backend_and_toml_injection_are_rejected(self):
        config = default_velocity_config()
        config["try"] = ["missing"]
        with self.assertRaisesRegex(VelocityConfigError, "neznámý backend"):
            normalize_velocity_config(config)

        config = default_velocity_config()
        config["backends"][0]["host"] = 'host"\nmalicious = true'
        with self.assertRaisesRegex(VelocityConfigError, "platný hostitel"):
            normalize_velocity_config(config)

    def test_layout_keeps_existing_forwarding_secret(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = write_velocity_layout(default_velocity_config(), temporary_directory)
            secret_path = Path(first["secret_path"])
            original_secret = secret_path.read_text(encoding="utf-8")

            second = write_velocity_layout(default_velocity_config(), temporary_directory)

            self.assertEqual(first, second)
            self.assertEqual(secret_path.read_text(encoding="utf-8"), original_secret)
            self.assertTrue(Path(first["config_path"]).is_file())
            self.assertEqual(secret_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
