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
import threading
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

from game_mover_backups import BackupError, create_workload_backup
from game_mover_jobs import OperationAlreadyRunning, OperationRegistry
from game_mover_mods import scan_mod_directory
from game_mover_minecraft import (
    configured_rcon,
    configured_server_port,
    count_known_players,
    local_server_addresses,
    query_server_rcon,
    query_server_status,
)
from game_mover_version import __version__
from game_mover_gate import (
    GateConfigError,
    chown_gate_layout,
    default_gate_config,
    normalize_gate_config,
    upsert_gate_route,
    write_gate_layout,
)
from game_mover_installs import (
    InstallError,
    container_environment,
    fresh_data_directory,
    list_backups,
    normalize_install_request,
    restore_backup,
)
from game_mover_workloads import CONTAINER_NAME_RE, SYSTEMD_UNIT_RE, WorkloadState, backend_for

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
GATE_CONFIG_PATH = os.path.join(LOCAL_ADMIN_TOKEN_DIR, "gate.json")
GATE_DATA_DIRECTORY = os.path.realpath(
    os.getenv("GAME_PLATFORM_GATE_DATA", "/var/lib/game-platform/proxies/gate")
)
PODMAN_USER = os.getenv("GAME_PLATFORM_PODMAN_USER", "gameplatform")
PODMAN_SOCKET_PATH = os.getenv("GAME_PLATFORM_PODMAN_SOCKET", "").strip() or None
PODMAN_DATA_ROOT = os.path.realpath(
    os.getenv("GAME_PLATFORM_DATA_ROOT", "/var/lib/game-platform/servers")
)
BACKUP_ROOT = os.path.realpath(
    os.getenv("GAME_PLATFORM_BACKUP_ROOT", "/var/lib/game-platform/backups")
)
WORKLOAD_LOCKS = {}
WORKLOAD_LOCKS_GUARD = threading.Lock()
OPERATIONS = OperationRegistry()
GATE_DEPLOY_OPERATION_ID = "gate-deploy"
SERVER_ACTIONS = ("start", "stop", "restart", "backup")
SERVER_AUTH_POLICIES = ("silent", "pam", "disabled")
MINECRAFT_STATUS_TIMEOUT = 8.0
MINECRAFT_STATUS_DISCOVERY_TIMEOUT = 3.0
MINECRAFT_STATUS_REFRESH_SECONDS = 30.0
MINECRAFT_STATUS_RETRY_SECONDS = 15.0
MINECRAFT_STATUS_CACHE = {}
MINECRAFT_STATUS_INFLIGHT = set()
MINECRAFT_STATUS_LOCK = threading.Lock()
MINECRAFT_STATUS_EXECUTOR = ThreadPoolExecutor(max_workers=4)
CGNAT_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def workload_lock(workload_id):
    with WORKLOAD_LOCKS_GUARD:
        return WORKLOAD_LOCKS.setdefault(workload_id, threading.Lock())


def default_game_servers():
    return [
        {"id": "minecraft", "name": "Minecraft", "backend": "systemd", "service": os.getenv("GAME_MOVER_MINECRAFT_SERVICE", "forge-srv.service"), "kind": "minecraft", "mods_dir": MINECRAFT_MODS_DIR, "permissions": {"start": "silent", "stop": "silent", "restart": "silent", "backup": "pam"}},
        {"id": "satisfactory", "name": "Satisfactory", "backend": "systemd", "service": os.getenv("GAME_MOVER_SATISFACTORY_SERVICE", "satisfactory.service"), "kind": "generic", "permissions": {"start": "silent", "stop": "silent", "restart": "silent", "backup": "pam"}},
    ]


def normalize_game_server(server):
    """Normalize legacy systemd entries without rewriting servers.json."""
    item = dict(server)
    backend = str(item.get("backend", "systemd")).strip().lower() or "systemd"
    item["backend"] = backend
    legacy_policy = str(item.get("control_auth", "silent")).strip().lower()
    if legacy_policy not in ("silent", "pam"):
        legacy_policy = "silent"
    configured_permissions = (
        item.get("permissions") if isinstance(item.get("permissions"), dict) else {}
    )
    item["permissions"] = {
        action: (
            configured_permissions.get(action)
            if configured_permissions.get(action) in SERVER_AUTH_POLICIES
            else (legacy_policy if action in ("start", "stop", "restart") else "pam")
        )
        for action in SERVER_ACTIONS
    }
    item.pop("control_auth", None)
    runtime = item.get("runtime") if isinstance(item.get("runtime"), dict) else {}
    if backend == "systemd":
        unit = str(runtime.get("unit") or item.get("service") or "").strip()
        item["service"] = unit
        item["runtime"] = {**runtime, "unit": unit}
    elif backend == "podman":
        container = str(runtime.get("container_name") or item.get("container") or "").strip()
        item["container"] = container
        item["runtime"] = {**runtime, "container_name": container}
    if item.get("kind") == "minecraft":
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        data_directory = str(data.get("directory") or "").strip()
        if not data_directory and item.get("mods_dir"):
            data_directory = os.path.dirname(str(item["mods_dir"]))
        if data_directory:
            item["data"] = {**data, "directory": data_directory}
        if not item.get("mods_dir") and data.get("directory"):
            relative = str(data.get("mods_relative_path", "mods")).strip() or "mods"
            item["mods_dir"] = os.path.join(str(data["directory"]), relative)
    return item


def load_game_servers():
    try:
        with open(GAME_SERVERS_CONFIG_PATH, "r") as config_file:
            servers = json.load(config_file)
        if isinstance(servers, list):
            return [normalize_game_server(server) for server in servers if isinstance(server, dict)]
    except Exception:
        pass
    return [normalize_game_server(server) for server in default_game_servers()]


def save_game_servers(servers):
    os.makedirs(LOCAL_ADMIN_TOKEN_DIR, exist_ok=True)
    temporary_path = f"{GAME_SERVERS_CONFIG_PATH}.tmp"
    with open(temporary_path, "w") as config_file:
        json.dump(servers, config_file, indent=2)
        config_file.write("\n")
    try:
        os.chown(temporary_path, 0, grp.getgrnam(GROUP_NAME).gr_gid)
        os.chmod(temporary_path, 0o640)
    except (KeyError, OSError):
        os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, GAME_SERVERS_CONFIG_PATH)


def load_gate_config():
    try:
        with open(GATE_CONFIG_PATH, "r", encoding="utf-8") as config_file:
            return normalize_gate_config(json.load(config_file))
    except (OSError, UnicodeError, json.JSONDecodeError, GateConfigError):
        return normalize_gate_config(default_gate_config())


def check_gate_tcp_ready(host, port, timeout=1.5):
    with socket.create_connection((host, int(port)), timeout=timeout):
        return True


def wait_for_gate_ready(host, port, timeout=120):
    deadline = time.monotonic() + timeout
    last_error = "Gate Lite ještě neposlouchá"
    while time.monotonic() < deadline:
        try:
            return check_gate_tcp_ready(host, port, timeout=1.5)
        except OSError as error:
            last_error = str(error)
            time.sleep(1)
    raise RuntimeError(f"Gate Lite se nespustil v časovém limitu: {last_error}")


def check_host_port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("0.0.0.0", int(port)))
    except OSError as error:
        raise InstallError(f"Port {port} už je obsazený; zvol jiný") from error
    return True


def wait_for_minecraft_install_ready(host, port, backend, workload, timeout=600):
    deadline = time.monotonic() + timeout
    last_error = "Minecraft server ještě neodpovídá"
    while time.monotonic() < deadline:
        state = backend.status(workload)
        if state.status == "inactive":
            raise RuntimeError(state.error or state.message or "Minecraft container se zastavil")
        try:
            return query_server_status(host, port, timeout=4)
        except (OSError, UnicodeError, ValueError) as error:
            last_error = str(error)
            time.sleep(2)
    raise RuntimeError(f"Minecraft server se nespustil v časovém limitu: {last_error}")


def save_gate_config(config):
    config = normalize_gate_config(config)
    os.makedirs(LOCAL_ADMIN_TOKEN_DIR, exist_ok=True)
    temporary_path = f"{GATE_CONFIG_PATH}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2, sort_keys=True)
        config_file.write("\n")
    try:
        os.chown(temporary_path, 0, grp.getgrnam(GROUP_NAME).gr_gid)
        os.chmod(temporary_path, 0o640)
    except (KeyError, OSError):
        os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, GATE_CONFIG_PATH)
    return config


def minecraft_route_targets(servers=None):
    """Expose registered Minecraft workloads as abstract Gate route targets."""
    targets = []
    for server in servers if servers is not None else load_game_servers():
        if server.get("kind") != "minecraft":
            continue
        backend_name = server.get("backend", "systemd")
        if backend_name == "podman":
            runtime = server.get("runtime") if isinstance(server.get("runtime"), dict) else {}
            container_name = str(runtime.get("container_name", "")).strip()
            connection = (
                server.get("connection") if isinstance(server.get("connection"), dict) else {}
            )
            if server.get("management_mode") == "managed":
                host = container_name
                port = 25565
            else:
                host = "host.containers.internal"
                port = connection.get("direct_port")
        elif backend_name == "systemd":
            data = server.get("data") if isinstance(server.get("data"), dict) else {}
            connection = (
                server.get("connection") if isinstance(server.get("connection"), dict) else {}
            )
            port = configured_server_port(data.get("directory"))
            if port is None:
                port = connection.get("direct_port")
            host = "host.containers.internal"
        else:
            continue
        if not host or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            continue
        target = {
            "id": server.get("id"),
            "name": server.get("name", server.get("id")),
            "backend": backend_name,
            "endpoint": {"host": host, "port": port},
        }
        if backend_name == "podman" and container_name:
            target["container_name"] = container_name
        targets.append(target)
    return targets


def abstract_gate_routes(config, targets):
    endpoints = {
        (target["endpoint"]["host"], target["endpoint"]["port"]): target["id"]
        for target in targets
    }
    routes = []
    for route in config["routes"]:
        target_id = endpoints.get((route["backend"]["host"], route["backend"]["port"]))
        if target_id is None:
            target_id = next((
                target["id"] for target in targets
                if target.get("backend") == "podman"
                and target.get("container_name") == route["backend"]["host"]
                and route["backend"]["port"] == 25565
            ), None)
        routes.append({
            "host": route["host"],
            "target_id": target_id,
            "backend": route["backend"],
        })
    return routes


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
    payload = req.get_json(silent=True) or {}
    token = req.headers.get(LOCAL_ADMIN_TOKEN_HEADER) or payload.get("token")
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
    if request.path not in ("/servers/status", "/servers/minecraft/mods", "/proxy/status"):
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


def systemctl_action(action: str, service_name: str):
    try:
        proc = subprocess.run(
            ["systemctl", action, service_name],
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "systemctl nenalezen"
    except Exception as e:
        return 1, "", str(e)


def systemctl_stop(service_name: str):
    return systemctl_action("stop", service_name)


def _minecraft_probe_hosts(hosts, preferred_host=None):
    candidates = []

    def add(host):
        host = str(host).strip()
        if not host or host in candidates:
            return
        try:
            address = ipaddress.ip_address(host.split("%", 1)[0])
            if address.version == 6 and address.is_link_local and "%" not in host:
                return
        except ValueError:
            pass
        candidates.append(host)

    for host in hosts:
        add(host)

    def priority(host):
        if host == preferred_host:
            return -1
        try:
            address = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return 4
        if address.version == 4 and address in CGNAT_IPV4_NETWORK:
            return 0
        if address.version == 4 and not address.is_loopback:
            return 1
        if address.version == 6 and not address.is_loopback:
            return 2
        return 3

    return sorted(candidates, key=priority)


def _refresh_minecraft_player_status(cache_key, hosts, port, rcon=None):
    counts = None
    errors = []
    selected_host = None
    with MINECRAFT_STATUS_LOCK:
        preferred_host = MINECRAFT_STATUS_CACHE.get(cache_key, {}).get("preferred_host")
    try:
        if isinstance(rcon, dict):
            try:
                counts = query_server_rcon(
                    "127.0.0.1", rcon["port"], rcon["password"],
                    timeout=MINECRAFT_STATUS_DISCOVERY_TIMEOUT,
                )
                selected_host = "rcon://127.0.0.1"
            except Exception as caught_error:
                errors.append(
                    f"RCON: {type(caught_error).__name__}: {caught_error}"
                )
        candidates = _minecraft_probe_hosts(hosts, preferred_host)
        for host in candidates if counts is None else ():
            timeout = (
                MINECRAFT_STATUS_TIMEOUT
                if host == preferred_host
                else MINECRAFT_STATUS_DISCOVERY_TIMEOUT
            )
            try:
                counts = query_server_status(host, port, timeout=timeout)
                selected_host = host
                break
            except Exception as caught_error:
                errors.append(f"{host}: {type(caught_error).__name__}: {caught_error}")
    except Exception as caught_error:
        errors.append(f"Interní chyba discovery: {type(caught_error).__name__}: {caught_error}")
    finally:
        error = None if counts is not None else "; ".join(errors) or "Žádná adresa k dotazu"
        with MINECRAFT_STATUS_LOCK:
            entry = MINECRAFT_STATUS_CACHE.setdefault(cache_key, {})
            entry["last_attempt"] = time.monotonic()
            entry["error"] = error
            if counts is not None:
                entry["counts"] = dict(counts)
                entry["last_success"] = entry["last_attempt"]
                entry["preferred_host"] = selected_host
            MINECRAFT_STATUS_INFLIGHT.discard(cache_key)


def cached_minecraft_player_status(server_id, hosts, port, rcon=None):
    """Return cached counts and schedule at most one non-blocking refresh."""
    hosts = tuple(str(host) for host in hosts)
    cache_key = (str(server_id), int(port))
    now = time.monotonic()
    should_refresh = False
    with MINECRAFT_STATUS_LOCK:
        entry = MINECRAFT_STATUS_CACHE.get(cache_key, {})
        counts = entry.get("counts")
        error = entry.get("error")
        last_attempt = entry.get("last_attempt", 0.0)
        refresh_after = (
            MINECRAFT_STATUS_RETRY_SECONDS
            if error or counts is None
            else MINECRAFT_STATUS_REFRESH_SECONDS
        )
        pending = cache_key in MINECRAFT_STATUS_INFLIGHT
        if not pending and now - last_attempt >= refresh_after:
            MINECRAFT_STATUS_INFLIGHT.add(cache_key)
            pending = True
            should_refresh = True
        snapshot = dict(counts) if isinstance(counts, dict) else None
    if should_refresh:
        try:
            MINECRAFT_STATUS_EXECUTOR.submit(
                _refresh_minecraft_player_status, cache_key, hosts, int(port), rcon,
            )
        except RuntimeError as caught_error:
            with MINECRAFT_STATUS_LOCK:
                MINECRAFT_STATUS_INFLIGHT.discard(cache_key)
            pending = False
            error = f"{type(caught_error).__name__}: {caught_error}"
    return snapshot, pending, error


def game_server_status(server):
    backend_name = server.get("backend", "systemd")
    runtime = server.get("runtime", {})
    reference = (
        runtime.get("container_name", "")
        if backend_name == "podman"
        else runtime.get("unit", server.get("service", ""))
    )
    backend = None
    try:
        backend = backend_for(
            server,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        state = backend.status(server)
    except (KeyError, ValueError) as error:
        state = WorkloadState(
            status="unknown",
            native_status="unknown",
            message="Backend není dostupný",
            error=str(error),
        )
    result = {
        "id": server.get("id", ""),
        "name": server.get("name", reference),
        "backend": backend_name,
        "service": server.get("service", ""),
        "runtime_label": f"{backend_name}: {reference}",
        "kind": server.get("kind", "generic"),
        "has_mods": bool(server.get("mods_dir")),
        "permissions": {
            action: server_action_policy(server, action) for action in SERVER_ACTIONS
        },
        "status": state.status,
        "native_status": state.native_status,
        "message": state.message,
        "error": state.error,
    }
    connection = server.get("connection") if isinstance(server.get("connection"), dict) else {}
    configured_port = connection.get("direct_port")
    direct_port = None
    port_source = None
    data = server.get("data") if isinstance(server.get("data"), dict) else {}
    result["backup_supported"] = (
        backend_name in ("systemd", "podman") and bool(data.get("directory"))
    )
    if server.get("kind") == "minecraft" and backend_name == "systemd":
        direct_port = configured_server_port(data.get("directory"))
        if direct_port is not None:
            port_source = "server.properties"
    elif server.get("kind") == "minecraft" and backend_name == "podman" and backend is not None:
        runtime_port = backend.published_port(server, 25565)
        if isinstance(runtime_port, int) and not isinstance(runtime_port, bool):
            direct_port = runtime_port
            port_source = "podman"
    if direct_port is None and isinstance(configured_port, int) and not isinstance(configured_port, bool):
        direct_port = configured_port
        port_source = "registry"
    if direct_port is not None:
        result["connection"] = {"direct_port": direct_port, "source": port_source}
    if server.get("kind") == "minecraft":
        players = {
            "online": None,
            "max": None,
            "known": count_known_players(data.get("directory")),
        }
        if state.status == "active" and direct_port is not None:
            probe_hosts = local_server_addresses()
            if probe_hosts:
                rcon = (
                    configured_rcon(data.get("directory"))
                    if backend_name == "systemd"
                    else None
                )
                counts, pending, probe_error = cached_minecraft_player_status(
                    server.get("id", ""), probe_hosts, direct_port, rcon=rcon,
                )
                if counts is not None:
                    players.update(counts)
                if pending:
                    players["query_pending"] = True
                if probe_error:
                    players["query_error"] = probe_error
        result["players"] = players
    return result

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
    payload = req.get_json(silent=True) or {}
    tok = req.headers.get("X-Timekpr-Token") or payload.get("token")
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


def server_action_policy(server, action):
    permissions = server.get("permissions") if isinstance(server.get("permissions"), dict) else {}
    policy = permissions.get(action)
    if policy in SERVER_AUTH_POLICIES:
        return policy
    legacy_policy = server.get("control_auth", "silent")
    if action in ("start", "stop", "restart") and legacy_policy in ("silent", "pam"):
        return legacy_policy
    return "pam" if action == "backup" else "disabled"


def require_local_server_action(req, server, action):
    if req.remote_addr not in ("127.0.0.1", "::1"):
        return False
    policy = server_action_policy(server, action)
    if policy == "pam":
        return bool(require_token(req))
    if policy == "silent":
        return require_local_admin(req)
    return False


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
    servers = load_game_servers()
    if servers:
        with ThreadPoolExecutor(max_workers=min(8, len(servers))) as executor:
            statuses = list(executor.map(game_server_status, servers))
    else:
        statuses = []
    known_ids = {server.get("id") for server in statuses}
    for operation in OPERATIONS.snapshots(kind="minecraft-install"):
        if operation.get("running") and operation.get("target_id") not in known_ids:
            statuses.append({
                "id": operation.get("target_id"),
                "name": operation.get("target_id"),
                "backend": "podman",
                "kind": "minecraft",
                "status": "activating",
                "native_status": operation.get("phase"),
                "message": operation.get("message"),
                "runtime_label": "Podman · instalace probíhá",
                "operation": operation,
                "backup_supported": False,
            })
    return jsonify({
        "version": __version__,
        "servers": statuses,
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.route("/proxy/status", methods=["GET"])
def gate_proxy_status():
    try:
        config = load_gate_config()
        workload = {
            "backend": "podman",
            "runtime": {"container_name": config["container_name"]},
        }
        proxy_backend = backend_for(
            workload,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        state = proxy_backend.status(workload)
        container_exists = proxy_backend.container_exists(workload)
    except (KeyError, OSError, ValueError) as error:
        state = WorkloadState("unknown", "unknown", "Proxy nelze načíst", str(error))
        container_exists = False
    ready = False
    if container_exists and state.status == "active":
        try:
            check_gate_tcp_ready("127.0.0.1", config["listen"]["port"], timeout=1.5)
            ready = True
        except OSError as error:
            state = WorkloadState(
                "activating",
                state.native_status,
                "Container běží, Gate Lite ještě není připravený",
                str(error),
            )
    return jsonify({
        "version": __version__,
        "proxy": {
            "type": "gate-lite",
            "status": state.status,
            "native_status": state.native_status,
            "message": state.message,
            "error": state.error,
            "listen": config["listen"],
            "routing_mode": "hostname",
            "routes_count": len(config["routes"]),
            "network": config["network"],
            "configured": os.path.isfile(GATE_CONFIG_PATH),
            "prepared": os.path.isdir(GATE_DATA_DIRECTORY),
            "deployed": container_exists,
            "ready": ready,
            "operation": OPERATIONS.snapshot(GATE_DEPLOY_OPERATION_ID),
        },
    })


@app.route("/proxy/config", methods=["GET", "PUT"])
def gate_proxy_config():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    if request.method == "GET":
        return jsonify({"proxy": load_gate_config()})
    try:
        config = save_gate_config((request.json or {}).get("proxy"))
    except (TypeError, ValueError, OSError) as error:
        return jsonify({"message": str(error)}), 400
    return jsonify({"proxy": config})


@app.route("/proxy/routes", methods=["GET", "PUT"])
def gate_proxy_routes():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    old_config = load_gate_config()
    targets = minecraft_route_targets()
    if request.method == "GET":
        return jsonify({
            "routes": abstract_gate_routes(old_config, targets),
            "targets": targets,
        })

    raw_routes = (request.json or {}).get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        return jsonify({"message": "Gate Lite potřebuje alespoň jednu trasu"}), 400
    target_by_id = {target["id"]: target for target in targets}
    concrete_routes = []
    try:
        for route in raw_routes:
            if not isinstance(route, dict):
                raise GateConfigError("Neplatná abstraktní Gate Lite trasa")
            target_id = str(route.get("target_id", "")).strip()
            if target_id not in target_by_id:
                raise GateConfigError("Cílový Minecraft server není registrovaný")
            concrete_routes.append({
                "host": route.get("host"),
                "backend": dict(target_by_id[target_id]["endpoint"]),
            })
        new_config = normalize_gate_config({**old_config, "routes": concrete_routes})
        if sum(route["host"] == "*" for route in new_config["routes"]) != 1:
            raise GateConfigError("Směrování musí obsahovat právě jednu výchozí * trasu")
    except (GateConfigError, TypeError, ValueError) as error:
        return jsonify({"message": str(error)}), 400

    workload = {
        "backend": "podman",
        "runtime": {"container_name": old_config["container_name"]},
    }
    gate_backend = None
    deployed = False
    try:
        with workload_lock("gate-router"):
            gate_backend = backend_for(
                workload,
                podman_user=PODMAN_USER,
                podman_socket_path=PODMAN_SOCKET_PATH,
            )
            deployed = gate_backend.container_exists(workload)
            layout = write_gate_layout(new_config, GATE_DATA_DIRECTORY)
            chown_gate_layout(layout, PODMAN_USER)
            save_gate_config(new_config)
            if deployed:
                restart_result = gate_backend.restart(workload)
                if restart_result.returncode != 0:
                    raise RuntimeError(
                        restart_result.error or restart_result.output
                        or "Gate Lite se nepodařilo načíst novou konfiguraci"
                    )
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        try:
            rollback_layout = write_gate_layout(old_config, GATE_DATA_DIRECTORY)
            chown_gate_layout(rollback_layout, PODMAN_USER)
            save_gate_config(old_config)
            if deployed and gate_backend is not None:
                gate_backend.restart(workload)
        except (KeyError, OSError, ValueError):
            pass
        return jsonify({"message": f"Uložení tras selhalo; původní konfigurace obnovena: {error}"}), 500
    return jsonify({
        "message": "Směrování Gate Lite bylo uloženo"
            + (" a Gate restartován" if deployed else " pro příští nasazení"),
        "routes": abstract_gate_routes(new_config, targets),
        "targets": targets,
        "restarted": deployed,
    })


@app.route("/proxy/deploy", methods=["POST"])
def gate_proxy_deploy():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    try:
        OPERATIONS.begin(
            GATE_DEPLOY_OPERATION_ID,
            kind="proxy-deploy",
            target_id="gate-router",
            message="Připravuji konfiguraci Gate Lite",
        )
        OPERATIONS.update(
            GATE_DEPLOY_OPERATION_ID,
            phase="preparing",
            message="Připravuji konfiguraci Gate Lite",
            progress=5,
        )
    except OperationAlreadyRunning:
        return jsonify({"message": "Nasazování Gate Lite už probíhá"}), 409
    created_container = False
    try:
        config = load_gate_config()
        workload = {
            "backend": "podman",
            "runtime": {"container_name": config["container_name"]},
        }
        proxy_backend = backend_for(
            workload,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        with workload_lock("gate-router"):
            if proxy_backend.container_exists(workload):
                OPERATIONS.finish(
                    GATE_DEPLOY_OPERATION_ID,
                    message="Gate Lite už je nasazený",
                )
                return jsonify({
                    "message": "Gate Lite container už existuje; změny vyžadují řízenou aktualizaci",
                }), 409
            OPERATIONS.update(
                GATE_DEPLOY_OPERATION_ID,
                phase="layout",
                message="Vytvářím validované hostname trasy",
                progress=10,
            )
            layout = write_gate_layout(config, GATE_DATA_DIRECTORY)
            chown_gate_layout(layout, PODMAN_USER)
            OPERATIONS.update(
                GATE_DEPLOY_OPERATION_ID,
                phase="network",
                message="Připravuji privátní Podman síť",
                progress=20,
            )
            if not proxy_backend.network_exists(config["network"]):
                network_result = proxy_backend.create_network(
                    config["network"],
                    labels={"io.game-platform.managed": "true"},
                )
                if network_result.returncode != 0:
                    raise RuntimeError(
                        network_result.error or network_result.output
                        or "Vytvoření privátní Podman sítě selhalo"
                    )
            OPERATIONS.update(
                GATE_DEPLOY_OPERATION_ID,
                phase="pulling",
                message="Stahuji ověřený image Gate",
                progress=35,
            )
            pull_result = proxy_backend.pull_image(config["image"])
            if pull_result.returncode != 0:
                raise RuntimeError(
                    pull_result.error or pull_result.output or "Stažení Gate image selhalo"
                )
            OPERATIONS.update(
                GATE_DEPLOY_OPERATION_ID,
                phase="creating",
                message="Vytvářím Gate Lite container",
                progress=75,
            )
            create_result = proxy_backend.create_container(
                workload,
                config["image"],
                mounts=[{"source": layout["config_path"], "target": "/config.yml"}],
                ports=[{
                    "host": config["listen"]["host"],
                    "host_port": config["listen"]["port"],
                    "container_port": 25565,
                }],
                labels={
                    "io.game-platform.workload-id": "gate-router",
                    "io.game-platform.kind": "minecraft-router",
                },
                restart_policy="unless-stopped",
                networks=[config["network"]],
            )
            if create_result.returncode != 0:
                raise RuntimeError(
                    create_result.error or create_result.output or "Vytvoření Gate Lite selhalo"
                )
            created_container = True
            OPERATIONS.update(
                GATE_DEPLOY_OPERATION_ID,
                phase="starting",
                message="Spouštím Gate Lite a ověřuji lifecycle",
                progress=90,
            )
            start_result = proxy_backend.start(workload)
            if start_result.returncode != 0:
                raise RuntimeError(
                    start_result.error or start_result.output or "Spuštění Gate Lite selhalo"
                )
            OPERATIONS.update(
                GATE_DEPLOY_OPERATION_ID,
                phase="verifying",
                message="Ověřuji TCP listener Gate Lite",
                progress=95,
            )
            wait_for_gate_ready("127.0.0.1", config["listen"]["port"])
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        if created_container:
            proxy_backend.remove_container(workload, force=True)
        OPERATIONS.fail(
            GATE_DEPLOY_OPERATION_ID,
            message=f"Nasazení selhalo: {error}",
        )
        return jsonify({"message": str(error)}), 500
    OPERATIONS.finish(
        GATE_DEPLOY_OPERATION_ID,
        message="Gate Lite byl úspěšně nasazen",
    )
    return jsonify({
        "message": "Gate Lite byl nasazen na testovací port",
        "listen": config["listen"],
        "container": config["container_name"],
    })


@app.route("/proxy/start", methods=["POST"])
@app.route("/proxy/stop", methods=["POST"])
@app.route("/proxy/restart", methods=["POST"])
def gate_proxy_control():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    action = request.path.rsplit("/", 1)[-1]
    config = load_gate_config()
    workload = {
        "backend": "podman",
        "runtime": {"container_name": config["container_name"]},
    }
    try:
        proxy_backend = backend_for(
            workload,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        with workload_lock("gate-router"):
            if not proxy_backend.container_exists(workload):
                return jsonify({"message": "Gate Lite ještě není nasazený"}), 404
            result = getattr(proxy_backend, action)(workload)
    except (AttributeError, KeyError, OSError, ValueError) as error:
        return jsonify({"message": str(error)}), 500
    if result.returncode != 0:
        return jsonify({
            "message": result.error or result.output or "Ovládání Gate Lite selhalo",
        }), 500
    messages = {
        "start": "Gate Lite byl spuštěn",
        "stop": "Gate Lite byl vypnut",
        "restart": "Gate Lite byl restartován",
    }
    return jsonify({"message": messages[action]})


def validate_game_server_entry(server, seen_ids):
    if not isinstance(server, dict):
        raise ValueError("Invalid server entry")

    server_id = str(server.get("id", "")).strip()
    name = str(server.get("name", "")).strip()
    kind = str(server.get("kind", "generic")).strip().lower()
    backend_name = str(server.get("backend", "systemd")).strip().lower()
    legacy_policy = str(server.get("control_auth", "silent")).strip().lower()
    if (
        not server_id
        or not server_id.replace("-", "").replace("_", "").isalnum()
        or server_id in seen_ids
        or not name
        or len(name) > 120
        or kind not in ("generic", "minecraft")
        or backend_name not in ("systemd", "podman")
        or legacy_policy not in ("silent", "pam")
    ):
        raise ValueError("Invalid server entry")

    item = {
        "id": server_id,
        "name": name,
        "backend": backend_name,
        "kind": kind,
    }
    raw_permissions = server.get("permissions")
    if raw_permissions is not None and not isinstance(raw_permissions, dict):
        raise ValueError("Invalid server permissions")
    raw_permissions = raw_permissions or {}
    unknown_actions = set(raw_permissions) - set(SERVER_ACTIONS)
    if unknown_actions:
        raise ValueError("Invalid server permission action")
    item["permissions"] = {}
    for action in SERVER_ACTIONS:
        default_policy = legacy_policy if action in ("start", "stop", "restart") else "pam"
        policy = str(raw_permissions.get(action, default_policy)).strip().lower()
        if policy not in SERVER_AUTH_POLICIES:
            raise ValueError("Invalid server permission policy")
        item["permissions"][action] = policy
    runtime = server.get("runtime") if isinstance(server.get("runtime"), dict) else {}
    if backend_name == "systemd":
        unit = str(runtime.get("unit") or server.get("service") or "").strip()
        if not SYSTEMD_UNIT_RE.fullmatch(unit):
            raise ValueError("Invalid systemd unit")
        item["service"] = unit
        item["runtime"] = {"unit": unit}
    else:
        container = str(runtime.get("container_name") or server.get("container") or "").strip()
        if not CONTAINER_NAME_RE.fullmatch(container):
            raise ValueError("Invalid Podman container name")
        item["runtime"] = {"container_name": container}
        management_mode = str(server.get("management_mode", "adopted")).strip().lower()
        if management_mode not in ("adopted", "managed"):
            raise ValueError("Invalid Podman management mode")
        item["management_mode"] = management_mode

    if kind == "minecraft":
        mods_dir = str(server.get("mods_dir", "")).strip()
        if not mods_dir.startswith("/"):
            raise ValueError("Minecraft needs an absolute mods directory")
        item["mods_dir"] = os.path.realpath(mods_dir)
        data = server.get("data") if isinstance(server.get("data"), dict) else {}
        raw_data_directory = str(data.get("directory") or os.path.dirname(mods_dir)).strip()
        if not raw_data_directory.startswith("/"):
            raise ValueError("Minecraft needs an absolute data directory")
        data_directory = os.path.realpath(raw_data_directory)
        item["data"] = {"directory": data_directory}
        if backend_name == "podman":
            expected_data_directory = os.path.realpath(
                os.path.join(PODMAN_DATA_ROOT, server_id, "data")
            )
            expected_mods_directory = os.path.join(expected_data_directory, "mods")
            if data_directory != expected_data_directory:
                raise ValueError("Podman data directory must match the registered workload ID")
            if item["mods_dir"] != expected_mods_directory:
                raise ValueError("Podman mods directory must be the managed data/mods path")
            item["data"] = {
                "directory": data_directory,
                "mods_relative_path": "mods",
            }

    connection = server.get("connection")
    if isinstance(connection, dict) and connection.get("direct_port") is not None:
        direct_port = int(connection["direct_port"])
        if not 1 <= direct_port <= 65535:
            raise ValueError("Invalid game port")
        item["connection"] = {"direct_port": direct_port}
    return item


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
        try:
            item = validate_game_server_entry(server, seen_ids)
        except (TypeError, ValueError) as error:
            return jsonify({"message": str(error)}), 400
        validated.append(item)
        seen_ids.add(item["id"])
    save_game_servers(validated)
    return jsonify({"servers": validated})


@app.route("/servers/backups", methods=["GET"])
def servers_backups():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    source_id = request.args.get("source_id", "")
    try:
        backups = list_backups(BACKUP_ROOT, source_id)
    except (InstallError, OSError, ValueError) as error:
        return jsonify({"message": str(error)}), 400
    return jsonify({"source_id": source_id, "backups": backups})


@app.route("/servers/minecraft/install", methods=["POST"])
def minecraft_install():
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    try:
        config = normalize_install_request(request.json or {})
    except (InstallError, TypeError, ValueError) as error:
        return jsonify({"message": str(error)}), 400

    operation_id = f"minecraft-install-{config['id']}"
    try:
        OPERATIONS.begin(
            operation_id,
            kind="minecraft-install",
            target_id=config["id"],
            message=f"Připravuji server {config['name']}",
        )
    except OperationAlreadyRunning:
        return jsonify({"message": "Instalace tohoto serveru už probíhá"}), 409

    workload = {
        "id": config["id"],
        "backend": "podman",
        "runtime": {"container_name": config["id"]},
    }
    container_created = False
    data_created = False
    registered = False
    proxy_backend = None
    data_directory = os.path.join(PODMAN_DATA_ROOT, config["id"], "data")
    try:
        with workload_lock(config["id"]):
            existing_servers = load_game_servers()
            if any(server.get("id") == config["id"] for server in existing_servers):
                raise InstallError("Server s tímto ID už je registrovaný")
            check_host_port_available(config["port"])
            proxy_backend = backend_for(
                workload,
                podman_user=PODMAN_USER,
                podman_socket_path=PODMAN_SOCKET_PATH,
            )
            if proxy_backend.container_exists(workload):
                raise InstallError("Podman container s tímto ID už existuje")

            backup = config.get("backup")
            if backup:
                OPERATIONS.update(
                    operation_id, phase="restoring",
                    message="Ověřuji kontrolní součet a obnovuji zálohu", progress=15,
                )
                restore_result = restore_backup(
                    backup_root=BACKUP_ROOT,
                    source_id=backup["source_id"],
                    backup_id=backup["id"],
                    data_root=PODMAN_DATA_ROOT,
                    target_id=config["id"],
                    owner_user=PODMAN_USER,
                )
                data_directory = restore_result["data_directory"]
            else:
                OPERATIONS.update(
                    operation_id, phase="data",
                    message="Vytvářím persistentní datový adresář", progress=15,
                )
                data_directory = fresh_data_directory(
                    data_root=PODMAN_DATA_ROOT,
                    target_id=config["id"],
                    owner_user=PODMAN_USER,
                )
            data_created = True

            OPERATIONS.update(
                operation_id, phase="pulling",
                message="Stahuji Minecraft server image", progress=40,
            )
            pull_result = proxy_backend.pull_image(config["image"])
            if pull_result.returncode != 0:
                raise InstallError(
                    pull_result.error or pull_result.output or "Stažení image selhalo"
                )

            OPERATIONS.update(
                operation_id, phase="network",
                message="Připravuji privátní Podman síť", progress=55,
            )
            gate_config = load_gate_config()
            if not proxy_backend.network_exists(gate_config["network"]):
                network_result = proxy_backend.create_network(
                    gate_config["network"],
                    labels={"io.game-platform.managed": "true"},
                )
                if network_result.returncode != 0:
                    raise InstallError(
                        network_result.error or network_result.output or "Vytvoření sítě selhalo"
                    )

            OPERATIONS.update(
                operation_id, phase="creating",
                message="Vytvářím Minecraft container", progress=70,
            )
            create_result = proxy_backend.create_container(
                workload,
                config["image"],
                environment=container_environment(config),
                mounts=[{"source": data_directory, "target": "/data"}],
                ports=[{
                    "host": "0.0.0.0",
                    "host_port": config["port"],
                    "container_port": 25565,
                }],
                labels={
                    "io.game-platform.workload-id": config["id"],
                    "io.game-platform.kind": "minecraft-server",
                    "io.game-platform.loader": config["loader"].lower(),
                    "io.game-platform.minecraft-version": config["version"],
                },
                restart_policy="unless-stopped",
                networks=[gate_config["network"]],
            )
            if create_result.returncode != 0:
                raise InstallError(
                    create_result.error or create_result.output or "Vytvoření containeru selhalo"
                )
            container_created = True

            OPERATIONS.update(
                operation_id, phase="starting",
                message="Spouštím Minecraft server", progress=82,
            )
            start_result = proxy_backend.start(workload)
            if start_result.returncode != 0:
                raise InstallError(
                    start_result.error or start_result.output or "Spuštění serveru selhalo"
                )
            OPERATIONS.update(
                operation_id, phase="verifying",
                message="Čekám na dokončení startu Minecraft serveru", progress=92,
            )
            player_status = wait_for_minecraft_install_ready(
                "127.0.0.1", config["port"], proxy_backend, workload,
            )

            server_entry = validate_game_server_entry({
                "id": config["id"],
                "name": config["name"],
                "backend": "podman",
                "kind": "minecraft",
                "runtime": {"container_name": config["id"]},
                "management_mode": "managed",
                "mods_dir": os.path.join(data_directory, "mods"),
                "data": {"directory": data_directory},
                "connection": {"direct_port": config["port"]},
                "permissions": {
                    "start": "silent", "stop": "silent",
                    "restart": "silent", "backup": "pam",
                },
            }, {server.get("id") for server in existing_servers})
            save_game_servers([*existing_servers, server_entry])
            registered = True

            route_warning = None
            if config["hostname"]:
                OPERATIONS.update(
                    operation_id, phase="routing",
                    message="Přidávám hostname trasu do Gate Lite", progress=97,
                )
                try:
                    gate_config = upsert_gate_route(
                        gate_config, config["hostname"], config["id"], 25565,
                    )
                    save_gate_config(gate_config)
                    layout = write_gate_layout(gate_config, GATE_DATA_DIRECTORY)
                    chown_gate_layout(layout, PODMAN_USER)
                    gate_workload = {
                        "backend": "podman",
                        "runtime": {"container_name": gate_config["container_name"]},
                    }
                    if proxy_backend.container_exists(gate_workload):
                        route_result = proxy_backend.restart(gate_workload)
                        if route_result.returncode != 0:
                            raise InstallError(
                                route_result.error or route_result.output
                                or "Restart Gate Lite selhal"
                            )
                except (GateConfigError, InstallError, OSError, ValueError) as error:
                    route_warning = str(error)
    except (InstallError, KeyError, OSError, RuntimeError, ValueError) as error:
        if container_created and proxy_backend is not None:
            proxy_backend.remove_container(workload, force=True)
        if data_created and not registered:
            shutil.rmtree(os.path.join(PODMAN_DATA_ROOT, config["id"]), ignore_errors=True)
        OPERATIONS.fail(operation_id, message=f"Instalace selhala: {error}")
        return jsonify({"message": str(error)}), 500

    OPERATIONS.finish(operation_id, message=f"Server {config['name']} je připravený")
    return jsonify({
        "message": f"Minecraft server {config['name']} byl nainstalován a ověřen",
        "server": server_entry,
        "players": player_status,
        "hostname": config["hostname"] or None,
        "route_warning": route_warning,
    })


def control_game_server(action):
    server = find_game_server((request.json or {}).get("id", ""))
    if not server:
        return jsonify({"message": "Server not found"}), 404
    if not require_local_server_action(request, server, action):
        return jsonify({"message": "Unauthorized"}), 403
    try:
        backend = backend_for(
            server,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        with workload_lock(server["id"]):
            result = getattr(backend, action)(server)
    except (AttributeError, KeyError, ValueError) as error:
        return jsonify({"message": str(error)}), 500

    success_messages = {
        "start": f"{server.get('name')} spuštěn",
        "stop": f"{server.get('name')} zastaven",
        "restart": f"{server.get('name')} restartován",
    }
    if result.returncode == 0:
        return jsonify({"message": success_messages[action]})
    return jsonify({"message": result.error or result.output or "Lifecycle operace selhala"}), 500


@app.route("/servers/start", methods=["POST"])
def servers_start():
    return control_game_server("start")


@app.route("/servers/stop", methods=["POST"])
def servers_stop():
    return control_game_server("stop")


@app.route("/servers/restart", methods=["POST"])
def servers_restart():
    return control_game_server("restart")


@app.route("/servers/backup", methods=["POST"])
def servers_backup():
    server = find_game_server((request.json or {}).get("id", ""))
    if not server:
        return jsonify({"message": "Server not found"}), 404
    data = server.get("data") if isinstance(server.get("data"), dict) else {}
    if server.get("backend", "systemd") not in ("systemd", "podman") or not data.get("directory"):
        return jsonify({"message": "Server nemá nakonfigurovaný datový adresář pro zálohu"}), 400
    if not require_local_server_action(request, server, "backup"):
        return jsonify({"message": "Unauthorized"}), 403
    try:
        backend = backend_for(
            server,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        with workload_lock(server["id"]):
            backup = create_workload_backup(
                server,
                backend,
                backup_root=BACKUP_ROOT,
                owner_user=PODMAN_USER,
            )
    except (BackupError, KeyError, OSError, ValueError) as error:
        return jsonify({"message": str(error)}), 500
    return jsonify({
        "message": f"Záloha serveru {server.get('name', server['id'])} byla vytvořena a ověřena",
        "backup": backup,
    })


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
