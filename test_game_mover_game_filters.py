import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from game_mover_game_filters import is_excluded_game, is_possible_game_residue


class GameFiltersTest(unittest.TestCase):
    def test_shared_mover_filters_ignore_support_payloads(self):
        self.assertTrue(is_excluded_game("steam", "SteamLinuxRuntime_sniper"))
        self.assertTrue(is_excluded_game("steam", "steamlinuxruntime_soldier"))
        self.assertTrue(is_excluded_game("steam", "Proton 9.0"))
        self.assertTrue(is_excluded_game("steam", "GE-Proton10-28"))
        self.assertTrue(is_excluded_game("steam", "Steam Controller Configs"))
        self.assertTrue(is_excluded_game("steam", "Steamworks Shared"))
        self.assertTrue(is_excluded_game("steam", "Half-Life Dedicated Server"))
        self.assertFalse(is_excluded_game("steam", "Half-Life 2"))
        self.assertFalse(is_excluded_game("epic", "Grand Theft Auto V Enhanced"))

    def test_steam_manifest_distinguishes_install_from_residue(self):
        with TemporaryDirectory() as temporary:
            steamapps = Path(temporary, "steamapps")
            common = steamapps / "common"
            installed = common / "Small Real Game"
            residue = common / "Old Game Saves"
            installed.mkdir(parents=True)
            residue.mkdir()
            Path(steamapps, "appmanifest_42.acf").write_text(
                '"AppState"\n{\n\t"appid" "42"\n'
                '\t"installdir" "Small Real Game"\n}\n',
                encoding="utf-8",
            )

            self.assertFalse(is_possible_game_residue("steam", installed))
            self.assertTrue(is_possible_game_residue("steam", residue))

    def test_complete_manual_gog_install_is_not_hidden_by_size(self):
        with TemporaryDirectory() as temporary:
            game = Path(temporary, "Tiny GOG Game")
            game.mkdir()
            Path(game, "start.sh").touch()
            Path(game, "uninstall-Tiny GOG Game.sh").touch()

            self.assertFalse(is_possible_game_residue("gog", game))

    def test_unknown_gog_directory_is_possible_residue(self):
        with TemporaryDirectory() as temporary:
            game = Path(temporary, "Old Saves")
            game.mkdir()
            Path(game, "save.dat").touch()

            self.assertTrue(is_possible_game_residue("gog", game))


if __name__ == "__main__":
    unittest.main()
