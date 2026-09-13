import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from game_mover_game_inventory import (
    game_slug,
    scan_installed_games,
    scan_user_installed_games,
)


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

    def test_shared_steam_payload_needs_a_player_manifest(self):
        shared_game = self.shared / "steam/Unregistered Shared Game"
        shared_game.mkdir(parents=True)
        (shared_game / "large.bin").write_bytes(b"x" * 32)

        items = scan_user_installed_games(
            str(self.user), "alice", str(self.shared), small_install_bytes=1,
        )
        item = {item["id"]: item for item in items}["unregistered-shared-game"]
        self.assertTrue(item["possible_residue"])

        steamapps = self.user / ".local/share/Steam/steamapps"
        common = steamapps / "common"
        common.mkdir(parents=True)
        (common / "Unregistered Shared Game").symlink_to(
            shared_game, target_is_directory=True,
        )
        (steamapps / "appmanifest_123.acf").write_text(
            '"AppState" { "appid" "123" "name" "Unregistered Shared Game" '
            '"installdir" "Unregistered Shared Game" }',
        )
        items = scan_user_installed_games(
            str(self.user), "alice", str(self.shared), small_install_bytes=1,
        )
        item = {item["id"]: item for item in items}["unregistered-shared-game"]
        self.assertFalse(item["possible_residue"])
        self.assertEqual(item["users"], ["alice"])

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

    def test_scans_heroic_ea_payload_and_flags_staged_download(self):
        config = self.user / ".config/heroic"
        (config / "sideload_apps").mkdir(parents=True)
        (config / "GamesConfig").mkdir()
        prefix = self.user / "Games/Heroic/Prefixes/default"
        games = prefix / "drive_c/Program Files/EA Games"
        launcher = prefix / "drive_c/Program Files/Electronic Arts/EA Desktop"
        launcher.mkdir(parents=True)
        complete = games / "Complete Game"
        incomplete = games / "Example Game II"
        complete.mkdir(parents=True)
        incomplete.mkdir()
        (complete / "large.bin").write_bytes(b"x" * 32)
        (incomplete / "large.bin").write_bytes(b"x" * 32)
        install_data = (
            prefix / "drive_c/ProgramData/EA Desktop/InstallData/Example Game II"
        )
        install_data.mkdir(parents=True)
        (install_data / "download.eazstate").write_text("pending")
        (config / "sideload_apps/library.json").write_text(json.dumps({
            "games": [{"title": "EA App", "app_name": "ea-app-local"}],
        }))
        (config / "GamesConfig/ea-app-local.json").write_text(json.dumps({
            "ea-app-local": {"winePrefix": str(prefix)},
        }))

        items = scan_installed_games(
            str(self.homes), str(self.shared), small_install_bytes=1,
        )
        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id["complete-game"]["platforms"], ["ea"])
        self.assertFalse(by_id["complete-game"]["possible_residue"])
        self.assertTrue(by_id["example-game-ii"]["possible_residue"])

    def test_battlefront_ea_payload_uses_supported_ea_epic_metadata(self):
        battlefront = self.shared / "EA/STAR WARS Battlefront II"
        battlefront.mkdir(parents=True)

        items = scan_installed_games(str(self.homes), str(self.shared))
        item = {item["id"]: item for item in items}[
            "star-wars-battlefront-ii-celebration-edition"
        ]
        self.assertEqual(
            item["name"], "STAR WARS Battlefront II: Celebration Edition",
        )
        self.assertEqual(item["launcher_type"], "ea_epic")
        self.assertEqual(item["epic_app_name"], "MtMassive")
        self.assertEqual(item["ea_prefix"], "EA_app")

    def test_current_player_scan_finds_private_ea_epic_payload(self):
        config = self.user / ".config/heroic"
        (config / "sideload_apps").mkdir(parents=True)
        (config / "GamesConfig").mkdir()
        prefix = self.user / "Games/Heroic/Prefixes/EA_app"
        games = prefix / "drive_c/Program Files/EA Games"
        launcher = prefix / "drive_c/Program Files/Electronic Arts/EA Desktop"
        launcher.mkdir(parents=True)
        battlefront = games / "STAR WARS Battlefront II"
        battlefront.mkdir(parents=True)
        (battlefront / "game.bin").write_bytes(b"x")
        (config / "sideload_apps/library.json").write_text(json.dumps({
            "games": [{"title": "EA App", "app_name": "ea-app-local"}],
        }))
        (config / "GamesConfig/ea-app-local.json").write_text(json.dumps({
            "ea-app-local": {"winePrefix": str(prefix)},
        }))

        items = scan_user_installed_games(
            str(self.user), "alice", str(self.shared),
        )
        item = {item["id"]: item for item in items}[
            "star-wars-battlefront-ii-celebration-edition"
        ]
        self.assertEqual(item["users"], ["alice"])
        self.assertEqual(item["epic_app_name"], "MtMassive")
        self.assertFalse(item["possible_residue"])

    def test_slug_is_stable_and_ascii(self):
        self.assertEqual(game_slug("Zaklínač® 3"), "zaklinac-3")


if __name__ == "__main__":
    unittest.main()
