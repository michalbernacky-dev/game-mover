#!/usr/bin/env python3
import os
import sys
import shutil
import requests
import psutil
import grp
import re
import json
import time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QMessageBox, QComboBox, QProgressBar, QListWidget, QListWidgetItem,
    QTabWidget, QHBoxLayout, QSpinBox, QLineEdit, QTimeEdit, QFrame,
    QFileDialog, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFormLayout
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QCoreApplication, QThread, pyqtSignal, QTimer

from game_mover_mods import compare_inventories, scan_mod_directory

# ------------------------------------------------------------
# KONFIGURACE
# ------------------------------------------------------------
FLASK_URL = "http://127.0.0.1:5000"
CLIENT_CONFIG_PATH = os.path.expanduser("~/.config/game-mover/config.json")


def load_client_config():
    try:
        with open(CLIENT_CONFIG_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


CLIENT_CONFIG = load_client_config()
LOCAL_ADMIN_TOKEN_PATH = "/etc/game_mover/api.token"
LOCAL_ADMIN_TOKEN_HEADER = "X-Game-Mover-Token"
SERVER_READ_TOKEN_PATH = os.getenv(
    "GAME_MOVER_SERVER_READ_TOKEN_PATH",
    CLIENT_CONFIG.get("server_read_token_path", "/etc/game_mover/read.token"),
)
SERVER_READ_TOKEN_HEADER = "X-Game-Mover-Read-Token"
PLATFORMS = ["steam", "gog", "epic", "ubisoft", "rockstar"]
DAY_NAMES = {
    1: "Po",
    2: "Út",
    3: "St",
    4: "Čt",
    5: "Pá",
    6: "So",
    7: "Ne",
}

EXCLUDE_PREFIXES = ("Steam", "steam", "Proton", "proton")
EXCLUDE_LIST = {
    "steam": ["Half-Life Dedicated Server"],
    "gog": [],
    "epic": [],
    "ubisoft": [],
    "rockstar": []
}

TIMEKPRA_DISABLE_SECONDS = 24 * 3600       # kolik času nastavit pro "vypnout kontrolu na dnešek"

# ------------------------------------------------------------
# Pomocné funkce
# ------------------------------------------------------------
def list_system_users():
    base = "/home"
    if os.path.isdir(base):
        return [u for u in os.listdir(base) if os.path.isdir(os.path.join(base, u))]
    return []

def list_wheel_users():
    try:
        wheel = grp.getgrnam("wheel")
        return wheel.gr_mem or []
    except KeyError:
        return []

def user_common_candidates(platform, user):
    if platform == "steam":
        return [
            f"/home/{user}/.steam/steam/steamapps/common",
            f"/home/{user}/.local/share/Steam/steamapps/common",
        ]

    elif platform == "gog":
        base = f"/home/{user}/Games/gog"
        commons = []
        if os.path.isdir(base):
            for prefix in os.listdir(base):
                gog_path = os.path.join(base, prefix, "drive_c", "GOG Games")
                if os.path.isdir(gog_path):
                    commons.append(gog_path)
        if not commons:
            commons.append(f"/home/{user}/GOG Games")
        return commons

    elif platform == "epic":
        return [
            f"/home/{user}/Games/Epic",
            f"/home/{user}/Epic Games",
            f"/home/{user}/Games/Heroic/Epic",
        ]

    elif platform == "ubisoft":
        return [
            f"/home/{user}/Ubisoft Game Launcher/games",
            f"/home/{user}/Games/Ubisoft Connect",
            f"/home/{user}/Games/Ubisoft",
        ]

    elif platform == "rockstar":
        return [
            f"/home/{user}/Rockstar Games",
            f"/home/{user}/Games/Rockstar Games",
        ]

    return []

def resolve_user_common(platform, user):
    for path in user_common_candidates(platform, user):
        if os.path.isdir(path):
            return path
    cands = user_common_candidates(platform, user)
    if cands:
        os.makedirs(cands[0], exist_ok=True)
        return cands[0]
    return f"/home/{user}/Games/{platform.capitalize()}"

def get_disk_usage(path):
    """Vrátí (used%, text) pro cestu."""
    usage = shutil.disk_usage(path)
    percent = int(usage.used / usage.total * 100)
    text = f"{path}: {usage.used // (2**30)}G / {usage.total // (2**30)}G ({percent}%)"
    return percent, text


def resolve_logo_path():
    candidates = [
        "/opt/game_mover/game_mover_logo.jpg",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_mover_logo.jpg"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def load_local_admin_token():
    try:
        with open(LOCAL_ADMIN_TOKEN_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def load_server_read_token():
    token_from_env = os.getenv("GAME_MOVER_SERVER_READ_TOKEN", "").strip()
    if token_from_env:
        return token_from_env
    try:
        with open(SERVER_READ_TOKEN_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_client_config(config):
    config_dir = os.path.dirname(CLIENT_CONFIG_PATH)
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    os.chmod(config_dir, 0o700)
    temporary_path = f"{CLIENT_CONFIG_PATH}.tmp"
    with open(temporary_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, CLIENT_CONFIG_PATH)


def server_profiles(config):
    profiles = config.get("server_profiles", [])
    if isinstance(profiles, list) and profiles:
        return [profile for profile in profiles if isinstance(profile, dict)]
    return [{
        "id": "local",
        "name": "Lokální počítač",
        "mode": "local",
        "address": "127.0.0.1:5000",
        "read_token_path": config.get("server_read_token_path", "/etc/game_mover/read.token"),
    }]


def server_read_headers(profile):
    token = profile.get("read_token", "").strip()
    token_path = profile.get("read_token_path", "")
    if not token and token_path:
        try:
            with open(token_path, "r") as f:
                token = f.read().strip()
        except Exception:
            token = ""
    return {SERVER_READ_TOKEN_HEADER: token} if token else {}

# ------------------------------------------------------------
# Worker pro přesun
# ------------------------------------------------------------
class MoveThread(QThread):
    finished = pyqtSignal(dict, str)

    def __init__(self, platform, game, user, headers):
        super().__init__()
        self.platform = platform
        self.game = game
        self.user = user
        self.headers = headers

    def run(self):
        try:
            resp = requests.post(f"{FLASK_URL}/move_game", json={
                "platform": self.platform,
                "game_name": self.game,
                "user": self.user
            }, headers=self.headers)
            data = resp.json()
            self.finished.emit(data, self.game)
        except Exception as e:
            self.finished.emit({"message": f"Chyba: {e}"}, self.game)


class ServerModsThread(QThread):
    loaded = pyqtSignal(dict)

    def __init__(self, server_url, headers):
        super().__init__()
        self.server_url = server_url
        self.headers = headers

    def run(self):
        try:
            resp = requests.get(
                f"{self.server_url}/servers/minecraft/mods",
                headers=self.headers,
                timeout=60,
            )
            data = resp.json()
            if resp.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {resp.status_code}"))
            self.loaded.emit({"inventory": data})
        except Exception as e:
            self.loaded.emit({"error": str(e)})


class CompareModsThread(QThread):
    compared = pyqtSignal(dict)

    def __init__(self, server_inventory, client_path):
        super().__init__()
        self.server_inventory = server_inventory
        self.client_path = client_path

    def run(self):
        try:
            client_inventory = scan_mod_directory(self.client_path)
            self.compared.emit({
                "result": compare_inventories(self.server_inventory, client_inventory),
                "client": client_inventory,
            })
        except Exception as e:
            self.compared.emit({"error": str(e)})

# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------
class GameMover(QWidget):
    def __init__(self):
        super().__init__()
        self.user = os.getenv("USER") or os.getenv("USERNAME") or "unknown"
        self.platform = "steam"
        self.local_admin_token = load_local_admin_token()
        self.timekpra_add_flag = None
        self.timekpra_mode = None  # "addflag" nebo "settimeleft"
        self.timekpr_token = ""
        self.client_config = load_client_config()
        self.server_profiles = server_profiles(self.client_config)
        self.active_server_profile_id = self.client_config.get(
            "active_server_profile", self.server_profiles[0]["id"]
        )
        # uchovává "původní" plán pro dnešní den per-uživatel tak, jak se načetl z timekpra
        self.original_hours_today = {}
        self.minecraft_mod_inventory = None
        self.server_mods_thread = None
        self.compare_mods_thread = None
        self.initUI()
        self.setStyleSheet("""
            QWidget { background-color: #121f28; color: #f3f6f8; }
            QPushButton {
                background-color: #2c414d;
                color: #f3f6f8;
                border: 1px solid #587080;
                border-radius: 3px;
                padding: 5px 10px;
            }
            QPushButton:hover:enabled { background-color: #3a5665; }
            QPushButton:pressed:enabled { background-color: #203541; }
            QPushButton:disabled {
                background-color: #24313a;
                color: #aab9c2;
                border: 1px solid #3c4d57;
            }
        """)

    def initUI(self):
        self.tabs = QTabWidget(self)
        self.init_mover_tab()
        self.init_servers_tab()
        self.init_server_registry_tab()
        self.init_timekpr_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self.setWindowTitle('Game Mover')
        self.resize(820, 720)
        self.refresh_cache_status()
        self.refresh_dnsmasq_status()
        self.refresh_server_statuses()
        self.refresh_game_lists()
        self.server_refresh_timer = QTimer(self)
        self.server_refresh_timer.timeout.connect(self.refresh_server_statuses)
        self.server_refresh_timer.start(10_000)
        self.show()
        QTimer.singleShot(0, self.show_compact_window)

    def show_compact_window(self):
        self.showNormal()
        self.resize(820, 720)

    def active_server_profile(self):
        for profile in self.server_profiles:
            if profile.get("id") == self.active_server_profile_id:
                return profile
        return self.server_profiles[0]

    def server_api_url(self):
        address = self.active_server_profile().get("address", "127.0.0.1:5000").strip().rstrip("/")
        return address if address.startswith(("http://", "https://")) else f"http://{address}"

    def local_admin_headers(self):
        self.local_admin_token = load_local_admin_token()
        if not self.local_admin_token:
            return {}
        return {LOCAL_ADMIN_TOKEN_HEADER: self.local_admin_token}

    def init_mover_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout()

        self.logo_label = QLabel(self)
        try:
            pixmap = QPixmap(resolve_logo_path())
            if not pixmap.isNull():
                pixmap = pixmap.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.logo_label.setPixmap(pixmap)
                self.logo_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(self.logo_label)
        except Exception:
            pass

        self.platform_combo = QComboBox(self)
        self.platform_combo.addItems(PLATFORMS)
        self.platform_combo.currentIndexChanged.connect(self.on_platform_changed)
        layout.addWidget(QLabel("Platforma:"))
        layout.addWidget(self.platform_combo)

        self.cache_button = QPushButton('Nastav sdílenou Steam cache', self)
        self.cache_button.clicked.connect(self.set_shared_cache)
        layout.addWidget(self.cache_button)

        self.fix_perms_button = QPushButton('Opravit oprávnění /var/Games', self)
        self.fix_perms_button.clicked.connect(self.fix_shared_permissions)
        layout.addWidget(self.fix_perms_button)

        layout.addWidget(QLabel("DNSmasq:"))
        dnsmasq_row = QHBoxLayout()
        self.dnsmasq_status_label = QLabel("Stav: —")
        dnsmasq_row.addWidget(self.dnsmasq_status_label)
        self.dnsmasq_stop_button = QPushButton("Vypnout dnsmasq", self)
        self.dnsmasq_stop_button.clicked.connect(self.stop_dnsmasq)
        dnsmasq_row.addWidget(self.dnsmasq_stop_button)
        layout.addLayout(dnsmasq_row)

        self.label_move = QLabel('Hry k přesunu do sdílené knihovny:')
        layout.addWidget(self.label_move)
        self.game_combo_move = QComboBox(self)
        layout.addWidget(self.game_combo_move)

        self.move_button = QPushButton('Přesunout hru', self)
        self.move_button.clicked.connect(self.move_game)
        layout.addWidget(self.move_button)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.label_symlink = QLabel('Hry v /var/Games dostupné pro symlink:')
        layout.addWidget(self.label_symlink)

        self.symlink_list = QListWidget(self)
        self.symlink_list.setSelectionMode(QListWidget.SingleSelection)
        self.symlink_list.setMaximumHeight(200)
        layout.addWidget(self.symlink_list)

        self.label_source_user = QLabel('Zdrojový uživatel prefixu (pro GOG/Epic/Ubisoft):')
        layout.addWidget(self.label_source_user)

        self.source_user_combo = QComboBox(self)
        self.source_user_combo.addItems(list_system_users())
        layout.addWidget(self.source_user_combo)

        self.label_source_user.setVisible(False)
        self.source_user_combo.setVisible(False)

        self.link_button = QPushButton('Vytvořit symlink', self)
        self.link_button.clicked.connect(self.create_symlink)
        layout.addWidget(self.link_button)

        self.disk_label = QLabel("Využití disků:")
        layout.addWidget(self.disk_label)

        self.var_bar = QProgressBar(self)
        self.var_bar.setFormat("/var/Games")
        layout.addWidget(self.var_bar)

        self.home_bar = QProgressBar(self)
        self.home_bar.setFormat(f"/home/{self.user}")
        layout.addWidget(self.home_bar)

        self.update_disk_bars()
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Mover")

    def init_timekpr_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Timekpr Next nástroje (bonus čas, vypnutí kontroly pro dnešek)."))

        self.timekpr_status_label = QLabel("Načítám stav timekpra…")
        layout.addWidget(self.timekpr_status_label)

        auth_row = QHBoxLayout()
        auth_row.addWidget(QLabel("Přihlásit jako (wheel):"))
        self.timekpr_auth_user_combo = QComboBox(self)
        wheel_users = list_wheel_users()
        if self.user not in wheel_users:
            wheel_users.insert(0, self.user)
        self.timekpr_auth_user_combo.addItems(wheel_users)
        auth_row.addWidget(self.timekpr_auth_user_combo)
        self.timekpr_auth_pass = QLineEdit(self)
        self.timekpr_auth_pass.setEchoMode(QLineEdit.Password)
        self.timekpr_auth_pass.setPlaceholderText("heslo wheel uživatele")
        auth_row.addWidget(self.timekpr_auth_pass)
        self.timekpr_unlock_button = QPushButton("Ověřit", self)
        self.timekpr_unlock_button.clicked.connect(self.unlock_timekpr)
        auth_row.addWidget(self.timekpr_unlock_button)
        layout.addLayout(auth_row)

        layout.addWidget(QLabel("Uživatel:"))
        self.timekpr_user_combo = QComboBox(self)
        users = list_system_users()
        if self.user not in users:
            users.insert(0, self.user)
        self.timekpr_user_combo.addItems(users)
        self.timekpr_user_combo.currentIndexChanged.connect(self.on_timekpr_user_changed)
        layout.addWidget(self.timekpr_user_combo)

        bonus_row = QHBoxLayout()
        bonus_row.addWidget(QLabel("Jednorázový bonus (minuty):"))
        self.bonus_spin = QSpinBox(self)
        self.bonus_spin.setRange(5, 600)
        self.bonus_spin.setSingleStep(5)
        self.bonus_spin.setValue(30)
        bonus_row.addWidget(self.bonus_spin)
        self.add_bonus_button = QPushButton("Přidat bonus", self)
        self.add_bonus_button.clicked.connect(self.add_bonus_time)
        bonus_row.addWidget(self.add_bonus_button)
        layout.addLayout(bonus_row)

        self.disable_button = QPushButton("Vypnout kontrolu na dnešek", self)
        self.disable_button.clicked.connect(self.disable_for_today)
        layout.addWidget(self.disable_button)

        self.time_left_button = QPushButton("Zobraz time left", self)
        self.time_left_button.clicked.connect(self.show_time_left)
        layout.addWidget(self.time_left_button)
        self.time_left_label = QLabel("Zbývající čas: —")
        layout.addWidget(self.time_left_label)

        self.reset_button = QPushButton("Reset na plán pro dnešek", self)
        self.reset_button.clicked.connect(self.reset_time_left)
        layout.addWidget(self.reset_button)
        self.plan_label = QLabel("Plán: —")
        self.plan_label.setWordWrap(True)
        self.plan_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.plan_label)

        window_row = QHBoxLayout()
        window_row.addWidget(QLabel("Okno dnes:"))
        self.window_start_time = QTimeEdit(self)
        self.window_start_time.setDisplayFormat("HH:mm")
        self.window_start_time.setTime(self.window_start_time.time().fromString("20:00", "HH:mm"))
        window_row.addWidget(self.window_start_time)
        self.window_end_time = QTimeEdit(self)
        self.window_end_time.setDisplayFormat("HH:mm")
        self.window_end_time.setTime(self.window_end_time.time().fromString("22:00", "HH:mm"))
        window_row.addWidget(self.window_end_time)
        self.apply_window_button = QPushButton("Nastavit okno", self)
        self.apply_window_button.clicked.connect(self.apply_window_today)
        window_row.addWidget(self.apply_window_button)
        self.reset_window_button = QPushButton("Obnovit okno", self)
        self.reset_window_button.clicked.connect(self.reset_window_today)
        window_row.addWidget(self.reset_window_button)
        layout.addLayout(window_row)

        layout.addStretch()
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Timekpr")
        self.set_timekpr_controls_enabled(False)
        self.load_timekpr_status()

    def init_server_registry_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout()
        title = QLabel("Připojení k hernímu serveru")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Profily se odemknou po ověření ve Timekpr. Port 5000 je výchozí pro Game Mover."))

        profile_row = QHBoxLayout()
        self.server_profile_combo = QComboBox(self)
        self.server_profile_combo.currentIndexChanged.connect(self.on_server_profile_changed)
        profile_row.addWidget(self.server_profile_combo)
        self.server_profile_new_button = QPushButton("Nový", self)
        self.server_profile_new_button.clicked.connect(self.new_server_profile)
        profile_row.addWidget(self.server_profile_new_button)
        self.server_profile_delete_button = QPushButton("Smazat", self)
        self.server_profile_delete_button.clicked.connect(self.delete_server_profile)
        profile_row.addWidget(self.server_profile_delete_button)
        layout.addLayout(profile_row)

        form = QFormLayout()
        self.server_profile_name = QLineEdit(self)
        form.addRow("Název:", self.server_profile_name)
        self.server_profile_address = QLineEdit(self)
        self.server_profile_address.setPlaceholderText("100.x.y.z nebo 192.168.x.x")
        form.addRow("Adresa:", self.server_profile_address)
        self.server_profile_port = QSpinBox(self)
        self.server_profile_port.setRange(1, 65535)
        self.server_profile_port.setValue(5000)
        form.addRow("Port:", self.server_profile_port)
        self.server_profile_token = QLineEdit(self)
        self.server_profile_token.setEchoMode(QLineEdit.Password)
        self.server_profile_token.setPlaceholderText("read.token ze vzdáleného serveru")
        form.addRow("Read token:", self.server_profile_token)
        layout.addLayout(form)

        registry_actions = QHBoxLayout()
        self.server_profile_save_button = QPushButton("Uložit profil", self)
        self.server_profile_save_button.clicked.connect(self.save_server_profile)
        registry_actions.addWidget(self.server_profile_save_button)
        self.server_profile_test_button = QPushButton("Otestovat spojení", self)
        self.server_profile_test_button.clicked.connect(self.test_server_profile)
        registry_actions.addWidget(self.server_profile_test_button)
        layout.addLayout(registry_actions)
        layout.addStretch()

        self.server_registry_widgets = [
            self.server_profile_combo, self.server_profile_new_button, self.server_profile_delete_button,
            self.server_profile_name, self.server_profile_address, self.server_profile_port,
            self.server_profile_token, self.server_profile_save_button, self.server_profile_test_button,
        ]
        self.set_server_registry_enabled(False)
        self.reload_server_profile_combo()
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Připojení")

    def init_servers_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout()

        title = QLabel("Stav herních serverů")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Stav se automaticky obnovuje každých 10 sekund."))
        self.endpoint_label = QLabel(f"Zdroj: {self.server_api_url()}")
        self.endpoint_label.setStyleSheet("color: #aab7c0;")
        layout.addWidget(self.endpoint_label)

        self.server_status_widgets = {}
        for server_id, name, service in (
            ("minecraft", "Minecraft", "forge-srv.service"),
            ("satisfactory", "Satisfactory", "satisfactory.service"),
        ):
            card = QFrame(self)
            card.setFrameShape(QFrame.StyledPanel)
            card_layout = QVBoxLayout(card)

            name_label = QLabel(name)
            name_label.setStyleSheet("font-size: 16px; font-weight: bold;")
            card_layout.addWidget(name_label)

            status_label = QLabel("● Načítám…")
            card_layout.addWidget(status_label)
            service_label = QLabel(service)
            service_label.setStyleSheet("color: #aab7c0;")
            card_layout.addWidget(service_label)

            self.server_status_widgets[server_id] = (status_label, service_label)

            if server_id == "minecraft":
                self.minecraft_mods_summary = QLabel("Mody: dosud nenačteny")
                card_layout.addWidget(self.minecraft_mods_summary)
                self.minecraft_mods_table = QTableWidget(self)
                self.minecraft_mods_table.setColumnCount(3)
                self.minecraft_mods_table.setHorizontalHeaderLabels(
                    ["Název", "Verze", "JAR soubor"]
                )
                self.minecraft_mods_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
                self.minecraft_mods_table.setSelectionBehavior(QAbstractItemView.SelectRows)
                self.minecraft_mods_table.setAlternatingRowColors(True)
                self.minecraft_mods_table.setSortingEnabled(True)
                self.minecraft_mods_table.setStyleSheet("""
                    QTableWidget {
                        background-color: #12212a;
                        alternate-background-color: #182b36;
                        color: #ced8de;
                        gridline-color: #304550;
                        selection-background-color: #31566b;
                        selection-color: #e3eaee;
                    }
                    QTableWidget::item {
                        padding: 3px;
                    }
                    QHeaderView::section {
                        background-color: #20323c;
                        color: #c4cfd5;
                        border: 0;
                        border-right: 1px solid #3a4c56;
                        border-bottom: 1px solid #3a4c56;
                        padding: 4px;
                    }
                """)
                self.minecraft_mods_table.verticalHeader().setVisible(False)
                header = self.minecraft_mods_table.horizontalHeader()
                header.setSectionResizeMode(0, QHeaderView.Interactive)
                header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
                header.setSectionResizeMode(2, QHeaderView.Stretch)
                self.minecraft_mods_table.setColumnWidth(0, 260)
                self.minecraft_mods_table.setFixedHeight(240)
                card_layout.addWidget(self.minecraft_mods_table)

                mods_buttons = QHBoxLayout()
                self.load_server_mods_button = QPushButton("Načíst seznam modů", self)
                self.load_server_mods_button.clicked.connect(self.load_server_mods)
                mods_buttons.addWidget(self.load_server_mods_button)
                self.compare_mods_button = QPushButton("Porovnat klientské mody…", self)
                self.compare_mods_button.clicked.connect(self.choose_client_mods)
                self.compare_mods_button.setEnabled(False)
                mods_buttons.addWidget(self.compare_mods_button)
                card_layout.addLayout(mods_buttons)

                self.minecraft_diff_output = QPlainTextEdit(self)
                self.minecraft_diff_output.setReadOnly(True)
                self.minecraft_diff_output.setPlaceholderText("Výsledek porovnání se zobrazí zde.")
                self.minecraft_diff_output.setFixedHeight(145)
                card_layout.addWidget(self.minecraft_diff_output)
            layout.addWidget(card)

        self.servers_updated_label = QLabel("Poslední aktualizace: —")
        layout.addWidget(self.servers_updated_label)
        refresh_button = QPushButton("Obnovit stav", self)
        refresh_button.clicked.connect(self.refresh_server_statuses)
        layout.addWidget(refresh_button)
        layout.addStretch()

        tab.setLayout(layout)
        self.tabs.addTab(tab, "Servery")

    # ----------------- registr serverů -----------------
    def set_server_registry_enabled(self, enabled):
        for widget in getattr(self, "server_registry_widgets", []):
            widget.setEnabled(enabled)

    def reload_server_profile_combo(self):
        self.server_profile_combo.blockSignals(True)
        self.server_profile_combo.clear()
        active_index = 0
        for index, profile in enumerate(self.server_profiles):
            self.server_profile_combo.addItem(profile.get("name", "Server"), profile.get("id"))
            if profile.get("id") == self.active_server_profile_id:
                active_index = index
        self.server_profile_combo.setCurrentIndex(active_index)
        self.server_profile_combo.blockSignals(False)
        self.load_active_server_profile()

    def split_server_address(self, address):
        address = address.strip().removeprefix("http://").removeprefix("https://")
        host, separator, port = address.rpartition(":")
        if separator and port.isdigit():
            return host, int(port)
        return address, 5000

    def load_active_server_profile(self):
        profile = self.active_server_profile()
        host, port = self.split_server_address(profile.get("address", ""))
        self.server_profile_name.setText(profile.get("name", ""))
        self.server_profile_address.setText(host)
        self.server_profile_port.setValue(port)
        self.server_profile_token.setText(profile.get("read_token", ""))
        self.endpoint_label.setText(f"Zdroj: {self.server_api_url()}")

    def on_server_profile_changed(self, index):
        profile_id = self.server_profile_combo.itemData(index)
        if profile_id:
            self.active_server_profile_id = profile_id
            self.load_active_server_profile()
            self.refresh_server_statuses()

    def new_server_profile(self):
        profile_id = f"server-{int(time.time() * 1000)}"
        self.server_profiles.append({"id": profile_id, "name": "Nový server", "mode": "tailscale", "address": "", "read_token": ""})
        self.active_server_profile_id = profile_id
        self.reload_server_profile_combo()

    def delete_server_profile(self):
        if len(self.server_profiles) == 1:
            QMessageBox.warning(self, "Servery", "Musí zůstat alespoň jeden profil.")
            return
        profile = self.active_server_profile()
        self.server_profiles = [item for item in self.server_profiles if item is not profile]
        self.active_server_profile_id = self.server_profiles[0]["id"]
        self.save_server_profiles()
        self.reload_server_profile_combo()
        self.refresh_server_statuses()

    def save_server_profiles(self):
        self.client_config["server_profiles"] = self.server_profiles
        self.client_config["active_server_profile"] = self.active_server_profile_id
        save_client_config(self.client_config)

    def save_server_profile(self):
        profile = self.active_server_profile()
        host = self.server_profile_address.text().strip()
        if not host:
            QMessageBox.warning(self, "Připojení", "Zadej adresu serveru.")
            return
        profile.update({
            "name": self.server_profile_name.text().strip() or "Server",
            "address": f"{host}:{self.server_profile_port.value()}",
            "read_token": self.server_profile_token.text().strip(),
        })
        self.save_server_profiles()
        self.reload_server_profile_combo()
        self.refresh_server_statuses()
        QMessageBox.information(self, "Připojení", "Profil byl uložen do uživatelské konfigurace s právy 0600.")

    def test_server_profile(self):
        profile = self.active_server_profile()
        try:
            response = requests.get(
                f"{self.server_api_url()}/servers/status",
                headers=server_read_headers(profile), timeout=5,
            )
            if response.status_code != 200:
                raise RuntimeError(response.json().get("message", f"HTTP {response.status_code}"))
            QMessageBox.information(self, "Servery", "Spojení se serverem funguje.")
        except Exception as error:
            QMessageBox.critical(self, "Servery", f"Spojení selhalo: {error}")

    # ----------------- pomocné -----------------
    def refresh_server_statuses(self):
        colors = {
            "active": "#66cc66",
            "activating": "#ffcc66",
            "deactivating": "#ffcc66",
            "inactive": "#aaaaaa",
            "failed": "#ff6666",
            "unknown": "#ff6666",
        }
        try:
            resp = requests.get(
                f"{self.server_api_url()}/servers/status",
                headers=server_read_headers(self.active_server_profile()),
                timeout=3,
            )
            resp.raise_for_status()
            data = resp.json()
            for server in data.get("servers", []):
                widgets = self.server_status_widgets.get(server.get("id"))
                if not widgets:
                    continue
                status_label, service_label = widgets
                status = server.get("status", "unknown")
                message = server.get("message", "Neznámý stav")
                status_label.setText(f"● {message}")
                status_label.setStyleSheet(
                    f"color: {colors.get(status, '#ff6666')}; font-weight: bold;"
                )
                service_label.setText(server.get("service", ""))
            updated_at = data.get("updated_at", "")
            if updated_at:
                updated_at = updated_at.replace("T", " ").split("+")[0]
            self.servers_updated_label.setText(f"Poslední aktualizace: {updated_at or '—'}")
        except Exception as e:
            for status_label, _service_label in self.server_status_widgets.values():
                status_label.setText("● Backend není dostupný")
                status_label.setStyleSheet("color: #ff6666; font-weight: bold;")
            self.servers_updated_label.setText(f"Poslední aktualizace: chyba ({e})")

    def load_server_mods(self):
        self.load_server_mods_button.setEnabled(False)
        self.compare_mods_button.setEnabled(False)
        self.minecraft_mods_summary.setText("Mody: načítám inventář…")
        self.server_mods_thread = ServerModsThread(self.server_api_url(), server_read_headers(self.active_server_profile()))
        self.server_mods_thread.loaded.connect(self.on_server_mods_loaded)
        self.server_mods_thread.start()

    def on_server_mods_loaded(self, payload):
        self.load_server_mods_button.setEnabled(True)
        error = payload.get("error")
        if error:
            self.minecraft_mods_summary.setText(f"Mody: chyba ({error})")
            self.compare_mods_button.setEnabled(False)
            return

        inventory = payload["inventory"]
        self.minecraft_mod_inventory = inventory
        self.minecraft_mods_table.setSortingEnabled(False)
        self.minecraft_mods_table.setRowCount(0)
        for jar in inventory.get("jars", []):
            mods = jar.get("mods", [])
            for mod in mods:
                row = self.minecraft_mods_table.rowCount()
                self.minecraft_mods_table.insertRow(row)
                values = (
                    mod.get("name", ""),
                    mod.get("version", ""),
                    jar.get("filename", "?"),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    self.minecraft_mods_table.setItem(row, column, item)
        self.minecraft_mods_table.setSortingEnabled(True)
        self.minecraft_mods_table.sortItems(0, Qt.AscendingOrder)
        count = inventory.get("jar_count", len(inventory.get("jars", [])))
        self.minecraft_mods_summary.setText(f"Mody na serveru: {count} JAR souborů")
        self.compare_mods_button.setEnabled(True)

    def choose_client_mods(self):
        path = QFileDialog.getExistingDirectory(self, "Vyber klientský adresář mods", os.path.expanduser("~"))
        if not path:
            return
        self.compare_mods_button.setEnabled(False)
        self.minecraft_diff_output.setPlainText(f"Prohledávám {path}…")
        self.compare_mods_thread = CompareModsThread(self.minecraft_mod_inventory, path)
        self.compare_mods_thread.compared.connect(self.on_mods_compared)
        self.compare_mods_thread.start()

    def on_mods_compared(self, payload):
        self.compare_mods_button.setEnabled(self.minecraft_mod_inventory is not None)
        error = payload.get("error")
        if error:
            self.minecraft_diff_output.setPlainText(f"Porovnání selhalo: {error}")
            return

        result = payload["result"]
        lines = [
            f"Server: {result['server_jar_count']} JAR, klient: {result['client_jar_count']} JAR",
            "",
        ]
        sections = (
            ("CHYBÍ NA KLIENTOVI", "missing"),
            ("JINÁ VERZE", "version_mismatch"),
            ("JINÝ OBSAH STEJNÉ VERZE", "content_mismatch"),
            ("NAVÍC NA KLIENTOVI", "extra"),
        )
        problem_count = 0
        for title, key in sections:
            items = result.get(key, [])
            problem_count += len(items) if key != "extra" else 0
            lines.append(f"{title} ({len(items)}):")
            if not items:
                lines.append("  —")
            for item in items:
                if key == "version_mismatch":
                    lines.append(
                        f"  {item['id']}: server {item['server_version']} / klient {item['client_version']}"
                    )
                elif key == "content_mismatch":
                    lines.append(
                        f"  {item['id']}: {item['server_file']} / {item['client_file']}"
                    )
                else:
                    version = f" {item.get('version')}" if item.get("version") else ""
                    lines.append(f"  {item['id']}{version} ({item['file']})")
            lines.append("")
        if problem_count == 0:
            lines.insert(0, "Nenalezen žádný chybějící mod ani rozdíl verze/obsahu.\n")
        else:
            lines.insert(0, f"Nalezeno {problem_count} potenciálních problémů.\n")
        self.minecraft_diff_output.setPlainText("\n".join(lines))

    def update_disk_bars(self):
        for path, bar in [("/var/Games", self.var_bar), (f"/home/{self.user}", self.home_bar)]:
            try:
                percent, text = get_disk_usage(path)
                bar.setValue(percent)
                bar.setFormat(text)
                if percent < 70:
                    bar.setStyleSheet("QProgressBar::chunk { background-color: green; }")
                elif percent < 90:
                    bar.setStyleSheet("QProgressBar::chunk { background-color: orange; }")
                else:
                    bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
            except FileNotFoundError:
                bar.setValue(0)
                bar.setFormat(f"{path}: nenalezeno")
                bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
            except PermissionError:
                bar.setValue(0)
                bar.setFormat(f"{path}: nelze číst")
                bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")

    def refresh_dnsmasq_status(self):
        try:
            resp = requests.get(f"{FLASK_URL}/dnsmasq/status")
            data = resp.json()
            status = data.get("status", "unknown")
            message = data.get("message", "Neznámý stav")
            self.dnsmasq_status_label.setText(f"Stav: {message}")
            self.dnsmasq_stop_button.setEnabled(status == "active")
        except Exception as e:
            self.dnsmasq_status_label.setText(f"Stav: chyba ({e})")
            self.dnsmasq_stop_button.setEnabled(False)

    def stop_dnsmasq(self):
        reply = QMessageBox.question(
            self,
            "DNSmasq",
            "Opravdu chceš zastavit dnsmasq?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        headers = self.local_admin_headers()
        if not headers:
            QMessageBox.warning(self, "DNSmasq", "Chybí lokální admin token. Zkontroluj skupinu gemers a /etc/game_mover/api.token.")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/dnsmasq/stop", headers=headers)
            data = resp.json()
            if resp.status_code != 200:
                QMessageBox.critical(self, "DNSmasq", data.get("message", "Chyba"))
            else:
                QMessageBox.information(self, "DNSmasq", data.get("message", "OK"))
        except Exception as e:
            QMessageBox.critical(self, "DNSmasq", f"Chyba: {e}")
        self.refresh_dnsmasq_status()

    def on_platform_changed(self, idx):
        self.platform = self.platform_combo.currentText()
        if self.platform == "steam":
            self.label_source_user.setVisible(False)
            self.source_user_combo.setVisible(False)
        else:
            self.label_source_user.setVisible(True)
            self.source_user_combo.setVisible(True)
        self.refresh_cache_status()
        self.refresh_game_lists()

    def get_game_list(self):
        common = resolve_user_common(self.platform, self.user)
        if common and os.path.exists(common):
            return [
                d for d in os.listdir(common)
                if os.path.isdir(os.path.join(common, d))
                and not os.path.islink(os.path.join(common, d))
                and not d.startswith(EXCLUDE_PREFIXES)
                and d not in EXCLUDE_LIST.get(self.platform, [])
            ]
        return []

    def get_symlink_candidates(self):
        try:
            resp = requests.get(f"{FLASK_URL}/list_shared",
                                params={"platform": self.platform})
            if resp.status_code == 200:
                data = resp.json()
                shared_games = data.get("games", [])
                user_common = resolve_user_common(self.platform, self.user)
                return [g for g in shared_games if not os.path.exists(os.path.join(user_common, g))]
        except Exception as e:
            print(f"Error getting shared list: {e}")
        return []

    def refresh_game_lists(self):
        # kandidáti k přesunu
        self.game_combo_move.clear()
        move_games = self.get_game_list()
        if not move_games:
            self.game_combo_move.addItem('Žádné hry k přesunu')
            self.move_button.setEnabled(False)
        else:
            self.game_combo_move.addItems(move_games)
            self.move_button.setEnabled(True)

        # kandidáti pro symlink
        self.symlink_list.clear()
        symlink_games = self.get_symlink_candidates()
        if not symlink_games:
            self.symlink_list.addItem('Žádné hry pro symlink')
            self.link_button.setEnabled(False)
        else:
            for g in symlink_games:
                self.symlink_list.addItem(QListWidgetItem(g))
            self.link_button.setEnabled(True)

        # update disk bars
        self.update_disk_bars()

    def set_timekpr_controls_enabled(self, enabled):
        widgets = [
            self.timekpr_user_combo,
            self.bonus_spin,
            self.add_bonus_button,
            self.disable_button,
            self.time_left_button,
            self.reset_button,
            self.apply_window_button,
            self.reset_window_button,
            self.window_start_time,
            self.window_end_time,
        ]
        for w in widgets:
            w.setEnabled(enabled)
        status = "Timekpr připraven" if enabled else "Timekpr není dostupný"
        self.timekpr_status_label.setText(status)

    def on_timekpr_user_changed(self, idx):
        """Po přepnutí uživatele automaticky načti plán a zbývající čas."""
        self.time_left_label.setText("Zbývající čas: —")
        self.plan_label.setText("Plán: —")
        if not self.timekpr_token:
            return
        self.fetch_day_plan(force=True)
        self.show_time_left()

    def _extract_time_left(self, msg):
        """Vrátí hodnotu TIME_LEFT_DAY z výstupu timekpra, pokud ji najde."""
        for line in msg.splitlines():
            if "TIME_LEFT_DAY" in line.upper():
                # vezmeme část za = nebo :
                if "=" in line:
                    return line.split("=", 1)[1].strip()
                if ":" in line:
                    return line.split(":", 1)[1].strip()
                return line.strip()
        return None

    def _format_minutes(self, value_str):
        try:
            seconds = int(value_str)
            minutes = seconds // 60
            return f"{minutes} min ({seconds} s)"
        except Exception:
            return value_str

    def _parse_plan_info(self, msg):
        """Vrátí textový popis ALLOWED_WEEKDAYS a ALLOWED_HOURS_X (přátelské formátování)."""
        weekdays_raw = ""
        hours_map = []
        for line in msg.splitlines():
            if line.startswith("ALLOWED_WEEKDAYS"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    weekdays_raw = parts[1].strip()
            elif line.startswith("ALLOWED_HOURS_"):
                m = re.match(r"ALLOWED_HOURS_(\d+)", line)
                if m:
                    idx = int(m.group(1))
                    hours_val = line.split(":", 1)[1].strip() if ":" in line else ""
                    hours_map.append((idx, hours_val))
        parts = []
        if weekdays_raw:
            days = []
            for d in [x.strip() for x in weekdays_raw.split(";") if x.strip()]:
                try:
                    di = int(d)
                    days.append(DAY_NAMES.get(di, d))
                except ValueError:
                    days.append(d)
            if days:
                parts.append(f"Dny povolené: {', '.join(days)}")
        if hours_map:
            parts.append("Hodiny dle plánu:")
            for idx, hstr in sorted(hours_map, key=lambda x: x[0]):
                nice = self._format_hours_pretty(hstr)
                parts.append(f"{DAY_NAMES.get(idx, idx)}: {nice}")
        return "\n".join(parts) if parts else "—"

    def _parse_hours_window(self, hours_str):
        """Vrátí (start, end) pokud jde o jednoduchý seznam hodin (bez ! a bez minut)."""
        parts = [p.strip() for p in hours_str.split(";") if p.strip()]
        start_min = None
        end_min = None
        for idx, p in enumerate(parts):
            if p.startswith("!"):
                return None
            minute_range = None
            if "[" in p and "]" in p:
                try:
                    min_part = p[p.find("[") + 1:p.find("]")]
                    lo, hi = min_part.split("-")
                    minute_range = (int(lo), int(hi))
                    p = p.split("[", 1)[0]
                except Exception:
                    return None
            try:
                hour = int(p)
            except ValueError:
                return None
            if idx == 0:
                if minute_range:
                    start_min = hour * 60 + minute_range[0]
                else:
                    start_min = hour * 60
            if idx == len(parts) - 1:
                if minute_range:
                    end_min = hour * 60 + minute_range[1] + 1
                else:
                    end_min = hour * 60 + 60
        if start_min is None or end_min is None:
            return None
        return start_min, end_min

    def _window_to_hours_string(self, start, end):
        """start/end v minutách. Vrátí string pro --setallowedhours (okno v jeden den)."""
        if end <= start or start < 0 or end > 24 * 60:
            return None
        start_h, start_m = divmod(start, 60)
        end_h, end_m = divmod(end, 60)
        parts = []
        # první hodina
        if start_m > 0:
            parts.append(f"{start_h}[{start_m}-59]")
            first_full = start_h + 1
        else:
            first_full = start_h
        # střední celé hodiny
        last_full = end_h - 1 if end_m == 0 else end_h - 0
        for h in range(first_full, last_full + 1):
            parts.append(str(h))
        # poslední částečná hodina
        if end_m > 0:
            parts.append(f"{end_h}[0-{end_m-1}]")
        return ";".join(parts)

    def _format_hours_pretty(self, hours_str):
        tokens = [t.strip() for t in hours_str.split(";") if t.strip()]
        if not tokens:
            return hours_str
        intervals = []
        for tok in tokens:
            free = tok.startswith("!")
            if free:
                tok = tok[1:]
            minute_range = (0, 59)
            if "[" in tok and "]" in tok:
                try:
                    min_part = tok[tok.find("[") + 1:tok.find("]")]
                    lo, hi = min_part.split("-")
                    minute_range = (int(lo), int(hi))
                    tok = tok.split("[", 1)[0]
                except Exception:
                    return hours_str
            try:
                hour = int(tok)
            except ValueError:
                return hours_str
            start_m = hour * 60 + minute_range[0]
            end_m = hour * 60 + minute_range[1]
            intervals.append((start_m, end_m, free))

        intervals.sort(key=lambda x: x[0])
        merged = []
        for start_m, end_m, free in intervals:
            if not merged:
                merged.append([start_m, end_m, free])
                continue
            last = merged[-1]
            if free == last[2] and start_m <= last[1] + 1:
                last[1] = max(last[1], end_m)
            else:
                merged.append([start_m, end_m, free])

        segments = []
        for start_m, end_m, free in merged:
            start_str = f"{start_m//60:02d}:{start_m%60:02d}"
            end_str = f"{end_m//60:02d}:{end_m%60:02d}"
            color = "#ff6666" if free else "#66cc66"
            segments.append(f'<span style="color:{color}">{start_str} - {end_str}</span>')
        return " | ".join(segments)

    def unlock_timekpr(self):
        """Ověří wheel uživatele přes server a získá token pro timekpr akce."""
        username = self.timekpr_auth_user_combo.currentText()
        password = self.timekpr_auth_pass.text()
        if not username or not password:
            QMessageBox.warning(self, "Timekpr", "Zadej uživatele a heslo (wheel).")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/auth",
                                 json={"username": username, "password": password},
                                 timeout=8)
            try:
                data = resp.json()
            except Exception:
                data = {}
            if resp.status_code != 200:
                msg = data.get("message") if isinstance(data, dict) else resp.text
                QMessageBox.critical(self, "Timekpr", msg or "Přihlášení selhalo")
                self.set_timekpr_controls_enabled(False)
                self.set_server_registry_enabled(False)
                return
            # nový login → smaž cache plánů
            self.original_hours_today = {}
            self.timekpr_token = data.get("token", "")
            self.timekpra_mode = data.get("mode")
            self.timekpra_add_flag = data.get("add_flag")
            self.timekpr_disable_seconds = data.get("disable_seconds", TIMEKPRA_DISABLE_SECONDS)
            self.timekpr_auth_pass.setText("")
            if self.timekpra_mode in ("settimeleft", "addflag") and self.timekpr_token:
                mode_label = "settimeleft" if self.timekpra_mode == "settimeleft" else self.timekpra_add_flag
                self.timekpr_status_label.setText(f"Odemčeno ({username}, mode: {mode_label})")
                self.set_timekpr_controls_enabled(True)
                self.set_server_registry_enabled(True)
                self.fetch_day_plan()
            else:
                detail = data.get("error") or "server nevrátil podporovaný mód"
                self.timekpr_status_label.setText("Timekpr neumí přidat čas")
                self.set_timekpr_controls_enabled(False)
                self.set_server_registry_enabled(bool(self.timekpr_token))
                QMessageBox.critical(self, "Timekpr", f"Přihlášení proběhlo, ale Timekpr není použitelný: {detail}")
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))
            self.set_timekpr_controls_enabled(False)
            self.set_server_registry_enabled(False)

    def load_timekpr_status(self):
        """Načte schopnosti timekpra z Flasku běžícího jako root, požaduje tajný klíč."""
        # ponecháno kvůli zpětné kompatibilitě, ale nyní se stav načte po autentizaci
        self.set_timekpr_controls_enabled(False)

    def add_bonus_time(self):
        user = self.timekpr_user_combo.currentText()
        minutes = self.bonus_spin.value()
        if not self.timekpr_token:
            QMessageBox.warning(self, "Timekpr", "Nejprve se přihlas jako wheel uživatel.")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/add_bonus",
                                 json={"user": user, "minutes": minutes, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=10)
            msg = resp.json().get("message", "")
            if resp.status_code == 200:
                QMessageBox.information(self, "Timekpr", msg or f"Přidán bonus {minutes} minut")
            else:
                QMessageBox.critical(self, "Timekpr", msg or "Chyba timekpra")
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))

    def disable_for_today(self):
        user = self.timekpr_user_combo.currentText()
        if not self.timekpr_token:
            QMessageBox.warning(self, "Timekpr", "Nejprve se přihlas jako wheel uživatel.")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/disable_today",
                                 json={"user": user, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=10)
            msg = resp.json().get("message", "")
            if resp.status_code == 200:
                QMessageBox.information(self, "Timekpr", msg or f"Vypnuto na dnešek pro {user}")
            else:
                QMessageBox.critical(self, "Timekpr", msg or "Chyba timekpra")
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))

    def reset_time_left(self):
        user = self.timekpr_user_combo.currentText()
        if not self.timekpr_token:
            QMessageBox.warning(self, "Timekpr", "Nejprve se přihlas jako wheel uživatel.")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/reset_today",
                                 json={"user": user, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=10)
            data = resp.json()
            msg = data.get("message", "")
            if resp.status_code == 200:
                limit = data.get("limit_seconds")
                if limit is not None:
                    formatted = self._format_minutes(str(limit))
                    self.time_left_label.setText(f"Zbývající čas: {formatted}")
                # reset také vrátí userinfo? ne; znovu načti plán pro konzistenci
                self.show_time_left()
                QMessageBox.information(self, "Timekpr", msg or "Resetováno na plánovaný limit")
            else:
                QMessageBox.critical(self, "Timekpr", msg or "Chyba timekpra")
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))

    def show_time_left(self):
        user = self.timekpr_user_combo.currentText()
        if not self.timekpr_token:
            QMessageBox.warning(self, "Timekpr", "Nejprve se přihlas jako wheel uživatel.")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/userinfo",
                                 json={"user": user, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=10)
            msg = resp.json().get("message", "")
            if resp.status_code == 200:
                extracted = self._extract_time_left(msg or "")
                if extracted:
                    formatted = self._format_minutes(extracted)
                    self.time_left_label.setText(f"Zbývající čas: {formatted}")
                else:
                    self.time_left_label.setText("Zbývající čas: nelze parsovat (viz detail)")
                    QMessageBox.information(self, "Timekpr", msg or "Time left načten")
                plan_text = self._parse_plan_info(msg or "")
                self.plan_label.setText(f"{plan_text}")
                # pokud umíme okno, zobrazíme formou hh:mm - hh:mm
                hours_line = None
                for line in (msg or "").splitlines():
                    if line.startswith("ALLOWED_HOURS_"):
                        hours_line = line.split(":", 1)[1].strip() if ":" in line else line
                        break
                parsed_window = self._parse_hours_window(hours_line) if hours_line else None
                if parsed_window:
                    start_min, end_min = parsed_window
                    start_str = f"{start_min//60:02d}:{start_min%60:02d}"
                    end_str = f"{end_min//60:02d}:{end_min%60:02d}"
                    self.plan_label.setText(f"Plán dnes: {start_str} - {end_str}\n{plan_text}")
                    self.window_start_time.setTime(self.window_start_time.time().fromString(start_str, "HH:mm"))
                    self.window_end_time.setTime(self.window_end_time.time().fromString(end_str, "HH:mm"))
            else:
                QMessageBox.critical(self, "Timekpr", msg or "Chyba timekpra")
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))

    def fetch_day_plan(self, force=False):
        user = self.timekpr_user_combo.currentText()
        if not self.timekpr_token:
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/day_plan",
                                 json={"user": user, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=8)
            data = resp.json()
            if resp.status_code == 200:
                hours = data.get("hours", "")
                if hours:
                    self.plan_label.setText(f"Plán:\n{hours}")
                    if force or user not in self.original_hours_today:
                        self.original_hours_today[user] = hours
                    parsed = self._parse_hours_window(hours)
                    if parsed:
                        start, end = parsed
                        self.window_start_time.setTime(self.window_start_time.time().fromString(f"{start//60:02d}:{start%60:02d}", "HH:mm"))
                        self.window_end_time.setTime(self.window_end_time.time().fromString(f"{end//60:02d}:{end%60:02d}", "HH:mm"))
            else:
                self.plan_label.setText(f"Plán: {data.get('message', 'Chyba')}")
        except Exception:
            pass

    def apply_window_today(self):
        if not self.timekpr_token:
            QMessageBox.warning(self, "Timekpr", "Nejprve se přihlas jako wheel uživatel.")
            return
        start_qt = self.window_start_time.time()
        end_qt = self.window_end_time.time()
        start_min = start_qt.hour() * 60 + start_qt.minute()
        end_min = end_qt.hour() * 60 + end_qt.minute()
        hours_str = self._window_to_hours_string(start_min, end_min)
        if not hours_str:
            QMessageBox.warning(self, "Timekpr", "Konec musí být větší než začátek.")
            return
        user = self.timekpr_user_combo.currentText()
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/set_hours_today",
                                 json={"user": user, "hours": hours_str, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=10)
            data = resp.json()
            if resp.status_code == 200:
                self.plan_label.setText(f"Plán:\n{hours_str}")
                QMessageBox.information(self, "Timekpr", data.get("message", "Okno upraveno"))
            else:
                QMessageBox.critical(self, "Timekpr", data.get("message", "Chyba timekpra"))
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))

    def reset_window_today(self):
        if not self.timekpr_token:
            QMessageBox.warning(self, "Timekpr", "Nejprve se přihlas jako wheel uživatel.")
            return
        user = self.timekpr_user_combo.currentText()
        orig_hours = self.original_hours_today.get(user)
        if not orig_hours:
            QMessageBox.warning(self, "Timekpr", "Nemám původní plán, nejprve načti stav.")
            return
        try:
            resp = requests.post(f"{FLASK_URL}/timekpr/set_hours_today",
                                 json={"user": user, "hours": orig_hours, "token": self.timekpr_token},
                                 headers={"X-Timekpr-Token": self.timekpr_token},
                                 timeout=10)
            data = resp.json()
            if resp.status_code == 200:
                self.plan_label.setText(f"Plán:\n{orig_hours}")
                parsed = self._parse_hours_window(orig_hours)
                if parsed:
                    start, end = parsed
                    self.window_start_time.setTime(self.window_start_time.time().fromString(f"{start//60:02d}:{start%60:02d}", "HH:mm"))
                    self.window_end_time.setTime(self.window_end_time.time().fromString(f"{end//60:02d}:{end%60:02d}", "HH:mm"))
                QMessageBox.information(self, "Timekpr", data.get("message", "Okno obnoveno"))
            else:
                QMessageBox.critical(self, "Timekpr", data.get("message", "Chyba timekpra"))
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))

    # ----------------- akce -----------------
    def refresh_cache_status(self):
        if self.platform != "steam":
            self.cache_button.setVisible(False)
            return

        self.cache_button.setVisible(True)
        status = "unknown"
        tooltip = ""
        try:
            resp = requests.get(f"{FLASK_URL}/steam_cache_status",
                                params={"user": self.user})
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                tooltip = data.get("message", "")
            else:
                status = "missing"
                tooltip = resp.json().get("message", "")
        except Exception as e:
            status = "error"
            tooltip = str(e)

        border = "green" if status == "shared" else "red"
        label = "Steam cache je sdílená" if status == "shared" else "Nastav sdílenou Steam cache"
        self.cache_button.setText(label)
        self.cache_button.setStyleSheet(
            f"QPushButton {{ background-color: #444444; color: white; border: 2px solid {border}; }}"
            f"QPushButton:disabled {{ background-color: #888888; color: #AAAAAA; border: 2px solid {border}; }}"
        )
        self.cache_button.setToolTip(tooltip)

    def set_shared_cache(self):
        if self.platform != "steam":
            return
        headers = self.local_admin_headers()
        if not headers:
            QMessageBox.warning(self, "Steam cache", "Chybí lokální admin token. Zkontroluj skupinu gemers a /etc/game_mover/api.token.")
            return

        try:
            resp = requests.post(f"{FLASK_URL}/set_steam_cache",
                                 json={"user": self.user},
                                 headers=headers)
            data = resp.json()
            if resp.status_code >= 400:
                QMessageBox.warning(self, "Steam cache", data.get("message", "Chyba při nastavování cache"))
            else:
                QMessageBox.information(self, "Steam cache", data.get("message", "Cache byla nastavena"))
        except Exception as e:
            QMessageBox.critical(self, "Chyba", str(e))
        finally:
            self.refresh_cache_status()

    def fix_shared_permissions(self):
        reply = QMessageBox.question(
            self,
            "Opravit oprávnění",
            "Opravdu chceš opravit oprávnění pro /var/Games?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        headers = self.local_admin_headers()
        if not headers:
            QMessageBox.warning(self, "Opravit oprávnění", "Chybí lokální admin token. Zkontroluj skupinu gemers a /etc/game_mover/api.token.")
            return

        try:
            path = "/var/Games"
            resp = requests.post(
                f"{FLASK_URL}/fix_perms",
                json={"path": path},
                headers=headers,
                timeout=30,
            )
            try:
                data = resp.json()
            except Exception:
                data = {"message": resp.text}

            if resp.status_code >= 400:
                QMessageBox.warning(self, "Opravit oprávnění", data.get("message", "Chyba při opravě oprávnění"))
            else:
                QMessageBox.information(self, "Opravit oprávnění", data.get("message", "Oprávnění upravena pro /var/Games"))
        except Exception as e:
            QMessageBox.critical(self, "Opravit oprávnění", str(e))
        finally:
            self.refresh_game_lists()
            self.refresh_cache_status()
            self.update_disk_bars()

    def move_game(self):
        game = self.game_combo_move.currentText()
        if not game or game.startswith("Žádné"):
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.move_button.setEnabled(False)
        self.link_button.setEnabled(False)
        headers = self.local_admin_headers()
        if not headers:
            QMessageBox.warning(self, "Výsledek", "Chybí lokální admin token. Zkontroluj skupinu gemers a /etc/game_mover/api.token.")
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 100)
            self.move_button.setEnabled(True)
            self.link_button.setEnabled(True)
            return
        self.thread = MoveThread(self.platform, game, self.user, headers)
        self.thread.finished.connect(self.move_finished)
        self.thread.start()

    def move_finished(self, data, game):
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.move_button.setEnabled(True)
        self.link_button.setEnabled(True)
        QMessageBox.information(self, "Výsledek", data.get("message", ""))
        self.refresh_game_lists()

    def create_symlink(self):
        item = self.symlink_list.currentItem()
        if not item or item.text().startswith("Žádné"):
            return
        game = item.text()
        headers = self.local_admin_headers()
        if not headers:
            QMessageBox.warning(self, "Chyba", "Chybí lokální admin token. Zkontroluj skupinu gemers a /etc/game_mover/api.token.")
            return
        payload = {"platform": self.platform, "game_name": game, "user": self.user}
        if self.platform in ("gog", "epic", "ubisoft"):
            source_user = self.source_user_combo.currentText()
            if not source_user:
                QMessageBox.warning(self, "Chyba", "Musíš vybrat zdrojového uživatele pro Lutris prefix")
                return
            payload["source_user"] = source_user

        try:
            resp = requests.post(f"{FLASK_URL}/create_symlink", json=payload, headers=headers)
            QMessageBox.information(self, "Výsledek", resp.json().get("message", ""))
            self.refresh_game_lists()
        except Exception as e:
            QMessageBox.critical(self, "Chyba", str(e))

# ------------------------------------------------------------
# Spuštění aplikace
# ------------------------------------------------------------
if __name__ == '__main__':
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv)
    ex = GameMover()
    sys.exit(app.exec_())
