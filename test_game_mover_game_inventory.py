import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from game_mover_game_inventory import game_slug, scan_installed_games


class InstalledGameInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.homes = self.root / "home"
        self.shared = self.root / "Games"
        self.user = self.homes / "alice"
        self.user.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scans_shared_steam_symlinks_heroic_curseforge_and_lutris(self):
        forest = self.shared / "steam" / "The Forest"
        forest.mkdir(parents=True)
        (forest / "game.bin").write_bytes(b"x" * 128)
        common = self.user / ".local/share/Steam/steamapps/common"
        common.mkdir(parents=True)
        (common / "The Forest").symlink_to(forest, target_is_directory=True)
        runtime = self.shared / "steam" / "SteamLinuxRuntime_sniper"
        runtime.mkdir(parents=True)
        (runtime / "runtime.bin").write_bytes(b"xx")

        gta = self.shared / "Heroic" / "GTAVEnhanced"
        gta.mkdir(parents=True)
        (gta / "game.bin").write_bytes(b"xx")
        heroic = self.user / ".config/heroic/legendaryConfig/legendary"
        heroic.mkdir(parents=True)
        (heroic / "installed.json").write_text(json.dumps({"gta": {
            "title": "Grand Theft Auto V Enhanced", "install_path": str(gta),
            "app_name": "gta-epic",
        }}))

        pack = self.user / "Documents/curseforge/minecraft/Instances/Prominence II: Hasturian Era"
        pack.mkdir(parents=True)
        (pack / "minecraftinstance.json").write_text(json.dumps({
            "name": "Prominence II: Hasturian Era",
        }))

        lutris = self.user / ".local/share/lutris"
        lutris.mkdir(parents=True)
        connection = sqlite3.connect(lutris / "pga.db")
        connection.execute(
            "CREATE TABLE games (name, slug, runner, directory, service, service_id, installed)"
        )
        connection.execute(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, 1)",
            ("POSTAL 2", "postal-2", "wine", str(self.shared / "gog" / "Postal 2"), "gog", "120"),
        )
        connection.execute(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, 1)",
            ("Ubisoft Connect", "ubisoft-connect", "wine", str(self.user / "Games/ubisoft"), None, None),
        )
        connection.commit()
        connection.close()
        (self.shared / "gog" / "Postal 2").mkdir(parents=True)
        (self.shared / "gog" / "Postal 2" / "game.bin").write_bytes(b"xx")
        rockstar_root = self.user / "Games/rockstar-games-launcher/drive_c/Program Files/Rockstar Games"
        (rockstar_root / "Red Dead Redemption 2").mkdir(parents=True)
        (rockstar_root / "Red Dead Redemption 2" / "game.bin").write_bytes(b"xx")
        (rockstar_root / "Launcher").mkdir()

        items = scan_installed_games(str(self.homes), str(self.shared), small_install_bytes=1)
        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id["the-forest"]["users"], ["alice"])
        self.assertEqual(by_id["the-forest"]["platforms"], ["steam"])
        self.assertIn("gta-v-enhanced", by_id["grand-theft-auto-v-enhanced"]["knowledge_aliases"])
        self.assertIn("prominence-2-hasturian-era", by_id["prominence-ii-hasturian-era"]["knowledge_aliases"])
        self.assertIn("postal-2", by_id)
        self.assertIn("red-dead-redemption-2", by_id)
        self.assertNotIn("ubisoft-connect", by_id)
        self.assertNotIn("launcher", by_id)
        self.assertNotIn("steamlinuxruntime-sniper", by_id)

    def test_marks_unverified_small_directories_as_possible_residue(self):
        tiny = self.user / "Games/Epic/Tiny Remnant"
        tiny.mkdir(parents=True)
        (tiny / "marker").write_bytes(b"x")
        items = scan_installed_games(str(self.homes), str(self.shared), small_install_bytes=8192)
        item = {item["id"]: item for item in items}["tiny-remnant"]
        self.assertTrue(item["possible_residue"])

    def test_reads_real_install_path_from_lutris_yaml_without_size_cutoff(self):
        executable = self.user / "dosgames/Agent/AGENT.EXE"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"game")
        lutris = self.user / ".local/share/lutris"
        lutris.mkdir(parents=True)
        connection = sqlite3.connect(lutris / "pga.db")
        connection.execute(
            "CREATE TABLE games (name, slug, runner, directory, service, service_id, installed, configpath)"
        )
        connection.execute(
            "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            ("Agent Mlčňák", "agent-mlinak", "dosbox", "", None, None, "agent-test"),
        )
        connection.commit()
        connection.close()
        configs = self.user / ".config/lutris/games"
        configs.mkdir(parents=True)
        (configs / "agent-test.yml").write_text(
            f"game:\n  main_file: {executable}\n", encoding="utf-8"
        )

        items = scan_installed_games(str(self.homes), str(self.shared))
        agent = {item["id"]: item for item in items}["agent-mlcnak"]
        self.assertEqual(agent["paths"], [str(executable.parent)])
        self.assertFalse(agent["possible_residue"])
        self.assertIn("agent-mlinak", agent["knowledge_aliases"])

    def test_slug_is_stable_and_ascii(self):
        self.assertEqual(game_slug("Zaklínač® 3"), "zaklinac-3")


if __name__ == "__main__":
    unittest.main()
