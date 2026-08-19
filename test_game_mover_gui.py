import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QTabBar

import game_mover


SAMPLE_SERVER = {
    "id": "mc-test",
    "name": "Minecraft Test",
    "kind": "minecraft",
    "status": "active",
    "message": "Běží",
    "runtime_label": "podman: mc-test",
    "connection": {"direct_port": 25570},
    "gate_connection": {"host": "vanilla.mc.example", "port": 25581, "route_host": "vanilla.mc.example"},
    "minecraft_version": {"name": "1.21.8", "protocol": 772},
    "players": {"online": 1, "max": 20, "known": 2},
    "permissions": {
        "start": "silent",
        "stop": "silent",
        "restart": "silent",
        "backup": "pam",
    },
    "backup_supported": True,
    "deletion_supported": True,
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
            "refresh_launcher_statuses",
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

    def test_launcher_tab_renders_outdated_and_missing_items(self):
        self.window.on_launcher_statuses_loaded({
            "update_policy": "pam",
            "launchers": [
                {
                    "id": "heroic", "name": "Heroic Games Launcher", "icon": "heroic",
                    "source": "Oficiální GitHub release", "installed": True,
                    "installed_version": "2.22.0", "latest_version": "2.22.1",
                    "update_available": True, "update_supported": True, "error": "",
                },
                {
                    "id": "lutris", "name": "Lutris", "icon": "net.lutris.Lutris",
                    "source": "Systémový RPM repozitář", "installed": False,
                    "installed_version": "", "latest_version": "",
                    "update_available": False, "update_supported": False, "error": "",
                },
            ],
        })
        self.assertEqual(self.window.tabs.tabText(self.window.launchers_tab_index), "Launchery (1)")
        self.assertIn("2.22.0 → 2.22.1", self.window.launcher_cards["heroic"]["status"].text())
        self.assertFalse(self.window.launcher_cards["lutris"]["card"].isEnabled())

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

    def test_servers_tab_has_stable_widget_reference(self):
        self.window.tabs.setCurrentIndex(0)
        self.window.tabs.setCurrentWidget(self.window.servers_tab)

        self.assertIs(self.window.tabs.currentWidget(), self.window.servers_tab)
        self.assertEqual(
            self.window.tabs.tabText(self.window.tabs.indexOf(self.window.servers_tab)),
            "Servery",
        )

    def test_management_page_contains_existing_server_capabilities(self):
        self.window.open_server_management("mc-test")
        entry = self.window.server_management_pages["mc-test"]

        self.assertEqual(entry["status"].text(), "Běží")
        self.assertEqual(entry["players"].text(), "1 / 20 online · již viděno 2")
        self.assertEqual(entry["minecraft_version"].text(), "1.21.8")
        self.assertEqual(entry["connection"].text(), "vanilla.mc.example:25581")
        self.assertEqual(entry["direct_connection"].text(), "127.0.0.1:25570")
        self.assertTrue(entry["direct_connection"].isVisibleTo(entry["page"]))
        self.assertEqual(entry["sections"].count(), 7)
        self.assertEqual(
            [entry["sections"].tabText(index) for index in range(7)],
            ["Přehled", "Logy", "Nastavení", "Hráči", "Whitelist", "Zálohy", "Mody"],
        )
        self.assertEqual(set(entry["lifecycle"]), {"start", "stop", "restart"})
        self.assertIn("properties_fields", entry)
        self.assertIn("logs_output", entry)
        self.assertIn("operators_table", entry)
        self.assertIn("whitelist_table", entry)
        self.assertTrue(entry["delete_server"].isVisibleTo(entry["page"]))

    def test_direct_connection_is_recommended_when_gate_route_is_absent(self):
        server = dict(SAMPLE_SERVER)
        server.pop("gate_connection", None)
        self.window.last_server_statuses = [server]
        self.window.open_server_management("mc-test")
        entry = self.window.server_management_pages["mc-test"]

        self.assertEqual(entry["connection"].text(), "127.0.0.1:25570")
        self.assertFalse(entry["direct_connection"].isVisibleTo(entry["page"]))
        self.assertEqual(
            self.window.server_recommended_connection_text(server), "127.0.0.1:25570",
        )

    def test_generic_server_uses_and_displays_registered_endpoints(self):
        server = {
            "id": "satisfactory", "name": "Satisfactory", "kind": "generic",
            "status": "inactive", "message": "Neběží",
            "runtime_label": "systemd: satisfactory.service",
            "permissions": {},
            "endpoints": [
                {"name": "Game/API", "protocol": "tcp", "port": 7778},
                {"name": "Game/Query", "protocol": "udp", "port": 7778},
                {"name": "Reliable", "protocol": "tcp", "port": 8888},
            ],
        }
        self.window.last_server_statuses = [server]
        self.window.render_server_cards([server])
        self.window.open_server_management("satisfactory")
        entry = self.window.server_management_pages["satisfactory"]

        self.assertEqual(entry["connection"].text(), "127.0.0.1:7778")
        self.assertEqual(entry["edit_endpoints"].text(), "Technické síťové porty…")

    def test_endpoint_editor_renders_discovered_rows_without_registry_values(self):
        server = {
            "id": "satisfactory", "name": "Satisfactory", "kind": "generic",
            "status": "active", "message": "Běží",
            "runtime_label": "systemd: satisfactory.service",
            "permissions": {},
            "endpoints": [
                {
                    "name": "Game/API", "protocol": "tcp", "port": 7778,
                    "source": "systemd ExecStart",
                },
                {
                    "name": "Game/Query", "protocol": "udp", "port": 7778,
                    "source": "systemd ExecStart",
                },
            ],
        }
        response = type("Response", (), {
            "status_code": 200,
            "json": lambda self: {"servers": [{
                "id": "satisfactory", "name": "Satisfactory",
                "backend": "systemd", "kind": "generic",
                "service": "satisfactory.service",
            }]},
        })()
        self.window.last_server_statuses = [server]
        self.window.app_mode = "server"
        with (
            patch.object(self.window, "local_operation_headers", return_value={"X": "ok"}),
            patch.object(game_mover.requests, "get", return_value=response),
            patch.object(game_mover.QDialog, "exec_", return_value=QDialog.Rejected),
        ):
            self.window.edit_server_endpoints("satisfactory")

    def test_server_card_prefers_gate_and_does_not_show_direct_backend(self):
        layout = self.window.server_card_layouts["mc-test"]
        labels = [
            layout.itemAt(index).widget().text()
            for index in range(layout.count())
            if layout.itemAt(index).widget() is not None
        ]

        self.assertIn("Připojení: vanilla.mc.example:25581", labels)
        self.assertNotIn("Připojení: 127.0.0.1:25570", labels)
        self.assertFalse(any(label.startswith("Síť:") for label in labels))

    def test_delete_progress_is_inline_and_success_has_no_modal_popup(self):
        self.window.open_server_management("mc-test")
        entry = self.window.server_management_pages["mc-test"]
        server = {
            **SAMPLE_SERVER,
            "operation": {
                "kind": "minecraft-delete", "running": True,
                "message": "Odstraňuji Podman container", "progress": 40,
            },
        }
        self.window.update_server_management_page(server)
        self.assertEqual(entry["delete_status"].text(), "Odstraňuji Podman container")
        self.assertEqual(entry["delete_progress"].value(), 40)
        self.assertTrue(entry["delete_progress"].isVisibleTo(entry["page"]))

        with patch.object(game_mover.QMessageBox, "information") as information:
            self.window.on_server_delete_completed({"id": "mc-test", "message": "Hotovo"})
        information.assert_not_called()
        self.assertNotIn("mc-test", self.window.server_management_pages)

    def test_properties_unauthorized_response_expires_host_pam_session(self):
        self.window.open_server_management("mc-test")
        self.window.timekpr_token = "temporary-host-token"
        with patch.object(self.window, "update_server_mode_ui") as update_ui:
            self.window.on_server_properties_completed({
                "server_id": "mc-test", "error": "Unauthorized",
            })
        self.assertEqual(self.window.timekpr_token, "")
        self.assertIn(
            "PAM relace už není platná",
            self.window.server_management_pages["mc-test"]["properties_status"].text(),
        )
        update_ui.assert_called_once()

    def test_modpack_catalog_renders_projects_and_server_pack_install_action(self):
        self.window.open_modpack_catalog(version="1.20.1", loader="fabric")
        payload = {
            "request_kind": "search",
            "items": [{
                "id": 123,
                "name": "Fabric Family Pack",
                "slug": "fabric-family-pack",
                "summary": "A friendly Fabric modpack.",
                "authors": ["Builder"],
                "download_count": 12345,
                "date_modified": "2026-08-15T10:00:00Z",
                "website_url": "https://www.curseforge.com/minecraft/modpacks/family",
            }],
            "pagination": {
                "index": 0, "pageSize": 20, "resultCount": 1, "totalCount": 1,
            },
        }
        self.window.on_modpack_catalog_loaded(payload)

        self.assertEqual(self.window.modpack_results.rowCount(), 1)
        self.assertEqual(self.window.modpack_results.item(0, 0).text(), "Fabric Family Pack")
        self.assertEqual(self.window.modpack_page_label.text(), "1–1 z 1")
        with patch.object(self.window, "start_modpack_catalog_request") as load_files:
            self.window.modpack_results.selectRow(0)
            QApplication.processEvents()
        self.assertIn("A friendly Fabric modpack", self.window.modpack_detail.text())
        self.assertTrue(self.window.modpack_website.isEnabled())
        load_files.assert_called_once()

        self.window.render_modpack_files({
            "project_id": 123,
            "items": [{
                "id": 455,
                "display_name": "Fabric Family Pack 1.0",
                "game_versions": ["1.20.1", "Fabric"],
                "release_type": 1,
                "file_length": 1048576,
                "is_server_pack": False,
                "server_pack_file_id": 456,
            }],
        })
        self.assertEqual(self.window.modpack_files.rowCount(), 1)
        self.assertEqual(self.window.modpack_files.item(0, 4).text(), "Ano")
        self.window.modpack_files.selectRow(0)
        QApplication.processEvents()
        self.assertFalse(self.window.modpack_install_button.isEnabled())
        self.window.app_mode = "server"
        with patch.object(
            self.window, "local_operation_headers", return_value={"X-Test": "allowed"},
        ):
            self.window.update_modpack_install_availability()
        self.assertTrue(self.window.modpack_install_button.isEnabled())
        with patch.object(self.window, "open_minecraft_installer") as open_installer:
            self.window.install_selected_server_pack()
        source = open_installer.call_args.kwargs["curseforge"]
        self.assertEqual(source["project_id"], 123)
        self.assertEqual(source["file_id"], 455)
        self.assertEqual(source["loader"], "FABRIC")
        self.assertEqual(source["version"], "1.20.1")

    def test_modpack_catalog_shows_missing_api_key_with_install_disabled(self):
        self.window.open_modpack_catalog()
        self.window.on_modpack_catalog_loaded({
            "request_kind": "status", "provider": "curseforge",
            "configured": False, "server_pack_install": True,
            "client_install": False, "cached": False,
        })
        self.assertIn("není nakonfigurovaný", self.window.modpack_status_label.text())
        self.assertFalse(self.window.modpack_install_button.isEnabled())

    def test_modpack_install_uses_exact_response_filter_for_legacy_loader_metadata(self):
        self.window.open_modpack_catalog(version="1.16.5", loader="forge")
        self.window.selected_modpack = {
            "id": 454031,
            "name": "Crucial 2 - The Refresh Update",
            "slug": "crucial-2",
        }
        self.window.render_modpack_files({
            "project_id": 454031,
            "filters": {"version": "1.16.5", "loader": "forge"},
            "items": [{
                "id": 3497749,
                "display_name": "Crucial2-1.3.6.zip",
                "game_versions": ["1.16.5"],
                "release_type": 1,
                "file_length": 1024,
                "is_server_pack": False,
                "server_pack_file_id": 3497751,
            }],
        })
        self.window.modpack_files.selectRow(0)
        QApplication.processEvents()

        with patch.object(self.window, "open_minecraft_installer") as open_installer:
            self.window.install_selected_server_pack()

        source = open_installer.call_args.kwargs["curseforge"]
        self.assertEqual(source["loader"], "FORGE")
        self.assertEqual(source["version"], "1.16.5")

    def test_modpack_install_uses_unique_project_loader_with_all_loaders_filter(self):
        self.window.open_modpack_catalog(version="1.16.5", loader="any")
        self.window.selected_modpack = {
            "id": 454031,
            "name": "Crucial 2 - The Refresh Update",
            "slug": "crucial-2",
            "loaders_by_version": {"1.16.5": ["forge"]},
        }
        self.window.render_modpack_files({
            "project_id": 454031,
            "filters": {"version": "1.16.5", "loader": "any"},
            "items": [{
                "id": 3497749,
                "display_name": "Crucial2-1.3.6.zip",
                "game_versions": ["1.16.5"],
                "release_type": 1,
                "file_length": 1024,
                "is_server_pack": False,
                "server_pack_file_id": 3497751,
            }],
        })
        self.window.modpack_files.selectRow(0)
        QApplication.processEvents()

        with patch.object(self.window, "open_minecraft_installer") as open_installer:
            self.window.install_selected_server_pack()

        source = open_installer.call_args.kwargs["curseforge"]
        self.assertEqual(source["loader"], "FORGE")
        self.assertEqual(source["version"], "1.16.5")

    def test_modpack_catalog_is_one_closable_contextual_tab(self):
        fixed_count = self.window.fixed_tab_count
        self.assertNotIn(
            "Modpacky",
            [self.window.tabs.tabText(index) for index in range(fixed_count)],
        )

        self.window.open_modpack_catalog(version="1.21.1", loader="neoforge")
        page = self.window.modpacks_tab
        self.window.open_modpack_catalog(version="1.20.1", loader="fabric")

        self.assertIs(self.window.modpacks_tab, page)
        self.assertEqual(self.window.tabs.count(), fixed_count + 1)
        self.assertEqual(self.window.tabs.tabText(fixed_count), "Minecraft: Modpacky")
        self.assertEqual(self.window.modpack_version.text(), "1.20.1")
        self.assertEqual(self.window.modpack_loader.currentData(), "fabric")

        self.window.close_server_management_tab(fixed_count)
        self.assertIsNone(self.window.modpacks_tab)
        self.assertEqual(self.window.tabs.count(), fixed_count)


if __name__ == "__main__":
    unittest.main()
