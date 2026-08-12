import os
import tempfile
import unittest
from pathlib import Path

from game_mover_properties import (
    MinecraftPropertiesError,
    read_minecraft_properties,
    validate_minecraft_properties,
    write_minecraft_properties,
)


class MinecraftPropertiesTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_directory = Path(self.temporary_directory.name) / "data"
        self.data_directory.mkdir()
        self.path = self.data_directory / "server.properties"
        self.path.write_text(
            "# Zachovat komentář\n"
            "motd=Původní svět\n"
            "max-players=20\n"
            "custom-mod-option=keep-me\n"
            "motd=Duplicitní MOTD\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def settings(self):
        return {
            "motd": "Nový svět",
            "level-name": "family world",
            "gamemode": "creative",
            "difficulty": "normal",
            "max-players": 12,
            "white-list": True,
            "online-mode": True,
            "pvp": False,
            "allow-flight": True,
            "enable-command-block": False,
            "view-distance": 16,
            "simulation-distance": 12,
        }

    def test_read_uses_defaults_for_missing_editable_values(self):
        result = read_minecraft_properties(str(self.data_directory))
        self.assertEqual(result["settings"]["motd"], "Původní svět")
        self.assertEqual(result["settings"]["difficulty"], "easy")
        self.assertEqual(result["effective"]["custom-mod-option"], "keep-me")

    def test_write_is_validated_atomic_and_preserves_unmanaged_content(self):
        before = os.stat(self.path)
        result = write_minecraft_properties(str(self.data_directory), self.settings())
        content = self.path.read_text(encoding="utf-8")
        after = os.stat(self.path)

        self.assertIn("# Zachovat komentář", content)
        self.assertIn("custom-mod-option=keep-me", content)
        self.assertEqual(content.count("motd=Nový svět"), 2)
        self.assertIn("level-name=family world", content)
        self.assertEqual(result["settings"]["max-players"], "12")
        self.assertIn("motd", result["changed"])
        self.assertEqual(before.st_mode & 0o777, after.st_mode & 0o777)

    def test_rejects_unknown_or_unsafe_values(self):
        settings = self.settings()
        settings["level-name"] = "../outside"
        with self.assertRaises(MinecraftPropertiesError):
            validate_minecraft_properties(settings)
        settings = self.settings()
        settings["unknown"] = "value"
        with self.assertRaises(MinecraftPropertiesError):
            validate_minecraft_properties(settings)
        settings = self.settings()
        settings["motd"] = "line one\nline two"
        with self.assertRaises(MinecraftPropertiesError):
            validate_minecraft_properties(settings)

    def test_rejects_symlinked_properties_file(self):
        self.path.unlink()
        target = Path(self.temporary_directory.name) / "outside.properties"
        target.write_text("motd=outside\n", encoding="utf-8")
        self.path.symlink_to(target)
        with self.assertRaises(MinecraftPropertiesError):
            read_minecraft_properties(str(self.data_directory))


if __name__ == "__main__":
    unittest.main()
