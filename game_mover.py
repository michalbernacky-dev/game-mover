#!/usr/bin/env python3
import os
import sys
import shutil
import requests
import psutil
import grp
import re
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QMessageBox, QComboBox, QProgressBar, QListWidget, QListWidgetItem,
    QTabWidget, QHBoxLayout, QSpinBox, QLineEdit, QTimeEdit
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QCoreApplication, QThread, pyqtSignal

# ------------------------------------------------------------
# KONFIGURACE
# ------------------------------------------------------------
FLASK_URL = "http://127.0.0.1:5000"
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

# ------------------------------------------------------------
# Worker pro přesun
# ------------------------------------------------------------
class MoveThread(QThread):
    finished = pyqtSignal(dict, str)

    def __init__(self, platform, game, user):
        super().__init__()
        self.platform = platform
        self.game = game
        self.user = user

    def run(self):
        try:
            resp = requests.post(f"{FLASK_URL}/move_game", json={
                "platform": self.platform,
                "game_name": self.game,
                "user": self.user
            })
            data = resp.json()
            self.finished.emit(data, self.game)
        except Exception as e:
            self.finished.emit({"message": f"Chyba: {e}"}, self.game)

# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------
class GameMover(QWidget):
    def __init__(self):
        super().__init__()
        self.user = os.getenv("USER") or os.getenv("USERNAME") or "unknown"
        self.platform = "steam"
        self.timekpra_add_flag = None
        self.timekpra_mode = None  # "addflag" nebo "settimeleft"
        self.timekpr_token = ""
        # uchovává "původní" plán pro dnešní den per-uživatel tak, jak se načetl z timekpra
        self.original_hours_today = {}
        self.initUI()
        self.setStyleSheet("""
            QWidget { background-color: #121f28; color: white; }
            QPushButton { background-color: #444444; color: white; }
            QPushButton:disabled { background-color: #888888; color: #AAAAAA; }
        """)

    def initUI(self):
        self.tabs = QTabWidget(self)
        self.init_mover_tab()
        self.init_timekpr_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self.setWindowTitle('Game Mover')
        self.refresh_cache_status()
        self.refresh_dnsmasq_status()
        self.refresh_game_lists()
        self.show()

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

    # ----------------- pomocné -----------------
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
        try:
            resp = requests.post(f"{FLASK_URL}/dnsmasq/stop")
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
                self.fetch_day_plan()
            else:
                self.timekpr_status_label.setText("Timekpr neumí přidat čas (zkontroluj instalaci)")
                self.set_timekpr_controls_enabled(False)
                QMessageBox.critical(self, "Timekpr", "Přihlášení se nezdařilo (chybí token nebo mód).")
        except Exception as e:
            QMessageBox.critical(self, "Timekpr", str(e))
            self.set_timekpr_controls_enabled(False)

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

        try:
            resp = requests.post(f"{FLASK_URL}/set_steam_cache",
                                 json={"user": self.user})
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

        try:
            path = "/var/Games"
            resp = requests.post(f"{FLASK_URL}/fix_perms", json={"path": path}, timeout=30)
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
        self.thread = MoveThread(self.platform, game, self.user)
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
        payload = {"platform": self.platform, "game_name": game, "user": self.user}
        if self.platform in ("gog", "epic", "ubisoft"):
            source_user = self.source_user_combo.currentText()
            if not source_user:
                QMessageBox.warning(self, "Chyba", "Musíš vybrat zdrojového uživatele pro Lutris prefix")
                return
            payload["source_user"] = source_user

        try:
            resp = requests.post(f"{FLASK_URL}/create_symlink", json=payload)
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
