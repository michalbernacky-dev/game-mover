import unittest

from game_mover_game_filters import is_excluded_game


class GameFiltersTest(unittest.TestCase):
    def test_shared_mover_filters_ignore_support_payloads(self):
        self.assertTrue(is_excluded_game("steam", "SteamLinuxRuntime_sniper"))
        self.assertTrue(is_excluded_game("steam", "steamlinuxruntime_soldier"))
        self.assertTrue(is_excluded_game("steam", "Proton 9.0"))
        self.assertTrue(is_excluded_game("steam", "Half-Life Dedicated Server"))
        self.assertFalse(is_excluded_game("steam", "Half-Life 2"))
        self.assertFalse(is_excluded_game("epic", "Grand Theft Auto V Enhanced"))


if __name__ == "__main__":
    unittest.main()
