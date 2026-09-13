import os
import unittest
from unittest.mock import MagicMock, patch

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
            "refresh_local_operation_policies",
            "refresh_cache_status",
            "refresh_dnsmasq_status",
            "refresh_launcher_statuses",
            "refresh_game_lists",
            "refresh_system_resources",
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

    def test_mover_uses_only_local_policy_and_credentials(self):
        self.window.local_admin_token = "silent-token"
        self.window.local_operation_policies["game.move"] = "silent"
        with patch.object(
            game_mover, "load_local_admin_token", return_value="silent-token",
        ):
            self.assertEqual(
                self.window.local_mover_operation_headers("game.move"),
                {game_mover.LOCAL_ADMIN_TOKEN_HEADER: "silent-token"},
            )

        self.window.app_mode = "client"
        self.window.timekpr_token = "remote-host-token"
        self.window.local_security_token = "local-pam-token"
        self.window.local_operation_policies["game.move"] = "pam"
        self.assertEqual(
            self.window.local_mover_operation_headers("game.move"),
            {"X-Timekpr-Token": "local-pam-token"},
        )
        self.window.local_operation_policies["game.move"] = "disabled"
        self.assertEqual(
            self.window.local_mover_operation_headers("game.move"), {},
        )

    def test_client_security_shows_only_local_workstation_operations(self):
        self.window.app_mode = "client"
        self.window.local_security_token = "local-pam-token"
        payload = {
            "policies": {
                "global": {
                    operation: "pam"
                    for operation in game_mover.GLOBAL_OPERATION_DEFAULTS
                },
                "servers": {"mc-test": dict(SAMPLE_SERVER["permissions"])},
            },
            "catalog": {
                "global": [
                    {"id": operation, "label": operation, "description": operation,
                     "default": "pam"}
                    for operation in game_mover.GLOBAL_OPERATION_DEFAULTS
                ],
                "fixed": list(game_mover.FIXED_OPERATION_DEFINITIONS),
            },
            "servers": [{"id": "mc-test", "name": "Minecraft Test"}],
        }
        response = MagicMock(status_code=200)
        response.json.return_value = payload

        with patch.object(game_mover.requests, "get", return_value=response) as get:
            self.window.load_security_policies()

        get.assert_called_once_with(
            "http://127.0.0.1:5000/security/policies",
            headers={"X-Timekpr-Token": "local-pam-token"}, timeout=10,
        )
        visible_operations = {
            self.window.security_global_table.item(row, 0).data(game_mover.Qt.UserRole)
            for row in range(self.window.security_global_table.rowCount())
        }
        self.assertEqual(
            visible_operations, game_mover.LOCAL_WORKSTATION_OPERATION_IDS,
        )
        self.assertTrue(self.window.security_server_table.isHidden())
        self.assertTrue(self.window.security_load_button.isEnabled())
        preserved = self.window.collect_security_policies()
        self.assertIn("server.registry", preserved["global"])
        self.assertIn("mc-test", preserved["servers"])

    def test_launcher_update_uses_local_client_policy_and_pam(self):
        self.window.app_mode = "client"
        self.window.timekpr_token = "remote-host-token"
        self.window.local_security_token = "local-pam-token"
        self.window.local_operation_policies["launcher.update"] = "pam"

        self.assertEqual(
            self.window.local_mover_operation_headers("launcher.update"),
            {"X-Timekpr-Token": "local-pam-token"},
        )
        self.assertEqual(len(self.window.local_pam_buttons), 1)

    def test_heroic_client_installer_uses_packagekit_without_sudo(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(game_mover.subprocess, "run", return_value=completed) as run:
            result = game_mover.LauncherUpdateThread._packagekit_install("/tmp/heroic.rpm")
        self.assertIs(result, completed)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/pkcon")
        self.assertIn("install-local", command)
        self.assertNotIn("sudo", command)

    def test_mover_mutates_only_steam_and_heroic_ea_payloads(self):
        self.assertEqual(self.window.platform, "steam")
        self.assertFalse(hasattr(self.window, "source_user_combo"))
        self.assertEqual(
            [self.window.platform_combo.itemData(index)
             for index in range(self.window.platform_combo.count())],
            ["steam", "gog", "epic", "ea", "ubisoft", "rockstar"],
        )
        self.assertIn("Steam hry", self.window.label_move.text())
        self.assertIn("Steam hry", self.window.label_symlink.text())

        self.window.platform_combo.setCurrentIndex(
            self.window.platform_combo.findData("gog")
        )
        self.assertEqual(self.window.platform, "gog")
        self.assertIn("spravuje Heroic", self.window.mover_scope_label.text())
        self.assertFalse(self.window.move_button.isEnabled())
        self.assertFalse(self.window.link_button.isEnabled())
        self.assertFalse(self.window.cache_button.isVisible())

        self.window.platform_combo.setCurrentIndex(
            self.window.platform_combo.findData("ea")
        )
        self.assertEqual(self.window.platform, "ea")
        self.assertIn("/var/Games/EA", self.window.mover_scope_label.text())
        self.assertNotIn("prefix", self.window.label_move.text().lower())
        self.assertFalse(self.window.cache_button.isVisible())

        self.window.platform_combo.setCurrentIndex(
            self.window.platform_combo.findData("ubisoft")
        )
        self.assertIn("pouze rozpoznávaný", self.window.mover_scope_label.text())
        self.assertFalse(self.window.move_button.isEnabled())

    def test_global_resource_bars_show_local_capacity(self):
        gib = 1024 ** 3
        self.window.on_system_resources_loaded({
            "target": "local",
            "memory": {
                "total_bytes": 32 * gib,
                "used_bytes": 24 * gib,
                "available_bytes": 8 * gib,
                "percent": 75.0,
            },
            "swap": {
                "total_bytes": 8 * gib,
                "used_bytes": 2 * gib,
                "free_bytes": 6 * gib,
                "percent": 25.0,
            },
        })

        self.assertEqual(self.window.system_resources_source.text(), "MÍSTNÍ POČÍTAČ")
        self.assertEqual(self.window.ram_bar.value(), 75)
        self.assertIn("24.0 GiB / 32.0 GiB", self.window.ram_bar.format())
        self.assertIn("volná 8.0 GiB", self.window.ram_bar.format())
        self.assertEqual(self.window.swap_bar.value(), 25)
        self.assertIn("2.0 GiB / 8.0 GiB", self.window.swap_bar.format())

        self.window.update_system_resources_context("host")
        self.assertEqual(self.window.system_resources_source.text(), "HOSTITEL · SSH TUNEL")
        self.assertIn("#cc8de8", self.window.system_resources_widget.styleSheet())

    def test_global_resource_target_switches_only_for_live_managed_tunnel(self):
        self.window.app_mode = "ssh_tunnel"
        self.window.ssh_tunnel_port = 5500
        with patch.object(self.window, "managed_ssh_tunnel_running", return_value=True):
            self.assertEqual(
                self.window.system_resources_target(),
                ("http://127.0.0.1:5500", "host"),
            )
        with patch.object(self.window, "managed_ssh_tunnel_running", return_value=False):
            self.assertEqual(
                self.window.system_resources_target(),
                (game_mover.LOCAL_API_URL, "local"),
            )

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

    def test_knowledge_base_supports_game_notes_with_platform_metadata(self):
        self.assertEqual(
            self.window.tabs.tabText(self.window.knowledge_tab_index), "Tipy a poznámky",
        )
        self.window.set_knowledge_target("game", "gta-v-enhanced")
        self.window.render_knowledge_notes([{
            "title": "Epic vlastnictví přes Rockstar Launcher",
            "platform": "Fedora · Heroic · Epic · Rockstar",
            "body": "Použij alternativní fix.bat a vypni UMU.",
            "checks": [{"type": "path_exists", "category": "installation",
                        "label": "Instalace", "paths": ["/definitely/missing/game"]}],
        }])

        self.assertEqual(self.window.knowledge_target_type.currentData(), "game")
        self.assertEqual(self.window.knowledge_target_id.text(), "gta-v-enhanced")
        self.assertEqual(self.window.knowledge_table.rowCount(), 1)
        self.assertIn("Heroic", self.window.knowledge_table.item(0, 1).text())
        self.assertIn("Vyžaduje zásah", self.window.knowledge_table.item(0, 2).text())
        self.assertIn("fix.bat", self.window.knowledge_body.toPlainText())

    def test_installed_game_catalog_attaches_existing_tip_alias_and_users(self):
        self.window.knowledge_saved_targets = {
            ("game", "gta-v-enhanced"),
            ("server", "prominence-2-hasturian-era"),
        }
        self.window.on_installed_knowledge_games({"games": [{
            "id": "grand-theft-auto-v-enhanced",
            "name": "Grand Theft Auto V Enhanced",
            "platforms": ["epic"], "users": ["alice", "bob"],
            "paths": ["/var/Games/Heroic/GTAVEnhanced"],
            "knowledge_aliases": ["grand-theft-auto-v-enhanced", "gta-v-enhanced"],
            "size_bytes": 100 * 1024**3, "possible_residue": False,
        }]})

        self.assertEqual(self.window.knowledge_games_table.rowCount(), 2)
        self.assertEqual(
            self.window.knowledge_games_table.item(0, 0).data(game_mover.Qt.UserRole),
            ("game", "gta-v-enhanced"),
        )
        self.assertEqual(self.window.knowledge_games_table.item(0, 3).text(), "alice, bob")
        self.assertEqual(self.window.knowledge_games_table.item(0, 5).text(), "💡")
        self.assertEqual(
            self.window.knowledge_games_table.item(0, 3).toolTip(),
            "Uživatelé: alice, bob",
        )
        self.assertIn(
            "/var/Games/Heroic/GTAVEnhanced",
            self.window.knowledge_games_table.item(0, 1).toolTip(),
        )
        self.assertFalse(self.window.knowledge_show_residue.isChecked())
        self.assertEqual(
            self.window.knowledge_games_table.horizontalHeader().sectionResizeMode(3),
            game_mover.QHeaderView.Interactive,
        )

    def test_ea_epic_catalog_entry_enables_current_user_launch(self):
        with patch.object(self.window, "load_knowledge_target"):
            self.window.on_installed_knowledge_games({"games": [{
                "id": "star-wars-battlefront-ii-celebration-edition",
                "name": "STAR WARS Battlefront II: Celebration Edition",
                "platforms": ["ea"], "users": ["alice"],
                "paths": ["/var/Games/EA/STAR WARS Battlefront II"],
                "knowledge_aliases": [
                    "star-wars-battlefront-ii-celebration-edition",
                ],
                "launcher_type": "ea_epic", "epic_app_name": "MtMassive",
                "size_bytes": 1, "possible_residue": False,
            }]})

        self.assertTrue(self.window.knowledge_launch_button.isEnabled())
        marker = self.window.knowledge_games_table.item(0, 0)
        self.assertEqual(marker.data(game_mover.Qt.UserRole + 2), "MtMassive")

    def test_installed_games_thread_scans_the_current_player_home(self):
        payloads = []
        thread = game_mover.InstalledGamesThread("Luky", force=True)
        thread.loaded.connect(payloads.append)
        with patch.object(
            game_mover, "scan_user_installed_games", return_value=[{"id": "game"}],
        ) as scan:
            thread.run()

        scan.assert_called_once_with(os.path.expanduser("~"), "Luky")
        self.assertEqual(payloads[0]["games"], [{"id": "game"}])

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

    def test_dnsmasq_controls_live_in_network_instead_of_mover(self):
        mover_tab = self.window.tabs.widget(0)
        self.assertTrue(self.window.network_tab.isAncestorOf(self.window.dnsmasq_status_label))
        self.assertTrue(self.window.network_tab.isAncestorOf(self.window.dnsmasq_stop_button))
        self.assertFalse(mover_tab.isAncestorOf(self.window.dnsmasq_status_label))

    def test_management_page_contains_existing_server_capabilities(self):
        self.window.open_server_management("mc-test")
        entry = self.window.server_management_pages["mc-test"]

        self.assertEqual(entry["status"].text(), "Běží")
        self.assertEqual(entry["players"].text(), "1 / 20 online · již viděno 2")
        self.assertEqual(entry["minecraft_version"].text(), "1.21.8")
        self.assertEqual(entry["connection"].text(), "vanilla.mc.example:25581")
        self.assertEqual(entry["direct_connection"].text(), "127.0.0.1:25570")
        self.assertTrue(entry["direct_connection"].isVisibleTo(entry["page"]))
        self.assertEqual(entry["sections"].count(), 8)
        self.assertEqual(
            [entry["sections"].tabText(index) for index in range(8)],
            [
                "Přehled", "Poznámky", "Logy", "Nastavení", "Hráči",
                "Whitelist", "Zálohy", "Mody",
            ],
        )
        self.assertEqual(set(entry["lifecycle"]), {"start", "stop", "restart"})
        self.assertIn("properties_fields", entry)
        self.assertIn("logs_output", entry)
        self.assertIn("operators_table", entry)
        self.assertIn("whitelist_table", entry)
        self.assertTrue(entry["delete_server"].isVisibleTo(entry["page"]))

    def test_management_page_renders_platform_specific_notes(self):
        server = {
            **SAMPLE_SERVER,
            "notes": [{
                "title": "Fedora + Wayland",
                "platform": "Fedora · CurseForge · NVIDIA",
                "body": "Použij systémové GLFW a spusť CurseForge přes X11.",
            }],
        }
        self.window.last_server_statuses = [server]
        self.window.open_server_management("mc-test")
        table = self.window.server_management_pages["mc-test"]["notes_table"]

        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.columnCount(), 2)
        self.assertLessEqual(table.maximumHeight(), 135)
        self.assertEqual(table.item(0, 0).text(), "Fedora + Wayland")
        self.assertEqual(table.item(0, 1).text(), "Fedora · CurseForge · NVIDIA")
        self.assertIn(
            "systémové GLFW",
            self.window.server_management_pages["mc-test"]["notes_body"].toPlainText(),
        )

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

    def test_host_pam_authentication_unlocks_every_contextual_control(self):
        self.window.app_mode = "server"
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "token": "shared-host-token",
            "mode": "settimeleft",
            "disable_seconds": 3600,
            "managed_users": ["alice", "bob", "carol", "dave"],
        }
        with (
            patch.object(game_mover.requests, "post", return_value=response) as post,
            patch.object(self.window, "update_server_mode_ui"),
            patch.object(self.window, "refresh_server_statuses"),
        ):
            self.window.authenticate_host_pam("admin", "secret")

        self.window.update_host_pam_buttons()
        self.assertEqual(self.window.timekpr_token, "shared-host-token")
        self.assertEqual(
            self.window.host_timekpr_payload["managed_users"],
            ["alice", "bob", "carol", "dave"],
        )
        self.assertEqual(len(self.window.host_pam_buttons), 5)
        self.assertTrue(all(
            button.text() == "PAM hostitele odemčeno" and not button.isEnabled()
            for button in self.window.host_pam_buttons
        ))
        post.assert_called_once_with(
            "http://127.0.0.1:5000/timekpr/auth",
            json={"username": "admin", "password": "secret"}, timeout=8,
        )

    def test_timekpr_switches_between_local_and_tunnel_host_context(self):
        local_payload = {
            "token": "local-token", "mode": "settimeleft",
            "managed_users": ["local-user"],
        }
        host_payload = {
            "token": "host-token", "mode": "addflag", "add_flag": "--addtime",
            "managed_users": ["host-user"],
        }
        self.window.local_security_token = "local-token"
        self.window.local_timekpr_payload = local_payload
        self.window.app_mode = "client"
        self.window.apply_active_timekpr_context()

        self.assertEqual(self.window.timekpr_api_url(), "http://127.0.0.1:5000")
        self.assertEqual(self.window.active_timekpr_token(), "local-token")
        self.assertEqual(self.window.timekpr_user_combo.currentText(), "local-user")
        self.assertIn("Místní PAM", self.window.timekpr_unlock_button.text())
        response = MagicMock(status_code=200)
        response.json.return_value = {"hours": "20;21"}
        with patch.object(game_mover.requests, "post", return_value=response) as post:
            self.window.fetch_day_plan()
        post.assert_called_once_with(
            "http://127.0.0.1:5000/timekpr/day_plan",
            json={"user": "local-user", "token": "local-token"},
            headers={"X-Timekpr-Token": "local-token"}, timeout=8,
        )

        self.window.timekpr_token = "host-token"
        self.window.host_timekpr_payload = host_payload
        self.window.app_mode = "ssh_tunnel"
        self.window.apply_active_timekpr_context()

        self.assertEqual(self.window.timekpr_api_url(), "http://127.0.0.1:5500")
        self.assertEqual(self.window.active_timekpr_token(), "host-token")
        self.assertEqual(self.window.timekpr_user_combo.currentText(), "host-user")
        self.assertIn("hostitele", self.window.timekpr_unlock_button.text())
        with patch.object(game_mover.requests, "post", return_value=response) as post:
            self.window.fetch_day_plan()
        post.assert_called_once_with(
            "http://127.0.0.1:5500/timekpr/day_plan",
            json={"user": "host-user", "token": "host-token"},
            headers={"X-Timekpr-Token": "host-token"}, timeout=8,
        )

        self.window.app_mode = "client"
        self.window.apply_active_timekpr_context()
        self.assertEqual(self.window.timekpr_user_combo.currentText(), "local-user")
        self.assertFalse(hasattr(self.window, "timekpr_auth_pass"))
        self.assertFalse(hasattr(self.window, "timekpr_auth_user_combo"))

    def test_host_pam_controls_stay_locked_in_read_only_client_mode(self):
        self.window.app_mode = "client"
        self.window.timekpr_token = ""
        self.window.update_host_pam_buttons()
        self.assertTrue(all(
            button.text() == "Odemknout PAM hostitele…" and not button.isEnabled()
            for button in self.window.host_pam_buttons
        ))

    def test_security_tab_can_immediately_revoke_shared_pam_session(self):
        self.window.app_mode = "server"
        self.window.timekpr_token = "temporary-host-token"
        self.window.update_security_mode_ui()
        self.assertTrue(self.window.security_host_pam_lock_button.isEnabled())
        self.assertIn("odemčeno", self.window.security_host_pam_status.text())
        response = MagicMock(status_code=200)
        with (
            patch.object(game_mover.requests, "post", return_value=response) as post,
            patch.object(self.window, "update_server_mode_ui"),
        ):
            self.window.lock_host_pam_session()
        self.assertEqual(self.window.timekpr_token, "")
        post.assert_called_once_with(
            "http://127.0.0.1:5000/timekpr/logout",
            headers={"X-Timekpr-Token": "temporary-host-token"}, timeout=8,
        )

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
