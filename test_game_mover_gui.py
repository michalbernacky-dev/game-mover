import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QTabBar

import game_mover


SAMPLE_SERVER = {
    "id": "mc-test",
    "name": "Minecraft Test",
    "kind": "minecraft",
    "status": "active",
    "message": "Běží",
    "runtime_label": "podman: mc-test",
    "connection": {"direct_port": 25570},
    "players": {"online": 1, "max": 20, "known": 2},
    "permissions": {
        "start": "silent",
        "stop": "silent",
        "restart": "silent",
        "backup": "pam",
    },
    "backup_supported": True,
    "has_mods": True,
}


class ServerManagementGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        no_op_methods = (
            "refresh_server_statuses",
            "refresh_cache_status",
            "refresh_dnsmasq_status",
            "refresh_game_lists",
            "update_disk_bars",
            "load_server_mods",
        )
        self.patches = [
            patch.object(game_mover.GameMover, method, lambda self, *args: None)
            for method in no_op_methods
        ]
        self.patches.append(patch.object(game_mover, "load_client_config", return_value={}))
        for active_patch in self.patches:
            active_patch.start()
        self.window = game_mover.GameMover()
        self.window.last_server_statuses = [dict(SAMPLE_SERVER)]
        self.window.render_server_cards(self.window.last_server_statuses)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_opens_only_one_closable_management_tab_per_server(self):
        fixed_count = self.window.fixed_tab_count
        self.window.open_server_management("mc-test")
        self.window.open_server_management("mc-test")

        self.assertEqual(self.window.tabs.count(), fixed_count + 1)
        self.assertEqual(self.window.tabs.currentWidget().property("server_id"), "mc-test")
        self.assertEqual(self.window.tabs.tabText(fixed_count), "Správa: Minecraft Test")
        for index in range(fixed_count):
            self.assertIsNone(
                self.window.tabs.tabBar().tabButton(index, QTabBar.RightSide)
            )

        self.window.close_server_management_tab(fixed_count)
        self.assertEqual(self.window.tabs.count(), fixed_count)
        self.assertNotIn("mc-test", self.window.server_management_pages)

    def test_management_page_contains_existing_server_capabilities(self):
        self.window.open_server_management("mc-test")
        entry = self.window.server_management_pages["mc-test"]

        self.assertEqual(entry["status"].text(), "Běží")
        self.assertEqual(entry["players"].text(), "1 / 20 online · již viděno 2")
        self.assertEqual(entry["sections"].count(), 4)
        self.assertEqual(
            [entry["sections"].tabText(index) for index in range(4)],
            ["Přehled", "Nastavení", "Zálohy", "Mody"],
        )
        self.assertEqual(set(entry["lifecycle"]), {"start", "stop", "restart"})
        self.assertIn("properties_fields", entry)


if __name__ == "__main__":
    unittest.main()
