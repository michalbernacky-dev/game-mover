#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request
import glob
import os
import shutil
import grp
import subprocess
import fileinput
import configparser
from typing import Optional
from dataclasses import dataclass
import time
import secrets
import datetime
import sys
import json

from game_mover_mods import scan_mod_directory

app = Flask(__name__)

# ------------------------------------------------------------
# KONFIGURACE
# ------------------------------------------------------------
GAMES_ROOT = "/var/Games"
GROUP_NAME = "gemers"
LOCAL_ADMIN_TOKEN_DIR = "/etc/game_mover"
LOCAL_ADMIN_TOKEN_PATH = os.path.join(LOCAL_ADMIN_TOKEN_DIR, "api.token")
LOCAL_ADMIN_TOKEN_HEADER = "X-Game-Mover-Token"
READ_TOKEN_PATH = os.getenv("GAME_MOVER_READ_TOKEN_PATH", "/etc/game_mover/read.token")
READ_TOKEN_HEADER = "X-Game-Mover-Read-Token"
MINECRAFT_MODS_DIR = os.getenv("GAME_MOVER_MINECRAFT_MODS_DIR", "/opt/forge_srv/mods")
GAME_SERVERS_CONFIG_PATH = os.path.join(LOCAL_ADMIN_TOKEN_DIR, "servers.json")


def default_game_servers():
    return [
        {"id": "minecraft", "name": "Minecraft", "service": os.getenv("GAME_MOVER_MINECRAFT_SERVICE", "forge-srv.service"), "kind": "minecraft", "mods_dir": MINECRAFT_MODS_DIR},
        {"id": "satisfactory", "name": "Satisfactory", "service": os.getenv("GAME_MOVER_SATISFACTORY_SERVICE", "satisfactory.service"), "kind": "generic"},
    ]


def load_game_servers():
    try:
        with open(GAME_SERVERS_CONFIG_PATH, "r") as config_file:
            servers = json.load(config_file)
        if isinstance(servers, list):
            return [server for server in servers if isinstance(server, dict)]
    except Exception:
        pass
    return default_game_servers()


def save_game_servers(servers):
    os.makedirs(LOCAL_ADMIN_TOKEN_DIR, exist_ok=True)
    temporary_path = f"{GAME_SERVERS_CONFIG_PATH}.tmp"
    with open(temporary_path, "w") as config_file:
        json.dump(servers, config_file, indent=2)
        config_file.write("\n")
    try:
        os.chown(temporary_path, 0, grp.getgrnam(GROUP_NAME).gr_gid)
        os.chmod(temporary_path, 0o640)
    except (KeyError, PermissionError):
        os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, GAME_SERVERS_CONFIG_PATH)


def find_game_server(server_id):
    for server in load_game_servers():
        if server.get("id") == server_id:
            return server
    return None

EXCLUDE_PREFIXES = ("SteamLinuxRuntime", "Proton")
EXCLUDE_LIST = {
    "steam": ["Half-Life Dedicated Server"],
    "gog": [],
    "epic": [],
    "ubisoft": [],
    "rockstar": []
}

TIMEKPRA_BIN = "timekpra"
TIMEKPRA_BIN_RESOLVED: list[str] | None = None
TIMEKPRA_ADD_FLAG_CANDIDATES = ["--addtime", "--add-allowedtime", "--addallowedtime"]
TIMEKPRA_SET_TIMELEFT = "--settimeleft"
TIMEKPRA_DISABLE_ARGS = ["--disable"]
TIMEKPRA_DISABLE_SECONDS = 24 * 3600
TIMEKPRA_SET_ALLOWED_HOURS = "--setallowedhours"

TOKEN_TTL_SECONDS = 15 * 60
TIMEKPRA_TOKENS = {}  # token -> (username, expiry)

# ------------------------------------------------------------
# Platform user common
# ------------------------------------------------------------
def steam_common_candidates(user):
    return [
        f"/home/{user}/.steam/steam/steamapps/common",
        f"/home/{user}/.local/share/Steam/steamapps/common",
    ]

def gog_common_candidates(user):
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

def epic_common_candidates(user):
    return [
        f"/home/{user}/Games/Epic",
        f"/home/{user}/Epic Games",
        f"/home/{user}/Games/Heroic/Epic",
    ]

def ubisoft_common_candidates(user):
    return [
        f"/home/{user}/Ubisoft Game Launcher/games",
        f"/home/{user}/Games/Ubisoft Connect",
        f"/home/{user}/Games/Ubisoft",
    ]

def rockstar_common_candidates(user):
    return [
        f"/home/{user}/Rockstar Games",
        f"/home/{user}/Games/Rockstar Games",
    ]

PLATFORMS = {
    "steam": {"user_common": steam_common_candidates},
    "gog": {"user_common": gog_common_candidates},
    "epic": {"user_common": epic_common_candidates},
    "ubisoft": {"user_common": ubisoft_common_candidates},
    "rockstar": {"user_common": rockstar_common_candidates},
}

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def first_existing(paths):
    for p in paths:
        if os.path.isdir(p):
            return p
    return None


def get_disk_usage(path="/var/Games"):
    usage = shutil.disk_usage(path)
    total_gb = usage.total // (1024**3)
    used_gb = (usage.total - usage.free) // (1024**3)
    percent = int((usage.total - usage.free) / usage.total * 100)
    return used_gb, total_gb, percent

def user_common_dir(platform, user, fallback_ok=True):
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform '{platform}'")
    cands = PLATFORMS[platform]["user_common"](user)
    existent = first_existing(cands)
    if existent:
        return existent
    if fallback_ok and cands:
        os.makedirs(cands[0], exist_ok=True)
        return cands[0]
    fallback = os.path.join(f"/home/{user}/Games", platform.capitalize())
    os.makedirs(fallback, exist_ok=True)
    return fallback

def shared_game_path(platform, game):
    return os.path.join(GAMES_ROOT, platform, game)

def steamapps_candidates(user):
    return [
        f"/home/{user}/.steam/steam/steamapps",
        f"/home/{user}/.local/share/Steam/steamapps",
    ]

def steamapps_dir(user) -> Optional[str]:
    return first_existing(steamapps_candidates(user))

def set_group_perms(path):
    try:
        gid = grp.getgrnam(GROUP_NAME).gr_gid
        for root, dirs, files in os.walk(path):
            os.chown(root, -1, gid)
            os.chmod(root, 0o2775)
            for f in files:
                fp = os.path.join(root, f)
                os.chown(fp, -1, gid)
                os.chmod(fp, 0o664)
    except Exception as e:
        print(f"Permission fix error: {e}")


def ensure_local_admin_token():
    os.makedirs(LOCAL_ADMIN_TOKEN_DIR, exist_ok=True)
    created = False
    if not os.path.exists(LOCAL_ADMIN_TOKEN_PATH):
        token = secrets.token_hex(32)
        with open(LOCAL_ADMIN_TOKEN_PATH, "w") as f:
            f.write(token + "\n")
        created = True
    try:
        gid = grp.getgrnam(GROUP_NAME).gr_gid
        os.chown(LOCAL_ADMIN_TOKEN_PATH, 0, gid)
        os.chmod(LOCAL_ADMIN_TOKEN_PATH, 0o640)
    except Exception as e:
        print(f"Permission setup error for local admin token: {e}")
    try:
        with open(LOCAL_ADMIN_TOKEN_PATH, "r") as f:
            return f.read().strip()
    except Exception as e:
        if created:
            print(f"Permission setup error for local admin token: {e}")
        return ""


def load_local_admin_token():
    try:
        with open(LOCAL_ADMIN_TOKEN_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


ensure_local_admin_token()


def ensure_read_token():
    token_dir = os.path.dirname(READ_TOKEN_PATH)
    os.makedirs(token_dir, exist_ok=True)
    if not os.path.exists(READ_TOKEN_PATH):
        with open(READ_TOKEN_PATH, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
    try:
        gid = grp.getgrnam(GROUP_NAME).gr_gid
        os.chown(READ_TOKEN_PATH, 0, gid)
        os.chmod(READ_TOKEN_PATH, 0o640)
    except Exception as e:
        print(f"Permission setup error for read token: {e}")


def load_read_token():
    try:
        with open(READ_TOKEN_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


ensure_read_token()

def find_gog_prefix_dir(user, game_name):
    """
    Najde prefix (~/Games/gog/<prefix>) podle hry, která je uvnitř drive_c/GOG Games/<game_name>
    """
    base = f"/home/{user}/Games/gog"
    if not os.path.isdir(base):
        return None
    for prefix in os.listdir(base):
        gog_path = os.path.join(base, prefix, "drive_c", "GOG Games", game_name)
        if os.path.isdir(gog_path):
            return os.path.join(base, prefix)
    return None


def require_local_admin(req):
    token = req.headers.get(LOCAL_ADMIN_TOKEN_HEADER) or (req.json or {}).get("token")
    expected = load_local_admin_token()
    return bool(token and expected and secrets.compare_digest(token, expected))


def require_read_access(req):
    token = req.headers.get(READ_TOKEN_HEADER)
    expected = load_read_token()
    return bool(token and expected and secrets.compare_digest(token, expected))


@app.before_request
def restrict_remote_api():
    """A remotely bound instance exposes only authenticated read-only server data."""
    if request.remote_addr in ("127.0.0.1", "::1"):
        return None
    if request.path not in ("/servers/status", "/servers/minecraft/mods"):
        return jsonify({"message": "Remote access is limited to server status endpoints"}), 403
    if not require_read_access(request):
        return jsonify({"message": "Unauthorized"}), 403
    return None

# ------------------------------------------------------------
# Systemctl helpers
# ------------------------------------------------------------
def systemctl_is_active(service_name: str):
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", service_name],
            text=True,
            capture_output=True,
            check=False,
        )
        status = proc.stdout.strip() or "unknown"
        return proc.returncode, status, proc.stderr.strip()
    except FileNotFoundError:
        return 127, "unknown", "systemctl nenalezen"
    except Exception as e:
        return 1, "unknown", str(e)


def systemctl_stop(service_name: str):
    try:
        proc = subprocess.run(
            ["systemctl", "stop", service_name],
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "systemctl nenalezen"
    except Exception as e:
        return 1, "", str(e)


def game_server_status(server):
    service_name = server.get("service", "")
    rc, status, err = systemctl_is_active(service_name)
    messages = {
        "active": "Běží",
        "activating": "Spouští se",
        "deactivating": "Zastavuje se",
        "inactive": "Neběží",
        "failed": "Chyba",
        "unknown": "Jednotka nenalezena",
    }
    return {
        "id": server.get("id", ""),
        "name": server.get("name", service_name),
        "service": service_name,
        "kind": server.get("kind", "generic"),
        "status": status,
        "message": messages.get(status, err or f"Stav: {status}"),
        "error": err if rc == 127 else "",
    }

# ------------------------------------------------------------
# Timekpr detection & helpers
# ------------------------------------------------------------
@dataclass
class TimekprCapabilities:
    mode: str = ""          # "settimeleft" nebo "addflag" nebo ""
    add_flag: Optional[str] = None
    bin_path: Optional[str] = None
    error: str = ""


def _candidate_timekpra_commands() -> list[list[str]]:
    commands: list[list[str]] = []

    def add_command(command: list[str]) -> None:
        if command and command not in commands:
            commands.append(command)

    detected = shutil.which(TIMEKPRA_BIN)
    if detected:
        add_command([detected])
    add_command([TIMEKPRA_BIN])
    for bin_path in (
        "/usr/bin/timekpra",
        "/usr/local/bin/timekpra",
        "/usr/sbin/timekpra",
        "/bin/timekpra",
        "/sbin/timekpra",
    ):
        add_command([bin_path])

    python_bins = [sys.executable, shutil.which("python3"), "/usr/bin/python3"]
    script_patterns = [
        "/usr/lib/python3/dist-packages/timekpr/client/timekpra.py",
        "/usr/local/lib/python3*/dist-packages/timekpr/client/timekpra.py",
        "/usr/lib/python3*/site-packages/timekpr/client/timekpra.py",
        "/usr/lib64/python3*/site-packages/timekpr/client/timekpra.py",
    ]
    scripts: list[str] = []
    for pattern in script_patterns:
        scripts.extend(glob.glob(pattern))
    for script in sorted(set(scripts)):
        if os.path.isfile(script):
            for python_bin in python_bins:
                if python_bin:
                    add_command([python_bin, script])

    return commands


def detect_timekpr() -> TimekprCapabilities:
    caps = TimekprCapabilities()
    global TIMEKPRA_BIN_RESOLVED
    last_error = ""
    for command in _candidate_timekpra_commands():
        label = " ".join(command)
        try:
            proc = subprocess.run(command + ["--help"], text=True, capture_output=True, check=False)
        except FileNotFoundError:
            last_error = f"{label} nenalezen"
            continue
        except Exception as e:
            last_error = str(e)
            continue

        help_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if not help_text.strip():
            last_error = f"{label} nevrátil žádný help výstup"
            continue

        if TIMEKPRA_SET_TIMELEFT in help_text:
            TIMEKPRA_BIN_RESOLVED = command
            caps.bin_path = label
            caps.mode = "settimeleft"
            return caps

        for candidate in TIMEKPRA_ADD_FLAG_CANDIDATES:
            if candidate in help_text:
                TIMEKPRA_BIN_RESOLVED = command
                caps.bin_path = label
                caps.mode = "addflag"
                caps.add_flag = candidate
                return caps

        if TIMEKPRA_SET_ALLOWED_HOURS in help_text:
            last_error = f"{label} podporuje jen {TIMEKPRA_SET_ALLOWED_HOURS}, ne změnu zbývajícího času"
        else:
            last_error = f"{label} nepodporuje {TIMEKPRA_SET_TIMELEFT} ani {', '.join(TIMEKPRA_ADD_FLAG_CANDIDATES)}"

    caps.error = last_error or f"{TIMEKPRA_BIN} nenalezen"
    return caps


TIMEKPRA_CAPS = detect_timekpr()


def run_timekpra(args: list[str]):
    """Spustí timekpra s předanými argy, vrací (rc, stdout, stderr)."""
    try:
        command = TIMEKPRA_BIN_RESOLVED or [TIMEKPRA_BIN]
        proc = subprocess.run(command + args, text=True, capture_output=True, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{TIMEKPRA_BIN} nenalezen (nainstaluj timekpr-next)"
    except Exception as e:
        return 1, "", str(e)

def user_in_wheel(username: str) -> bool:
    try:
        wheel = grp.getgrnam("wheel")
        return username in wheel.gr_mem
    except KeyError:
        return False


def issue_token(username: str) -> str:
    token = secrets.token_hex(16)
    TIMEKPRA_TOKENS[token] = (username, time.time() + TOKEN_TTL_SECONDS)
    return token


def require_token(req):
    tok = req.headers.get("X-Timekpr-Token") or (req.json or {}).get("token")
    if not tok:
        return None
    entry = TIMEKPRA_TOKENS.get(tok)
    if not entry:
        return None
    user, expiry = entry
    if time.time() > expiry:
        del TIMEKPRA_TOKENS[tok]
        return None
    return user

def require_local_pam_session(req):
    return req.remote_addr in ("127.0.0.1", "::1") and bool(require_token(req))



def limit_for_today(user: str) -> Optional[int]:
    """Vrátí limit pro dnešní den (v sekundách) z timekpr configu uživatele."""
    cfg_path = f"/var/lib/timekpr/config/timekpr.{user}.conf"
    if not os.path.exists(cfg_path):
        return None
    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read(cfg_path)
        allowed_raw = parser.get(user, "ALLOWED_WEEKDAYS", fallback="")
        limits_raw = parser.get(user, "LIMITS_PER_WEEKDAYS", fallback="")
        allowed_days = [d for d in allowed_raw.split(";") if d]
        limits = []
        for val in limits_raw.split(";"):
            val = val.strip()
            if val:
                try:
                    limits.append(int(val))
                except ValueError:
                    pass
        day_idx = datetime.date.today().isoweekday()  # 1-7, Monday=1
        per_day = dict(zip(allowed_days, limits))
        val = per_day.get(str(day_idx))
        if val is None and len(limits) == 7:
            # fallback pokud jsou limity v pořadí dní
            try:
                val = limits[day_idx - 1]
            except Exception:
                val = None
        return val
    except Exception:
        return None


def allowed_hours_for_day(user: str, day_idx: int) -> Optional[str]:
    cfg_path = f"/var/lib/timekpr/config/timekpr.{user}.conf"
    if not os.path.exists(cfg_path):
        return None
    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read(cfg_path)
        key = f"ALLOWED_HOURS_{day_idx}"
        return parser.get(user, key, fallback=None)
    except Exception:
        return None


def ensure_day_allowed(user: str, day_idx: int) -> None:
    """Přidá aktuální den do ALLOWED_WEEKDAYS, pokud tam chybí."""
    cfg_path = f"/var/lib/timekpr/config/timekpr.{user}.conf"
    if not os.path.exists(cfg_path):
        return
    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read(cfg_path)
        allowed_raw = parser.get(user, "ALLOWED_WEEKDAYS", fallback="")
        allowed_days = [d for d in allowed_raw.split(";") if d]
        if str(day_idx) not in allowed_days:
            allowed_days.append(str(day_idx))
            parser.set(user, "ALLOWED_WEEKDAYS", ";".join(allowed_days))
            with open(cfg_path, "w") as f:
                parser.write(f)
    except Exception:
        # best effort, nechceme zablokovat hlavní operaci
        pass

# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------
@app.route("/list_user_games", methods=["GET"])
def api_list_user_games():
    platform = request.args.get("platform", "")
    user = request.args.get("user", "")
    if not platform or not user:
        return jsonify({"message": "Missing platform or user"}), 400
    if platform not in PLATFORMS:
        return jsonify({"message": f"Unsupported platform '{platform}'"}), 400

    try:
        common = user_common_dir(platform, user, fallback_ok=True)
        if not os.path.isdir(common):
            return jsonify({"platform": platform, "games": []})

        games = [
            d for d in os.listdir(common)
            if os.path.isdir(os.path.join(common, d))
            and not os.path.islink(os.path.join(common, d))
            and not any(d.startswith(p) for p in EXCLUDE_PREFIXES)
            and d not in EXCLUDE_LIST.get(platform, [])
        ]
        return jsonify({"platform": platform, "user": user, "games": sorted(games)})
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/list_shared", methods=["GET"])
def api_list_shared():
    platform = request.args.get("platform", "")
    if not platform:
        return jsonify({"message": "Missing platform"}), 400
    if platform not in PLATFORMS:
        return jsonify({"message": f"Unsupported platform '{platform}'"}), 400

    base = os.path.join(GAMES_ROOT, platform)
    if not os.path.isdir(base):
        return jsonify({"platform": platform, "games": []})

    try:
        games = [
            d for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))
            and not any(d.startswith(p) for p in EXCLUDE_PREFIXES)
            and d not in EXCLUDE_LIST.get(platform, [])
        ]
        return jsonify({"platform": platform, "games": sorted(games)})
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/move_game", methods=["POST"])
def move_game():
    if not require_local_admin(request):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json
    platform = data.get("platform")
    game_name = data.get("game_name")
    user = data.get("user")

    if not platform or not game_name or not user:
        return jsonify({"message": "Missing parameters"}), 400

    if platform == "steam":
        source_common = user_common_dir(platform, user)
        source_path = os.path.join(source_common, game_name)
        target_base = os.path.join(GAMES_ROOT, platform)
        target_path = os.path.join(target_base, game_name)

        if not os.path.exists(source_path):
            return jsonify({"message": "Game not found in source"}), 404

        os.makedirs(target_base, exist_ok=True)

        try:
            shutil.move(source_path, target_path)
            set_group_perms(target_path)

            # proxy symlink
            proxy_base = f"/var/Games_links/{user}/{platform}"
            os.makedirs(proxy_base, exist_ok=True)
            proxy_path = os.path.join(proxy_base, game_name)
            if not os.path.exists(proxy_path):
                os.symlink(target_path, proxy_path)

            # symlink v common → proxy
            os.symlink(proxy_path, source_path)

            return jsonify({"message": f"Game '{game_name}' moved to shared, proxy and symlink created"})
        except Exception as e:
            return jsonify({"message": str(e)}), 500

    # TODO: move logic for gog/epic/ubisoft/rockstar pokud bude potřeba
    return jsonify({"message": f"Move not supported for {platform}"}), 400

@app.route("/create_symlink", methods=["POST"])
def create_symlink():
    if not require_local_admin(request):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json
    platform = data.get("platform")
    game_name = data.get("game_name")
    user = data.get("user")
    source_user = data.get("source_user")  # jen pro gog/epic/ubisoft

    if not platform or not game_name or not user:
        return jsonify({"message": "Missing parameters"}), 400

    target_path = os.path.join(GAMES_ROOT, platform, game_name)
    if not os.path.exists(target_path):
        return jsonify({"message": "Target path does not exist"}), 404

    if platform == "steam":
        source_common = user_common_dir(platform, user)
        source_path = os.path.join(source_common, game_name)

        if os.path.exists(source_path):
            return jsonify({"message": "Symlink already exists"}), 400

        try:
            proxy_base = f"/var/Games_links/{user}/{platform}"
            os.makedirs(proxy_base, exist_ok=True)
            proxy_path = os.path.join(proxy_base, game_name)
            if not os.path.exists(proxy_path):
                os.symlink(target_path, proxy_path)

            os.symlink(proxy_path, source_path)
            return jsonify({"message": "Steam symlink created via proxy"})
        except Exception as e:
            return jsonify({"message": str(e)}), 500

    if platform in ("gog", "epic", "ubisoft"):
        if not source_user:
            return jsonify({"message": "Missing source_user"}), 400

        src_prefix = find_gog_prefix_dir(source_user, game_name)
        if not src_prefix:
            return jsonify({"message": f"Source prefix for {game_name} not found"}), 404

        dst_base = f"/home/{user}/Games/{platform}"
        dst_prefix = os.path.join(dst_base, os.path.basename(src_prefix))

        if os.path.exists(dst_prefix):
            return jsonify({"message": "Prefix already exists"}), 400

        try:
            os.makedirs(dst_base, exist_ok=True)
            subprocess.check_call([
                "rsync", "-aH",
                src_prefix + "/", dst_prefix + "/"
            ])

            # opravíme reg soubory
            reg_files = [
                os.path.join(dst_prefix, "system.reg"),
                os.path.join(dst_prefix, "user.reg"),
                os.path.join(dst_prefix, "userdef.reg"),
            ]
            old_home = f"/home/{source_user}"
            new_home = f"/home/{user}"
            for reg_file in reg_files:
                if os.path.isfile(reg_file):
                    with fileinput.FileInput(reg_file, inplace=True, backup=".bak") as f:
                        for line in f:
                            print(line.replace(old_home, new_home), end="")

            return jsonify({"message": f"Prefix copied from {source_user} to {user} and registry updated"})
        except subprocess.CalledProcessError as e:
            return jsonify({"message": f"rsync error: {e}"}), 500
        except Exception as e:
            return jsonify({"message": str(e)}), 500

    return jsonify({"message": f"Unsupported platform {platform}"}), 400

@app.route("/fix_perms", methods=["POST"])
def fix_perms():
    if not require_local_admin(request):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json
    path = data.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"message": "Invalid path"}), 400
    try:
        set_group_perms(path)
        return jsonify({"message": f"Permissions fixed for {path}"})
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/steam_cache_status", methods=["GET"])
def steam_cache_status():
    user = request.args.get("user", "")
    if not user:
        return jsonify({"message": "Missing user"}), 400

    steamapps = steamapps_dir(user)
    if not steamapps:
        return jsonify({"message": "Steam knihovna nenalezena", "status": "missing"}), 404

    download_path = os.path.join(steamapps, "downloading")
    shared_base = os.path.join(GAMES_ROOT, "steam-cache")
    shared_downloading = os.path.join(shared_base, "downloading")
    shared_base_real = os.path.realpath(shared_base)
    shared_downloading_real = os.path.join(shared_base_real, "downloading")

    status = "missing"
    target = None
    message = ""
    if os.path.islink(download_path):
        target = os.path.realpath(download_path)
        try:
            if (
                target == shared_downloading_real
                or target == shared_base_real
                or os.path.commonpath([target, shared_base_real]) == shared_base_real
            ):
                status = "shared"
            else:
                status = "custom"
            message = f"Symlink {download_path} -> {target}"
        except FileNotFoundError:
            status = "custom"
            message = f"Symlink {download_path} má neexistující cíl"
    elif os.path.isdir(download_path):
        status = "local"
        message = f"Používá lokální cestu {download_path}"
    elif os.path.exists(download_path):
        status = "unknown"
        message = f"{download_path} není adresář ani symlink"
    else:
        message = f"{download_path} neexistuje"

    return jsonify({
        "status": status,
        "download_path": download_path,
        "target": target,
        "shared_path": shared_downloading,
        "message": message,
    })

@app.route("/set_steam_cache", methods=["POST"])
def set_steam_cache():
    if not require_local_admin(request):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    if not user:
        return jsonify({"message": "Missing user"}), 400

    steamapps = steamapps_dir(user)
    if not steamapps:
        return jsonify({"message": "Steam knihovna nenalezena"}), 404

    try:
        download_path = os.path.join(steamapps, "downloading")
        shared_base = os.path.join(GAMES_ROOT, "steam-cache")
        shared_downloading = os.path.join(shared_base, "downloading")

        os.makedirs(shared_base, exist_ok=True)
        try:
            gid = grp.getgrnam(GROUP_NAME).gr_gid
            os.chown(shared_base, -1, gid)
            os.chmod(shared_base, 0o2775)
        except Exception:
            pass

        if os.path.islink(download_path):
            target = os.path.realpath(download_path)
            if target == os.path.realpath(shared_downloading):
                return jsonify({"message": "Steam cache už je sdílená", "status": "shared"})
            os.unlink(download_path)
        elif os.path.isdir(download_path):
            if os.path.exists(shared_downloading):
                return jsonify({"message": f"Cílový {shared_downloading} už existuje, nejprve jej odstraň nebo přesuň"}), 409
            shutil.move(download_path, shared_downloading)
        elif os.path.exists(download_path):
            return jsonify({"message": f"{download_path} není adresář ani symlink"}), 400
        else:
            os.makedirs(shared_downloading, exist_ok=True)

        if not os.path.exists(shared_downloading):
            os.makedirs(shared_downloading, exist_ok=True)

        os.symlink(shared_downloading, download_path)

        set_group_perms(shared_downloading)
        return jsonify({
            "message": f"Steam cache přesunuta do {shared_downloading} a vytvořen symlink",
            "status": "shared",
            "shared_path": shared_downloading,
        })
    except FileExistsError:
        return jsonify({"message": "Nepodařilo se vytvořit symlink, cesta už existuje"}), 409
    except Exception as e:
        return jsonify({"message": str(e)}), 500

# ------------------------------------------------------------
# DNSmasq
# ------------------------------------------------------------
@app.route("/servers/status", methods=["GET"])
def servers_status():
    return jsonify({
        "servers": [game_server_status(server) for server in load_game_servers()],
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.route("/servers/config", methods=["GET", "PUT"])
def servers_config():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    if request.method == "GET":
        return jsonify({"servers": load_game_servers()})
    servers = (request.json or {}).get("servers")
    if not isinstance(servers, list):
        return jsonify({"message": "Invalid server list"}), 400
    seen_ids = set()
    validated = []
    for server in servers:
        if not isinstance(server, dict):
            return jsonify({"message": "Invalid server entry"}), 400
        server_id = str(server.get("id", "")).strip()
        name = str(server.get("name", "")).strip()
        service = str(server.get("service", "")).strip()
        kind = str(server.get("kind", "generic"))
        if not server_id or not server_id.replace("-", "").replace("_", "").isalnum() or server_id in seen_ids or not name or not service or "\n" in service or kind not in ("generic", "minecraft"):
            return jsonify({"message": "Invalid server entry"}), 400
        item = {"id": server_id, "name": name, "service": service, "kind": kind}
        if kind == "minecraft":
            mods_dir = str(server.get("mods_dir", "")).strip()
            if not mods_dir.startswith("/"):
                return jsonify({"message": "Minecraft needs an absolute mods directory"}), 400
            item["mods_dir"] = mods_dir
        validated.append(item)
        seen_ids.add(server_id)
    save_game_servers(validated)
    return jsonify({"servers": validated})


@app.route("/servers/stop", methods=["POST"])
def servers_stop():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    server = find_game_server((request.json or {}).get("id", ""))
    if not server:
        return jsonify({"message": "Server not found"}), 404
    rc, out, err = systemctl_stop(server.get("service", ""))
    if rc == 0:
        return jsonify({"message": f"{server.get('name')} zastaven"})
    return jsonify({"message": err or out or "Nepodařilo se zastavit službu"}), 500


@app.route("/servers/minecraft/mods", methods=["GET"])
def minecraft_mods():
    if request.remote_addr not in ("127.0.0.1", "::1") and not require_read_access(request):
        return jsonify({"message": "Unauthorized"}), 403
    server = find_game_server(request.args.get("server_id", "minecraft"))
    if not server or server.get("kind") != "minecraft":
        return jsonify({"message": "Minecraft server not found"}), 404
    mods_dir = server.get("mods_dir", MINECRAFT_MODS_DIR)
    try:
        inventory = scan_mod_directory(mods_dir)
    except FileNotFoundError as e:
        return jsonify({"message": str(e)}), 404
    except PermissionError:
        return jsonify({"message": f"Nelze číst {mods_dir}"}), 403
    except Exception as e:
        return jsonify({"message": f"Inventář modů selhal: {e}"}), 500
    inventory["updated_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return jsonify(inventory)


@app.route("/dnsmasq/status", methods=["GET"])
def dnsmasq_status():
    rc, status, err = systemctl_is_active("dnsmasq")
    if rc == 127:
        return jsonify({"status": "unknown", "message": err}), 500
    if status == "active":
        message = "Běží"
    elif status in ("inactive", "failed"):
        message = "Neběží"
    else:
        message = err or f"Stav: {status}"
    return jsonify({"status": status, "message": message})


@app.route("/dnsmasq/stop", methods=["POST"])
def dnsmasq_stop():
    if not require_local_admin(request):
        return jsonify({"message": "Unauthorized"}), 403
    rc, status, err = systemctl_is_active("dnsmasq")
    if rc == 127:
        return jsonify({"message": err}), 500
    if status != "active":
        return jsonify({"message": "dnsmasq už neběží", "status": status})
    rc, out, err = systemctl_stop("dnsmasq")
    if rc == 0:
        return jsonify({"message": "dnsmasq zastaven"})
    return jsonify({"message": err or out or "Nepodařilo se zastavit dnsmasq"}), 500

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
@app.route("/timekpr/status", methods=["GET"])
def timekpr_status():
    user = require_token(request)
    if not user:
        return jsonify({"message": "Unauthorized"}), 403
    caps = TIMEKPRA_CAPS
    return jsonify({
        "mode": caps.mode,
        "add_flag": caps.add_flag,
        "bin_path": caps.bin_path,
        "error": caps.error,
        "disable_seconds": TIMEKPRA_DISABLE_SECONDS,
        "user": user,
    })


@app.route("/timekpr/add_bonus", methods=["POST"])
def timekpr_add_bonus():
    user_token = require_token(request)
    if not user_token:
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    minutes = data.get("minutes")
    try:
        minutes = int(minutes)
    except Exception:
        return jsonify({"message": "Invalid minutes"}), 400
    if minutes <= 0:
        return jsonify({"message": "Minutes must be > 0"}), 400
    seconds = minutes * 60

    caps = TIMEKPRA_CAPS
    if caps.mode == "settimeleft":
        args = [TIMEKPRA_SET_TIMELEFT, user, "+", str(seconds)]
    elif caps.mode == "addflag" and caps.add_flag:
        args = [caps.add_flag, user, str(seconds)]
    else:
        return jsonify({"message": "timekpra nepodporuje přidání času (v --help není settimeleft ani addtime)"}), 500

    rc, out, err = run_timekpra(args)
    if rc == 0:
        return jsonify({"message": out or f"Přidáno {minutes} minut pro {user}"})
    return jsonify({"message": err or out or "timekpra selhalo"}), 500


@app.route("/timekpr/disable_today", methods=["POST"])
def timekpr_disable_today():
    user_token = require_token(request)
    if not user_token:
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    if not user:
        return jsonify({"message": "Missing user"}), 400

    caps = TIMEKPRA_CAPS
    if caps.mode == "settimeleft":
        args = [TIMEKPRA_SET_TIMELEFT, user, "=", str(TIMEKPRA_DISABLE_SECONDS)]
    elif caps.mode == "addflag" and caps.add_flag:
        args = [caps.add_flag, user, str(TIMEKPRA_DISABLE_SECONDS)]
    else:
        return jsonify({"message": "timekpra nepodporuje změnu času pro dnešek"}), 500

    rc, out, err = run_timekpra(args)
    if rc == 0:
        return jsonify({"message": out or f"Nastaveno {TIMEKPRA_DISABLE_SECONDS//3600}h pro {user}"})
    return jsonify({"message": err or out or "timekpra selhalo"}), 500


@app.route("/timekpr/auth", methods=["POST"])
def timekpr_auth():
    data = request.json or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"message": "Missing credentials"}), 400
    if not user_in_wheel(username):
        return jsonify({"message": "Uživatel není ve wheel"}), 403
    try:
        import pam
    except ImportError:
        return jsonify({"message": "Chybí modul pam (python3-pam)"}), 500

    p = pam.pam()
    if not p.authenticate(username, password):
        return jsonify({"message": "Neplatné přihlášení"}), 401
    token = issue_token(username)
    caps = TIMEKPRA_CAPS
    return jsonify({
        "token": token,
        "mode": caps.mode,
        "add_flag": caps.add_flag,
        "bin_path": caps.bin_path,
        "error": caps.error,
        "disable_seconds": TIMEKPRA_DISABLE_SECONDS,
        "user": username,
        "ttl_seconds": TOKEN_TTL_SECONDS,
    })


@app.route("/timekpr/userinfo", methods=["POST"])
def timekpr_userinfo():
    user_token = require_token(request)
    if not user_token:
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    if not user:
        return jsonify({"message": "Missing user"}), 400
    rc, out, err = run_timekpra(["--userinfo", user])
    if rc == 0:
        return jsonify({"message": out or "OK"})
    return jsonify({"message": err or out or "timekpra selhalo"}), 500


@app.route("/timekpr/day_plan", methods=["POST"])
def timekpr_day_plan():
    user_token = require_token(request)
    if not user_token:
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    if not user:
        return jsonify({"message": "Missing user"}), 400
    day_idx = datetime.date.today().isoweekday()
    hours = allowed_hours_for_day(user, day_idx)
    if hours is None:
        return jsonify({"message": "Nepodařilo se načíst plán dnešního dne"}), 500
    limit = limit_for_today(user)
    return jsonify({"day": day_idx, "hours": hours, "limit": limit})


@app.route("/timekpr/set_hours_today", methods=["POST"])
def timekpr_set_hours_today():
    user_token = require_token(request)
    if not user_token:
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    hours = data.get("hours")
    if not user or not hours:
        return jsonify({"message": "Missing user or hours"}), 400
    day_idx = datetime.date.today().isoweekday()
    args = [TIMEKPRA_SET_ALLOWED_HOURS, user, str(day_idx), str(hours)]
    rc, out, err = run_timekpra(args)
    if rc == 0:
        # pokud byl den vypnutý, zajistíme, že se objeví v ALLOWED_WEEKDAYS
        ensure_day_allowed(user, day_idx)
        return jsonify({"message": out or "Plán upraven", "hours": hours, "day": day_idx})
    return jsonify({"message": err or out or "timekpra selhalo"}), 500


@app.route("/timekpr/reset_today", methods=["POST"])
def timekpr_reset_today():
    user_token = require_token(request)
    if not user_token:
        return jsonify({"message": "Unauthorized"}), 403
    data = request.json or {}
    user = data.get("user")
    if not user:
        return jsonify({"message": "Missing user"}), 400

    caps = TIMEKPRA_CAPS
    if caps.mode != "settimeleft":
        return jsonify({"message": "Reset vyžaduje podporu --settimeleft"}), 400

    limit = limit_for_today(user)
    if limit is None:
        return jsonify({"message": "Nepodařilo se načíst limit pro dnešek"}), 500

    rc, out, err = run_timekpra([TIMEKPRA_SET_TIMELEFT, user, "=", str(limit)])
    if rc == 0:
        return jsonify({"message": out or f"Nastaven plánovaný limit {limit} s", "limit_seconds": limit})
    return jsonify({"message": err or out or "timekpra selhalo"}), 500


if __name__ == "__main__":
    app.run(
        host=os.getenv("GAME_MOVER_BIND_HOST", "127.0.0.1"),
        port=int(os.getenv("GAME_MOVER_PORT", "5000")),
        debug=False,
    )
