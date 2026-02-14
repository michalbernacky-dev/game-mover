#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request
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

app = Flask(__name__)

# ------------------------------------------------------------
# KONFIGURACE
# ------------------------------------------------------------
GAMES_ROOT = "/var/Games"
GROUP_NAME = "gemers"

EXCLUDE_PREFIXES = ("SteamLinuxRuntime", "Proton")
EXCLUDE_LIST = {
    "steam": ["Half-Life Dedicated Server"],
    "gog": [],
    "epic": [],
    "ubisoft": [],
    "rockstar": []
}

TIMEKPRA_BIN = "timekpra"
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

# ------------------------------------------------------------
# Timekpr detection & helpers
# ------------------------------------------------------------
@dataclass
class TimekprCapabilities:
    mode: str = ""          # "settimeleft" nebo "addflag" nebo ""
    add_flag: Optional[str] = None


def detect_timekpr() -> TimekprCapabilities:
    caps = TimekprCapabilities()
    try:
        proc = subprocess.run([TIMEKPRA_BIN, "--help"], text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            return caps
        help_text = proc.stdout
        if TIMEKPRA_SET_TIMELEFT in help_text:
            caps.mode = "settimeleft"
        if caps.mode != "settimeleft":
            for candidate in TIMEKPRA_ADD_FLAG_CANDIDATES:
                if candidate in help_text:
                    caps.mode = "addflag"
                    caps.add_flag = candidate
                    break
    except FileNotFoundError:
        pass
    return caps


TIMEKPRA_CAPS = detect_timekpr()


def run_timekpra(args: list[str]):
    """Spustí timekpra s předanými argy, vrací (rc, stdout, stderr)."""
    try:
        proc = subprocess.run([TIMEKPRA_BIN] + args, text=True, capture_output=True, check=False)
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
    app.run(host="127.0.0.1", port=5000, debug=True)
