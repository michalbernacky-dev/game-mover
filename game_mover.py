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
    QHeaderView, QAbstractItemView, QFormLayout, QScrollArea, QCheckBox,
    QDialog, QDialogButtonBox, QTabBar
)
from PyQt5.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPixmap
from PyQt5.QtCore import Qt, QCoreApplication, QProcess, QSize, QThread, QUrl, pyqtSignal, QTimer

from game_mover_mods import compare_inventories, scan_mod_directory
from game_mover_connections import (
    DEFAULT_SSH_PORT,
    DEFAULT_SSH_TUNNEL_PORT,
    LOCAL_API_URL,
    connection_profile_host,
    is_host_management_mode,
    managed_ssh_tunnel_arguments,
    management_api_url,
    normalize_app_mode,
    normalize_ssh_port,
    normalize_ssh_tunnel_port,
    ssh_tunnel_api_url,
)
from game_mover_security import (
    FIXED_OPERATION_DEFINITIONS,
    GLOBAL_OPERATION_DEFAULTS,
    SERVER_ACTION_IDS,
)
from game_mover_version import __version__
from game_mover_endpoints import (
    normalize_endpoints,
)

# ------------------------------------------------------------
# KONFIGURACE
# ------------------------------------------------------------
FLASK_URL = LOCAL_API_URL
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
SERVER_ACTIONS = SERVER_ACTION_IDS
SERVER_ACTION_LABELS = {
    "start": "Spuštění", "stop": "Vypnutí", "restart": "Restart", "backup": "Záloha",
}
SERVER_POLICY_LABELS = {"silent": "tiché", "pam": "PAM", "disabled": "zakázáno"}

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

    def __init__(self, server_url, headers, server_id):
        super().__init__()
        self.server_url = server_url
        self.headers = headers
        self.server_id = server_id

    def run(self):
        try:
            resp = requests.get(
                f"{self.server_url}/servers/minecraft/mods",
                headers=self.headers,
                params={"server_id": self.server_id},
                timeout=60,
            )
            data = resp.json()
            if resp.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {resp.status_code}"))
            self.loaded.emit({"inventory": data, "server_id": self.server_id})
        except Exception as e:
            self.loaded.emit({"error": str(e), "server_id": self.server_id})


class ModpackCatalogThread(QThread):
    loaded = pyqtSignal(dict)

    def __init__(self, base_url, headers, request_kind, *, project_id=None, params=None):
        super().__init__()
        self.base_url = base_url
        self.headers = headers
        self.request_kind = request_kind
        self.project_id = project_id
        self.params = params or {}

    def run(self):
        paths = {
            "status": "/minecraft/modpacks/status",
            "search": "/minecraft/modpacks/search",
            "files": f"/minecraft/modpacks/{self.project_id}/files",
        }
        try:
            response = requests.get(
                f"{self.base_url}{paths[self.request_kind]}",
                headers=self.headers,
                params=self.params,
                timeout=30,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.loaded.emit({**data, "request_kind": self.request_kind})
        except Exception as error:
            self.loaded.emit({"request_kind": self.request_kind, "error": str(error)})


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


class ServerBackupThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, server_id, headers):
        super().__init__()
        self.base_url = base_url
        self.server_id = server_id
        self.headers = headers

    def run(self):
        try:
            response = requests.post(
                f"{self.base_url}/servers/backup",
                json={"id": self.server_id},
                headers=self.headers,
                timeout=3600,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit(data)
        except Exception as error:
            self.completed.emit({"error": str(error)})


class ServerDeleteThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, payload, headers):
        super().__init__()
        self.base_url = base_url
        self.payload = payload
        self.headers = headers

    def run(self):
        try:
            response = requests.delete(
                f"{self.base_url}/servers/minecraft/delete",
                json=self.payload,
                headers=self.headers,
                timeout=900,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit(data)
        except Exception as error:
            self.completed.emit({"error": str(error), "id": self.payload.get("id", "")})


class ServerBackupsThread(QThread):
    loaded = pyqtSignal(dict)

    def __init__(self, base_url, server_id, headers):
        super().__init__()
        self.base_url = base_url
        self.server_id = server_id
        self.headers = headers

    def run(self):
        try:
            response = requests.get(
                f"{self.base_url}/servers/backups",
                params={"source_id": self.server_id},
                headers=self.headers,
                timeout=30,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.loaded.emit({"backups": data.get("backups", []), "server_id": self.server_id})
        except Exception as error:
            self.loaded.emit({"error": str(error), "server_id": self.server_id})


class ServerPropertiesThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, server_id, headers, settings=None):
        super().__init__()
        self.base_url = base_url
        self.server_id = server_id
        self.headers = headers
        self.settings = settings

    def run(self):
        try:
            if self.settings is None:
                response = requests.get(
                    f"{self.base_url}/servers/minecraft/properties",
                    params={"server_id": self.server_id}, headers=self.headers, timeout=20,
                )
                action = "load"
            else:
                response = requests.put(
                    f"{self.base_url}/servers/minecraft/properties",
                    json={"server_id": self.server_id, "settings": self.settings},
                    headers=self.headers, timeout=20,
                )
                action = "save"
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit({"server_id": self.server_id, "action": action, "payload": data})
        except Exception as error:
            self.completed.emit({"server_id": self.server_id, "error": str(error)})


class ServerOperatorsThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, server_id, headers, action=None, player=None):
        super().__init__()
        self.base_url = base_url
        self.server_id = server_id
        self.headers = headers
        self.action = action
        self.player = player

    def run(self):
        try:
            if self.action is None:
                response = requests.get(
                    f"{self.base_url}/servers/minecraft/operators",
                    params={"server_id": self.server_id},
                    headers=self.headers, timeout=20,
                )
                operation = "load"
            else:
                response = requests.post(
                    f"{self.base_url}/servers/minecraft/operators",
                    json={
                        "server_id": self.server_id,
                        "action": self.action,
                        "player": self.player,
                    },
                    headers=self.headers, timeout=20,
                )
                operation = self.action
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit({
                "server_id": self.server_id, "action": operation, "payload": data,
            })
        except Exception as error:
            self.completed.emit({"server_id": self.server_id, "error": str(error)})


class ServerWhitelistThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, server_id, headers, action=None, player=None):
        super().__init__()
        self.base_url = base_url
        self.server_id = server_id
        self.headers = headers
        self.action = action
        self.player = player

    def run(self):
        try:
            if self.action is None:
                response = requests.get(
                    f"{self.base_url}/servers/minecraft/whitelist",
                    params={"server_id": self.server_id},
                    headers=self.headers, timeout=20,
                )
                operation = "load"
            else:
                response = requests.post(
                    f"{self.base_url}/servers/minecraft/whitelist",
                    json={
                        "server_id": self.server_id,
                        "action": self.action,
                        "player": self.player,
                    },
                    headers=self.headers, timeout=20,
                )
                operation = self.action
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit({
                "server_id": self.server_id, "action": operation, "payload": data,
            })
        except Exception as error:
            self.completed.emit({"server_id": self.server_id, "error": str(error)})


class ServerLogsThread(QThread):
    loaded = pyqtSignal(dict)

    def __init__(self, base_url, server_id, tail, headers):
        super().__init__()
        self.base_url = base_url
        self.server_id = server_id
        self.tail = tail
        self.headers = headers

    def run(self):
        try:
            response = requests.get(
                f"{self.base_url}/servers/logs",
                params={"server_id": self.server_id, "tail": self.tail},
                headers=self.headers, timeout=35,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.loaded.emit({"server_id": self.server_id, "payload": data})
        except Exception as error:
            self.loaded.emit({"server_id": self.server_id, "error": str(error)})


class GateDeployThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, headers):
        super().__init__()
        self.base_url = base_url
        self.headers = headers

    def run(self):
        try:
            response = requests.post(
                f"{self.base_url}/proxy/deploy", headers=self.headers, timeout=900,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit(data)
        except Exception as error:
            self.completed.emit({"error": str(error)})


class GateRoutesSaveThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, routes, headers):
        super().__init__()
        self.base_url = base_url
        self.routes = routes
        self.headers = headers

    def run(self):
        try:
            response = requests.put(
                f"{self.base_url}/proxy/routes",
                json={"routes": self.routes},
                headers=self.headers,
                timeout=240,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit(data)
        except Exception as error:
            self.completed.emit({"error": str(error)})


class MinecraftInstallThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, payload, headers):
        super().__init__()
        self.base_url = base_url
        self.payload = payload
        self.headers = headers

    def run(self):
        try:
            response = requests.post(
                f"{self.base_url}/servers/minecraft/install",
                json=self.payload,
                headers=self.headers,
                timeout=3600,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit(data)
        except Exception as error:
            self.completed.emit({"error": str(error)})


class ServerStatusThread(QThread):
    loaded = pyqtSignal(dict)

    def __init__(self, base_url, headers):
        super().__init__()
        self.base_url = base_url
        self.headers = headers

    def run(self):
        try:
            response = requests.get(
                f"{self.base_url}/servers/status",
                headers=self.headers,
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            try:
                proxy_response = requests.get(
                    f"{self.base_url}/proxy/status",
                    headers=self.headers,
                    timeout=12,
                )
                if proxy_response.status_code == 200:
                    payload["proxy"] = proxy_response.json().get("proxy")
            except requests.RequestException:
                pass
            self.loaded.emit(payload)
        except Exception as error:
            self.loaded.emit({"request_error": str(error)})


class LauncherStatusThread(QThread):
    loaded = pyqtSignal(dict)

    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url

    def run(self):
        try:
            response = requests.get(f"{self.base_url}/launchers/status", timeout=90)
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.loaded.emit(data)
        except Exception as error:
            self.loaded.emit({"error": str(error)})


class LauncherUpdateThread(QThread):
    completed = pyqtSignal(dict)

    def __init__(self, base_url, launcher_id, headers):
        super().__init__()
        self.base_url = base_url
        self.launcher_id = launcher_id
        self.headers = headers

    def run(self):
        try:
            response = requests.post(
                f"{self.base_url}/launchers/{self.launcher_id}/update",
                headers=self.headers, timeout=1200,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.completed.emit({**data, "launcher_id": self.launcher_id})
        except Exception as error:
            self.completed.emit({"launcher_id": self.launcher_id, "error": str(error)})

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
        self.local_security_token = ""
        self.client_config = load_client_config()
        self.app_mode = normalize_app_mode(self.client_config.get("app_mode", "client"))
        # An SSH management mode is valid only while this process owns a live
        # tunnel. Never restore it from a previous GUI session.
        if self.app_mode == "ssh_tunnel":
            self.app_mode = "client"
            self.client_config["app_mode"] = "client"
        self.ssh_tunnel_port = normalize_ssh_tunnel_port(
            self.client_config.get("ssh_tunnel_port", DEFAULT_SSH_TUNNEL_PORT),
        )
        self.ssh_port = normalize_ssh_port(
            self.client_config.get("ssh_port", DEFAULT_SSH_PORT),
        )
        self.ssh_user = str(self.client_config.get("ssh_user", self.user)).strip() or self.user
        self.ssh_tunnel_process = None
        self.ssh_tunnel_stopping = False
        self.ssh_tunnel_error_reported = False
        self.ssh_tunnel_ready_deadline = 0.0
        self.application_closing = False
        self.server_profiles = server_profiles(self.client_config)
        self.active_server_profile_id = self.client_config.get(
            "active_server_profile", self.server_profiles[0]["id"]
        )
        # uchovává "původní" plán pro dnešní den per-uživatel tak, jak se načetl z timekpra
        self.original_hours_today = {}
        self.minecraft_mod_inventory = None
        self.server_status_thread = None
        self.server_mods_thread = None
        self.compare_mods_thread = None
        self.server_backup_thread = None
        self.server_backup_server_id = ""
        self.server_delete_thread = None
        self.server_management_pages = {}
        self.server_mod_inventories = {}
        self.server_mod_threads = {}
        self.compare_mod_threads = {}
        self.server_backups_threads = {}
        self.server_properties_threads = {}
        self.server_operator_threads = {}
        self.server_whitelist_threads = {}
        self.server_log_threads = {}
        self.modpack_catalog_thread = None
        self.modpack_search_index = 0
        self.selected_modpack = None
        self.selected_modpack_file = None
        self.modpacks_tab = None
        self.gate_deploy_thread = None
        self.gate_routes_thread = None
        self.minecraft_install_thread = None
        self.launcher_status_thread = None
        self.launcher_update_thread = None
        self.launcher_cards = {}
        self.launcher_update_policy = "pam"
        self.last_server_statuses = []
        self.global_operation_policies = dict(GLOBAL_OPERATION_DEFAULTS)
        self.security_payload = None
        self.ssh_tunnel_probe_timer = QTimer(self)
        self.ssh_tunnel_probe_timer.setInterval(250)
        self.ssh_tunnel_probe_timer.timeout.connect(self.probe_managed_ssh_tunnel)
        self.initUI()
        self.setStyleSheet("""
            QWidget { background-color: #121f28; color: #f3f6f8; }
            QAbstractItemView {
                alternate-background-color: #2d3439;
            }
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
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #91a8b5;
                border-radius: 3px;
                background-color: #182832;
            }
            QCheckBox::indicator:hover { border-color: #d8e4ea; }
            QCheckBox::indicator:checked {
                border-color: #69db7c;
                background-color: #2f9e44;
            }
        """)

    def initUI(self):
        self.tabs = QTabWidget(self)
        version_label = QLabel(__version__, self.tabs)
        version_label.setStyleSheet("color: #8fa1ab; padding: 0 8px;")
        version_label.setToolTip(f"Game Mover {__version__}")
        self.tabs.setCornerWidget(version_label, Qt.TopRightCorner)
        self.init_mover_tab()
        self.init_launchers_tab()
        self.init_servers_tab()
        self.init_network_tab()
        self.init_server_registry_tab()
        self.init_security_tab()
        self.init_timekpr_tab()
        self.fixed_tab_count = self.tabs.count()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_server_management_tab)
        for index in range(self.fixed_tab_count):
            self.tabs.tabBar().setTabButton(index, QTabBar.LeftSide, None)
            self.tabs.tabBar().setTabButton(index, QTabBar.RightSide, None)

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self.setWindowTitle('Game Mover')
        self.setMinimumSize(720, 500)
        self.resize(960, 720)
        self.refresh_cache_status()
        self.refresh_dnsmasq_status()
        self.refresh_managed_dns_status()
        self.operation_refresh_timer = QTimer(self)
        self.operation_refresh_timer.timeout.connect(self.refresh_server_statuses)
        self.refresh_server_statuses()
        self.refresh_game_lists()
        self.refresh_launcher_statuses()
        self.server_refresh_timer = QTimer(self)
        self.server_refresh_timer.timeout.connect(self.refresh_server_statuses)
        self.server_refresh_timer.start(10_000)
        self.show()

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

    def init_launchers_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        title = QLabel("Herní launchery", tab)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        help_label = QLabel(
            "Game Mover rozpozná nativně nainstalované launchery a porovná jejich verze. "
            "Zašedlá položka na tomto počítači není nainstalovaná. Heroic lze bezpečně "
            "aktualizovat z jeho oficiálního GitHub release.", tab,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        toolbar = QHBoxLayout()
        self.launchers_summary = QLabel("Kontroluji launchery…", tab)
        toolbar.addWidget(self.launchers_summary, 1)
        self.launchers_refresh_button = QPushButton("Zkontrolovat aktualizace", tab)
        self.launchers_refresh_button.clicked.connect(self.refresh_launcher_statuses)
        toolbar.addWidget(self.launchers_refresh_button)
        layout.addLayout(toolbar)

        scroll = QScrollArea(tab)
        scroll.setWidgetResizable(True)
        self.launchers_widget = QWidget(scroll)
        self.launchers_layout = QVBoxLayout(self.launchers_widget)
        self.launchers_layout.addStretch()
        scroll.setWidget(self.launchers_widget)
        layout.addWidget(scroll)
        self.launchers_tab = tab
        self.launchers_tab_index = self.tabs.addTab(tab, "Launchery")

    def launcher_api_url(self):
        if is_host_management_mode(self.app_mode):
            return self.host_management_api_url()
        return FLASK_URL

    def refresh_launcher_statuses(self):
        if self.launcher_status_thread and self.launcher_status_thread.isRunning():
            return
        self.launchers_summary.setText("Kontroluji nainstalované a aktuální verze…")
        self.launchers_refresh_button.setEnabled(False)
        self.launcher_status_thread = LauncherStatusThread(self.launcher_api_url())
        self.launcher_status_thread.loaded.connect(self.on_launcher_statuses_loaded)
        self.launcher_status_thread.start()

    def launcher_icon_pixmap(self, icon_name, installed):
        icon = QIcon.fromTheme(icon_name)
        if icon.isNull():
            icon = QIcon.fromTheme("applications-games")
        mode = QIcon.Normal if installed else QIcon.Disabled
        return icon.pixmap(QSize(64, 64), mode)

    def on_launcher_statuses_loaded(self, payload):
        self.launchers_refresh_button.setEnabled(True)
        if payload.get("error"):
            self.launchers_summary.setText(f"Kontrola launcherů selhala: {payload['error']}")
            return
        self.launcher_update_policy = payload.get("update_policy", "pam")
        self.clear_layout(self.launchers_layout)
        self.launcher_cards = {}
        launchers = payload.get("launchers") if isinstance(payload.get("launchers"), list) else []
        update_count = 0
        for launcher in launchers:
            launcher_id = str(launcher.get("id", ""))
            installed = bool(launcher.get("installed"))
            update_available = bool(launcher.get("update_available"))
            update_count += int(update_available)
            card = QFrame(self.launchers_widget)
            card.setFrameShape(QFrame.StyledPanel)
            card.setEnabled(installed)
            row = QHBoxLayout(card)
            icon = QLabel(card)
            icon.setFixedSize(72, 72)
            icon.setAlignment(Qt.AlignCenter)
            icon.setPixmap(self.launcher_icon_pixmap(launcher.get("icon", ""), installed))
            row.addWidget(icon)
            text_layout = QVBoxLayout()
            name = QLabel(launcher.get("name", launcher_id), card)
            name.setStyleSheet("font-size: 16px; font-weight: bold;")
            text_layout.addWidget(name)
            installed_version = launcher.get("installed_version") or "—"
            latest_version = launcher.get("latest_version") or "—"
            if not installed:
                status_text, color = "Není nainstalováno", "#9aa8b0"
            elif update_available:
                status_text = f"Aktualizace: {installed_version} → {latest_version}"
                color = "#ffcc66"
            elif launcher.get("error"):
                status_text = f"Nainstalováno {installed_version} · kontrola verze selhala"
                color = "#ff8a80"
            elif latest_version:
                status_text = f"Aktuální · verze {installed_version}"
                color = "#66cc66"
            else:
                status_text = f"Nainstalováno · verze {installed_version}"
                color = "#d7e0e5"
            status = QLabel(status_text, card)
            status.setStyleSheet(f"color: {color}; font-weight: bold;")
            if launcher.get("error"):
                status.setToolTip(str(launcher["error"]))
            text_layout.addWidget(status)
            source = QLabel(str(launcher.get("source", "")), card)
            source.setStyleSheet("color: #9fb0ba;")
            text_layout.addWidget(source)
            row.addLayout(text_layout, 1)
            update_button = QPushButton("Aktualizovat", card)
            update_button.setVisible(bool(launcher.get("update_supported")))
            update_button.setEnabled(
                update_available
                and bool(self.local_server_action_headers(self.launcher_update_policy))
            )
            update_button.clicked.connect(
                lambda _checked=False, item=dict(launcher): self.update_launcher(item)
            )
            row.addWidget(update_button)
            self.launchers_layout.addWidget(card)
            self.launcher_cards[launcher_id] = {
                "card": card, "status": status, "update": update_button,
                "launcher": dict(launcher),
            }
        self.launchers_layout.addStretch()
        self.launchers_summary.setText(
            f"Dostupné aktualizace: {update_count}" if update_count
            else "Nainstalované launchery jsou aktuální."
        )
        self.tabs.setTabText(
            self.launchers_tab_index,
            f"Launchery ({update_count})" if update_count else "Launchery",
        )

    def update_launcher(self, launcher):
        if self.launcher_update_thread and self.launcher_update_thread.isRunning():
            return
        headers = self.local_server_action_headers(self.launcher_update_policy)
        if not headers:
            QMessageBox.warning(
                self, "Aktualizace launcheru",
                "Aktualizace vyžaduje platné oprávnění. Ověř se jako wheel uživatel v Timekpr.",
            )
            return
        name = launcher.get("name", launcher.get("id", "Launcher"))
        target = launcher.get("latest_version", "novou verzi")
        answer = QMessageBox.question(
            self, "Aktualizovat launcher",
            f"Aktualizovat {name} na verzi {target}?\n\nLauncher musí být před aktualizací ukončený.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        launcher_id = str(launcher.get("id", ""))
        card = self.launcher_cards.get(launcher_id, {})
        if card:
            card["update"].setEnabled(False)
            card["status"].setText("Stahuji a instaluji aktualizaci…")
            card["status"].setStyleSheet("color: #74c0fc; font-weight: bold;")
        self.launcher_update_thread = LauncherUpdateThread(
            self.launcher_api_url(), launcher_id, headers,
        )
        self.launcher_update_thread.completed.connect(self.on_launcher_update_completed)
        self.launcher_update_thread.start()

    def on_launcher_update_completed(self, payload):
        error = payload.get("error")
        if error:
            QMessageBox.critical(self, "Aktualizace launcheru", str(error))
        else:
            QMessageBox.information(
                self, "Aktualizace launcheru", payload.get("message", "Aktualizace dokončena."),
            )
        self.refresh_launcher_statuses()

    def init_timekpr_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Timekpr Next nástroje (bonus čas, vypnutí kontroly pro dnešek)."))

        self.timekpr_status_label = QLabel("Načítám stav timekpra…")
        layout.addWidget(self.timekpr_status_label)

        auth_row = QHBoxLayout()
        auth_row.addWidget(QLabel("Přihlásit jako (wheel):"))
        self.timekpr_auth_user_combo = QComboBox(self)
        self.timekpr_auth_user_combo.setEditable(True)
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
        self.timekpr_user_combo.setEditable(True)
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
        connection_help = QLabel(
            "Profily lze vybírat a testovat bez ověření. Jejich úpravy chrání místní "
            "pojistka níže. Port 5000 je výchozí pro Game Mover. Vzdálenou správu "
            "a SSH tunel odemyká PAM v záložce Zabezpečení."
        )
        connection_help.setWordWrap(True)
        layout.addWidget(connection_help)
        self.connection_edit_checkbox = QCheckBox("Povolit úpravy připojení", self)
        self.connection_edit_checkbox.setChecked(False)
        self.connection_edit_checkbox.toggled.connect(self.update_server_mode_ui)
        layout.addWidget(self.connection_edit_checkbox)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Režim aplikace:"))
        self.app_mode_combo = QComboBox(self)
        self.app_mode_combo.addItem("Klient – vzdálený náhled", "client")
        self.app_mode_combo.addItem("Server – místní správa služeb", "server")
        selected_mode = self.app_mode_combo.findData(self.app_mode)
        self.app_mode_combo.setCurrentIndex(max(0, selected_mode))
        self.app_mode_combo.currentIndexChanged.connect(self.on_app_mode_changed)
        mode_row.addWidget(self.app_mode_combo)
        layout.addLayout(mode_row)

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

        self.local_services_label = QLabel("Sledované služby tohoto počítače")
        layout.addWidget(self.local_services_label)
        self.local_services_table = QTableWidget(self)
        self.local_services_table.setColumnCount(7)
        self.local_services_table.setHorizontalHeaderLabels([
            "ID", "Název", "Backend", "Jednotka / container", "Typ",
            "Datový adresář", "Adresář mods",
        ])
        self.local_services_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.local_services_table.setFixedHeight(190)
        services_header = self.local_services_table.horizontalHeader()
        services_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        services_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        services_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        services_header.setSectionResizeMode(3, QHeaderView.Stretch)
        services_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        services_header.setSectionResizeMode(5, QHeaderView.Stretch)
        services_header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.local_services_table)
        services_actions = QHBoxLayout()
        self.local_services_refresh = QPushButton("Načíst", self)
        self.local_services_refresh.clicked.connect(self.load_local_services)
        services_actions.addWidget(self.local_services_refresh)
        self.local_services_add = QPushButton("Přidat službu", self)
        self.local_services_add.clicked.connect(self.add_local_service_row)
        services_actions.addWidget(self.local_services_add)
        self.local_services_remove = QPushButton("Smazat vybranou", self)
        self.local_services_remove.clicked.connect(self.remove_local_service_rows)
        services_actions.addWidget(self.local_services_remove)
        self.local_services_save = QPushButton("Uložit služby", self)
        self.local_services_save.clicked.connect(self.save_local_services)
        services_actions.addWidget(self.local_services_save)
        layout.addLayout(services_actions)
        layout.addStretch()

        self.server_profile_edit_widgets = [
            self.server_profile_new_button, self.server_profile_delete_button,
            self.server_profile_name, self.server_profile_address, self.server_profile_port,
            self.server_profile_token, self.server_profile_save_button,
        ]
        self.reload_server_profile_combo()
        self.update_server_mode_ui()
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Připojení")

    def init_security_tab(self):
        tab = QWidget(self)
        tab_layout = QVBoxLayout(tab)
        scroll_area = QScrollArea(tab)
        scroll_area.setWidgetResizable(True)
        content = QWidget(scroll_area)
        layout = QVBoxLayout(content)
        title = QLabel("Zabezpečení herní platformy", content)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        help_label = QLabel(
            "Každá operace má samostatnou backendem vynucovanou zásadu. "
            "Tichá používá místní api.token, PAM vyžaduje wheel ověření a "
            "zakázaná operace není dostupná. Změny tohoto registru vždy vyžadují PAM.",
            content,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.security_status_label = QLabel("Zásady zatím nejsou načtené.", content)
        self.security_status_label.setStyleSheet("color: #aab7c0;")
        layout.addWidget(self.security_status_label)

        layout.addWidget(QLabel("Vzdálená správa hostitele:", content))
        tunnel_unlock_row = QHBoxLayout()
        self.security_tunnel_lock_status = QLabel(
            "Nastavení spravovaného SSH tunelu je uzamčené.", content,
        )
        self.security_tunnel_lock_status.setStyleSheet("color: #aab7c0;")
        tunnel_unlock_row.addWidget(self.security_tunnel_lock_status, 1)
        self.security_tunnel_unlock_button = QPushButton("Odemknout místním PAM…", content)
        self.security_tunnel_unlock_button.clicked.connect(self.unlock_tunnel_management)
        tunnel_unlock_row.addWidget(self.security_tunnel_unlock_button)
        layout.addLayout(tunnel_unlock_row)

        self.security_tunnel_panel = QFrame(content)
        self.security_tunnel_panel.setFrameShape(QFrame.StyledPanel)
        tunnel_layout = QVBoxLayout(self.security_tunnel_panel)
        self.security_tunnel_target_label = QLabel(self.security_tunnel_panel)
        self.security_tunnel_target_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tunnel_layout.addWidget(self.security_tunnel_target_label)
        tunnel_help = QLabel(
            "Tunel používá pouze SSH klíč nebo ssh-agent a známý host key. "
            "SSH heslo Game Mover nepřijímá ani neukládá.",
            self.security_tunnel_panel,
        )
        tunnel_help.setWordWrap(True)
        tunnel_layout.addWidget(tunnel_help)
        tunnel_form = QFormLayout()
        self.security_ssh_user = QLineEdit(self.ssh_user, self.security_tunnel_panel)
        tunnel_form.addRow("SSH uživatel:", self.security_ssh_user)
        self.security_ssh_port = QSpinBox(self.security_tunnel_panel)
        self.security_ssh_port.setRange(1, 65535)
        self.security_ssh_port.setValue(self.ssh_port)
        tunnel_form.addRow("SSH port:", self.security_ssh_port)
        self.ssh_tunnel_port_spin = QSpinBox(self.security_tunnel_panel)
        self.ssh_tunnel_port_spin.setRange(1024, 65535)
        self.ssh_tunnel_port_spin.setValue(self.ssh_tunnel_port)
        tunnel_form.addRow("Místní port tunelu:", self.ssh_tunnel_port_spin)
        tunnel_layout.addLayout(tunnel_form)
        self.security_tunnel_status = QLabel("Tunel není spuštěný.", self.security_tunnel_panel)
        self.security_tunnel_status.setStyleSheet("color: #aab7c0;")
        self.security_tunnel_status.setWordWrap(True)
        tunnel_layout.addWidget(self.security_tunnel_status)
        tunnel_actions = QHBoxLayout()
        self.security_tunnel_start_button = QPushButton("Otevřít SSH tunel", self.security_tunnel_panel)
        self.security_tunnel_start_button.clicked.connect(self.start_managed_ssh_tunnel)
        tunnel_actions.addWidget(self.security_tunnel_start_button)
        self.security_tunnel_stop_button = QPushButton("Zavřít SSH tunel", self.security_tunnel_panel)
        self.security_tunnel_stop_button.clicked.connect(self.stop_managed_ssh_tunnel)
        tunnel_actions.addWidget(self.security_tunnel_stop_button)
        self.security_tunnel_lock_button = QPushButton("Zamknout", self.security_tunnel_panel)
        self.security_tunnel_lock_button.clicked.connect(self.lock_tunnel_management)
        tunnel_actions.addWidget(self.security_tunnel_lock_button)
        tunnel_layout.addLayout(tunnel_actions)
        self.security_tunnel_panel.setVisible(False)
        layout.addWidget(self.security_tunnel_panel)

        layout.addWidget(QLabel("Globální operace:", content))
        self.security_global_table = QTableWidget(content)
        self.security_global_table.setColumnCount(3)
        self.security_global_table.setHorizontalHeaderLabels([
            "Operace", "Popis", "Ověření",
        ])
        self.security_global_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.security_global_table.setWordWrap(False)
        self.security_global_table.verticalHeader().setVisible(False)
        self.security_global_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.security_global_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.security_global_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.security_global_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.security_global_table)

        layout.addWidget(QLabel("Akce jednotlivých serverů:", content))
        self.security_server_table = QTableWidget(content)
        self.security_server_table.setColumnCount(1 + len(SERVER_ACTIONS))
        self.security_server_table.setHorizontalHeaderLabels([
            "Server", *[SERVER_ACTION_LABELS[action] for action in SERVER_ACTIONS],
        ])
        self.security_server_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.security_server_table.setWordWrap(False)
        self.security_server_table.verticalHeader().setVisible(False)
        self.security_server_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 1 + len(SERVER_ACTIONS)):
            self.security_server_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents,
            )
        self.security_server_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.security_server_table)

        layout.addWidget(QLabel("Pevně chráněné operace:", content))
        self.security_fixed_table = QTableWidget(content)
        self.security_fixed_table.setColumnCount(3)
        self.security_fixed_table.setHorizontalHeaderLabels([
            "Operace", "Popis", "Ověření",
        ])
        self.security_fixed_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.security_fixed_table.setWordWrap(False)
        self.security_fixed_table.verticalHeader().setVisible(False)
        self.security_fixed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.security_fixed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.security_fixed_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.security_fixed_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.security_fixed_table)
        self.populate_fixed_security_operations(FIXED_OPERATION_DEFINITIONS)

        actions = QHBoxLayout()
        self.security_load_button = QPushButton("Načíst zásady", content)
        self.security_load_button.clicked.connect(self.load_security_policies)
        actions.addWidget(self.security_load_button)
        self.security_save_button = QPushButton("Uložit zásady", content)
        self.security_save_button.clicked.connect(self.save_security_policies)
        actions.addWidget(self.security_save_button)
        layout.addLayout(actions)
        layout.addStretch()
        scroll_area.setWidget(content)
        tab_layout.addWidget(scroll_area)
        self.tabs.addTab(tab, "Zabezpečení")
        self.update_security_mode_ui()

    def local_security_headers(self):
        return (
            {"X-Timekpr-Token": self.local_security_token}
            if self.local_security_token else {}
        )

    def unlock_tunnel_management(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Odemknout vzdálenou správu")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "Toto místní PAM ověření pouze zpřístupní vytvoření SSH tunelu. "
            "Po jeho otevření bude správa hostitele vyžadovat samostatné PAM ověření.",
            dialog,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        username = QComboBox(dialog)
        username.setEditable(True)
        wheel_users = list_wheel_users()
        if self.user not in wheel_users:
            wheel_users.insert(0, self.user)
        username.addItems(wheel_users)
        password = QLineEdit(dialog)
        password.setEchoMode(QLineEdit.Password)
        password.setPlaceholderText("heslo místního wheel uživatele")
        form.addRow("Místní uživatel:", username)
        form.addRow("Heslo:", password)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Ok).setText("Odemknout")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        password.returnPressed.connect(dialog.accept)
        if dialog.exec_() != QDialog.Accepted:
            return
        local_user = username.currentText().strip()
        local_password = password.text()
        password.clear()
        if not local_user or not local_password:
            QMessageBox.warning(self, "Zabezpečení", "Zadej místního wheel uživatele a heslo.")
            return
        try:
            response = requests.post(
                f"{LOCAL_API_URL}/timekpr/auth",
                json={"username": local_user, "password": local_password},
                timeout=8,
            )
            payload = response.json()
            if response.status_code != 200 or not payload.get("token"):
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            self.local_security_token = payload["token"]
            self.security_tunnel_lock_status.setText(
                f"Správa tunelu je místním PAM odemčená pro {local_user}."
            )
            self.update_security_tunnel_ui()
        except requests.ConnectionError:
            QMessageBox.critical(
                self, "Zabezpečení",
                "Místní Game Mover backend není dostupný. Spusť službu příkazem "
                "sudo systemctl enable --now game_mover.service.",
            )
        except Exception as error:
            QMessageBox.critical(self, "Zabezpečení", f"Místní PAM ověření selhalo: {error}")

    def lock_tunnel_management(self, _checked=False):
        if self.managed_ssh_tunnel_running():
            QMessageBox.warning(
                self, "Zabezpečení", "Před zamknutím nejprve zavři spravovaný SSH tunel.",
            )
            return
        self.local_security_token = ""
        self.security_tunnel_lock_status.setText(
            "Nastavení spravovaného SSH tunelu je uzamčené."
        )
        self.update_security_tunnel_ui()

    def managed_ssh_tunnel_running(self):
        return bool(
            self.ssh_tunnel_process
            and self.ssh_tunnel_process.state() != QProcess.NotRunning
        )

    def update_security_tunnel_ui(self):
        unlocked = bool(self.local_security_token)
        running = self.managed_ssh_tunnel_running()
        active = running and self.app_mode == "ssh_tunnel"
        self.security_tunnel_unlock_button.setVisible(not unlocked)
        self.security_tunnel_panel.setVisible(unlocked)
        try:
            profile = self.active_server_profile()
            host = connection_profile_host(profile.get("address", ""))
            target = f"{profile.get('name', profile.get('id', 'Hostitel'))}: {host}"
        except ValueError as error:
            target = str(error)
        self.security_tunnel_target_label.setText(f"Cíl z aktivního profilu: {target}")
        settings_enabled = unlocked and not running and self.app_mode != "server"
        for widget in (
            self.security_ssh_user, self.security_ssh_port, self.ssh_tunnel_port_spin,
        ):
            widget.setEnabled(settings_enabled)
        self.security_tunnel_start_button.setEnabled(settings_enabled)
        self.security_tunnel_stop_button.setEnabled(running)
        self.security_tunnel_lock_button.setEnabled(unlocked and not running)
        if self.app_mode == "server" and not running:
            self.security_tunnel_status.setText(
                "Na tomto počítači je aktivní místní režim Server. Pro tunel zvol nejprve Klient."
            )
        elif active:
            self.security_tunnel_status.setText(
                f"SSH tunel běží na {ssh_tunnel_api_url(self.ssh_tunnel_port)}. "
                "Pro správu hostitele se nyní ověř v Timekpr."
            )
            self.security_tunnel_status.setStyleSheet("color: #66cc66; font-weight: bold;")
        elif running:
            self.security_tunnel_status.setText("SSH proces běží; ověřuji dostupnost hostitelského API…")
            self.security_tunnel_status.setStyleSheet("color: #ffcc66; font-weight: bold;")
        else:
            self.security_tunnel_status.setText("Tunel není spuštěný.")
            self.security_tunnel_status.setStyleSheet("color: #aab7c0;")

    def start_managed_ssh_tunnel(self):
        if self.managed_ssh_tunnel_running() or not self.local_security_token:
            return
        try:
            validation = requests.get(
                f"{LOCAL_API_URL}/timekpr/status",
                headers=self.local_security_headers(), timeout=5,
            )
            if validation.status_code != 200:
                self.lock_tunnel_management()
                raise RuntimeError("Místní PAM relace vypršela; odemkni správu znovu")
            profile = self.active_server_profile()
            host = connection_profile_host(profile.get("address", ""))
            username = self.security_ssh_user.text().strip()
            local_port = int(self.ssh_tunnel_port_spin.value())
            ssh_port = int(self.security_ssh_port.value())
            if local_port == 5000:
                raise ValueError("Port 5000 používá místní Game Mover backend; zvol jiný port")
            arguments = managed_ssh_tunnel_arguments(
                host, username, local_port, ssh_port,
            )
            ssh_program = "/usr/bin/ssh"
            if not os.path.isfile(ssh_program) or not os.access(ssh_program, os.X_OK):
                raise RuntimeError("Chybí důvěryhodný systémový SSH klient /usr/bin/ssh")
        except Exception as error:
            QMessageBox.critical(self, "SSH tunel", str(error))
            return

        self.ssh_user = username
        self.ssh_port = ssh_port
        self.ssh_tunnel_port = local_port
        self.client_config.update({
            "ssh_user": username,
            "ssh_port": ssh_port,
            "ssh_tunnel_port": local_port,
            "app_mode": "client",
        })
        save_client_config(self.client_config)
        self.timekpr_token = ""
        self.set_timekpr_controls_enabled(False)
        self.ssh_tunnel_stopping = False
        self.ssh_tunnel_error_reported = False
        self.ssh_tunnel_ready_deadline = time.monotonic() + 15
        process = QProcess(self)
        process.setProgram(ssh_program)
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.started.connect(self.managed_ssh_tunnel_started)
        process.finished.connect(self.managed_ssh_tunnel_finished)
        process.errorOccurred.connect(self.managed_ssh_tunnel_process_error)
        self.ssh_tunnel_process = process
        self.security_tunnel_status.setText("Spouštím SSH proces…")
        self.update_security_tunnel_ui()
        process.start()

    def managed_ssh_tunnel_started(self):
        self.ssh_tunnel_probe_timer.start()
        self.update_security_tunnel_ui()

    def managed_ssh_process_owns_listener(self):
        """Do not send host credentials through a listener not owned by our ssh PID."""
        if not self.managed_ssh_tunnel_running():
            return False
        try:
            process = psutil.Process(int(self.ssh_tunnel_process.processId()))
            for connection in process.net_connections(kind="tcp"):
                address = connection.laddr
                address_ip = getattr(address, "ip", address[0] if address else None)
                address_port = getattr(address, "port", address[1] if address else None)
                if (
                    connection.status == psutil.CONN_LISTEN
                    and address_ip == "127.0.0.1"
                    and address_port == self.ssh_tunnel_port
                ):
                    return True
        except (psutil.Error, OSError, ValueError):
            return False
        return False

    def probe_managed_ssh_tunnel(self):
        if not self.managed_ssh_tunnel_running():
            self.ssh_tunnel_probe_timer.stop()
            return
        try:
            if not self.managed_ssh_process_owns_listener():
                raise requests.ConnectionError("SSH ještě nevlastní místní listener")
            response = requests.get(
                f"{ssh_tunnel_api_url(self.ssh_tunnel_port)}/health",
                timeout=0.5,
            )
            if response.status_code == 200 and response.json().get("status") == "ok":
                self.ssh_tunnel_probe_timer.stop()
                self.app_mode = "ssh_tunnel"
                self.timekpr_token = ""
                self.set_timekpr_controls_enabled(False)
                self.update_server_mode_ui()
                self.update_security_tunnel_ui()
                self.refresh_server_statuses()
                QMessageBox.information(
                    self, "SSH tunel",
                    "Tunel je připravený. Nyní se v záložce Timekpr ověř vůči hostiteli.",
                )
                return
        except (requests.RequestException, ValueError):
            pass
        if time.monotonic() >= self.ssh_tunnel_ready_deadline:
            self.ssh_tunnel_probe_timer.stop()
            self.ssh_tunnel_error_reported = True
            QMessageBox.critical(
                self, "SSH tunel",
                "SSH proces sice běží, ale hostitelské Game Mover API se přes tunel "
                "do 15 sekund neozvalo.",
            )
            self.stop_managed_ssh_tunnel(silent=True)

    def managed_ssh_tunnel_output(self):
        if not self.ssh_tunnel_process:
            return ""
        raw = bytes(self.ssh_tunnel_process.readAll()).decode("utf-8", errors="replace").strip()
        return raw[-1200:]

    def managed_ssh_tunnel_process_error(self, _error):
        if not self.ssh_tunnel_process or self.ssh_tunnel_error_reported:
            return
        if self.ssh_tunnel_process.state() != QProcess.NotRunning:
            return
        self.ssh_tunnel_error_reported = True
        process = self.ssh_tunnel_process
        detail = process.errorString()
        self.ssh_tunnel_process = None
        process.deleteLater()
        self.deactivate_managed_ssh_tunnel()
        QMessageBox.critical(self, "SSH tunel", f"SSH proces se nepodařilo spustit: {detail}")

    def managed_ssh_tunnel_finished(self, exit_code, _exit_status):
        expected = self.ssh_tunnel_stopping or self.application_closing
        detail = self.managed_ssh_tunnel_output()
        process = self.ssh_tunnel_process
        self.ssh_tunnel_process = None
        if process:
            process.deleteLater()
        self.ssh_tunnel_probe_timer.stop()
        self.deactivate_managed_ssh_tunnel()
        if not expected and not self.ssh_tunnel_error_reported:
            self.ssh_tunnel_error_reported = True
            message = (
                "SSH tunel byl neočekávaně ukončen. Ověř SSH klíč/agent a známý host key."
            )
            if detail:
                message += f"\n\n{detail}"
            else:
                message += f"\n\nSSH skončilo s kódem {exit_code}."
            QMessageBox.critical(self, "SSH tunel", message)

    def deactivate_managed_ssh_tunnel(self):
        if self.app_mode == "ssh_tunnel":
            self.app_mode = "client"
        self.timekpr_token = ""
        self.security_payload = None
        self.global_operation_policies = dict(GLOBAL_OPERATION_DEFAULTS)
        self.set_timekpr_controls_enabled(False)
        self.ssh_tunnel_stopping = False
        self.update_server_mode_ui()
        self.update_security_tunnel_ui()
        if not self.application_closing:
            self.refresh_server_statuses()

    def stop_managed_ssh_tunnel(self, _checked=False, silent=False):
        if not self.managed_ssh_tunnel_running():
            self.deactivate_managed_ssh_tunnel()
            return
        if not silent:
            answer = QMessageBox.question(
                self, "Zavřít SSH tunel",
                "Opravdu ukončit vzdálenou správu hostitele?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.ssh_tunnel_stopping = True
        self.ssh_tunnel_probe_timer.stop()
        self.ssh_tunnel_process.terminate()
        QTimer.singleShot(1500, self.force_stop_managed_ssh_tunnel)

    def force_stop_managed_ssh_tunnel(self):
        if self.managed_ssh_tunnel_running():
            self.ssh_tunnel_process.kill()

    def create_policy_combo(self, parent, policy):
        combo = QComboBox(parent)
        combo.addItem("Tichá", "silent")
        combo.addItem("Vyžaduje PAM", "pam")
        combo.addItem("Zakázaná", "disabled")
        combo.setCurrentIndex(max(0, combo.findData(policy)))
        return combo

    def fit_security_table_height(self, table):
        """Show every table row and let the enclosing page handle scrolling."""
        table.resizeRowsToContents()
        height = table.horizontalHeader().height() + (2 * table.frameWidth()) + 2
        height += sum(table.rowHeight(row) for row in range(table.rowCount()))
        table.setFixedHeight(height)

    def populate_fixed_security_operations(self, definitions):
        self.security_fixed_table.setRowCount(0)
        for definition in definitions:
            row = self.security_fixed_table.rowCount()
            self.security_fixed_table.insertRow(row)
            values = (
                definition.get("label", definition.get("id", "")),
                definition.get("description", ""),
            )
            for column, value in enumerate(values):
                self.security_fixed_table.setItem(row, column, QTableWidgetItem(value))
            policy_combo = QComboBox(self.security_fixed_table)
            policy_combo.addItem("Vyžaduje PAM", "pam")
            policy_combo.setEnabled(False)
            self.security_fixed_table.setCellWidget(row, 2, policy_combo)
        self.fit_security_table_height(self.security_fixed_table)

    def update_security_mode_ui(self):
        enabled = is_host_management_mode(self.app_mode) and bool(self.timekpr_token)
        for widget in (
            self.security_global_table, self.security_server_table,
            self.security_load_button, self.security_save_button,
        ):
            widget.setEnabled(enabled)
        if not is_host_management_mode(self.app_mode):
            text = (
                "Zásady hostitele jsou dostupné v místním režimu Server nebo po otevření "
                "spravovaného SSH tunelu níže."
            )
        elif not self.timekpr_token:
            text = "Pro správu zásad se nyní ověř jako wheel uživatel hostitele v Timekpr."
        elif self.security_payload:
            text = "Bezpečnostní zásady jsou načtené z hostitele."
        else:
            text = "PAM je ověřený; načti bezpečnostní zásady hostitele."
        self.security_status_label.setText(text)
        if hasattr(self, "security_tunnel_panel"):
            self.update_security_tunnel_ui()

    def load_security_policies(self):
        if not is_host_management_mode(self.app_mode) or not self.timekpr_token:
            self.update_security_mode_ui()
            return
        try:
            response = requests.get(
                f"{self.host_management_api_url()}/security/policies",
                headers=self.local_pam_headers(), timeout=10,
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            self.populate_security_tables(payload)
        except Exception as error:
            QMessageBox.critical(self, "Zabezpečení", f"Načtení zásad selhalo: {error}")

    def populate_security_tables(self, payload):
        self.security_payload = payload
        policies = payload.get("policies") if isinstance(payload.get("policies"), dict) else {}
        global_policies = policies.get("global") if isinstance(policies.get("global"), dict) else {}
        server_policies = policies.get("servers") if isinstance(policies.get("servers"), dict) else {}
        catalog = payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {}

        global_definitions = catalog.get("global") if isinstance(catalog.get("global"), list) else []
        self.security_global_table.setRowCount(0)
        for definition in global_definitions:
            operation = definition.get("id", "")
            row = self.security_global_table.rowCount()
            self.security_global_table.insertRow(row)
            name_item = QTableWidgetItem(definition.get("label", operation))
            name_item.setData(Qt.UserRole, operation)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.security_global_table.setItem(row, 0, name_item)
            description_item = QTableWidgetItem(definition.get("description", ""))
            description_item.setFlags(description_item.flags() & ~Qt.ItemIsEditable)
            self.security_global_table.setItem(row, 1, description_item)
            self.security_global_table.setCellWidget(
                row, 2,
                self.create_policy_combo(
                    self.security_global_table,
                    global_policies.get(operation, definition.get("default", "pam")),
                ),
            )

        self.security_server_table.setRowCount(0)
        for server in payload.get("servers", []):
            server_id = server.get("id", "")
            row = self.security_server_table.rowCount()
            self.security_server_table.insertRow(row)
            name_item = QTableWidgetItem(server.get("name", server_id))
            name_item.setData(Qt.UserRole, server_id)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.security_server_table.setItem(row, 0, name_item)
            configured = server_policies.get(server_id, {})
            for column, action in enumerate(SERVER_ACTIONS, start=1):
                self.security_server_table.setCellWidget(
                    row, column,
                    self.create_policy_combo(
                        self.security_server_table, configured.get(action, "disabled"),
                    ),
                )

        self.fit_security_table_height(self.security_global_table)
        self.fit_security_table_height(self.security_server_table)

        fixed_definitions = catalog.get("fixed") if isinstance(catalog.get("fixed"), list) else []
        self.populate_fixed_security_operations(
            fixed_definitions or FIXED_OPERATION_DEFINITIONS,
        )

        self.global_operation_policies = {
            **GLOBAL_OPERATION_DEFAULTS, **global_policies,
        }
        self.update_security_mode_ui()
        self.update_management_action_availability()

    def collect_security_policies(self):
        global_policies = {}
        for row in range(self.security_global_table.rowCount()):
            item = self.security_global_table.item(row, 0)
            combo = self.security_global_table.cellWidget(row, 2)
            if item and combo:
                global_policies[item.data(Qt.UserRole)] = combo.currentData()
        server_policies = {}
        for row in range(self.security_server_table.rowCount()):
            item = self.security_server_table.item(row, 0)
            if not item:
                continue
            server_id = item.data(Qt.UserRole)
            server_policies[server_id] = {}
            for column, action in enumerate(SERVER_ACTIONS, start=1):
                combo = self.security_server_table.cellWidget(row, column)
                server_policies[server_id][action] = (
                    combo.currentData() if combo else "disabled"
                )
        return {"global": global_policies, "servers": server_policies}

    def save_security_policies(self):
        if not is_host_management_mode(self.app_mode) or not self.timekpr_token:
            self.update_security_mode_ui()
            return
        answer = QMessageBox.question(
            self, "Uložit zabezpečení",
            "Opravdu uložit nové zásady? Změny se projeví okamžitě na backendu hostitele.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            response = requests.put(
                f"{self.host_management_api_url()}/security/policies",
                json={"policies": self.collect_security_policies()},
                headers=self.local_pam_headers(), timeout=15,
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            self.populate_security_tables(payload)
            self.refresh_server_statuses()
            QMessageBox.information(
                self, "Zabezpečení",
                payload.get("message", "Bezpečnostní zásady byly uloženy."),
            )
        except Exception as error:
            QMessageBox.critical(self, "Zabezpečení", f"Uložení zásad selhalo: {error}")

    def init_servers_tab(self):
        tab = QWidget(self)
        self.servers_tab = tab
        tab_layout = QVBoxLayout(tab)
        scroll_area = QScrollArea(tab)
        scroll_area.setWidgetResizable(True)
        content = QWidget(scroll_area)
        layout = QVBoxLayout(content)

        title = QLabel("Stav herních serverů")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Stav se automaticky obnovuje každých 10 sekund."))
        self.endpoint_label = QLabel(f"Zdroj: {self.server_api_url()}")
        self.endpoint_label.setStyleSheet("color: #aab7c0;")
        layout.addWidget(self.endpoint_label)

        servers_separator = QFrame(content)
        servers_separator.setFrameShape(QFrame.HLine)
        servers_separator.setFrameShadow(QFrame.Sunken)
        layout.addSpacing(4)
        layout.addWidget(servers_separator)
        layout.addSpacing(4)

        self.server_cards_widget = QWidget(content)
        self.server_cards_layout = QVBoxLayout(self.server_cards_widget)
        self.server_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.server_card_layouts = {}
        layout.addWidget(self.server_cards_widget)

        self.servers_updated_label = QLabel("Poslední aktualizace: —")
        layout.addWidget(self.servers_updated_label)
        server_actions = QHBoxLayout()
        self.minecraft_install_button = QPushButton("Nový Minecraft server…", self)
        self.minecraft_install_button.clicked.connect(self.open_minecraft_installer)
        server_actions.addWidget(self.minecraft_install_button)
        refresh_button = QPushButton("Obnovit stav", self)
        refresh_button.clicked.connect(self.refresh_server_statuses)
        server_actions.addWidget(refresh_button)
        layout.addLayout(server_actions)
        layout.addStretch()
        scroll_area.setWidget(content)
        tab_layout.addWidget(scroll_area)
        self.tabs.addTab(tab, "Servery")

    def init_network_tab(self):
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        title = QLabel("Síťové služby", tab)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        description = QLabel(
            "Privátní autoritativní DNS převádí konkrétní hostname Gate tras na adresu "
            "herního ingressu. DNS integrace jsou volitelné a ve výchozím stavu vypnuté; "
            "Game Mover může použít vlastní službu nebo zapnutý externí provider.",
            tab,
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        self.managed_dns_provider_label = QLabel("—", tab)
        self.managed_dns_zone_label = QLabel("—", tab)
        self.managed_dns_service_label = QLabel("—", tab)
        self.managed_dns_listen_label = QLabel("—", tab)
        self.managed_dns_answer_label = QLabel("—", tab)
        form.addRow("Provider:", self.managed_dns_provider_label)
        form.addRow("Privátní zóna:", self.managed_dns_zone_label)
        form.addRow("Služba:", self.managed_dns_service_label)
        form.addRow("Poslechové adresy:", self.managed_dns_listen_label)
        form.addRow("Adresa Gate v DNS:", self.managed_dns_answer_label)
        layout.addLayout(form)

        self.managed_dns_records = QTableWidget(tab)
        self.managed_dns_records.setColumnCount(2)
        self.managed_dns_records.setHorizontalHeaderLabels(["Hostname Gate", "DNS adresa"])
        self.managed_dns_records.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.managed_dns_records.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.managed_dns_records.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.managed_dns_records.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.managed_dns_records)

        hint = QLabel(
            "LAN router může tuto zónu podmíněně směrovat na poslechovou LAN adresu. "
            "Tailscale použije stejnou zónu jako Restricted nameserver; exit node není nutný. "
            "Pokud port 53 už obsluhuje Pi-hole, nech vestavěný provider vypnutý.",
            tab,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aab7c0;")
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.managed_dns_config_button = QPushButton("Nastavit DNS integraci…", tab)
        self.managed_dns_config_button.clicked.connect(self.open_managed_dns_config)
        actions.addWidget(self.managed_dns_config_button)
        refresh = QPushButton("Obnovit stav", tab)
        refresh.clicked.connect(self.refresh_managed_dns_status)
        actions.addWidget(refresh)
        layout.addLayout(actions)
        self.tabs.addTab(tab, "Síť")

    def refresh_managed_dns_status(self):
        try:
            base_url, headers = self.server_request_target()
            response = requests.get(f"{base_url}/dns/status", headers=headers, timeout=10)
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            dns = payload.get("dns", {})
            provider_names = {
                item.get("id"): item.get("name") for item in dns.get("provider_catalog", [])
            }
            self.managed_dns_provider_label.setText(
                provider_names.get(dns.get("provider"), dns.get("provider", "—"))
            )
            self.managed_dns_zone_label.setText(dns.get("zone", "—"))
            self.managed_dns_service_label.setText(
                "Běží" if dns.get("active") else f"Neběží ({dns.get('service_status', '—')})"
            )
            self.managed_dns_listen_label.setText(
                "zajišťuje Pi-hole" if dns.get("provider") == "pihole_local" else
                ", ".join(dns.get("listen_addresses") or []) or "nenastavené"
            )
            self.managed_dns_answer_label.setText(
                ", ".join(dns.get("answer_addresses") or []) or "nenastavená"
            )
            records = dns.get("records") if isinstance(dns.get("records"), list) else []
            self.managed_dns_records.setRowCount(len(records))
            for row, record in enumerate(records):
                self.managed_dns_records.setItem(row, 0, QTableWidgetItem(record.get("name", "")))
                self.managed_dns_records.setItem(row, 1, QTableWidgetItem(record.get("address", "")))
        except Exception as error:
            self.managed_dns_service_label.setText(f"Stav nelze načíst: {error}")
        self.managed_dns_config_button.setEnabled(is_host_management_mode(self.app_mode))

    def open_managed_dns_config(self):
        if not is_host_management_mode(self.app_mode):
            return
        headers = self.local_operation_headers("dns.config")
        if not headers:
            QMessageBox.warning(self, "Herní DNS", "Změna DNS není podle zásady povolená.")
            return
        try:
            response = requests.get(
                f"{self.host_management_api_url()}/dns/config", headers=headers, timeout=10,
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            current = payload.get("dns", {})
        except Exception as error:
            QMessageBox.critical(self, "Herní DNS", f"Konfiguraci nelze načíst: {error}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("DNS integrace")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        help_label = QLabel(
            "DNS záznamy vznikají automaticky z konkrétních Gate hostname. "
            "Integrace jsou ve výchozím stavu vypnuté. Vestavěná služba používá vlastní "
            "poslechové adresy; lokální Pi-hole provider zapisuje pouze záznamy Game Moveru "
            "do Local DNS Records a ostatní záznamy zachová.", dialog,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        form = QFormLayout()
        provider = QComboBox(dialog)
        provider_catalog = payload.get("provider_catalog")
        if not isinstance(provider_catalog, list):
            provider_catalog = [{"id": "disabled", "name": "Vypnuto"}]
        for item in provider_catalog:
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                provider.addItem(str(item["name"]), str(item["id"]))
        provider.setCurrentIndex(max(0, provider.findData(current.get("provider", "disabled"))))
        zone = QLineEdit(current.get("zone", "mc.home.arpa"), dialog)
        listen = QLineEdit(", ".join(current.get("listen_addresses") or []), dialog)
        listen.setPlaceholderText("např. 192.0.2.66, 100.x.y.z")
        answers = QLineEdit(", ".join(current.get("answer_addresses") or []), dialog)
        answers.setPlaceholderText("např. 192.0.2.66")
        ttl = QSpinBox(dialog)
        ttl.setRange(5, 86400)
        ttl.setValue(int(current.get("ttl", 60)))
        ttl.setSuffix(" s")
        form.addRow("Provider:", provider)
        form.addRow("Zóna:", zone)
        form.addRow("Poslouchat na:", listen)
        form.addRow("Vrácené adresy Gate:", answers)
        form.addRow("TTL:", ttl)
        layout.addLayout(form)

        def update_provider_fields():
            builtin = provider.currentData() == "builtin"
            listen.setEnabled(builtin)
            ttl.setEnabled(builtin)

        provider.currentIndexChanged.connect(update_provider_fields)
        update_provider_fields()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Save).setText("Uložit")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        dns = {
            "provider": provider.currentData(),
            "zone": zone.text().strip(),
            "listen_addresses": [item.strip() for item in listen.text().split(",") if item.strip()],
            "answer_addresses": [item.strip() for item in answers.text().split(",") if item.strip()],
            "ttl": ttl.value(),
        }
        try:
            response = requests.put(
                f"{self.host_management_api_url()}/dns/config",
                json={"dns": dns}, headers=headers, timeout=20,
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            QMessageBox.information(self, "Herní DNS", payload.get("message", "Uloženo"))
        except Exception as error:
            QMessageBox.critical(self, "Herní DNS", f"Uložení selhalo: {error}")
        self.refresh_managed_dns_status()

    def open_modpack_catalog(self, *, version="", loader=""):
        if self.modpacks_tab is not None and self.tabs.indexOf(self.modpacks_tab) >= 0:
            if version:
                self.modpack_version.setText(version)
            loader_index = self.modpack_loader.findData(loader.lower()) if loader else -1
            if loader_index >= 0:
                self.modpack_loader.setCurrentIndex(loader_index)
            self.tabs.setCurrentWidget(self.modpacks_tab)
            return

        tab = QWidget(self.tabs)
        tab.setProperty("dynamic_kind", "modpack_catalog")
        self.modpacks_tab = tab
        layout = QVBoxLayout(tab)
        title = QLabel("CurseForge modpacky", tab)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        help_label = QLabel(
            "Katalog CurseForge server packů.",
            tab,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        status_row = QHBoxLayout()
        self.modpack_status_label = QLabel("Stav API zatím nebyl ověřen.", tab)
        status_row.addWidget(self.modpack_status_label, 1)
        self.modpack_status_button = QPushButton("Ověřit API", tab)
        self.modpack_status_button.clicked.connect(self.refresh_modpack_catalog_status)
        status_row.addWidget(self.modpack_status_button)
        layout.addLayout(status_row)

        filters = QHBoxLayout()
        self.modpack_query = QLineEdit(tab)
        self.modpack_query.setPlaceholderText("Název nebo autor modpacku")
        self.modpack_query.returnPressed.connect(lambda: self.search_modpacks(reset=True))
        filters.addWidget(self.modpack_query, 2)
        self.modpack_version = QLineEdit(version or "1.20.1", tab)
        self.modpack_version.setPlaceholderText("Minecraft verze")
        self.modpack_version.setMaximumWidth(130)
        filters.addWidget(self.modpack_version)
        self.modpack_loader = QComboBox(tab)
        for label, value in (
            ("Všechny loadery", "any"), ("Forge", "forge"),
            ("Fabric", "fabric"), ("NeoForge", "neoforge"), ("Quilt", "quilt"),
        ):
            self.modpack_loader.addItem(label, value)
        loader_index = self.modpack_loader.findData(loader.lower()) if loader else -1
        if loader_index >= 0:
            self.modpack_loader.setCurrentIndex(loader_index)
        filters.addWidget(self.modpack_loader)
        self.modpack_sort = QComboBox(tab)
        for label, value in (
            ("Popularita", "popularity"), ("Naposledy změněné", "updated"),
            ("Název", "name"), ("Stažení", "downloads"),
            ("Datum vydání", "released"), ("Hodnocení", "rating"),
        ):
            self.modpack_sort.addItem(label, value)
        filters.addWidget(self.modpack_sort)
        self.modpack_search_button = QPushButton("Hledat", tab)
        self.modpack_search_button.clicked.connect(lambda: self.search_modpacks(reset=True))
        filters.addWidget(self.modpack_search_button)
        layout.addLayout(filters)

        self.modpack_results = QTableWidget(tab)
        self.modpack_results.setColumnCount(4)
        self.modpack_results.setHorizontalHeaderLabels([
            "Modpack", "Autoři", "Stažení", "Aktualizováno",
        ])
        self.modpack_results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.modpack_results.setSelectionMode(QAbstractItemView.SingleSelection)
        self.modpack_results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        results_header = self.modpack_results.horizontalHeader()
        results_header.setSectionResizeMode(0, QHeaderView.Stretch)
        results_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        results_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        results_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.modpack_results.itemSelectionChanged.connect(self.on_modpack_selected)
        layout.addWidget(self.modpack_results, 2)

        paging = QHBoxLayout()
        self.modpack_previous = QPushButton("Předchozí", tab)
        self.modpack_previous.clicked.connect(self.previous_modpack_page)
        self.modpack_previous.setEnabled(False)
        paging.addWidget(self.modpack_previous)
        self.modpack_page_label = QLabel("Výsledky zatím nebyly načtené.", tab)
        self.modpack_page_label.setAlignment(Qt.AlignCenter)
        paging.addWidget(self.modpack_page_label, 1)
        self.modpack_next = QPushButton("Další", tab)
        self.modpack_next.clicked.connect(self.next_modpack_page)
        self.modpack_next.setEnabled(False)
        paging.addWidget(self.modpack_next)
        layout.addLayout(paging)

        detail_row = QHBoxLayout()
        self.modpack_detail = QLabel("Vyber modpack pro zobrazení dostupných souborů.", tab)
        self.modpack_detail.setWordWrap(True)
        detail_row.addWidget(self.modpack_detail, 1)
        self.modpack_website = QPushButton("Otevřít na CurseForge", tab)
        self.modpack_website.setEnabled(False)
        self.modpack_website.clicked.connect(self.open_selected_modpack_website)
        detail_row.addWidget(self.modpack_website)
        self.modpack_install_button = QPushButton("Nainstalovat server pack…", tab)
        self.modpack_install_button.setEnabled(False)
        self.modpack_install_button.clicked.connect(self.install_selected_server_pack)
        detail_row.addWidget(self.modpack_install_button)
        layout.addLayout(detail_row)

        self.modpack_files = QTableWidget(tab)
        self.modpack_files.setColumnCount(5)
        self.modpack_files.setHorizontalHeaderLabels([
            "Soubor", "Verze", "Vydání", "Velikost", "Server pack",
        ])
        self.modpack_files.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.modpack_files.setSelectionMode(QAbstractItemView.SingleSelection)
        self.modpack_files.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.modpack_files.itemSelectionChanged.connect(self.on_modpack_file_selected)
        files_header = self.modpack_files.horizontalHeader()
        files_header.setSectionResizeMode(0, QHeaderView.Stretch)
        files_header.setSectionResizeMode(1, QHeaderView.Stretch)
        files_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        files_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        files_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self.modpack_files, 1)
        self.tabs.addTab(tab, "Minecraft: Modpacky")
        self.tabs.setCurrentWidget(tab)

    def start_modpack_catalog_request(self, request_kind, *, project_id=None, params=None):
        if self.modpack_catalog_thread and self.modpack_catalog_thread.isRunning():
            return
        base_url, headers = self.server_request_target()
        self.modpack_catalog_thread = ModpackCatalogThread(
            base_url, headers, request_kind, project_id=project_id, params=params,
        )
        self.modpack_catalog_thread.loaded.connect(self.on_modpack_catalog_loaded)
        self.modpack_catalog_thread.start()

    def refresh_modpack_catalog_status(self):
        self.modpack_status_label.setText("Ověřuji konfiguraci CurseForge API…")
        self.start_modpack_catalog_request("status")

    def search_modpacks(self, *, reset):
        if reset:
            self.modpack_search_index = 0
        if self.modpack_loader.currentData() != "any" and not self.modpack_version.text().strip():
            QMessageBox.warning(
                self, "CurseForge modpacky",
                "Při filtrování podle loaderu zadej také verzi Minecraftu.",
            )
            return
        self.modpack_status_label.setText("Načítám modpacky z CurseForge…")
        self.modpack_search_button.setEnabled(False)
        self.start_modpack_catalog_request("search", params={
            "query": self.modpack_query.text().strip(),
            "version": self.modpack_version.text().strip(),
            "loader": self.modpack_loader.currentData(),
            "sort": self.modpack_sort.currentData(),
            "index": self.modpack_search_index,
            "page_size": 20,
        })

    def previous_modpack_page(self):
        self.modpack_search_index = max(0, self.modpack_search_index - 20)
        self.search_modpacks(reset=False)

    def next_modpack_page(self):
        self.modpack_search_index += 20
        self.search_modpacks(reset=False)

    def on_modpack_catalog_loaded(self, payload):
        if self.modpacks_tab is None:
            return
        request_kind = payload.get("request_kind")
        self.modpack_search_button.setEnabled(True)
        if payload.get("error"):
            self.modpack_status_label.setText(f"Načtení selhalo: {payload['error']}")
            return
        if request_kind == "status":
            self.modpack_status_label.setText(
                "CurseForge API je připravené."
                if payload.get("configured")
                else "CurseForge API klíč zatím není nakonfigurovaný."
            )
        elif request_kind == "search":
            self.render_modpack_results(payload)
        elif request_kind == "files":
            self.render_modpack_files(payload)

    def render_modpack_results(self, payload):
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        self.modpack_results.blockSignals(True)
        self.modpack_results.setRowCount(len(items))
        for row, project in enumerate(items):
            values = (
                project.get("name", ""),
                ", ".join(project.get("authors") or []),
                f"{int(project.get('download_count') or 0):,}".replace(",", " "),
                str(project.get("date_modified", "")).replace("T", " ").replace("Z", "")[:19],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, project)
                self.modpack_results.setItem(row, column, item)
        self.modpack_results.blockSignals(False)
        self.selected_modpack = None
        self.selected_modpack_file = None
        self.modpack_files.setRowCount(0)
        self.modpack_detail.setText("Vyber modpack pro zobrazení dostupných souborů.")
        self.modpack_website.setEnabled(False)
        self.modpack_install_button.setEnabled(False)
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        index = int(pagination.get("index") or self.modpack_search_index)
        count = int(pagination.get("resultCount") or len(items))
        total = int(pagination.get("totalCount") or 0)
        first = index + 1 if count else 0
        last = index + count
        self.modpack_page_label.setText(f"{first}–{last} z {total}")
        self.modpack_previous.setEnabled(index > 0)
        self.modpack_next.setEnabled(last < total and last < 10_000)
        self.modpack_status_label.setText(f"Načteno {count} modpacků z CurseForge.")

    def on_modpack_selected(self):
        selected = self.modpack_results.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        name_item = self.modpack_results.item(row, 0)
        project = name_item.data(Qt.UserRole) if name_item else None
        if not isinstance(project, dict) or not project.get("id"):
            return
        self.selected_modpack = project
        authors = ", ".join(project.get("authors") or []) or "neznámý autor"
        summary = project.get("summary") or "Bez popisu."
        self.modpack_detail.setText(f"{project.get('name')} · {authors}\n{summary}")
        website = str(project.get("website_url", ""))
        self.modpack_website.setEnabled(website.startswith("https://www.curseforge.com/"))
        self.modpack_files.setRowCount(0)
        self.selected_modpack_file = None
        self.modpack_install_button.setEnabled(False)
        self.modpack_status_label.setText("Načítám dostupné soubory modpacku…")
        self.start_modpack_catalog_request(
            "files", project_id=project["id"], params={
                "version": self.modpack_version.text().strip(),
                "loader": self.modpack_loader.currentData(),
                "index": 0,
                "page_size": 50,
            },
        )

    def render_modpack_files(self, payload):
        if not self.selected_modpack or payload.get("project_id") != self.selected_modpack.get("id"):
            return
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        release_labels = {1: "Release", 2: "Beta", 3: "Alpha"}
        self.modpack_files.setRowCount(len(items))
        for row, item in enumerate(items):
            size_mib = int(item.get("file_length") or 0) / (1024 ** 2)
            values = (
                item.get("display_name") or item.get("file_name", ""),
                ", ".join(item.get("game_versions") or []),
                release_labels.get(item.get("release_type"), "—"),
                f"{size_mib:.1f} MiB",
                "Ano" if item.get("is_server_pack") or item.get("server_pack_file_id") else "Ne",
            )
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                if column == 0:
                    table_item.setData(Qt.UserRole, item)
                self.modpack_files.setItem(row, column, table_item)
        self.modpack_status_label.setText(
            f"Modpack nabízí {len(items)} odpovídajících souborů."
        )

    def on_modpack_file_selected(self):
        selected = self.modpack_files.selectedItems()
        self.selected_modpack_file = None
        if selected:
            item = self.modpack_files.item(selected[0].row(), 0)
            data = item.data(Qt.UserRole) if item else None
            if isinstance(data, dict):
                self.selected_modpack_file = data
        self.update_modpack_install_availability()

    def update_modpack_install_availability(self):
        if self.modpacks_tab is None or not hasattr(self, "modpack_install_button"):
            return
        item = self.selected_modpack_file or {}
        has_server_pack = bool(item.get("is_server_pack") or item.get("server_pack_file_id"))
        allowed = (
            has_server_pack
            and is_host_management_mode(self.app_mode)
            and bool(self.local_operation_headers("minecraft.install"))
        )
        self.modpack_install_button.setEnabled(allowed)

    def install_selected_server_pack(self):
        project = self.selected_modpack or {}
        item = self.selected_modpack_file or {}
        if not project.get("id") or not item.get("id"):
            return
        if not (item.get("is_server_pack") or item.get("server_pack_file_id")):
            QMessageBox.warning(
                self, "CurseForge server pack", "Vybraný soubor nemá server pack.",
            )
            return
        game_versions = [str(value) for value in item.get("game_versions") or []]
        loader_map = {"forge": "FORGE", "fabric": "FABRIC", "neoforge": "NEOFORGE"}
        pack_loader = next(
            (loader_map[value.lower()] for value in game_versions if value.lower() in loader_map),
            None,
        )
        pack_version = next(
            (value for value in game_versions if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value)),
            "",
        )
        if not pack_loader or not pack_version:
            QMessageBox.warning(
                self, "CurseForge server pack",
                "U vybraného server packu nelze určit podporovaný loader a verzi Minecraftu.",
            )
            return
        self.open_minecraft_installer(curseforge={
            "project_id": int(project["id"]),
            "file_id": int(item["id"]),
            "project_name": str(project.get("name", "")),
            "slug": str(project.get("slug", "")),
            "file_name": str(item.get("display_name") or item.get("file_name", "")),
            "loader": pack_loader,
            "version": pack_version,
        })

    def open_selected_modpack_website(self):
        if not self.selected_modpack:
            return
        website = str(self.selected_modpack.get("website_url", ""))
        if website.startswith("https://www.curseforge.com/"):
            QDesktopServices.openUrl(QUrl(website))

    # ----------------- registr serverů -----------------
    def on_app_mode_changed(self, index):
        new_mode = normalize_app_mode(self.app_mode_combo.itemData(index))
        if new_mode != self.app_mode:
            self.timekpr_token = ""
            self.set_timekpr_controls_enabled(False)
        self.app_mode = new_mode
        self.client_config["app_mode"] = self.app_mode
        save_client_config(self.client_config)
        self.update_server_mode_ui()
        self.refresh_server_statuses()
        self.refresh_launcher_statuses()

    def host_management_api_url(self):
        return management_api_url(self.app_mode, self.ssh_tunnel_port)

    def timekpr_api_url(self):
        if is_host_management_mode(self.app_mode):
            return self.host_management_api_url()
        return FLASK_URL

    def update_server_mode_ui(self):
        server_mode = self.app_mode == "server"
        tunnel_mode = self.app_mode == "ssh_tunnel"
        management_mode = is_host_management_mode(self.app_mode)
        connection_editing = self.connection_edit_checkbox.isChecked()
        self.app_mode_combo.blockSignals(True)
        tunnel_index = self.app_mode_combo.findData("ssh_tunnel")
        if tunnel_mode and tunnel_index < 0:
            self.app_mode_combo.addItem("Hostitel – aktivní spravovaný SSH tunel", "ssh_tunnel")
            tunnel_index = self.app_mode_combo.findData("ssh_tunnel")
        elif not tunnel_mode and tunnel_index >= 0:
            self.app_mode_combo.removeItem(tunnel_index)
        selected_mode = self.app_mode_combo.findData(self.app_mode)
        self.app_mode_combo.setCurrentIndex(max(0, selected_mode))
        self.app_mode_combo.blockSignals(False)
        if server_mode:
            endpoint_text = "Zdroj: místní server"
        elif tunnel_mode:
            endpoint_text = f"Správa hostitele: {self.host_management_api_url()} přes SSH tunel"
        else:
            endpoint_text = f"Zdroj: {self.server_api_url()}"
        self.endpoint_label.setText(endpoint_text)
        self.local_services_label.setText(
            "Sledované služby tohoto počítače" if server_mode
            else (
                "Služby hostitele spravované přes SSH tunel" if tunnel_mode
                else "Správa služeb je dostupná jen v režimu Server nebo přes SSH tunel."
            )
        )
        self.connection_edit_checkbox.setEnabled(not tunnel_mode)
        self.app_mode_combo.setEnabled(connection_editing and not tunnel_mode)
        self.server_profile_combo.setEnabled(not server_mode and not tunnel_mode)
        self.server_profile_test_button.setEnabled(True)
        for widget in self.server_profile_edit_widgets:
            widget.setEnabled(not server_mode and not tunnel_mode and connection_editing)
        for field in (
            self.server_profile_name, self.server_profile_address, self.server_profile_token,
        ):
            field.setReadOnly(server_mode or tunnel_mode or not connection_editing)
        self.update_management_action_availability()
        if hasattr(self, "security_status_label"):
            self.update_security_mode_ui()
        if hasattr(self, "security_tunnel_panel"):
            self.update_security_tunnel_ui()
        if management_mode and self.timekpr_token:
            self.load_local_services()
            if hasattr(self, "security_global_table"):
                self.load_security_policies()

    def update_management_action_availability(self):
        management_mode = is_host_management_mode(self.app_mode)
        registry_enabled = management_mode and bool(
            self.local_operation_headers("server.registry")
        )
        for widget in (
            self.local_services_table, self.local_services_refresh, self.local_services_add,
            self.local_services_remove, self.local_services_save,
        ):
            widget.setEnabled(registry_enabled)
        install_enabled = management_mode and bool(
            self.local_operation_headers("minecraft.install")
        )
        self.minecraft_install_button.setEnabled(install_enabled)
        self.update_modpack_install_availability()
        for entry in self.launcher_cards.values():
            launcher = entry.get("launcher", {})
            entry["update"].setEnabled(
                bool(launcher.get("update_available"))
                and bool(self.local_server_action_headers(self.launcher_update_policy))
                and not (
                    self.launcher_update_thread is not None
                    and self.launcher_update_thread.isRunning()
                )
            )
        if self.server_management_pages:
            self.update_open_server_management_pages()

    def local_pam_headers(self):
        return {"X-Timekpr-Token": self.timekpr_token} if self.timekpr_token else {}

    def host_pam_session_expired(self, detail=""):
        """Fail closed immediately when the host rejects a cached PAM session."""
        self.timekpr_token = ""
        self.security_payload = None
        self.set_timekpr_controls_enabled(False)
        self.update_server_mode_ui()
        message = "Hostitelská PAM relace už není platná. Znovu se ověř v Timekpr."
        return f"{message} ({detail})" if detail else message

    def operation_policy(self, operation):
        return self.global_operation_policies.get(
            operation, GLOBAL_OPERATION_DEFAULTS.get(operation, "disabled"),
        )

    def local_operation_headers(self, operation):
        return self.local_server_action_headers(self.operation_policy(operation))

    def load_local_services(self):
        headers = self.local_operation_headers("server.registry")
        if not is_host_management_mode(self.app_mode) or not headers:
            return
        try:
            response = requests.get(
                f"{self.host_management_api_url()}/servers/config",
                headers=headers, timeout=5,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            self.local_services_table.setRowCount(0)
            for service in data.get("servers", []):
                self.add_local_service_row(service)
        except Exception as error:
            QMessageBox.critical(self, "Služby", f"Načtení služeb selhalo: {error}")

    def add_local_service_row(self, service=None):
        service = service if isinstance(service, dict) else {}
        row = self.local_services_table.rowCount()
        self.local_services_table.insertRow(row)
        backend = service.get("backend", "systemd")
        runtime = service.get("runtime") if isinstance(service.get("runtime"), dict) else {}
        reference = (
            runtime.get("container_name", service.get("container", "mc-server"))
            if backend == "podman"
            else runtime.get("unit", service.get("service", "server.service"))
        )
        data = service.get("data") if isinstance(service.get("data"), dict) else {}
        values = {
            0: service.get("id", f"server-{int(time.time() * 1000)}"),
            1: service.get("name", "Nový server"),
            3: reference,
            5: data.get("directory", ""),
            6: service.get("mods_dir", ""),
        }
        for column, value in values.items():
            item = QTableWidgetItem(str(value))
            if column == 0:
                item.setData(Qt.UserRole, dict(service))
            self.local_services_table.setItem(row, column, item)
        backend_combo = QComboBox(self.local_services_table)
        backend_combo.addItem("systemd", "systemd")
        backend_combo.addItem("Podman", "podman")
        backend_combo.setCurrentIndex(max(0, backend_combo.findData(backend)))
        self.local_services_table.setCellWidget(row, 2, backend_combo)
        kind_combo = QComboBox(self.local_services_table)
        kind_combo.addItem("Obecná", "generic")
        kind_combo.addItem("Minecraft", "minecraft")
        kind = service.get("kind", "generic")
        kind_combo.setCurrentIndex(max(0, kind_combo.findData(kind)))
        self.local_services_table.setCellWidget(row, 4, kind_combo)

    def remove_local_service_rows(self):
        rows = sorted({item.row() for item in self.local_services_table.selectedItems()}, reverse=True)
        for row in rows:
            self.local_services_table.removeRow(row)

    def save_local_services(self):
        headers = self.local_operation_headers("server.registry")
        if not is_host_management_mode(self.app_mode) or not headers:
            QMessageBox.warning(
                self, "Služby",
                "Pro změnu registru serverů chybí oprávnění podle bezpečnostní zásady.",
            )
            return
        servers = []
        for row in range(self.local_services_table.rowCount()):
            def cell_text(column):
                item = self.local_services_table.item(row, column)
                return item.text().strip() if item else ""

            backend_combo = self.local_services_table.cellWidget(row, 2)
            kind_combo = self.local_services_table.cellWidget(row, 4)
            backend = backend_combo.currentData() if backend_combo else "systemd"
            kind = kind_combo.currentData() if kind_combo else "generic"
            runtime_reference = cell_text(3)
            data_directory = cell_text(5)
            mods_dir = cell_text(6)
            if kind == "minecraft":
                if not data_directory.startswith("/"):
                    QMessageBox.warning(self, "Služby", "Minecraft workload musí mít absolutní datový adresář.")
                    self.local_services_table.setCurrentCell(row, 5)
                    return
                if not mods_dir.startswith("/"):
                    QMessageBox.warning(self, "Služby", "Minecraft workload musí mít absolutní cestu k adresáři mods.")
                    self.local_services_table.setCurrentCell(row, 6)
                    return
            id_item = self.local_services_table.item(row, 0)
            original = id_item.data(Qt.UserRole) if id_item else {}
            entry = dict(original) if isinstance(original, dict) else {}
            entry.update({
                "id": cell_text(0), "name": cell_text(1), "backend": backend,
                "kind": kind,
            })
            entry.pop("control_auth", None)
            if backend == "systemd":
                entry["service"] = runtime_reference
                entry["runtime"] = {"unit": runtime_reference}
                entry.pop("container", None)
                entry.pop("management_mode", None)
            else:
                entry["runtime"] = {"container_name": runtime_reference}
                entry.pop("service", None)
                entry["management_mode"] = "adopted"
            if kind == "minecraft":
                entry["mods_dir"] = mods_dir
                entry["data"] = {"directory": data_directory}
                if backend == "podman":
                    entry["data"]["mods_relative_path"] = "mods"
            else:
                entry.pop("mods_dir", None)
                entry.pop("data", None)
            servers.append(entry)
        try:
            response = requests.put(
                f"{self.host_management_api_url()}/servers/config", json={"servers": servers},
                headers=headers, timeout=8,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            QMessageBox.information(self, "Služby", "Seznam služeb byl uložen.")
            self.load_local_services()
            self.refresh_server_statuses()
        except Exception as error:
            QMessageBox.critical(self, "Služby", f"Uložení služeb selhalo: {error}")

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
        self.update_server_mode_ui()

    def on_server_profile_changed(self, index):
        profile_id = self.server_profile_combo.itemData(index)
        if profile_id:
            self.active_server_profile_id = profile_id
            self.load_active_server_profile()
            self.refresh_server_statuses()

    def new_server_profile(self):
        if not self.connection_edit_checkbox.isChecked() or self.app_mode == "server":
            return
        profile_id = f"server-{int(time.time() * 1000)}"
        self.server_profiles.append({"id": profile_id, "name": "Nový server", "mode": "tailscale", "address": "", "read_token": ""})
        self.active_server_profile_id = profile_id
        self.reload_server_profile_combo()

    def delete_server_profile(self):
        if not self.connection_edit_checkbox.isChecked() or self.app_mode == "server":
            return
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
        if not self.connection_edit_checkbox.isChecked() or self.app_mode == "server":
            return
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
        base_url, headers = self.server_request_target()
        try:
            response = requests.get(
                f"{base_url}/servers/status", headers=headers, timeout=5,
            )
            if response.status_code != 200:
                raise RuntimeError(response.json().get("message", f"HTTP {response.status_code}"))
            QMessageBox.information(self, "Servery", "Spojení se serverem funguje.")
        except Exception as error:
            QMessageBox.critical(self, "Servery", f"Spojení selhalo: {error}")

    # ----------------- pomocné -----------------
    def server_request_target(self):
        if is_host_management_mode(self.app_mode):
            return self.host_management_api_url(), {}
        return self.server_api_url(), server_read_headers(self.active_server_profile())

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def render_server_cards(self, servers):
        self.clear_layout(self.server_cards_layout)
        self.server_card_layouts = {}
        colors = {
            "active": "#66cc66", "activating": "#ffcc66", "deactivating": "#ffcc66",
            "inactive": "#aaaaaa", "failed": "#ff6666", "unknown": "#ff6666",
        }
        for server in servers:
            card = QFrame(self.server_cards_widget)
            card.setFrameShape(QFrame.StyledPanel)
            card_layout = QVBoxLayout(card)
            server_id = server.get("id", "")
            self.server_card_layouts[server_id] = card_layout
            name_label = QLabel(server.get("name", server.get("service", "Server")))
            name_label.setStyleSheet("font-size: 16px; font-weight: bold;")
            card_layout.addWidget(name_label)
            status = server.get("status", "unknown")
            status_label = QLabel(f"● {server.get('message', 'Neznámý stav')}")
            status_label.setStyleSheet(f"color: {colors.get(status, '#ff6666')}; font-weight: bold;")
            card_layout.addWidget(status_label)
            recommended_connection = self.server_recommended_connection_text(server)
            if recommended_connection:
                connection_label = QLabel(f"Připojení: {recommended_connection}")
                connection_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                card_layout.addWidget(connection_label)
            if server.get("kind") == "minecraft":
                version_label = QLabel(
                    f"Verze Minecraftu: {self.server_minecraft_version_text(server)}"
                )
                version = server.get("minecraft_version")
                if isinstance(version, dict) and version.get("protocol") is not None:
                    version_label.setToolTip(
                        f"Minecraft protokol: {version['protocol']}"
                    )
                card_layout.addWidget(version_label)
            players = server.get("players") if isinstance(server.get("players"), dict) else {}
            online = players.get("online")
            maximum = players.get("max")
            if online is not None and maximum is not None:
                card_layout.addWidget(QLabel(f"Hráči: {online} / {maximum} online"))
            elif server.get("kind") == "minecraft":
                player_label = QLabel(
                    "Hráči: zjišťuji…" if players.get("query_pending")
                    else "Hráči: nezjištěno"
                )
                if players.get("query_error"):
                    player_label.setToolTip(players["query_error"])
                card_layout.addWidget(player_label)
            known = players.get("known")
            if known is not None:
                card_layout.addWidget(QLabel(f"Již viděno hráčů: {known}"))
            service_label = QLabel(server.get("runtime_label", server.get("service", "")))
            service_label.setStyleSheet("color: #aab7c0;")
            card_layout.addWidget(service_label)
            operation = server.get("operation") if isinstance(server.get("operation"), dict) else {}
            local_operation_running = (
                server.get("kind") == "proxy"
                and self.gate_deploy_thread is not None
                and self.gate_deploy_thread.isRunning()
            )
            if operation.get("running") or local_operation_running:
                operation_label = QLabel(
                    operation.get("message") or "Zahajuji dlouhou operaci…", card,
                )
                operation_label.setStyleSheet("color: #74c0fc; font-weight: bold;")
                card_layout.addWidget(operation_label)
                operation_progress = QProgressBar(card)
                operation_progress.setRange(0, 100)
                operation_progress.setValue(int(operation.get("progress", 1)))
                operation_progress.setFormat("%p %")
                operation_progress.setTextVisible(True)
                card_layout.addWidget(operation_progress)
            actions = QHBoxLayout()
            if is_host_management_mode(self.app_mode) and server.get("kind") == "proxy":
                actions.addStretch()
                routes_button = QPushButton("Směrování…", card)
                routes_button.setFixedWidth(130)
                routes_button.setEnabled(bool(self.local_operation_headers("gate.routes")))
                routes_button.clicked.connect(self.open_gate_routes)
                actions.addWidget(routes_button)
                if not server.get("deployed", False):
                    deploy_button = QPushButton("Nasadit Gate Lite", card)
                    deploy_button.setFixedWidth(150)
                    deploy_button.setEnabled(bool(self.local_operation_headers("gate.deploy")))
                    deploy_button.clicked.connect(self.deploy_gate_proxy)
                    actions.addWidget(deploy_button)
                else:
                    proxy_authorized = bool(self.local_operation_headers("gate.lifecycle"))
                    for action, label, enabled_states in (
                        ("start", "Spustit", ("inactive", "failed")),
                        ("restart", "Restartovat", ("active", "activating")),
                        ("stop", "Vypnout", ("active", "activating")),
                    ):
                        button = QPushButton(label, card)
                        button.setFixedWidth(120)
                        button.setEnabled(proxy_authorized and status in enabled_states)
                        button.clicked.connect(
                            lambda _checked=False, selected_action=action:
                            self.control_gate_proxy(selected_action)
                        )
                        actions.addWidget(button)
            if server.get("kind") != "proxy":
                actions.addStretch()
                if is_host_management_mode(self.app_mode):
                    quick_action = (
                        "start" if status in ("inactive", "failed") else "stop"
                    )
                    permissions = (
                        server.get("permissions")
                        if isinstance(server.get("permissions"), dict) else {}
                    )
                    quick_policy = permissions.get(quick_action, "disabled")
                    quick_button = QPushButton(
                        "Spustit" if quick_action == "start" else "Vypnout", card,
                    )
                    quick_button.setFixedWidth(120)
                    quick_button.setEnabled(
                        bool(self.local_server_action_headers(quick_policy))
                        and status in ("inactive", "failed", "active", "activating")
                    )
                    quick_button.clicked.connect(
                        lambda _checked=False, selected_id=server_id:
                        self.quick_control_server(selected_id)
                    )
                    actions.addWidget(quick_button)
                manage_button = QPushButton("Správa serveru…", card)
                manage_button.setFixedWidth(160)
                manage_button.clicked.connect(
                    lambda _checked=False, selected_id=server_id:
                    self.open_server_management(selected_id)
                )
                actions.addWidget(manage_button)
            if actions.count():
                card_layout.addLayout(actions)
            self.server_cards_layout.addWidget(card)
        self.server_cards_layout.addStretch()

    def server_status_by_id(self, server_id):
        for server in self.last_server_statuses:
            if server.get("id") == server_id:
                return server
        return None

    def active_game_host(self):
        if self.app_mode == "server":
            return "127.0.0.1"
        host, _api_port = self.split_server_address(
            self.active_server_profile().get("address", "")
        )
        return host

    @staticmethod
    def formatted_game_endpoint(host, port):
        host = str(host or "")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{port}"

    def server_direct_connection_text(self, server):
        connection = server.get("connection") if isinstance(server.get("connection"), dict) else {}
        port = connection.get("direct_port")
        if port is None:
            return "Přímé připojení není zveřejněné"
        return self.formatted_game_endpoint(self.active_game_host(), port)

    def server_gate_connection_text(self, server):
        gate = server.get("gate_connection")
        if not isinstance(gate, dict) or gate.get("port") is None:
            return ""
        host = gate.get("host") or self.active_game_host()
        return self.formatted_game_endpoint(host, gate["port"])

    def server_recommended_connection_text(self, server):
        gate = self.server_gate_connection_text(server)
        if gate:
            return gate
        direct = self.server_direct_connection_text(server)
        if direct != "Přímé připojení není zveřejněné":
            return direct
        endpoints = server.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            return self.formatted_game_endpoint(
                self.active_game_host(), endpoints[0].get("port"),
            )
        return ""

    def server_players_text(self, server):
        if server.get("kind") != "minecraft":
            return "Tento typ serveru neposkytuje Minecraft statistiky"
        players = server.get("players") if isinstance(server.get("players"), dict) else {}
        online = players.get("online")
        maximum = players.get("max")
        if online is not None and maximum is not None:
            known = players.get("known")
            known_text = f" · již viděno {known}" if known is not None else ""
            return f"{online} / {maximum} online{known_text}"
        if players.get("query_pending"):
            return "Zjišťuji…"
        return "Nezjištěno"

    def server_minecraft_version_text(self, server):
        version = server.get("minecraft_version")
        if isinstance(version, dict) and version.get("name"):
            return str(version["name"])
        players = server.get("players") if isinstance(server.get("players"), dict) else {}
        if players.get("query_pending"):
            return "zjišťuji…"
        return "nezjištěna"

    def quick_control_server(self, server_id):
        server = self.server_status_by_id(server_id)
        if not server:
            return
        action = "start" if server.get("status") in ("inactive", "failed") else "stop"
        permissions = server.get("permissions") if isinstance(server.get("permissions"), dict) else {}
        self.control_local_server(
            action, server_id, server.get("name", "Server"),
            permissions.get(action, "disabled"),
        )

    def close_server_management_tab(self, index):
        if index < getattr(self, "fixed_tab_count", self.tabs.count()):
            return
        page = self.tabs.widget(index)
        server_id = page.property("server_id") if page else None
        dynamic_kind = page.property("dynamic_kind") if page else None
        self.tabs.removeTab(index)
        if dynamic_kind == "modpack_catalog":
            if self.modpack_catalog_thread and self.modpack_catalog_thread.isRunning():
                try:
                    self.modpack_catalog_thread.loaded.disconnect(
                        self.on_modpack_catalog_loaded
                    )
                except TypeError:
                    pass
            self.modpacks_tab = None
            self.selected_modpack = None
            self.selected_modpack_file = None
        if server_id:
            entry = self.server_management_pages.get(server_id, {})
            timer = entry.get("logs_timer")
            if timer:
                timer.stop()
            self.server_management_pages.pop(server_id, None)
            self.server_mod_inventories.pop(server_id, None)
        if page:
            page.deleteLater()

    def open_server_management(self, server_id):
        existing = self.server_management_pages.get(server_id)
        if existing:
            self.tabs.setCurrentWidget(existing["page"])
            return
        server = self.server_status_by_id(server_id)
        if not server:
            QMessageBox.warning(self, "Správa serveru", "Server už není v registru dostupný.")
            return

        page = QWidget(self.tabs)
        page.setProperty("server_id", server_id)
        outer = QVBoxLayout(page)
        title = QLabel(page)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        outer.addWidget(title)
        subtitle = QLabel(
            "Provozní příkazy se provedou okamžitě. Formulářové změny "
            "jsou před uložením jasně oddělené.", page,
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #aab7c0;")
        outer.addWidget(subtitle)

        sections = QTabWidget(page)
        outer.addWidget(sections)
        overview = QWidget(sections)
        overview_layout = QVBoxLayout(overview)
        overview_form = QFormLayout()
        status_label = QLabel(overview)
        connection_label = QLabel(overview)
        connection_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        direct_connection_caption = QLabel("Přímý backend:", overview)
        direct_connection_label = QLabel(overview)
        direct_connection_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        players_label = QLabel(overview)
        runtime_label = QLabel(overview)
        runtime_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        permissions_label = QLabel(overview)
        permissions_label.setWordWrap(True)
        overview_form.addRow("Stav:", status_label)
        overview_form.addRow("Připojení:", connection_label)
        overview_form.addRow(direct_connection_caption, direct_connection_label)
        minecraft_version_label = None
        if server.get("kind") == "minecraft":
            minecraft_version_label = QLabel(overview)
            minecraft_version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            overview_form.addRow("Verze Minecraftu:", minecraft_version_label)
        overview_form.addRow("Hráči:", players_label)
        overview_form.addRow("Runtime:", runtime_label)
        overview_form.addRow("Oprávnění:", permissions_label)
        overview_layout.addLayout(overview_form)
        endpoint_actions = QHBoxLayout()
        endpoint_actions.addStretch()
        edit_endpoints = QPushButton("Technické síťové porty…", overview)
        edit_endpoints.clicked.connect(
            lambda _checked=False, selected_id=server_id:
            self.edit_server_endpoints(selected_id)
        )
        endpoint_actions.addWidget(edit_endpoints)
        overview_layout.addLayout(endpoint_actions)
        lifecycle = QHBoxLayout()
        lifecycle_buttons = {}
        for action, label in (("start", "Spustit"), ("stop", "Vypnout"), ("restart", "Restartovat")):
            button = QPushButton(label, overview)
            button.clicked.connect(
                lambda _checked=False, selected_action=action, selected_id=server_id:
                self.control_management_server(selected_action, selected_id)
            )
            lifecycle.addWidget(button)
            lifecycle_buttons[action] = button
        lifecycle.addStretch()
        overview_layout.addLayout(lifecycle)
        delete_row = QHBoxLayout()
        delete_help = QLabel(
            "Úplné odstranění je dostupné jen pro platformou spravovaný Podman Minecraft.",
            overview,
        )
        delete_help.setStyleSheet("color: #d9a0a0;")
        delete_help.setWordWrap(True)
        delete_row.addWidget(delete_help, 1)
        delete_server = QPushButton("Odstranit server…", overview)
        delete_server.setStyleSheet(
            "QPushButton { color: #ffb3b3; border-color: #a84a4a; }"
            "QPushButton:hover { background-color: #633333; }"
        )
        delete_server.clicked.connect(
            lambda _checked=False, selected_id=server_id:
            self.delete_management_server(selected_id)
        )
        delete_row.addWidget(delete_server)
        overview_layout.addLayout(delete_row)
        delete_status = QLabel("", overview)
        delete_status.setWordWrap(True)
        delete_status.setStyleSheet("color: #74c0fc;")
        delete_status.setVisible(False)
        overview_layout.addWidget(delete_status)
        delete_progress = QProgressBar(overview)
        delete_progress.setVisible(False)
        overview_layout.addWidget(delete_progress)
        overview_layout.addStretch()
        sections.addTab(overview, "Přehled")

        entry = {
            "page": page,
            "title": title,
            "sections": sections,
            "status": status_label,
            "connection": connection_label,
            "direct_connection_caption": direct_connection_caption,
            "direct_connection": direct_connection_label,
            "minecraft_version": minecraft_version_label,
            "players": players_label,
            "runtime": runtime_label,
            "edit_endpoints": edit_endpoints,
            "permissions": permissions_label,
            "lifecycle": lifecycle_buttons,
            "delete_help": delete_help,
            "delete_server": delete_server,
            "delete_status": delete_status,
            "delete_progress": delete_progress,
        }

        logs_page = QWidget(sections)
        logs_layout = QVBoxLayout(logs_page)
        logs_help = QLabel(
            "Read-only výpis posledních řádků logu: systemd journal nebo Podman container. "
            "Výpis je omezený počtem řádků i velikostí odpovědi.", logs_page,
        )
        logs_help.setWordWrap(True)
        logs_layout.addWidget(logs_help)
        logs_status = QLabel("Log zatím nebyl načten.", logs_page)
        logs_status.setStyleSheet("color: #aab7c0;")
        logs_layout.addWidget(logs_status)
        logs_output = QPlainTextEdit(logs_page)
        logs_output.setReadOnly(True)
        logs_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        logs_output.setPlaceholderText("Výpis logu se zobrazí zde.")
        logs_layout.addWidget(logs_output)
        logs_actions = QHBoxLayout()
        logs_tail = QComboBox(logs_page)
        for value in (50, 100, 250, 500):
            logs_tail.addItem(f"Posledních {value} řádků", value)
        logs_tail.setCurrentIndex(1)
        logs_actions.addWidget(logs_tail)
        logs_reload = QPushButton("Načíst log", logs_page)
        logs_reload.clicked.connect(
            lambda _checked=False, selected_id=server_id:
            self.load_server_logs(selected_id)
        )
        logs_actions.addWidget(logs_reload)
        logs_auto = QCheckBox("Obnovovat každých 5 s", logs_page)
        logs_actions.addWidget(logs_auto)
        logs_actions.addStretch()
        logs_layout.addLayout(logs_actions)
        logs_timer = QTimer(logs_page)
        logs_timer.setInterval(5_000)
        logs_timer.timeout.connect(
            lambda selected_id=server_id: self.load_server_logs(selected_id)
        )
        logs_auto.toggled.connect(
            lambda checked, selected_id=server_id: self.set_server_logs_auto(selected_id, checked)
        )
        sections.addTab(logs_page, "Logy")
        entry.update({
            "logs_status": logs_status,
            "logs_output": logs_output,
            "logs_tail": logs_tail,
            "logs_reload": logs_reload,
            "logs_auto": logs_auto,
            "logs_timer": logs_timer,
        })

        if server.get("kind") == "minecraft":
            properties_page = QWidget(sections)
            properties_layout = QVBoxLayout(properties_page)
            properties_help = QLabel(
                "Formulář upravuje jen běžné validované položky server.properties. "
                "Neznámé položky, komentáře i RCON heslo zůstávají beze změny. "
                "Změny se projeví po restartu serveru.",
                properties_page,
            )
            properties_help.setWordWrap(True)
            properties_layout.addWidget(properties_help)
            properties_status = QLabel(
                "Nastavení zatím nebylo načteno.", properties_page,
            )
            properties_status.setStyleSheet("color: #aab7c0;")
            properties_status.setWordWrap(True)
            properties_layout.addWidget(properties_status)
            properties_form = QFormLayout()
            properties_fields = {}
            motd = QLineEdit(properties_page)
            motd.setMaxLength(120)
            properties_form.addRow("MOTD:", motd)
            properties_fields["motd"] = motd
            world = QLineEdit(properties_page)
            world.setMaxLength(64)
            properties_form.addRow("Název světa:", world)
            properties_fields["level-name"] = world
            for key, label, values in (
                ("gamemode", "Výchozí herní režim:", ("survival", "creative", "adventure", "spectator")),
                ("difficulty", "Obtížnost:", ("peaceful", "easy", "normal", "hard")),
            ):
                combo = QComboBox(properties_page)
                for value in values:
                    combo.addItem(value, value)
                properties_form.addRow(label, combo)
                properties_fields[key] = combo
            max_players = QSpinBox(properties_page)
            max_players.setRange(1, 1000)
            properties_form.addRow("Max. hráčů:", max_players)
            properties_fields["max-players"] = max_players
            for key, label in (
                ("white-list", "Použít whitelist"),
                ("online-mode", "Ověřovat Minecraft účty (online-mode)"),
                ("pvp", "Povolit PvP"),
                ("allow-flight", "Povolit létání"),
                ("enable-command-block", "Povolit command blocky"),
            ):
                checkbox = QCheckBox(label, properties_page)
                properties_form.addRow(checkbox)
                properties_fields[key] = checkbox
            for key, label in (
                ("view-distance", "View distance:"),
                ("simulation-distance", "Simulation distance:"),
            ):
                spin = QSpinBox(properties_page)
                spin.setRange(3, 32)
                properties_form.addRow(label, spin)
                properties_fields[key] = spin
            port_label = QLabel("—", properties_page)
            rcon_label = QLabel("—", properties_page)
            properties_form.addRow("Interní server port (jen informace):", port_label)
            properties_form.addRow("RCON (jen informace):", rcon_label)
            properties_layout.addLayout(properties_form)
            properties_actions = QHBoxLayout()
            properties_reload = QPushButton("Načíst znovu", properties_page)
            properties_reload.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.load_server_properties(selected_id)
            )
            properties_actions.addWidget(properties_reload)
            properties_save = QPushButton("Uložit nastavení", properties_page)
            properties_save.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.save_server_properties(selected_id)
            )
            properties_actions.addWidget(properties_save)
            properties_actions.addStretch()
            properties_layout.addLayout(properties_actions)
            properties_layout.addStretch()
            sections.addTab(properties_page, "Nastavení")
            entry.update({
                "properties_status": properties_status,
                "properties_fields": properties_fields,
                "properties_port": port_label,
                "properties_rcon": rcon_label,
                "properties_reload": properties_reload,
                "properties_save": properties_save,
            })

            players_page = QWidget(sections)
            players_layout = QVBoxLayout(players_page)
            operators_title = QLabel("Operátoři serveru (OP)", players_page)
            operators_title.setStyleSheet("font-size: 16px; font-weight: bold;")
            players_layout.addWidget(operators_title)
            operators_help = QLabel(
                "Seznam se čte z ops.json. Přidání a odebrání se provede okamžitě "
                "přes interní RCON; heslo neopouští hostitele.", players_page,
            )
            operators_help.setWordWrap(True)
            players_layout.addWidget(operators_help)
            operators_status = QLabel("Seznam operátorů zatím nebyl načten.", players_page)
            operators_status.setStyleSheet("color: #aab7c0;")
            operators_status.setWordWrap(True)
            players_layout.addWidget(operators_status)
            operators_table = QTableWidget(players_page)
            operators_table.setColumnCount(3)
            operators_table.setHorizontalHeaderLabels([
                "Minecraft jméno", "Úroveň", "Mimo limit hráčů",
            ])
            operators_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            operators_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            operators_table.setSelectionMode(QAbstractItemView.SingleSelection)
            operators_table.setAlternatingRowColors(True)
            operators_table.verticalHeader().setVisible(False)
            operators_header = operators_table.horizontalHeader()
            operators_header.setSectionResizeMode(0, QHeaderView.Stretch)
            operators_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            operators_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            players_layout.addWidget(operators_table)
            operators_actions = QHBoxLayout()
            operator_name = QLineEdit(players_page)
            operator_name.setMaxLength(16)
            operator_name.setPlaceholderText("Minecraft jméno hráče")
            operators_actions.addWidget(operator_name)
            operator_add = QPushButton("Přidat OP", players_page)
            operator_add.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.change_server_operator(selected_id, "op")
            )
            operators_actions.addWidget(operator_add)
            operator_remove = QPushButton("Odebrat vybraného", players_page)
            operator_remove.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.remove_selected_server_operator(selected_id)
            )
            operators_actions.addWidget(operator_remove)
            operators_reload = QPushButton("Načíst znovu", players_page)
            operators_reload.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.load_server_operators(selected_id)
            )
            operators_actions.addWidget(operators_reload)
            players_layout.addLayout(operators_actions)
            sections.addTab(players_page, "Hráči")
            entry.update({
                "operators_status": operators_status,
                "operators_table": operators_table,
                "operator_name": operator_name,
                "operator_add": operator_add,
                "operator_remove": operator_remove,
                "operators_reload": operators_reload,
            })

            whitelist_page = QWidget(sections)
            whitelist_layout = QVBoxLayout(whitelist_page)
            whitelist_title = QLabel("Povolení hráči (whitelist)", whitelist_page)
            whitelist_title.setStyleSheet("font-size: 16px; font-weight: bold;")
            whitelist_layout.addWidget(whitelist_title)
            whitelist_help = QLabel(
                "Zapnutí whitelistu dovolí nové připojení pouze hráčům v tomto seznamu. "
                "Již připojené nepovolené hráče příkaz automaticky neodpojí.", whitelist_page,
            )
            whitelist_help.setWordWrap(True)
            whitelist_layout.addWidget(whitelist_help)
            whitelist_status = QLabel("Whitelist zatím nebyl načten.", whitelist_page)
            whitelist_status.setStyleSheet("color: #aab7c0;")
            whitelist_status.setWordWrap(True)
            whitelist_layout.addWidget(whitelist_status)
            whitelist_state = QLabel("Stav: —", whitelist_page)
            whitelist_state.setStyleSheet("font-weight: bold;")
            whitelist_layout.addWidget(whitelist_state)
            whitelist_table = QTableWidget(whitelist_page)
            whitelist_table.setColumnCount(2)
            whitelist_table.setHorizontalHeaderLabels(["Minecraft jméno", "UUID"])
            whitelist_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            whitelist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            whitelist_table.setSelectionMode(QAbstractItemView.SingleSelection)
            whitelist_table.setAlternatingRowColors(True)
            whitelist_table.verticalHeader().setVisible(False)
            whitelist_header = whitelist_table.horizontalHeader()
            whitelist_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            whitelist_header.setSectionResizeMode(1, QHeaderView.Stretch)
            whitelist_layout.addWidget(whitelist_table)
            whitelist_actions = QHBoxLayout()
            whitelist_name = QLineEdit(whitelist_page)
            whitelist_name.setMaxLength(16)
            whitelist_name.setPlaceholderText("Minecraft jméno hráče")
            whitelist_actions.addWidget(whitelist_name)
            whitelist_add = QPushButton("Přidat hráče", whitelist_page)
            whitelist_add.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.change_server_whitelist(selected_id, "add")
            )
            whitelist_actions.addWidget(whitelist_add)
            whitelist_remove = QPushButton("Odebrat vybraného", whitelist_page)
            whitelist_remove.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.remove_selected_whitelist_player(selected_id)
            )
            whitelist_actions.addWidget(whitelist_remove)
            whitelist_toggle = QPushButton("Zapnout whitelist", whitelist_page)
            whitelist_toggle.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.toggle_server_whitelist(selected_id)
            )
            whitelist_actions.addWidget(whitelist_toggle)
            whitelist_apply = QPushButton("Reload serveru", whitelist_page)
            whitelist_apply.setToolTip("Načte whitelist.json znovu do běžícího Minecraft serveru")
            whitelist_apply.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.change_server_whitelist(selected_id, "reload")
            )
            whitelist_actions.addWidget(whitelist_apply)
            whitelist_reload = QPushButton("Obnovit zobrazení", whitelist_page)
            whitelist_reload.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.load_server_whitelist(selected_id)
            )
            whitelist_actions.addWidget(whitelist_reload)
            whitelist_layout.addLayout(whitelist_actions)
            sections.addTab(whitelist_page, "Whitelist")
            entry.update({
                "whitelist_status": whitelist_status,
                "whitelist_state": whitelist_state,
                "whitelist_table": whitelist_table,
                "whitelist_name": whitelist_name,
                "whitelist_add": whitelist_add,
                "whitelist_remove": whitelist_remove,
                "whitelist_toggle": whitelist_toggle,
                "whitelist_apply": whitelist_apply,
                "whitelist_reload": whitelist_reload,
                "whitelist_enabled": False,
            })

        if server.get("backup_supported"):
            backups_page = QWidget(sections)
            backups_layout = QVBoxLayout(backups_page)
            backups_help = QLabel(
                "Zálohy jsou ověřené úplné archivy persistentních dat. Obnova do nového "
                "izolovaného serveru je dostupná v instalátoru Minecraftu; bezpečnou obnovu "
                "do tohoto existujícího serveru doplníme jako samostatnou operaci.",
                backups_page,
            )
            backups_help.setWordWrap(True)
            backups_layout.addWidget(backups_help)
            backups_status = QLabel("Katalog záloh zatím nebyl načten.", backups_page)
            backups_status.setStyleSheet("color: #aab7c0;")
            backups_layout.addWidget(backups_status)
            backups_table = QTableWidget(backups_page)
            backups_table.setColumnCount(4)
            backups_table.setHorizontalHeaderLabels(["Vytvořeno", "Velikost", "Zdroj", "ID zálohy"])
            backups_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            backups_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            backups_table.setAlternatingRowColors(True)
            backups_table.verticalHeader().setVisible(False)
            backups_header = backups_table.horizontalHeader()
            backups_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            backups_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            backups_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            backups_header.setSectionResizeMode(3, QHeaderView.Stretch)
            backups_layout.addWidget(backups_table)
            backups_actions = QHBoxLayout()
            create_backup = QPushButton("Vytvořit zálohu", backups_page)
            create_backup.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.backup_management_server(selected_id)
            )
            backups_actions.addWidget(create_backup)
            reload_backups = QPushButton("Obnovit seznam", backups_page)
            reload_backups.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.load_server_backups(selected_id)
            )
            backups_actions.addWidget(reload_backups)
            backups_actions.addStretch()
            backups_layout.addLayout(backups_actions)
            sections.addTab(backups_page, "Zálohy")
            entry.update({
                "backups_status": backups_status,
                "backups_table": backups_table,
                "create_backup": create_backup,
                "reload_backups": reload_backups,
            })

        if server.get("kind") == "minecraft" and server.get("has_mods", True):
            mods_page = QWidget(sections)
            mods_layout = QVBoxLayout(mods_page)
            mods_summary = QLabel("Inventář modů zatím nebyl načten.", mods_page)
            mods_layout.addWidget(mods_summary)
            mods_table = QTableWidget(mods_page)
            mods_table.setColumnCount(3)
            mods_table.setHorizontalHeaderLabels(["Název", "Verze", "JAR soubor"])
            mods_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            mods_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            mods_table.setAlternatingRowColors(True)
            mods_table.setSortingEnabled(True)
            mods_table.verticalHeader().setVisible(False)
            mods_header = mods_table.horizontalHeader()
            mods_header.setSectionResizeMode(0, QHeaderView.Interactive)
            mods_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            mods_header.setSectionResizeMode(2, QHeaderView.Stretch)
            mods_table.setColumnWidth(0, 260)
            mods_layout.addWidget(mods_table)
            mods_actions = QHBoxLayout()
            reload_mods = QPushButton("Načíst mody", mods_page)
            reload_mods.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.load_server_mods(selected_id)
            )
            mods_actions.addWidget(reload_mods)
            compare_mods = QPushButton("Porovnat klientské mody…", mods_page)
            compare_mods.setEnabled(False)
            compare_mods.clicked.connect(
                lambda _checked=False, selected_id=server_id:
                self.choose_client_mods(selected_id)
            )
            mods_actions.addWidget(compare_mods)
            mods_actions.addStretch()
            mods_layout.addLayout(mods_actions)
            mods_diff = QPlainTextEdit(mods_page)
            mods_diff.setReadOnly(True)
            mods_diff.setPlaceholderText("Výsledek porovnání se zobrazí zde.")
            mods_diff.setFixedHeight(140)
            mods_layout.addWidget(mods_diff)
            sections.addTab(mods_page, "Mody")
            entry.update({
                "mods_summary": mods_summary,
                "mods_table": mods_table,
                "reload_mods": reload_mods,
                "compare_mods": compare_mods,
                "mods_diff": mods_diff,
            })

        self.server_management_pages[server_id] = entry
        index = self.tabs.addTab(page, f"Správa: {server.get('name', server_id)}")
        self.tabs.setCurrentIndex(index)
        self.update_server_management_page(server)
        if "logs_output" in entry:
            self.load_server_logs(server_id)
        if "properties_fields" in entry:
            self.load_server_properties(server_id)
        if "operators_table" in entry:
            self.load_server_operators(server_id)
        if "whitelist_table" in entry:
            self.load_server_whitelist(server_id)
        if "mods_table" in entry:
            self.load_server_mods(server_id)
        if "backups_table" in entry and self.local_operation_headers("backup.catalog"):
            self.load_server_backups(server_id)

    def edit_server_endpoints(self, server_id):
        headers = self.local_operation_headers("server.registry")
        if not is_host_management_mode(self.app_mode) or not headers:
            QMessageBox.warning(
                self, "Síťové endpointy",
                "Úprava registru serverů vyžaduje oprávnění správce hostitele.",
            )
            return
        try:
            response = requests.get(
                f"{self.host_management_api_url()}/servers/config",
                headers=headers, timeout=5,
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
            servers = payload.get("servers", [])
            server = next(
                (item for item in servers if item.get("id") == server_id), None,
            )
            if server is None:
                raise RuntimeError("Server už není v registru dostupný")
            endpoints = normalize_endpoints(server.get("endpoints"))
        except Exception as error:
            QMessageBox.critical(
                self, "Síťové endpointy", f"Načtení endpointů selhalo: {error}",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Síťové endpointy – {server.get('name', server_id)}")
        dialog.setMinimumSize(650, 390)
        layout = QVBoxLayout(dialog)
        help_label = QLabel(
            "Zaregistruj všechny porty, které hra zpřístupňuje na hostiteli. "
            "První řádek se používá jako doporučená adresa pro připojení; "
            "TCP a UDP na stejném čísle jsou dva samostatné endpointy.",
            dialog,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        status_server = self.server_status_by_id(server_id) or {}
        automatic_endpoints = [
            endpoint for endpoint in status_server.get("endpoints", [])
            if isinstance(endpoint, dict) and endpoint.get("source") != "registr"
        ]
        if not automatic_endpoints and status_server.get("endpoint_discovery_error"):
            discovery_error = QLabel(
                f"Automatické zjištění selhalo: "
                f"{status_server['endpoint_discovery_error']}",
                dialog,
            )
            discovery_error.setWordWrap(True)
            discovery_error.setStyleSheet("color: #ff8f8f;")
            layout.addWidget(discovery_error)
        table = QTableWidget(dialog)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Název", "Protokol", "Port"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(table)

        def add_endpoint_row(endpoint=None, automatic=False):
            endpoint = endpoint if isinstance(endpoint, dict) else {}
            row = table.rowCount()
            table.insertRow(row)
            name_item = QTableWidgetItem(str(endpoint.get("name", "Hra")))
            name_item.setData(Qt.UserRole, bool(automatic))
            table.setItem(row, 0, name_item)
            if automatic:
                source = str(endpoint.get("source", "automaticky zjištěno"))
                protocol_item = QTableWidgetItem(
                    str(endpoint.get("protocol", "")).upper()
                )
                port_item = QTableWidgetItem(str(endpoint.get("port", "")))
                for item in (name_item, protocol_item, port_item):
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    item.setForeground(QBrush(QColor("#66cc99")))
                    item.setToolTip(f"Automaticky zjištěno: {source}")
                table.setItem(row, 1, protocol_item)
                table.setItem(row, 2, port_item)
                return
            protocol = QComboBox(table)
            protocol.addItem("TCP", "tcp")
            protocol.addItem("UDP", "udp")
            protocol.setCurrentIndex(max(0, protocol.findData(endpoint.get("protocol", "tcp"))))
            table.setCellWidget(row, 1, protocol)
            port = QSpinBox(table)
            port.setRange(1, 65535)
            port.setValue(int(endpoint.get("port", 25565)))
            table.setCellWidget(row, 2, port)
            table.setCurrentCell(row, 0)

        for endpoint in automatic_endpoints:
            add_endpoint_row(endpoint, automatic=True)
        for endpoint in endpoints:
            add_endpoint_row(endpoint)

        row_actions = QHBoxLayout()
        add_button = QPushButton("Přidat endpoint", dialog)
        add_button.clicked.connect(lambda _checked=False: add_endpoint_row())
        row_actions.addWidget(add_button)
        remove_button = QPushButton("Smazat vybraný", dialog)

        def remove_selected_endpoint():
            row = table.currentRow()
            item = table.item(row, 0) if row >= 0 else None
            if item is not None and not item.data(Qt.UserRole):
                table.removeRow(row)

        def update_remove_button():
            row = table.currentRow()
            item = table.item(row, 0) if row >= 0 else None
            remove_button.setEnabled(
                item is not None and not bool(item.data(Qt.UserRole))
            )

        remove_button.clicked.connect(remove_selected_endpoint)
        table.currentCellChanged.connect(
            lambda _row, _column, _previous_row, _previous_column:
            update_remove_button()
        )
        update_remove_button()
        row_actions.addWidget(remove_button)
        row_actions.addStretch()
        layout.addLayout(row_actions)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, dialog,
        )
        buttons.button(QDialogButtonBox.Save).setText("Uložit endpointy")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        while dialog.exec_() == QDialog.Accepted:
            configured = []
            for row in range(table.rowCount()):
                name_item = table.item(row, 0)
                if name_item is not None and name_item.data(Qt.UserRole):
                    continue
                protocol = table.cellWidget(row, 1)
                port = table.cellWidget(row, 2)
                configured.append({
                    "name": name_item.text().strip() if name_item else "",
                    "protocol": protocol.currentData() if protocol else "",
                    "port": port.value() if port else 0,
                })
            try:
                configured = normalize_endpoints(configured)
            except ValueError as error:
                QMessageBox.warning(dialog, "Síťové endpointy", str(error))
                continue
            if configured:
                server["endpoints"] = configured
            else:
                server.pop("endpoints", None)
            try:
                response = requests.put(
                    f"{self.host_management_api_url()}/servers/config",
                    json={"servers": servers}, headers=headers, timeout=8,
                )
                result = response.json()
                if response.status_code != 200:
                    raise RuntimeError(result.get("message", f"HTTP {response.status_code}"))
            except Exception as error:
                QMessageBox.critical(
                    dialog, "Síťové endpointy", f"Uložení endpointů selhalo: {error}",
                )
                continue
            self.load_local_services()
            self.refresh_server_statuses()
            QMessageBox.information(
                self, "Síťové endpointy", "Síťové endpointy byly uloženy.",
            )
            break

    def control_management_server(self, action, server_id):
        server = self.server_status_by_id(server_id)
        if not server:
            return
        permissions = server.get("permissions") if isinstance(server.get("permissions"), dict) else {}
        self.control_local_server(
            action, server_id, server.get("name", "Server"),
            permissions.get(action, "disabled"),
        )

    def backup_management_server(self, server_id):
        server = self.server_status_by_id(server_id)
        if not server:
            return
        permissions = server.get("permissions") if isinstance(server.get("permissions"), dict) else {}
        self.backup_local_server(
            server_id, server.get("name", "Server"),
            permissions.get("backup", "disabled"),
        )

    def delete_management_server(self, server_id):
        server = self.server_status_by_id(server_id)
        if not server or not server.get("deletion_supported"):
            QMessageBox.warning(
                self, "Odstranit server",
                "Úplné odstranění je dostupné jen pro platformou spravovaný Podman Minecraft.",
            )
            return
        if self.server_delete_thread and self.server_delete_thread.isRunning():
            QMessageBox.information(
                self, "Odstranit server", "Jiné odstranění serveru právě probíhá.",
            )
            return
        headers = self.local_operation_headers("minecraft.delete")
        if not is_host_management_mode(self.app_mode) or not headers:
            QMessageBox.warning(
                self, "Odstranit server",
                "Odstranění serveru není podle bezpečnostní zásady povolené.",
            )
            return

        name = server.get("name", server_id)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Odstranit server {name}")
        dialog.setMinimumWidth(620)
        layout = QVBoxLayout(dialog)
        warning = QLabel(
            "Tato operace nevratně odstraní Podman container a registraci serveru. "
            "Příslušné hostname trasy budou odebrány z Gate Lite.",
            dialog,
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #ff9f9f; font-weight: bold;")
        layout.addWidget(warning)
        delete_data = QCheckBox("Smazat persistentní data serveru", dialog)
        delete_data.setChecked(True)
        layout.addWidget(delete_data)
        delete_backups = QCheckBox("Smazat také všechny zálohy tohoto serveru", dialog)
        layout.addWidget(delete_backups)
        detail = QLabel(
            "Bez smazání záloh lze server později obnovit pod novým ID. "
            "Pro úplnou likvidaci zaškrtni obě volby.",
            dialog,
        )
        detail.setWordWrap(True)
        detail.setStyleSheet("color: #aab7c0;")
        layout.addWidget(detail)
        layout.addWidget(QLabel(f"Pro potvrzení napiš přesné ID serveru: {server_id}", dialog))
        confirmation = QLineEdit(dialog)
        confirmation.setPlaceholderText(server_id)
        layout.addWidget(confirmation)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        delete_button = buttons.button(QDialogButtonBox.Ok)
        delete_button.setText("Nevratně odstranit")
        delete_button.setEnabled(False)
        confirmation.textChanged.connect(
            lambda text: delete_button.setEnabled(text == server_id)
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return

        payload = {
            "id": server_id,
            "confirmation": confirmation.text(),
            "delete_data": delete_data.isChecked(),
            "delete_backups": delete_backups.isChecked(),
        }
        self.server_delete_thread = ServerDeleteThread(
            self.host_management_api_url(), payload, headers,
        )
        self.server_delete_thread.completed.connect(self.on_server_delete_completed)
        self.server_delete_thread.start()
        entry = self.server_management_pages.get(server_id)
        if entry:
            entry["delete_server"].setEnabled(False)
            entry["delete_server"].setText("Odstraňuji…")
            entry["delete_status"].setText("Zahajuji bezpečné odstranění serveru…")
            entry["delete_status"].setStyleSheet("color: #74c0fc;")
            entry["delete_status"].setVisible(True)
            entry["delete_progress"].setRange(0, 0)
            entry["delete_progress"].setVisible(True)
        self.operation_refresh_timer.start(1_000)
        QTimer.singleShot(150, self.refresh_server_statuses)
        self.update_open_server_management_pages()

    def on_server_delete_completed(self, payload):
        error = payload.get("error")
        if error:
            if error == "Unauthorized":
                error = self.host_pam_session_expired()
            server_id = payload.get("id", "")
            entry = self.server_management_pages.get(server_id)
            if entry:
                entry["delete_status"].setText(f"Odstranění selhalo: {error}")
                entry["delete_status"].setStyleSheet("color: #ff8f8f;")
                entry["delete_status"].setVisible(True)
                entry["delete_progress"].setVisible(False)
                entry["delete_server"].setText("Odstranit server…")
            self.update_open_server_management_pages()
            return
        server_id = payload.get("id", "")
        entry = self.server_management_pages.get(server_id)
        if entry:
            index = self.tabs.indexOf(entry["page"])
            if index >= 0:
                self.close_server_management_tab(index)
        self.refresh_server_statuses()
        if is_host_management_mode(self.app_mode) and self.timekpr_token:
            self.load_security_policies()

    def update_server_management_page(self, server):
        entry = self.server_management_pages.get(server.get("id"))
        if not entry:
            return
        name = server.get("name", server.get("id", "Server"))
        status = server.get("status", "unknown")
        entry["title"].setText(f"Správa serveru: {name}")
        tab_index = self.tabs.indexOf(entry["page"])
        if tab_index >= 0:
            self.tabs.setTabText(tab_index, f"Správa: {name}")
        entry["status"].setText(server.get("message", "Neznámý stav"))
        gate_connection = self.server_gate_connection_text(server)
        if gate_connection:
            entry["connection"].setText(gate_connection)
            entry["direct_connection"].setText(
                self.server_direct_connection_text(server)
            )
            entry["direct_connection_caption"].setVisible(True)
            entry["direct_connection"].setVisible(True)
        else:
            entry["connection"].setText(
                self.server_recommended_connection_text(server)
                or "Připojení není zveřejněné"
            )
            entry["direct_connection_caption"].setVisible(False)
            entry["direct_connection"].setVisible(False)
        if entry.get("minecraft_version") is not None:
            entry["minecraft_version"].setText(
                self.server_minecraft_version_text(server)
            )
            version = server.get("minecraft_version")
            protocol = version.get("protocol") if isinstance(version, dict) else None
            entry["minecraft_version"].setToolTip(
                f"Minecraft protokol: {protocol}" if protocol is not None else ""
            )
        entry["players"].setText(self.server_players_text(server))
        entry["runtime"].setText(server.get("runtime_label", server.get("service", "—")))
        entry["edit_endpoints"].setEnabled(
            is_host_management_mode(self.app_mode)
            and bool(self.local_operation_headers("server.registry"))
        )
        permissions = server.get("permissions") if isinstance(server.get("permissions"), dict) else {}
        summary = " · ".join(
            f"{SERVER_ACTION_LABELS[action]}: {SERVER_POLICY_LABELS.get(permissions.get(action), 'zakázáno')}"
            for action in SERVER_ACTIONS
            if action != "backup" or server.get("backup_supported")
        )
        entry["permissions"].setText(summary or "Pro tento server nejsou definované provozní akce")
        management = is_host_management_mode(self.app_mode)
        enabled_states = {
            "start": ("inactive", "failed"),
            "stop": ("active", "activating"),
            "restart": ("active", "activating"),
        }
        for action, button in entry["lifecycle"].items():
            policy = permissions.get(action, "disabled")
            button.setEnabled(
                management
                and status in enabled_states[action]
                and bool(self.local_server_action_headers(policy))
            )
        if "create_backup" in entry:
            backup_policy = permissions.get("backup", "disabled")
            entry["create_backup"].setEnabled(
                management and bool(self.local_server_action_headers(backup_policy))
            )
            entry["reload_backups"].setEnabled(
                management and bool(self.local_operation_headers("backup.catalog"))
            )
        deletion_supported = bool(server.get("deletion_supported"))
        entry["delete_help"].setVisible(deletion_supported)
        entry["delete_server"].setVisible(deletion_supported)
        entry["delete_server"].setEnabled(
            deletion_supported
            and management
            and bool(self.local_operation_headers("minecraft.delete"))
            and not (
                self.server_delete_thread is not None
                and self.server_delete_thread.isRunning()
            )
        )
        deletion = server.get("operation")
        deletion = deletion if isinstance(deletion, dict) else {}
        deletion_running = (
            deletion.get("kind") == "minecraft-delete" and deletion.get("running")
        )
        if deletion_running:
            entry["delete_server"].setText("Odstraňuji…")
            entry["delete_server"].setEnabled(False)
            entry["delete_status"].setText(
                deletion.get("message") or "Odstraňuji server…"
            )
            entry["delete_status"].setStyleSheet("color: #74c0fc;")
            entry["delete_status"].setVisible(True)
            entry["delete_progress"].setRange(0, 100)
            entry["delete_progress"].setValue(int(deletion.get("progress", 0)))
            entry["delete_progress"].setVisible(True)
        elif not (
            self.server_delete_thread is not None
            and self.server_delete_thread.isRunning()
        ):
            entry["delete_server"].setText("Odstranit server…")
        if "properties_fields" in entry:
            properties_allowed = management and bool(
                self.local_operation_headers("minecraft.properties")
            )
            for field in entry["properties_fields"].values():
                field.setEnabled(properties_allowed)
            entry["properties_reload"].setEnabled(properties_allowed)
            entry["properties_save"].setEnabled(properties_allowed)
            if not properties_allowed:
                entry["properties_status"].setText(
                    "Nastavení vyžaduje správu hostitele a oprávnění podle zásady „Nastavení Minecraft serveru“."
                )
        if "logs_output" in entry:
            logs_allowed = management and bool(self.local_operation_headers("server.logs"))
            entry["logs_tail"].setEnabled(logs_allowed)
            entry["logs_reload"].setEnabled(logs_allowed)
            entry["logs_auto"].setEnabled(logs_allowed)
            if not logs_allowed:
                entry["logs_auto"].setChecked(False)
                entry["logs_status"].setText(
                    "Logy vyžadují správu hostitele a oprávnění podle zásady „Logy serverů“."
                )
        if "operators_table" in entry:
            operators_allowed = management and bool(
                self.local_operation_headers("minecraft.operators")
            )
            entry["operators_reload"].setEnabled(operators_allowed)
            entry["operator_name"].setEnabled(operators_allowed and status == "active")
            entry["operator_add"].setEnabled(operators_allowed and status == "active")
            entry["operator_remove"].setEnabled(operators_allowed and status == "active")
            if not operators_allowed:
                entry["operators_status"].setText(
                    "Operátoři vyžadují správu hostitele a oprávnění podle zásady „Operátoři Minecraftu“."
                )
            elif status != "active":
                entry["operators_status"].setText(
                    "Seznam lze načíst, ale změny přes RCON vyžadují běžící server."
                )
        if "whitelist_table" in entry:
            whitelist_allowed = management and bool(
                self.local_operation_headers("minecraft.whitelist")
            )
            entry["whitelist_reload"].setEnabled(whitelist_allowed)
            for key in (
                "whitelist_name", "whitelist_add", "whitelist_remove",
                "whitelist_toggle", "whitelist_apply",
            ):
                entry[key].setEnabled(whitelist_allowed and status == "active")
            if not whitelist_allowed:
                entry["whitelist_status"].setText(
                    "Whitelist vyžaduje správu hostitele a oprávnění podle zásady „Whitelist Minecraftu“."
                )
            elif status != "active":
                entry["whitelist_status"].setText(
                    "Seznam lze načíst, ale změny přes RCON vyžadují běžící server."
                )

    def update_open_server_management_pages(self):
        current = {server.get("id"): server for server in self.last_server_statuses}
        for server_id, entry in list(self.server_management_pages.items()):
            server = current.get(server_id)
            if server:
                self.update_server_management_page(server)
            else:
                entry["status"].setText("Server už není v registru dostupný")
                for button in entry["lifecycle"].values():
                    button.setEnabled(False)
                for key in (
                    "create_backup", "reload_backups", "reload_mods", "compare_mods",
                    "logs_tail", "logs_reload", "logs_auto", "operator_name",
                    "operator_add", "operator_remove", "operators_reload",
                    "whitelist_name", "whitelist_add", "whitelist_remove",
                    "whitelist_toggle", "whitelist_apply", "whitelist_reload",
                ):
                    if key in entry:
                        entry[key].setEnabled(False)

    def set_server_logs_auto(self, server_id, enabled):
        entry = self.server_management_pages.get(server_id)
        if not entry:
            return
        if enabled:
            entry["logs_timer"].start()
            self.load_server_logs(server_id)
        else:
            entry["logs_timer"].stop()

    def load_server_logs(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry or "logs_output" not in entry:
            return
        if not is_host_management_mode(self.app_mode):
            entry["logs_status"].setText("Logy jsou dostupné pouze ve správě hostitele.")
            return
        headers = self.local_operation_headers("server.logs")
        if not headers:
            entry["logs_status"].setText("Pro načtení logu chybí oprávnění podle zásady.")
            return
        running = self.server_log_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["logs_status"].setText("Načítám log…")
        entry["logs_reload"].setEnabled(False)
        thread = ServerLogsThread(
            self.host_management_api_url(), server_id,
            entry["logs_tail"].currentData(), headers,
        )
        self.server_log_threads[server_id] = thread
        thread.loaded.connect(self.on_server_logs_loaded)
        thread.start()

    def on_server_logs_loaded(self, result):
        server_id = result.get("server_id", "")
        entry = self.server_management_pages.get(server_id)
        if not entry or "logs_output" not in entry:
            return
        server = self.server_status_by_id(server_id)
        if server:
            self.update_server_management_page(server)
        error = result.get("error")
        if error:
            if "Unauthorized" in error or "403" in error:
                entry["logs_status"].setText(self.host_pam_session_expired("Pak log načti znovu"))
            else:
                entry["logs_status"].setText(f"Načtení logu selhalo: {error}")
            return
        payload = result.get("payload", {})
        output = payload.get("output", "")
        entry["logs_output"].setPlainText(output or "Pro tento výběr nejsou žádné řádky logu.")
        cursor = entry["logs_output"].textCursor()
        cursor.movePosition(cursor.End)
        entry["logs_output"].setTextCursor(cursor)
        updated = str(payload.get("updated_at", "")).replace("T", " ").split("+")[0]
        clipped = " Výpis byl zkrácen na bezpečnou maximální velikost." if payload.get("truncated") else ""
        source_labels = {
            "minecraft-file": "Minecraft latest.log",
            "systemd-runtime": "systemd journal",
            "podman-runtime": "Podman stdout",
        }
        source = source_labels.get(payload.get("source"), "registrovaný zdroj")
        entry["logs_status"].setText(
            f"Log načten z {source}: {updated or 'nyní'}.{clipped}"
        )

    def load_server_properties(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry or "properties_fields" not in entry:
            return
        if not is_host_management_mode(self.app_mode):
            entry["properties_status"].setText("Nastavení je dostupné pouze ve správě hostitele.")
            return
        headers = self.local_operation_headers("minecraft.properties")
        if not headers:
            entry["properties_status"].setText("Pro načtení nastavení chybí oprávnění podle zásady.")
            return
        running = self.server_properties_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["properties_status"].setText("Načítám server.properties…")
        entry["properties_reload"].setEnabled(False)
        entry["properties_save"].setEnabled(False)
        thread = ServerPropertiesThread(
            self.host_management_api_url(), server_id, headers,
        )
        self.server_properties_threads[server_id] = thread
        thread.completed.connect(self.on_server_properties_completed)
        thread.start()

    def current_server_properties(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry:
            return {}
        values = {}
        for key, field in entry["properties_fields"].items():
            if isinstance(field, QLineEdit):
                values[key] = field.text().strip()
            elif isinstance(field, QComboBox):
                values[key] = field.currentData()
            elif isinstance(field, QSpinBox):
                values[key] = field.value()
            elif isinstance(field, QCheckBox):
                values[key] = field.isChecked()
        return values

    def set_server_properties(self, server_id, settings):
        entry = self.server_management_pages.get(server_id)
        if not entry or not isinstance(settings, dict):
            return
        for key, field in entry["properties_fields"].items():
            value = settings.get(key)
            if value is None:
                continue
            if isinstance(field, QLineEdit):
                field.setText(str(value))
            elif isinstance(field, QComboBox):
                field.setCurrentIndex(max(0, field.findData(str(value))))
            elif isinstance(field, QSpinBox):
                field.setValue(int(value))
            elif isinstance(field, QCheckBox):
                field.setChecked(str(value).lower() == "true")

    def save_server_properties(self, server_id):
        entry = self.server_management_pages.get(server_id)
        server = self.server_status_by_id(server_id)
        if not entry or not server:
            return
        headers = self.local_operation_headers("minecraft.properties")
        if not headers:
            QMessageBox.warning(
                self, "Nastavení Minecraftu",
                "Pro uložení nastavení chybí oprávnění podle zásady.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Uložit Minecraft nastavení",
            f"Uložit validované změny server.properties pro {server.get('name', server_id)}?\n\n"
            "Nastavení se uloží bezpečně a projeví se po příštím restartu serveru.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        entry["properties_status"].setText("Ukládám server.properties…")
        entry["properties_reload"].setEnabled(False)
        entry["properties_save"].setEnabled(False)
        thread = ServerPropertiesThread(
            self.host_management_api_url(), server_id, headers,
            settings=self.current_server_properties(server_id),
        )
        self.server_properties_threads[server_id] = thread
        thread.completed.connect(self.on_server_properties_completed)
        thread.start()

    def on_server_properties_completed(self, result):
        server_id = result.get("server_id", "")
        entry = self.server_management_pages.get(server_id)
        if not entry or "properties_fields" not in entry:
            return
        server = self.server_status_by_id(server_id)
        if server:
            self.update_server_management_page(server)
        error = result.get("error")
        if error:
            if "Unauthorized" in error or "403" in error:
                entry["properties_status"].setText(
                    self.host_pam_session_expired("Pak nastavení načti znovu")
                )
            else:
                entry["properties_status"].setText(f"Operace nad server.properties selhala: {error}")
            return
        payload = result.get("payload", {})
        if result.get("action") == "load":
            self.set_server_properties(server_id, payload.get("settings", {}))
            effective = payload.get("effective") if isinstance(payload.get("effective"), dict) else {}
            entry["properties_port"].setText(str(effective.get("server-port", "25565")))
            enabled_rcon = str(effective.get("enable-rcon", "false")).lower() == "true"
            entry["properties_rcon"].setText("zapnuté" if enabled_rcon else "vypnuté")
            entry["properties_status"].setText(
                "Nastavení načteno. Neznámé položky, komentáře a RCON heslo formulář nemění."
            )
            return
        changed = payload.get("changed", [])
        changed_text = ", ".join(changed) if changed else "žádné hodnoty"
        entry["properties_status"].setText(
            f"Nastavení bylo uloženo. Změněno: {changed_text}. Projeví se po restartu serveru."
        )
        self.set_server_properties(server_id, payload.get("settings", {}))
        self.refresh_server_statuses()

    def set_server_operators(self, server_id, operators):
        entry = self.server_management_pages.get(server_id)
        if not entry or "operators_table" not in entry:
            return
        table = entry["operators_table"]
        table.setRowCount(0)
        for operator in operators if isinstance(operators, list) else []:
            row = table.rowCount()
            table.insertRow(row)
            values = (
                operator.get("name", "—"),
                operator.get("level", "—"),
                "ano" if operator.get("bypasses_player_limit") else "ne",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, column, item)

    def load_server_operators(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry or "operators_table" not in entry:
            return
        if not is_host_management_mode(self.app_mode):
            entry["operators_status"].setText(
                "Operátoři jsou dostupní pouze ve správě hostitele."
            )
            return
        headers = self.local_operation_headers("minecraft.operators")
        if not headers:
            entry["operators_status"].setText(
                "Pro načtení operátorů chybí oprávnění podle zásady."
            )
            return
        running = self.server_operator_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["operators_status"].setText("Načítám seznam operátorů…")
        entry["operators_reload"].setEnabled(False)
        thread = ServerOperatorsThread(
            self.host_management_api_url(), server_id, headers,
        )
        self.server_operator_threads[server_id] = thread
        thread.completed.connect(self.on_server_operators_completed)
        thread.start()

    def change_server_operator(self, server_id, action, player=None):
        entry = self.server_management_pages.get(server_id)
        server = self.server_status_by_id(server_id)
        if not entry or not server or server.get("status") != "active":
            return
        player = str(player or entry["operator_name"].text()).strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", player):
            QMessageBox.warning(
                self, "Operátoři Minecraftu",
                "Minecraft jméno musí mít 3–16 znaků: písmena, číslice nebo podtržítko.",
            )
            return
        headers = self.local_operation_headers("minecraft.operators")
        if not headers:
            QMessageBox.warning(
                self, "Operátoři Minecraftu",
                "Pro změnu operátorů chybí oprávnění podle zásady.",
            )
            return
        running = self.server_operator_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["operators_status"].setText(
            f"Provádím {'op' if action == 'op' else 'deop'} pro hráče {player}…"
        )
        entry["operator_add"].setEnabled(False)
        entry["operator_remove"].setEnabled(False)
        thread = ServerOperatorsThread(
            self.host_management_api_url(), server_id, headers,
            action=action, player=player,
        )
        self.server_operator_threads[server_id] = thread
        thread.completed.connect(self.on_server_operators_completed)
        thread.start()

    def remove_selected_server_operator(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry:
            return
        row = entry["operators_table"].currentRow()
        item = entry["operators_table"].item(row, 0) if row >= 0 else None
        if item is None:
            QMessageBox.information(
                self, "Operátoři Minecraftu", "Nejdřív vyber operátora v tabulce."
            )
            return
        player = item.text()
        answer = QMessageBox.question(
            self, "Odebrat operátora",
            f"Odebrat hráči {player} oprávnění OP?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.change_server_operator(server_id, "deop", player)

    def on_server_operators_completed(self, result):
        server_id = result.get("server_id", "")
        entry = self.server_management_pages.get(server_id)
        if not entry or "operators_table" not in entry:
            return
        server = self.server_status_by_id(server_id)
        if server:
            self.update_server_management_page(server)
        error = result.get("error")
        if error:
            if "Unauthorized" in error or "403" in error:
                entry["operators_status"].setText(
                    self.host_pam_session_expired("Pak seznam operátorů načti znovu")
                )
            else:
                entry["operators_status"].setText(f"Operace s operátory selhala: {error}")
            return
        payload = result.get("payload", {})
        operators = payload.get("operators", [])
        self.set_server_operators(server_id, operators)
        action = result.get("action")
        if action == "load":
            entry["operators_status"].setText(
                f"Načteno operátorů: {len(operators)}."
            )
        else:
            entry["operator_name"].clear()
            entry["operators_status"].setText(
                payload.get("message", "RCON změna byla provedena.")
            )

    def set_server_whitelist(self, server_id, payload):
        entry = self.server_management_pages.get(server_id)
        if not entry or "whitelist_table" not in entry:
            return
        enabled = bool(payload.get("enabled"))
        entry["whitelist_enabled"] = enabled
        entry["whitelist_state"].setText(
            "Stav: zapnutý" if enabled else "Stav: vypnutý"
        )
        entry["whitelist_state"].setStyleSheet(
            "font-weight: bold; color: #55d66b;" if enabled
            else "font-weight: bold; color: #e4b95f;"
        )
        entry["whitelist_toggle"].setText(
            "Vypnout whitelist" if enabled else "Zapnout whitelist"
        )
        table = entry["whitelist_table"]
        table.setRowCount(0)
        players = payload.get("players", [])
        for player in players if isinstance(players, list) else []:
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate((player.get("name", "—"), player.get("uuid", "—"))):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, column, item)

    def load_server_whitelist(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry or "whitelist_table" not in entry:
            return
        if not is_host_management_mode(self.app_mode):
            entry["whitelist_status"].setText(
                "Whitelist je dostupný pouze ve správě hostitele."
            )
            return
        headers = self.local_operation_headers("minecraft.whitelist")
        if not headers:
            entry["whitelist_status"].setText(
                "Pro načtení whitelistu chybí oprávnění podle zásady."
            )
            return
        running = self.server_whitelist_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["whitelist_status"].setText("Načítám whitelist…")
        entry["whitelist_reload"].setEnabled(False)
        thread = ServerWhitelistThread(
            self.host_management_api_url(), server_id, headers,
        )
        self.server_whitelist_threads[server_id] = thread
        thread.completed.connect(self.on_server_whitelist_completed)
        thread.start()

    def change_server_whitelist(self, server_id, action, player=None):
        entry = self.server_management_pages.get(server_id)
        server = self.server_status_by_id(server_id)
        if not entry or not server or server.get("status") != "active":
            return
        if action in ("add", "remove"):
            player = str(player or entry["whitelist_name"].text()).strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", player):
                QMessageBox.warning(
                    self, "Whitelist Minecraftu",
                    "Minecraft jméno musí mít 3–16 znaků: písmena, číslice nebo podtržítko.",
                )
                return
        headers = self.local_operation_headers("minecraft.whitelist")
        if not headers:
            QMessageBox.warning(
                self, "Whitelist Minecraftu",
                "Pro změnu whitelistu chybí oprávnění podle zásady.",
            )
            return
        running = self.server_whitelist_threads.get(server_id)
        if running and running.isRunning():
            return
        descriptions = {
            "on": "Zapínám whitelist…",
            "off": "Vypínám whitelist…",
            "add": f"Přidávám hráče {player}…",
            "remove": f"Odebírám hráče {player}…",
            "reload": "Načítám whitelist.json do serveru…",
        }
        entry["whitelist_status"].setText(descriptions[action])
        for key in (
            "whitelist_add", "whitelist_remove", "whitelist_toggle",
            "whitelist_apply", "whitelist_reload",
        ):
            entry[key].setEnabled(False)
        thread = ServerWhitelistThread(
            self.host_management_api_url(), server_id, headers,
            action=action, player=player,
        )
        self.server_whitelist_threads[server_id] = thread
        thread.completed.connect(self.on_server_whitelist_completed)
        thread.start()

    def toggle_server_whitelist(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if entry:
            self.change_server_whitelist(
                server_id, "off" if entry.get("whitelist_enabled") else "on"
            )

    def remove_selected_whitelist_player(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry:
            return
        row = entry["whitelist_table"].currentRow()
        item = entry["whitelist_table"].item(row, 0) if row >= 0 else None
        if item is None:
            QMessageBox.information(
                self, "Whitelist Minecraftu", "Nejdřív vyber hráče v tabulce."
            )
            return
        player = item.text()
        answer = QMessageBox.question(
            self, "Odebrat hráče z whitelistu",
            f"Odebrat hráče {player} z whitelistu?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.change_server_whitelist(server_id, "remove", player)

    def on_server_whitelist_completed(self, result):
        server_id = result.get("server_id", "")
        entry = self.server_management_pages.get(server_id)
        if not entry or "whitelist_table" not in entry:
            return
        server = self.server_status_by_id(server_id)
        if server:
            self.update_server_management_page(server)
        error = result.get("error")
        if error:
            if "Unauthorized" in error or "403" in error:
                entry["whitelist_status"].setText(
                    self.host_pam_session_expired("Pak whitelist načti znovu")
                )
            else:
                entry["whitelist_status"].setText(f"Operace s whitelistem selhala: {error}")
            return
        payload = result.get("payload", {})
        self.set_server_whitelist(server_id, payload)
        if result.get("action") == "load":
            entry["whitelist_status"].setText(
                f"Načteno povolených hráčů: {len(payload.get('players', []))}."
            )
        else:
            entry["whitelist_name"].clear()
            entry["whitelist_status"].setText(
                payload.get("message", "Whitelist změna byla provedena.")
            )

    def load_server_backups(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry or "backups_table" not in entry:
            return
        if not is_host_management_mode(self.app_mode):
            entry["backups_status"].setText("Katalog záloh je dostupný pouze ve správě hostitele.")
            return
        headers = self.local_operation_headers("backup.catalog")
        if not headers:
            entry["backups_status"].setText("Pro katalog záloh chybí oprávnění podle zásady.")
            return
        running = self.server_backups_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["backups_status"].setText("Načítám katalog záloh…")
        entry["reload_backups"].setEnabled(False)
        thread = ServerBackupsThread(
            self.host_management_api_url(), server_id, headers,
        )
        self.server_backups_threads[server_id] = thread
        thread.loaded.connect(self.on_server_backups_loaded)
        thread.start()

    def on_server_backups_loaded(self, payload):
        server_id = payload.get("server_id", "")
        entry = self.server_management_pages.get(server_id)
        if not entry or "backups_table" not in entry:
            return
        entry["reload_backups"].setEnabled(
            is_host_management_mode(self.app_mode)
            and bool(self.local_operation_headers("backup.catalog"))
        )
        error = payload.get("error")
        if error:
            entry["backups_status"].setText(f"Načtení katalogu selhalo: {error}")
            return
        backups = payload.get("backups", [])
        table = entry["backups_table"]
        table.setRowCount(0)
        for backup in backups:
            row = table.rowCount()
            table.insertRow(row)
            size_mib = int(backup.get("size_bytes") or 0) / (1024 * 1024)
            values = (
                backup.get("created_at") or "—",
                f"{size_mib:.1f} MiB",
                backup.get("source_backend") or "—",
                backup.get("id") or "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(row, column, item)
        entry["backups_status"].setText(
            f"Nalezeno záloh: {len(backups)}" if backups else "Pro tento server zatím není žádná záloha."
        )

    def refresh_server_statuses(self):
        if self.server_status_thread is not None and self.server_status_thread.isRunning():
            return
        base_url, headers = self.server_request_target()
        self.server_status_thread = ServerStatusThread(base_url, headers)
        self.server_status_thread.loaded.connect(self.server_status_loaded)
        self.server_status_thread.start()

    def server_status_loaded(self, data):
        error = data.get("request_error")
        if error:
            self.servers_updated_label.setText(f"Poslední aktualizace: chyba ({error})")
            return
        operation_policies = data.get("operation_policies")
        if isinstance(operation_policies, dict):
            self.global_operation_policies = {
                **GLOBAL_OPERATION_DEFAULTS, **operation_policies,
            }
            self.update_management_action_availability()
        servers = list(data.get("servers", []))
        self.last_server_statuses = list(servers)
        proxy = data.get("proxy")
        if isinstance(proxy, dict):
            listen = proxy.get("listen") if isinstance(proxy.get("listen"), dict) else {}
            servers.insert(0, {
                "id": "gate-router",
                "name": "Gate Lite",
                "kind": "proxy",
                "status": proxy.get("status", "unknown"),
                "message": proxy.get("message", "Neznámý stav"),
                "runtime_label": (
                    f"Podman · {proxy.get('network', 'game-platform')} · "
                    f"{proxy.get('routing_mode', 'hostname')} routing · "
                    f"{proxy.get('routes_count', 0)} tras"
                ),
                "connection": {"direct_port": listen.get("port")}
                    if listen.get("port") is not None else {},
                "deployed": proxy.get("deployed", False),
                "operation": proxy.get("operation"),
            })
        has_running_operation = any(
            isinstance(server.get("operation"), dict)
            and server["operation"].get("running")
            for server in servers
        )
        local_worker_running = any(
            worker is not None and worker.isRunning()
            for worker in (
                self.gate_deploy_thread, self.minecraft_install_thread,
                self.server_delete_thread,
            )
        )
        if (
            (has_running_operation or local_worker_running)
            and hasattr(self, "operation_refresh_timer")
            and not self.operation_refresh_timer.isActive()
        ):
            self.operation_refresh_timer.start(1_000)
        elif (
            hasattr(self, "operation_refresh_timer")
            and self.operation_refresh_timer.isActive()
            and not has_running_operation
            and not local_worker_running
        ):
            self.operation_refresh_timer.stop()
        self.render_server_cards(servers)
        self.update_open_server_management_pages()
        updated_at = data.get("updated_at", "")
        if updated_at:
            updated_at = updated_at.replace("T", " ").split("+")[0]
        self.servers_updated_label.setText(f"Poslední aktualizace: {updated_at or '—'}")

    def open_minecraft_installer(self, curseforge=None):
        curseforge = curseforge if isinstance(curseforge, dict) else None
        if not is_host_management_mode(self.app_mode):
            return
        if self.minecraft_install_thread and self.minecraft_install_thread.isRunning():
            QMessageBox.information(self, "Minecraft instalace", "Jiná instalace právě probíhá.")
            return
        headers = self.local_operation_headers("minecraft.install")
        if not headers:
            QMessageBox.warning(
                self, "Minecraft instalace",
                "Instalace není podle bezpečnostní zásady povolená.",
            )
            return

        suggested_port = 25570
        try:
            response = requests.get(
                f"{self.host_management_api_url()}/servers/minecraft/install",
                headers=headers,
                timeout=10,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            suggested_port = int(data["suggested_port"])
        except (KeyError, TypeError, ValueError, requests.RequestException, RuntimeError) as error:
            QMessageBox.warning(
                self,
                "Minecraft instalace",
                "Automatické zjištění volného portu selhalo. "
                f"Zkontroluj proto nabídnutý port ručně.\n\n{error}",
            )

        dialog = QDialog(self)
        dialog.setWindowTitle("Nový Minecraft server")
        dialog.setMinimumWidth(540)
        outer = QVBoxLayout(dialog)
        description = QLabel(
            "Game Mover vytvoří persistentní data a spravovaný rootless Podman container. "
            "Volitelně může data obnovit z ověřené zálohy jiného Minecraft serveru.",
            dialog,
        )
        description.setWordWrap(True)
        outer.addWidget(description)
        form = QFormLayout()
        suggested_id = "novy-minecraft"
        suggested_name = "Nový Minecraft"
        if curseforge:
            suggested_name = curseforge.get("project_name") or suggested_name
            suggested_id = re.sub(r"[^a-z0-9_-]+", "-", curseforge.get("slug", "").lower())
            suggested_id = suggested_id.strip("-_")[:32]
            if not suggested_id or not suggested_id[0].isalpha():
                suggested_id = "minecraft-pack"
        server_id = QLineEdit(suggested_id, dialog)
        name = QLineEdit(suggested_name, dialog)
        loader = QComboBox(dialog)
        loader.addItems(["VANILLA", "FORGE", "FABRIC", "NEOFORGE"])
        if curseforge:
            loader.setCurrentText(curseforge["loader"])
        version = QLineEdit(curseforge.get("version", "1.20.1") if curseforge else "1.20.1", dialog)
        loader_version = QLineEdit("", dialog)
        loader_version.setPlaceholderText("např. 47.4.4 (prázdné = doporučená)")
        memory = QSpinBox(dialog)
        memory.setRange(1024, 24576)
        memory.setSingleStep(1024)
        memory.setValue(8192)
        memory.setSuffix(" MiB")
        java_runtime = QComboBox(dialog)
        java_runtime.addItems(["Java 17", "Java 21"])
        if curseforge:
            version_parts = tuple(int(part) for part in curseforge["version"].split("."))
            if version_parts >= (1, 20, 5):
                java_runtime.setCurrentText("Java 21")
        port = QSpinBox(dialog)
        port.setRange(1024, 65535)
        port.setValue(suggested_port)
        port.setToolTip(
            "První volný port od 25570; před instalací se dostupnost znovu ověří."
        )
        hostname = QLineEdit("", dialog)
        hostname.setPlaceholderText("volitelně, např. forge.mc.example")
        form.addRow("ID serveru:", server_id)
        form.addRow("Název:", name)
        form.addRow("Typ serveru:", loader)
        form.addRow("Minecraft verze:", version)
        form.addRow("Verze loaderu:", loader_version)
        form.addRow("Paměť:", memory)
        form.addRow("Java runtime:", java_runtime)
        form.addRow("Přímý LAN port:", port)
        form.addRow("Gate hostname:", hostname)
        if curseforge:
            source_name = QLabel(curseforge.get("file_name") or curseforge.get("project_name"), dialog)
            source_name.setWordWrap(True)
            form.addRow("Server pack:", source_name)

        restore_checkbox = QCheckBox("Použít data z existující zálohy", dialog)
        source = QComboBox(dialog)
        for server in self.last_server_statuses:
            if server.get("kind") == "minecraft" and server.get("id"):
                source.addItem(server.get("name", server["id"]), server["id"])
        backup = QComboBox(dialog)
        backup.addItem("Nejprve vyber zdroj", None)
        form.addRow(restore_checkbox)
        form.addRow("Zdrojový server:", source)
        form.addRow("Záloha:", backup)
        if curseforge:
            restore_checkbox.setEnabled(False)
            source.setEnabled(False)
            backup.setEnabled(False)
        eula_checkbox = QCheckBox("Souhlasím s Minecraft EULA (aka.ms/MinecraftEULA)", dialog)
        form.addRow(eula_checkbox)
        outer.addLayout(form)

        catalog_requested = {"value": False}
        catalog_button = None
        if not curseforge:
            catalog_button = QPushButton("Procházet CurseForge modpacky…", dialog)
            outer.addWidget(catalog_button)

        def update_loader_fields():
            is_vanilla = loader.currentText() == "VANILLA"
            loader_version.setEnabled(not is_vanilla)
            if is_vanilla:
                loader_version.clear()

        def load_backups():
            backup.clear()
            if not restore_checkbox.isChecked() or source.currentData() is None:
                backup.addItem("Obnova není zvolená", None)
                return
            backup.addItem("Načítám zálohy…", None)
            QApplication.processEvents()
            try:
                response = requests.get(
                    f"{self.host_management_api_url()}/servers/backups",
                    params={"source_id": source.currentData()},
                    headers=self.local_operation_headers("backup.catalog"),
                    timeout=15,
                )
                data = response.json()
                if response.status_code != 200:
                    raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
                backup.clear()
                for item in data.get("backups", []):
                    size_gib = int(item.get("size_bytes") or 0) / (1024 ** 3)
                    backup.addItem(
                        f"{item.get('created_at', item.get('id'))} · {size_gib:.2f} GiB",
                        item.get("id"),
                    )
                if backup.count() == 0:
                    backup.addItem("Pro tento server není žádná záloha", None)
            except Exception as error:
                backup.clear()
                backup.addItem(f"Načtení selhalo: {error}", None)

        def update_restore_fields():
            enabled = restore_checkbox.isChecked()
            source.setEnabled(enabled)
            backup.setEnabled(enabled)
            load_backups()

        loader.currentIndexChanged.connect(update_loader_fields)
        restore_checkbox.toggled.connect(update_restore_fields)
        source.currentIndexChanged.connect(load_backups)
        update_loader_fields()
        update_restore_fields()

        def browse_modpacks():
            catalog_requested["value"] = True
            dialog.reject()

        if catalog_button is not None:
            catalog_button.clicked.connect(browse_modpacks)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Ok).setText("Nainstalovat")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            if catalog_requested["value"]:
                catalog_loader = loader.currentText().lower()
                if catalog_loader == "vanilla":
                    catalog_loader = "any"
                self.open_modpack_catalog(
                    version=version.text().strip(), loader=catalog_loader,
                )
            return
        if restore_checkbox.isChecked() and backup.currentData() is None:
            QMessageBox.warning(self, "Minecraft instalace", "Vyber platnou zálohu k obnovení.")
            return
        if not eula_checkbox.isChecked():
            QMessageBox.warning(self, "Minecraft instalace", "Před instalací potvrď Minecraft EULA.")
            return
        java_tag = "java17" if java_runtime.currentText() == "Java 17" else "java21"
        payload = {
            "id": server_id.text().strip(),
            "name": name.text().strip(),
            "loader": loader.currentText(),
            "version": version.text().strip(),
            "loader_version": loader_version.text().strip(),
            "memory_mb": memory.value(),
            "port": port.value(),
            "hostname": hostname.text().strip(),
            "image": f"docker.io/itzg/minecraft-server:{java_tag}",
            "accept_eula": True,
        }
        if restore_checkbox.isChecked():
            payload["backup"] = {
                "source_id": source.currentData(), "id": backup.currentData(),
            }
        elif curseforge:
            payload["curseforge"] = {
                "project_id": curseforge["project_id"],
                "file_id": curseforge["file_id"],
            }
        restore_text = (
            f"\nData budou obnovena ze zálohy serveru {source.currentText()}."
            if restore_checkbox.isChecked() else "\nServer dostane nový prázdný datový adresář."
        )
        if curseforge:
            restore_text = f"\nBude použit server pack {curseforge.get('file_name', '')}."
        answer = QMessageBox.question(
            self,
            "Potvrdit instalaci",
            f"Vytvořit Podman server {payload['name']} na portu {payload['port']}?"
            f"{restore_text}\n\nZdrojový server ani jeho data se nezmění.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.tabs.setCurrentWidget(self.servers_tab)
        self.minecraft_install_thread = MinecraftInstallThread(
            self.host_management_api_url(), payload, headers,
        )
        self.minecraft_install_thread.completed.connect(self.minecraft_install_completed)
        self.minecraft_install_thread.start()
        self.operation_refresh_timer.start(1_000)
        QTimer.singleShot(150, self.refresh_server_statuses)

    def minecraft_install_completed(self, payload):
        self.operation_refresh_timer.stop()
        if payload.get("error"):
            QMessageBox.critical(
                self, "Minecraft instalace", f"Instalace selhala: {payload['error']}",
            )
        else:
            warning = payload.get("route_warning")
            route_text = (
                f"\n\nServer funguje, ale Gate trasa se nepodařila: {warning}"
                if warning else ""
            )
            QMessageBox.information(
                self, "Minecraft instalace",
                f"{payload.get('message', 'Minecraft server byl nainstalován.')}"
                f"{route_text}",
            )
        self.refresh_server_statuses()

    def deploy_gate_proxy(self):
        if self.gate_deploy_thread and self.gate_deploy_thread.isRunning():
            return
        headers = self.local_operation_headers("gate.deploy")
        if not headers:
            QMessageBox.warning(
                self, "Gate Lite", "Nasazení Gate Lite není podle zásady povolené.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Nasadit Gate Lite",
            "Stáhnout a spustit Gate Lite na testovacím portu 25581?\n\n"
            "Produkční Forge na portu 25565 zůstane beze změny.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.gate_deploy_thread = GateDeployThread(self.host_management_api_url(), headers)
        self.gate_deploy_thread.completed.connect(self.gate_deploy_completed)
        self.gate_deploy_thread.start()
        self.operation_refresh_timer.start(1_000)
        QTimer.singleShot(150, self.refresh_server_statuses)

    def open_gate_routes(self):
        if not is_host_management_mode(self.app_mode):
            return
        if self.gate_routes_thread and self.gate_routes_thread.isRunning():
            QMessageBox.information(self, "Směrování Gate", "Uložení tras právě probíhá.")
            return
        headers = self.local_operation_headers("gate.routes")
        if not headers:
            QMessageBox.warning(
                self, "Směrování Gate", "Úprava tras není podle zásady povolená.",
            )
            return
        try:
            response = requests.get(
                f"{self.host_management_api_url()}/proxy/routes", headers=headers, timeout=15,
            )
            payload = response.json()
            if response.status_code != 200:
                raise RuntimeError(payload.get("message", f"HTTP {response.status_code}"))
        except Exception as error:
            QMessageBox.critical(self, "Směrování Gate", f"Trasy nelze načíst: {error}")
            return

        targets = payload.get("targets", [])
        dialog = QDialog(self)
        dialog.setWindowTitle("Směrování Gate Lite")
        dialog.setMinimumSize(680, 380)
        layout = QVBoxLayout(dialog)
        help_label = QLabel(
            "Hostname určuje adresu zadanou hráčem. Cíl se vybírá z registrovaných "
            "Minecraft serverů; Game Mover sám použije správný host port nebo "
            "privátní jméno spravovaného Podman containeru. Výchozí * trasa musí být právě jedna.",
            dialog,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        table = QTableWidget(dialog)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Hostname", "Cílový server", "Odvozený backend"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(table)

        target_by_id = {target.get("id"): target for target in targets}

        def add_route(host="", target_id=None, backend=None):
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(host))
            combo = QComboBox(table)
            for target in targets:
                combo.addItem(
                    f"{target.get('name')} ({target.get('backend')})", target.get("id"),
                )
            selected = combo.findData(target_id)
            if selected >= 0:
                combo.setCurrentIndex(selected)
            elif target_id is None:
                combo.insertItem(0, "Vyber cílový server", None)
                combo.setCurrentIndex(0)
            table.setCellWidget(row, 1, combo)
            endpoint = target_by_id.get(target_id, {}).get("endpoint", backend or {})
            endpoint_text = (
                f"{endpoint.get('host')}:{endpoint.get('port')}" if endpoint else "nelze odvodit"
            )
            endpoint_item = QTableWidgetItem(endpoint_text)
            endpoint_item.setFlags(endpoint_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 2, endpoint_item)

            def update_endpoint():
                selected_target = target_by_id.get(combo.currentData(), {})
                selected_endpoint = selected_target.get("endpoint", {})
                for current_row in range(table.rowCount()):
                    if table.cellWidget(current_row, 1) is combo:
                        table.item(current_row, 2).setText(
                            f"{selected_endpoint.get('host')}:{selected_endpoint.get('port')}"
                            if selected_endpoint else "nelze odvodit"
                        )
                        break

            combo.currentIndexChanged.connect(update_endpoint)

        for route in payload.get("routes", []):
            add_route(route.get("host", ""), route.get("target_id"), route.get("backend"))

        row_actions = QHBoxLayout()
        add_button = QPushButton("Přidat trasu", dialog)
        add_button.clicked.connect(lambda: add_route())
        row_actions.addWidget(add_button)
        remove_button = QPushButton("Smazat vybranou", dialog)

        def remove_selected():
            rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
            for row in rows:
                table.removeRow(row)

        remove_button.clicked.connect(remove_selected)
        row_actions.addWidget(remove_button)
        default_button = QPushButton("Nastavit jako výchozí (*)", dialog)

        def set_default():
            row = table.currentRow()
            if row < 0:
                return
            for index in range(table.rowCount()):
                item = table.item(index, 0)
                if item and item.text().strip() == "*":
                    item.setText("")
            table.item(row, 0).setText("*")

        default_button.clicked.connect(set_default)
        row_actions.addWidget(default_button)
        layout.addLayout(row_actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, dialog)
        buttons.button(QDialogButtonBox.Save).setText("Uložit směrování")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return

        routes = []
        for row in range(table.rowCount()):
            host_item = table.item(row, 0)
            target_combo = table.cellWidget(row, 1)
            routes.append({
                "host": host_item.text().strip() if host_item else "",
                "target_id": target_combo.currentData() if target_combo else None,
            })
        routes.sort(key=lambda route: route["host"] == "*")
        if not routes or sum(route["host"] == "*" for route in routes) != 1:
            QMessageBox.warning(
                self, "Směrování Gate", "Nastav právě jednu výchozí * trasu.",
            )
            return
        if any(not route["host"] or not route["target_id"] for route in routes):
            QMessageBox.warning(
                self, "Směrování Gate", "Každá trasa musí mít hostname a cílový server.",
            )
            return
        self.gate_routes_thread = GateRoutesSaveThread(
            self.host_management_api_url(), routes, headers,
        )
        self.gate_routes_thread.completed.connect(self.gate_routes_saved)
        self.gate_routes_thread.start()

    def gate_routes_saved(self, payload):
        if payload.get("error"):
            QMessageBox.critical(
                self, "Směrování Gate", f"Uložení tras selhalo: {payload['error']}",
            )
        else:
            QMessageBox.information(
                self, "Směrování Gate",
                payload.get("message", "Směrování Gate Lite bylo uloženo."),
            )
        self.refresh_server_statuses()

    def gate_deploy_completed(self, payload):
        self.operation_refresh_timer.stop()
        if payload.get("error"):
            QMessageBox.critical(self, "Gate Lite", f"Nasazení selhalo: {payload['error']}")
        else:
            listen = payload.get("listen", {})
            QMessageBox.information(
                self, "Gate Lite",
                f"{payload.get('message', 'Gate Lite byl nasazen.')}\n"
                f"Testovací port: {listen.get('port', '—')}",
            )
        self.refresh_server_statuses()

    def control_gate_proxy(self, action):
        if not is_host_management_mode(self.app_mode):
            return
        headers = self.local_operation_headers("gate.lifecycle")
        if not headers:
            QMessageBox.warning(
                self, "Gate Lite", "Ovládání Gate Lite není podle zásady povolené.",
            )
            return
        labels = {
            "start": ("Spustit Gate Lite", "spustit"),
            "stop": ("Vypnout Gate Lite", "vypnout"),
            "restart": ("Restartovat Gate Lite", "restartovat"),
        }
        title, verb = labels[action]
        if action in ("stop", "restart"):
            answer = QMessageBox.question(
                self, title, f"Opravdu {verb} Gate Lite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        try:
            response = requests.post(
                f"{self.host_management_api_url()}/proxy/{action}",
                headers=headers, timeout=210,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            QMessageBox.information(self, "Gate Lite", data.get("message", "Operace dokončena."))
        except Exception as error:
            QMessageBox.critical(self, "Gate Lite", f"Operace selhala: {error}")
        self.refresh_server_statuses()

    def local_server_action_headers(self, policy):
        if policy == "pam":
            return self.local_pam_headers()
        if policy == "silent":
            if self.app_mode == "ssh_tunnel":
                return self.local_pam_headers()
            return self.local_admin_headers()
        return {}

    def control_local_server(self, action, server_id, name, policy):
        if not is_host_management_mode(self.app_mode):
            return
        if action in ("stop", "restart"):
            verb = "vypnout" if action == "stop" else "restartovat"
            title = "Vypnout server" if action == "stop" else "Restartovat server"
            answer = QMessageBox.question(self, title, f"Opravdu {verb} {name}?")
            if answer != QMessageBox.Yes:
                return
        headers = self.local_server_action_headers(policy)
        if not headers:
            QMessageBox.warning(self, "Server", "Pro zvolený způsob ovládání chybí oprávnění.")
            return
        action_label = {
            "start": "Spuštění",
            "stop": "Vypnutí",
            "restart": "Restart",
        }[action]
        try:
            response = requests.post(
                f"{self.host_management_api_url()}/servers/{action}",
                json={"id": server_id}, headers=headers, timeout=210,
            )
            data = response.json()
            if response.status_code != 200:
                raise RuntimeError(data.get("message", f"HTTP {response.status_code}"))
            QMessageBox.information(self, "Server", data.get("message", f"{action_label} dokončeno."))
            self.refresh_server_statuses()
        except Exception as error:
            QMessageBox.critical(self, "Server", f"{action_label} selhalo: {error}")

    def backup_local_server(self, server_id, name, policy):
        if not is_host_management_mode(self.app_mode):
            return
        if self.server_backup_thread and self.server_backup_thread.isRunning():
            QMessageBox.information(self, "Záloha serveru", "Jiná záloha právě probíhá.")
            return
        headers = self.local_server_action_headers(policy)
        if not headers:
            QMessageBox.warning(self, "Záloha serveru", "Pro vytvoření zálohy chybí zvolené oprávnění.")
            return
        answer = QMessageBox.question(
            self,
            "Vytvořit zálohu",
            f"Vytvořit úplnou zálohu dat serveru {name}?\n\n"
            "Pokud server běží, bude po dobu zálohování korektně vypnut a poté znovu spuštěn.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.server_backup_thread = ServerBackupThread(
            self.host_management_api_url(), server_id, headers,
        )
        self.server_backup_server_id = server_id
        self.server_backup_thread.completed.connect(
            lambda payload, selected_id=server_id:
            self.on_server_backup_completed(payload, selected_id)
        )
        self.server_backup_thread.start()
        QMessageBox.information(
            self,
            "Záloha serveru",
            "Záloha byla spuštěna. Okno zůstává použitelné; výsledek se zobrazí po dokončení.",
        )

    def on_server_backup_completed(self, payload, server_id=""):
        error = payload.get("error")
        if error:
            QMessageBox.critical(self, "Záloha serveru", f"Záloha selhala: {error}")
        else:
            backup = payload.get("backup", {})
            size_mib = int(backup.get("size_bytes", 0)) / (1024 * 1024)
            QMessageBox.information(
                self,
                "Záloha serveru",
                f"{payload.get('message', 'Záloha byla dokončena.')}\n"
                f"Velikost: {size_mib:.1f} MiB\n"
                f"Archiv: {backup.get('archive', '—')}",
            )
        if server_id in self.server_management_pages:
            self.load_server_backups(server_id)
        self.refresh_server_statuses()

    def load_server_mods(self, server_id):
        entry = self.server_management_pages.get(server_id)
        if not entry or "mods_table" not in entry:
            return
        running = self.server_mod_threads.get(server_id)
        if running and running.isRunning():
            return
        entry["reload_mods"].setEnabled(False)
        entry["compare_mods"].setEnabled(False)
        entry["mods_summary"].setText("Načítám inventář modů…")
        base_url, headers = self.server_request_target()
        thread = ServerModsThread(base_url, headers, server_id)
        self.server_mod_threads[server_id] = thread
        thread.loaded.connect(self.on_server_mods_loaded)
        thread.start()

    def on_server_mods_loaded(self, payload):
        server_id = payload.get("server_id", "")
        entry = self.server_management_pages.get(server_id)
        if not entry or "mods_table" not in entry:
            return
        entry["reload_mods"].setEnabled(True)
        error = payload.get("error")
        if error:
            entry["mods_summary"].setText(f"Mody: chyba ({error})")
            entry["compare_mods"].setEnabled(False)
            return
        inventory = payload["inventory"]
        self.server_mod_inventories[server_id] = inventory
        table = entry["mods_table"]
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for jar in inventory.get("jars", []):
            for mod in jar.get("mods", []):
                row = table.rowCount()
                table.insertRow(row)
                values = (mod.get("name", ""), mod.get("version", ""), jar.get("filename", "?"))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    table.setItem(row, column, item)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.AscendingOrder)
        count = inventory.get("jar_count", len(inventory.get("jars", [])))
        entry["mods_summary"].setText(f"Server obsahuje {count} JAR souborů")
        entry["compare_mods"].setEnabled(True)

    def choose_client_mods(self, server_id):
        entry = self.server_management_pages.get(server_id)
        inventory = self.server_mod_inventories.get(server_id)
        if not entry or not inventory:
            return
        path = QFileDialog.getExistingDirectory(self, "Vyber klientský adresář mods", os.path.expanduser("~"))
        if not path:
            return
        entry["compare_mods"].setEnabled(False)
        entry["mods_diff"].setPlainText(f"Prohledávám {path}…")
        thread = CompareModsThread(inventory, path)
        self.compare_mod_threads[server_id] = thread
        thread.compared.connect(
            lambda payload, selected_id=server_id:
            self.on_mods_compared(selected_id, payload)
        )
        thread.start()

    def on_mods_compared(self, server_id, payload):
        entry = self.server_management_pages.get(server_id)
        if not entry or "mods_diff" not in entry:
            return
        entry["compare_mods"].setEnabled(server_id in self.server_mod_inventories)
        error = payload.get("error")
        if error:
            entry["mods_diff"].setPlainText(f"Porovnání selhalo: {error}")
            return
        result = payload["result"]
        lines = [f"Server: {result['server_jar_count']} JAR, klient: {result['client_jar_count']} JAR", ""]
        sections = (
            ("CHYBÍ NA KLIENTOVI", "missing"), ("JINÁ VERZE", "version_mismatch"),
            ("JINÝ OBSAH STEJNÉ VERZE", "content_mismatch"), ("NAVÍC NA KLIENTOVI", "extra"),
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
                    lines.append(f"  {item['id']}: server {item['server_version']} / klient {item['client_version']}")
                elif key == "content_mismatch":
                    lines.append(f"  {item['id']}: {item['server_file']} / {item['client_file']}")
                else:
                    version = f" {item.get('version')}" if item.get("version") else ""
                    lines.append(f"  {item['id']}{version} ({item['file']})")
            lines.append("")
        prefix = "Nenalezen žádný chybějící mod ani rozdíl verze/obsahu." if problem_count == 0 else f"Nalezeno {problem_count} potenciálních problémů."
        lines.insert(0, prefix + "\n")
        entry["mods_diff"].setPlainText("\n".join(lines))

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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/auth",
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
                self.update_server_mode_ui()
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
                self.update_server_mode_ui()
                self.refresh_server_statuses()
                self.fetch_day_plan()
            else:
                detail = data.get("error") or "server nevrátil podporovaný mód"
                self.timekpr_status_label.setText("Timekpr neumí přidat čas")
                self.set_timekpr_controls_enabled(False)
                self.update_server_mode_ui()
                QMessageBox.critical(self, "Timekpr", f"Přihlášení proběhlo, ale Timekpr není použitelný: {detail}")
        except requests.ConnectionError:
            backend_url = self.timekpr_api_url()
            guidance = (
                "Zkontroluj spuštěný SSH tunel."
                if self.app_mode == "ssh_tunnel"
                else "Spusť jej příkazem: sudo systemctl enable --now game_mover.service"
            )
            QMessageBox.critical(
                self, "Timekpr",
                f"Game Mover backend na {backend_url} není dostupný.\n{guidance}",
            )
            self.set_timekpr_controls_enabled(False)
            self.update_server_mode_ui()
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))
            self.set_timekpr_controls_enabled(False)
            self.update_server_mode_ui()

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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/add_bonus",
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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/disable_today",
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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/reset_today",
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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/userinfo",
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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/day_plan",
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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/set_hours_today",
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
            resp = requests.post(f"{self.timekpr_api_url()}/timekpr/set_hours_today",
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

    def closeEvent(self, event):
        self.application_closing = True
        self.ssh_tunnel_probe_timer.stop()
        if self.managed_ssh_tunnel_running():
            self.ssh_tunnel_stopping = True
            process = self.ssh_tunnel_process
            process.terminate()
            if not process.waitForFinished(1200):
                process.kill()
                process.waitForFinished(500)
        event.accept()

# ------------------------------------------------------------
# Spuštění aplikace
# ------------------------------------------------------------
if __name__ == '__main__':
    QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv)
    ex = GameMover()
    sys.exit(app.exec_())
