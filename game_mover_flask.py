#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import Flask, jsonify, request
import glob
import os
import shutil
import grp
import subprocess
import configparser
from typing import Optional
from dataclasses import dataclass
import time
import secrets
import datetime
import sys
import json
import sqlite3
import threading
import ipaddress
import socket
import stat
import math
import psutil
from concurrent.futures import ThreadPoolExecutor

from game_mover_backups import BackupError, create_workload_backup
from game_mover_catalog import (
    CatalogNotConfigured,
    CatalogUpstreamError,
    CatalogValidationError,
    CurseForgeCatalogProvider,
    load_curseforge_api_key,
)
from game_mover_jobs import OperationAlreadyRunning, OperationRegistry
from game_mover_endpoints import normalize_endpoints
from game_mover_satisfactory import discover_satisfactory_endpoints
from game_mover_privileged import PrivilegedError, privileged_call
from game_mover_dns import (
    DnsConfigError,
    default_dns_config,
    dns_provider_catalog,
    normalize_dns_config,
    runtime_dns_config,
    write_runtime_config,
)
from game_mover_pihole import PiholeAdapterError, sync_pihole_records
from game_mover_mods import scan_mod_directory
from game_mover_users import interactive_user_home, interactive_usernames
from game_mover_security import (
    POLICY_MODES,
    SERVER_ACTION_IDS,
    disabled_security_config,
    global_policy,
    normalize_security_config,
    public_security_payload,
    server_policy,
    validate_security_update,
)
from game_mover_minecraft import (
    configured_rcon,
    configured_server_port,
    count_known_players,
    execute_rcon_command,
    local_server_addresses,
    query_server_rcon,
    query_server_status,
)
from game_mover_properties import (
    MinecraftPropertiesError,
    read_minecraft_properties,
    write_minecraft_properties,
)
from game_mover_operators import (
    MinecraftOperatorsError,
    operator_command,
    read_operators,
)
from game_mover_whitelist import (
    MinecraftWhitelistError,
    read_whitelist,
    whitelist_command,
)
from game_mover_logs import WorkloadLogError, read_minecraft_latest_log
from game_mover_launchers import LauncherError, launcher_statuses, update_launcher
from game_mover_game_inventory import scan_installed_games
from game_mover_game_filters import is_excluded_game
from game_mover_notes import (
    NotesError, delete_target_notes, initialize_notes_database, list_all_notes,
    list_notes, replace_notes,
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
    detect_server_pack_loader_version,
    fresh_data_directory,
    install_curseforge_server_pack,
    list_backups,
    normalize_install_request,
    restore_backup,
)
from game_mover_workloads import (
    BackendResult, CONTAINER_NAME_RE, SYSTEMD_UNIT_RE, WorkloadState,
    backend_for as make_backend, bounded_log_output, normalize_log_tail, run_command,
)

app = Flask(__name__)

# ------------------------------------------------------------
# KONFIGURACE
# ------------------------------------------------------------
GAMES_ROOT = "/var/Games"
GAMES_LINKS_ROOT = "/var/Games_links"
GROUP_NAME = "gemers"
SETFACL_PATH = "/usr/bin/setfacl"
PERMISSION_TARGETS = {
    "steam-library": os.path.join(GAMES_ROOT, "steam"),
}
LOCAL_ADMIN_TOKEN_DIR = "/etc/game_mover"
STATE_DIRECTORY = os.path.realpath(
    os.getenv("GAME_MOVER_STATE_DIRECTORY", "/var/lib/game-mover")
)
LOCAL_ADMIN_TOKEN_PATH = os.path.join(LOCAL_ADMIN_TOKEN_DIR, "api.token")
LOCAL_ADMIN_TOKEN_HEADER = "X-Game-Mover-Token"
READ_TOKEN_PATH = os.getenv("GAME_MOVER_READ_TOKEN_PATH", "/etc/game_mover/read.token")
READ_TOKEN_HEADER = "X-Game-Mover-Read-Token"
MINECRAFT_MODS_DIR = os.getenv("GAME_MOVER_MINECRAFT_MODS_DIR", "/opt/forge_srv/mods")
GAME_SERVERS_CONFIG_PATH = os.path.join(STATE_DIRECTORY, "servers.json")
GATE_CONFIG_PATH = os.path.join(STATE_DIRECTORY, "gate.json")
SECURITY_CONFIG_PATH = os.path.join(STATE_DIRECTORY, "security.json")
DNS_CONFIG_PATH = os.path.join(STATE_DIRECTORY, "dns.json")
DNS_RUNTIME_CONFIG_PATH = os.path.join(STATE_DIRECTORY, "dns-runtime.json")
DNS_PIHOLE_STATE_PATH = os.path.join(STATE_DIRECTORY, "dns-pihole-state.json")
DNS_SERVICE_NAME = "game-mover-dns.service"
PIHOLE_SERVICE_NAME = "pihole-FTL.service"
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
NOTES_DB_PATH = os.path.realpath(
    os.getenv("GAME_MOVER_NOTES_DB_PATH", "/var/lib/game-platform/game-mover-notes.sqlite3")
)
CURSEFORGE_API_KEY_PATH = os.getenv(
    "GAME_MOVER_CURSEFORGE_API_KEY_PATH",
    os.path.join(LOCAL_ADMIN_TOKEN_DIR, "curseforge.key"),
)
PRIVILEGED_HELPER_ENABLED = os.getenv(
    "GAME_MOVER_PRIVILEGED_HELPER", "0",
).strip() == "1"
WORKLOAD_LOCKS = {}
WORKLOAD_LOCKS_GUARD = threading.Lock()
OPERATIONS = OperationRegistry()
GATE_DEPLOY_OPERATION_ID = "gate-deploy"
SERVER_ACTIONS = SERVER_ACTION_IDS
SERVER_AUTH_POLICIES = POLICY_MODES
MINECRAFT_STATUS_TIMEOUT = 8.0
MINECRAFT_STATUS_DISCOVERY_TIMEOUT = 3.0
MINECRAFT_STATUS_REFRESH_SECONDS = 30.0
MINECRAFT_STATUS_RETRY_SECONDS = 15.0
MINECRAFT_STATUS_CACHE = {}
MINECRAFT_STATUS_INFLIGHT = set()
MINECRAFT_STATUS_LOCK = threading.Lock()
MINECRAFT_STATUS_EXECUTOR = ThreadPoolExecutor(max_workers=4)
GAME_INVENTORY_CACHE = {"updated": 0.0, "games": []}
GAME_INVENTORY_LOCK = threading.Lock()
GAME_INVENTORY_CACHE_SECONDS = 300
SECURITY_CONFIG_LOCK = threading.Lock()
DNS_CONFIG_LOCK = threading.Lock()
CGNAT_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
MINECRAFT_INSTALL_PORT_START = 25570
MINECRAFT_INSTALL_PORT_END = 65535


def _privileged_systemd(verb, unit, timeout=30):
    result = privileged_call(
        "systemd", {"verb": verb, "unit": unit, "timeout": timeout},
        timeout=timeout + 5,
    )
    return (
        int(result.get("returncode", 1)),
        str(result.get("stdout", "")).strip(),
        str(result.get("stderr", "")).strip(),
    )


def workload_command_runner(command, timeout=30):
    """Route system services through the broker; keep rootless Podman local."""
    if not PRIVILEGED_HELPER_ENABLED:
        return run_command(command, timeout)
    executable = os.path.basename(str(command[0])) if command else ""
    try:
        if executable == "podman":
            return run_command(command, timeout)
        if executable == "systemctl" and len(command) == 3 and command[1] == "is-active":
            # Reading a registered host unit's state is unprivileged.  Keep it
            # out of the root broker so visibility does not depend on the
            # separate allowlist that protects lifecycle operations.
            return run_command(command, timeout)
        if executable == "systemctl" and len(command) == 3:
            rc, output, error = _privileged_systemd(command[1], command[2], timeout)
            return BackendResult(rc, output, error)
        if executable == "journalctl" and len(command) == 7:
            unit = command[2]
            tail = command[6]
            result = privileged_call(
                "systemd", {"verb": "logs", "unit": unit, "tail": tail, "timeout": timeout},
                timeout=timeout + 5,
            )
            return BackendResult(
                int(result.get("returncode", 1)),
                str(result.get("stdout", "")).strip(),
                str(result.get("stderr", "")).strip(),
            )
    except PrivilegedError as error:
        return BackendResult(126, error=str(error))
    return BackendResult(126, error="Příkaz není povolen privilegovaným brokerem")


def backend_for(workload, *, podman_user, runner=None, podman_socket_path=None):
    return make_backend(
        workload,
        podman_user=podman_user,
        runner=runner or workload_command_runner,
        podman_socket_path=podman_socket_path,
    )


def pihole_command_runner(command, **_kwargs):
    if not PRIVILEGED_HELPER_ENABLED:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=20, check=False,
        )
    try:
        if len(command) not in (3, 4) or command[1:3] != ["--config", "dns.hosts"]:
            raise PrivilegedError("Pi-hole příkaz není povolen")
        parameters = {}
        if len(command) == 4:
            records = json.loads(command[3])
            parameters["records"] = records
        result = privileged_call("pihole", parameters, timeout=25)
        return subprocess.CompletedProcess(
            command,
            int(result.get("returncode", 1)),
            str(result.get("stdout", "")),
            str(result.get("stderr", "")),
        )
    except (PrivilegedError, ValueError, TypeError, json.JSONDecodeError) as error:
        return subprocess.CompletedProcess(command, 126, "", str(error))


def workload_lock(workload_id):
    with WORKLOAD_LOCKS_GUARD:
        return WORKLOAD_LOCKS.setdefault(workload_id, threading.Lock())


def curseforge_catalog_provider():
    return CurseForgeCatalogProvider(load_curseforge_api_key(CURSEFORGE_API_KEY_PATH))


def default_game_servers():
    return [
        {"id": "minecraft", "name": "Minecraft", "backend": "systemd", "service": os.getenv("GAME_MOVER_MINECRAFT_SERVICE", "forge-srv.service"), "kind": "minecraft", "mods_dir": MINECRAFT_MODS_DIR, "permissions": {"start": "silent", "stop": "silent", "restart": "silent", "backup": "pam"}},
        {"id": "satisfactory", "name": "Satisfactory", "backend": "systemd", "service": os.getenv("GAME_MOVER_SATISFACTORY_SERVICE", "satisfactory.service"), "kind": "generic", "adapter": "satisfactory", "permissions": {"start": "silent", "stop": "silent", "restart": "silent", "backup": "pam"}},
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
    try:
        endpoints = normalize_endpoints(item.get("endpoints"))
    except ValueError:
        endpoints = []
    if endpoints:
        item["endpoints"] = endpoints
    else:
        item.pop("endpoints", None)
    runtime = item.get("runtime") if isinstance(item.get("runtime"), dict) else {}
    if backend == "systemd":
        unit = str(runtime.get("unit") or item.get("service") or "").strip()
        item["service"] = unit
        item["runtime"] = {**runtime, "unit": unit}
    elif backend == "podman":
        container = str(runtime.get("container_name") or item.get("container") or "").strip()
        item["container"] = container
        item["runtime"] = {**runtime, "container_name": container}
    adapter = str(item.get("adapter", "")).strip().lower()
    effective_unit = str(item.get("runtime", {}).get("unit", "")).strip().lower()
    if not adapter and (
        str(item.get("id", "")).strip().lower() == "satisfactory"
        or effective_unit == "satisfactory.service"
    ):
        adapter = "satisfactory"
    if adapter == "satisfactory":
        item["adapter"] = adapter
    else:
        item.pop("adapter", None)
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
    os.makedirs(os.path.dirname(GAME_SERVERS_CONFIG_PATH), mode=0o750, exist_ok=True)
    temporary_path = f"{GAME_SERVERS_CONFIG_PATH}.tmp"
    with open(temporary_path, "w") as config_file:
        json.dump(servers, config_file, indent=2)
        config_file.write("\n")
    os.chmod(temporary_path, 0o640)
    os.replace(temporary_path, GAME_SERVERS_CONFIG_PATH)


def load_security_config(servers=None):
    servers = load_game_servers() if servers is None else servers
    try:
        with SECURITY_CONFIG_LOCK:
            with open(SECURITY_CONFIG_PATH, "r", encoding="utf-8") as config_file:
                raw_config = json.load(config_file)
    except FileNotFoundError:
        raw_config = None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return disabled_security_config(servers)
    return normalize_security_config(raw_config, servers)


def save_security_config(config):
    os.makedirs(os.path.dirname(SECURITY_CONFIG_PATH), mode=0o750, exist_ok=True)
    temporary_path = f"{SECURITY_CONFIG_PATH}.tmp"
    with SECURITY_CONFIG_LOCK:
        with open(temporary_path, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2, sort_keys=True)
            config_file.write("\n")
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, SECURITY_CONFIG_PATH)
    return config


def load_gate_config():
    try:
        with open(GATE_CONFIG_PATH, "r", encoding="utf-8") as config_file:
            return normalize_gate_config(json.load(config_file))
    except (OSError, UnicodeError, json.JSONDecodeError, GateConfigError):
        return normalize_gate_config(default_gate_config())


def load_dns_config():
    try:
        with open(DNS_CONFIG_PATH, "r", encoding="utf-8") as config_file:
            return normalize_dns_config(json.load(config_file))
    except FileNotFoundError:
        return default_dns_config()
    except (OSError, UnicodeError, json.JSONDecodeError, DnsConfigError):
        return default_dns_config()


def sync_dns_runtime_config(config=None, gate_config=None):
    config = load_dns_config() if config is None else normalize_dns_config(config)
    gate_config = load_gate_config() if gate_config is None else gate_config
    runtime = write_runtime_config(DNS_RUNTIME_CONFIG_PATH, config, gate_config)
    pihole_enabled = config["provider"] == "pihole_local"
    if pihole_enabled or os.path.exists(DNS_PIHOLE_STATE_PATH):
        sync_options = {"enabled": pihole_enabled}
        if PRIVILEGED_HELPER_ENABLED:
            sync_options["runner"] = pihole_command_runner
        sync_pihole_records(runtime["records"], DNS_PIHOLE_STATE_PATH, **sync_options)
    return runtime


def save_dns_config(config):
    config = normalize_dns_config(config)
    os.makedirs(os.path.dirname(DNS_CONFIG_PATH), mode=0o750, exist_ok=True)
    temporary_path = f"{DNS_CONFIG_PATH}.tmp"
    with DNS_CONFIG_LOCK:
        with open(temporary_path, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=2, sort_keys=True)
            config_file.write("\n")
        os.chmod(temporary_path, 0o640)
        sync_dns_runtime_config(config=config)
        os.replace(temporary_path, DNS_CONFIG_PATH)
    return config


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


def host_port_available(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("0.0.0.0", int(port)))
    except OSError:
        return False
    return True


def check_host_port_available(port):
    if not host_port_available(port):
        raise InstallError(f"Port {port} už je obsazený; zvol jiný")
    return True


def reserved_host_ports(servers=None, gate_config=None):
    """Return ports reserved by configuration, including stopped workloads."""
    servers = load_game_servers() if servers is None else servers
    reserved = set()
    for server in servers:
        discovered_endpoints = []
        if server.get("adapter") == "satisfactory":
            runtime = server.get("runtime") if isinstance(server.get("runtime"), dict) else {}
            try:
                discovered_endpoints = discover_satisfactory_endpoints(
                    runtime.get("unit", server.get("service", ""))
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
                discovered_endpoints = []
        try:
            endpoints = normalize_endpoints(server.get("endpoints"))
        except ValueError:
            endpoints = []
        reserved.update(
            endpoint["port"] for endpoint in [*discovered_endpoints, *endpoints]
            if endpoint["protocol"] == "tcp"
        )
        connection = server.get("connection")
        if isinstance(connection, dict):
            direct_port = connection.get("direct_port")
            if isinstance(direct_port, int) and not isinstance(direct_port, bool):
                reserved.add(direct_port)

        if (
            server.get("kind") != "minecraft"
            or server.get("backend", "systemd") != "systemd"
        ):
            continue
        data = server.get("data") if isinstance(server.get("data"), dict) else {}
        data_directory = data.get("directory")
        server_port = configured_server_port(data_directory)
        if server_port is not None:
            reserved.add(server_port)
        rcon = configured_rcon(data_directory)
        if isinstance(rcon, dict) and isinstance(rcon.get("port"), int):
            reserved.add(rcon["port"])

    gate_config = load_gate_config() if gate_config is None else gate_config
    listen = gate_config.get("listen") if isinstance(gate_config, dict) else {}
    gate_port = listen.get("port") if isinstance(listen, dict) else None
    if isinstance(gate_port, int) and not isinstance(gate_port, bool):
        reserved.add(gate_port)
    return reserved


def next_available_minecraft_port(
    start=MINECRAFT_INSTALL_PORT_START,
    end=MINECRAFT_INSTALL_PORT_END,
    *,
    servers=None,
    gate_config=None,
):
    reserved = reserved_host_ports(servers=servers, gate_config=gate_config)
    for port in range(int(start), int(end) + 1):
        if port not in reserved and host_port_available(port):
            return port
    raise InstallError(f"V rozsahu {start}–{end} není volný port pro Minecraft server")


def wait_for_minecraft_install_ready(host, port, backend, workload, timeout=600):
    deadline = time.monotonic() + timeout
    last_error = "Minecraft server ještě neodpovídá"
    while time.monotonic() < deadline:
        state = backend.status(workload)
        if state.status == "inactive":
            message = state.error or state.message or "Minecraft container se zastavil"
            log_result = backend.logs(workload, 40)
            log_text = log_result.output or log_result.error
            if log_text:
                lines = [line.strip() for line in log_text.splitlines() if line.strip()]
                detail = "\n".join(lines[-8:])[-2000:]
                if detail:
                    message = f"{message}\n\nPoslední log containeru:\n{detail}"
            raise RuntimeError(message)
        try:
            return query_server_status(host, port, timeout=4)
        except (OSError, UnicodeError, ValueError) as error:
            last_error = str(error)
            time.sleep(2)
    raise RuntimeError(f"Minecraft server se nespustil v časovém limitu: {last_error}")


def save_gate_config(config):
    config = normalize_gate_config(config)
    os.makedirs(os.path.dirname(GATE_CONFIG_PATH), mode=0o750, exist_ok=True)
    temporary_path = f"{GATE_CONFIG_PATH}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, ensure_ascii=False, indent=2, sort_keys=True)
        config_file.write("\n")
    os.chmod(temporary_path, 0o640)
    os.replace(temporary_path, GATE_CONFIG_PATH)
    try:
        sync_dns_runtime_config(gate_config=config)
    except (DnsConfigError, PiholeAdapterError, OSError):
        pass
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


def gate_connections_by_server(servers):
    """Return usable public Gate routes keyed by registered Minecraft server ID."""
    if not os.path.isfile(GATE_CONFIG_PATH):
        return {}
    try:
        config = load_gate_config()
        routes = abstract_gate_routes(config, minecraft_route_targets(servers))
    except (GateConfigError, KeyError, OSError, TypeError, ValueError):
        return {}
    connections = {}
    for route in routes:
        target_id = route.get("target_id")
        route_host = str(route.get("host", ""))
        if not target_id or not route_host:
            continue
        if route_host != "*" and any(marker in route_host for marker in ("*", "?")):
            continue
        connection = {
            "port": config["listen"]["port"],
            "route_host": route_host,
        }
        if route_host != "*":
            connection["host"] = route_host
        existing = connections.get(target_id)
        if existing and existing.get("route_host") != "*":
            continue
        # A concrete hostname is the useful, user-facing address and therefore
        # always replaces a wildcard route, regardless of config ordering.
        connections[target_id] = connection
    return connections


def find_game_server(server_id):
    for server in load_game_servers():
        if server.get("id") == server_id:
            return server
    return None

TIMEKPRA_BIN = "timekpra"
TIMEKPRA_BIN_RESOLVED: list[str] | None = None
TIMEKPRA_ADD_FLAG_CANDIDATES = ["--addtime", "--add-allowedtime", "--addallowedtime"]
TIMEKPRA_SET_TIMELEFT = "--settimeleft"
TIMEKPRA_DISABLE_ARGS = ["--disable"]
TIMEKPRA_DISABLE_SECONDS = 24 * 3600
TIMEKPRA_SET_ALLOWED_HOURS = "--setallowedhours"

TOKEN_TTL_SECONDS = 15 * 60
TIMEKPRA_TOKENS = {}  # token -> (username, expiry)
PAM_AUTH_BACKOFF_BASE_SECONDS = 1
PAM_AUTH_BACKOFF_MAX_SECONDS = 60
PAM_AUTH_THROTTLE_RETENTION_SECONDS = 15 * 60
PAM_AUTH_THROTTLE_MAX_USERS = 1024
PAM_AUTH_THROTTLE_MAX_SOURCES = 128


@dataclass
class PamAuthThrottleEntry:
    failures: int = 0
    blocked_until: float = 0.0
    updated_at: float = 0.0


PAM_AUTH_USER_FAILURES: dict[str, PamAuthThrottleEntry] = {}
PAM_AUTH_SOURCE_FAILURES: dict[str, PamAuthThrottleEntry] = {}
PAM_AUTH_THROTTLE_LOCK = threading.Lock()
PAM_AUTH_ATTEMPT_LOCK = threading.Lock()

# ------------------------------------------------------------
# Platform user common
# ------------------------------------------------------------
def steam_common_candidates(home):
    return [
        os.path.join(home, ".local/share/Steam/steamapps/common"),
        os.path.join(home, ".steam/steam/steamapps/common"),
    ]

PLATFORMS = {
    "steam": {"user_common": steam_common_candidates},
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

def safe_path_component(value, label="path component"):
    if (
        not isinstance(value, str)
        or not value
        or value in (".", "..")
        or value != value.strip()
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"Invalid {label}")
    return value


def resolve_beneath(root, candidate, *, allow_leaf_symlink=False):
    """Resolve an existing or prospective child and keep it below root."""
    root_path = os.path.abspath(root)
    if os.path.islink(root_path):
        raise ValueError("Managed root must not be a symbolic link")
    resolved_root = os.path.realpath(root_path)
    candidate_path = os.path.abspath(candidate)
    resolved_candidate = os.path.realpath(candidate_path)
    if os.path.commonpath([resolved_candidate, resolved_root]) != resolved_root:
        raise ValueError("Path escapes its managed root")
    if not allow_leaf_symlink and os.path.islink(candidate_path):
        raise ValueError("Symbolic-link path is not allowed")
    return resolved_candidate


def ensure_managed_directory(root, *components):
    """Create fixed/safe path components without accepting symlink redirects."""
    if not os.path.isdir(root) or os.path.islink(root):
        raise ValueError("Managed root is unavailable or unsafe")
    current = os.path.realpath(root)
    for component in components:
        safe_path_component(component)
        child = os.path.join(current, component)
        if os.path.lexists(child):
            if os.path.islink(child) or not os.path.isdir(child):
                raise ValueError("Managed directory is unsafe")
        else:
            os.mkdir(child)
        current = resolve_beneath(root, child)
    return current


def user_common_dir(platform, user):
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform '{platform}'")
    home = interactive_user_home(user)
    candidates = PLATFORMS[platform]["user_common"](home)
    existent = first_existing(candidates)
    if not existent:
        return None
    return resolve_beneath(home, existent)

def shared_game_path(platform, game):
    return os.path.join(GAMES_ROOT, platform, game)

def steamapps_candidates(home):
    return [
        os.path.join(home, ".local/share/Steam/steamapps"),
        os.path.join(home, ".steam/steam/steamapps"),
    ]

def steamapps_dir(user) -> Optional[str]:
    home = interactive_user_home(user)
    existent = first_existing(steamapps_candidates(home))
    if not existent:
        return None
    return resolve_beneath(home, existent)

def permission_target_path(target_id):
    """Resolve a fixed permission target without trusting a client path."""
    path = PERMISSION_TARGETS.get(target_id)
    if path is None:
        raise ValueError("Unknown permission target")
    if os.path.islink(GAMES_ROOT) or os.path.islink(path):
        raise ValueError("Permission target must not be a symbolic link")

    games_root = os.path.realpath(GAMES_ROOT)
    resolved = os.path.realpath(path)
    if os.path.commonpath([resolved, games_root]) != games_root:
        raise ValueError("Permission target escapes the game library")
    if not os.path.isdir(resolved):
        raise FileNotFoundError("Permission target does not exist")
    return resolved


def _set_group_perms_fd(directory_fd, gid):
    """Apply shared-library modes using descriptors and never follow symlinks."""
    os.fchown(directory_fd, -1, gid)
    os.fchmod(directory_fd, 0o775)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK

    for name in os.listdir(directory_fd):
        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(entry.st_mode):
            continue
        if stat.S_ISDIR(entry.st_mode):
            child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
            try:
                _set_group_perms_fd(child_fd, gid)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(entry.st_mode):
            file_fd = os.open(name, file_flags, dir_fd=directory_fd)
            try:
                os.fchown(file_fd, -1, gid)
                os.fchmod(file_fd, 0o664)
            finally:
                os.close(file_fd)


def set_group_perms(path):
    """Grant durable shared access without relying on the SGID filesystem bit."""
    gid = grp.getgrnam(GROUP_NAME).gr_gid
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_fd = os.open(path, flags)
    try:
        _set_group_perms_fd(directory_fd, gid)
        descriptor_path = f"/proc/self/fd/{directory_fd}"
        common = {
            "check": True,
            "capture_output": True,
            "text": True,
            "pass_fds": (directory_fd,),
        }
        # The command-line /proc path resolves to the already validated and
        # opened directory. setfacl's recursive walk skips nested symlinks.
        subprocess.run(
            [
                SETFACL_PATH, "-R", "-m",
                f"g:{gid}:rwX,m::rwX", descriptor_path,
            ],
            **common,
        )
        subprocess.run(
            [
                SETFACL_PATH, "-R", "-d", "-m",
                f"g:{gid}:rwx,m::rwx", descriptor_path,
            ],
            **common,
        )
    finally:
        os.close(directory_fd)


def ensure_local_admin_token():
    if PRIVILEGED_HELPER_ENABLED:
        if not os.path.isfile(LOCAL_ADMIN_TOKEN_PATH):
            raise RuntimeError("Instalátor nevytvořil lokální administrační token")
        return load_local_admin_token()
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
    if PRIVILEGED_HELPER_ENABLED:
        if not os.path.isfile(READ_TOKEN_PATH):
            raise RuntimeError("Instalátor nevytvořil vzdálený read-only token")
        return
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
    read_only_paths = (
        "/servers/status",
        "/notes",
        "/servers/minecraft/mods",
        "/proxy/status",
        "/dns/status",
        "/minecraft/modpacks/status",
        "/minecraft/modpacks/search",
    )
    read_only_prefixes = ("/minecraft/modpacks/", "/notes/")
    if (
        request.method != "GET"
        or (
            request.path not in read_only_paths
            and not request.path.startswith(read_only_prefixes)
        )
    ):
        return jsonify({"message": "Remote access is limited to server status endpoints"}), 403
    if not require_read_access(request):
        return jsonify({"message": "Unauthorized"}), 403
    return None

# ------------------------------------------------------------
# Systemctl helpers
# ------------------------------------------------------------
def systemctl_is_active(service_name: str):
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return _privileged_systemd("is-active", service_name, 15)
        except PrivilegedError as error:
            return 126, "unknown", str(error)
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
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return _privileged_systemd(action, service_name, 60)
        except PrivilegedError as error:
            return 126, "", str(error)
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


def systemctl_enable_now(service_name: str):
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return _privileged_systemd("enable-now", service_name, 60)
        except PrivilegedError as error:
            return 126, "", str(error)
    try:
        proc = subprocess.run(
            ["systemctl", "enable", "--now", service_name],
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "systemctl nenalezen"
    except Exception as error:
        return 1, "", str(error)


def systemctl_disable_now(service_name: str):
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return _privileged_systemd("disable-now", service_name, 60)
        except PrivilegedError as error:
            return 126, "", str(error)
    try:
        proc = subprocess.run(
            ["systemctl", "disable", "--now", service_name],
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "systemctl nenalezen"
    except Exception as error:
        return 1, "", str(error)


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
    version = None
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
        for host in candidates:
            timeout = (
                MINECRAFT_STATUS_TIMEOUT
                if host == preferred_host
                else MINECRAFT_STATUS_DISCOVERY_TIMEOUT
            )
            try:
                status = query_server_status(host, port, timeout=timeout)
                if counts is None:
                    counts = {
                        "online": status["online"],
                        "max": status["max"],
                    }
                if isinstance(status.get("version"), dict):
                    version = dict(status["version"])
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
                if version is not None:
                    entry["version"] = dict(version)
                else:
                    entry.pop("version", None)
                entry["last_success"] = entry["last_attempt"]
                if selected_host:
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
        version = entry.get("version")
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
        if snapshot is not None and isinstance(version, dict):
            snapshot["version"] = dict(version)
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


def game_server_status(server, security_config=None):
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
        "adapter": server.get("adapter", ""),
        "has_mods": bool(server.get("mods_dir")),
        "permissions": {
            action: server_action_policy(server, action, security_config)
            for action in SERVER_ACTIONS
        },
        "status": state.status,
        "native_status": state.native_status,
        "message": state.message,
        "error": state.error,
    }
    try:
        result["notes"] = list_notes(
            NOTES_DB_PATH, "server", server.get("id", ""),
        )
    except (NotesError, OSError, sqlite3.Error):
        result["notes"] = []
    result["deletion_supported"] = (
        server.get("kind") == "minecraft"
        and backend_name == "podman"
        and server.get("management_mode") == "managed"
    )
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
    endpoints = []
    if direct_port is not None:
        source_labels = {
            "server.properties": "server.properties",
            "podman": "Podman",
            "registry": "registr",
        }
        endpoints.append({
            "name": "Minecraft" if server.get("kind") == "minecraft" else "Hra",
            "protocol": "tcp", "port": direct_port,
            "source": source_labels.get(port_source, str(port_source or "")),
        })
    if server.get("adapter") == "satisfactory":
        unit = runtime.get("unit", server.get("service", ""))
        try:
            endpoints.extend(discover_satisfactory_endpoints(unit))
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            result["endpoint_discovery_error"] = str(error)
    try:
        configured_endpoints = normalize_endpoints(server.get("endpoints"))
    except ValueError:
        configured_endpoints = []
    occupied = {
        (endpoint["protocol"], endpoint["port"])
        for endpoint in endpoints
    }
    for endpoint in configured_endpoints:
        key = (endpoint["protocol"], endpoint["port"])
        if key not in occupied:
            endpoints.append({**endpoint, "source": "registr"})
            occupied.add(key)
    result["endpoints"] = endpoints
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
                    version = counts.pop("version", None)
                    players.update(counts)
                    if isinstance(version, dict):
                        result["minecraft_version"] = version
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
    if PRIVILEGED_HELPER_ENABLED:
        try:
            result = privileged_call("timekpr", {"args": args}, timeout=35)
            return (
                int(result.get("returncode", 1)),
                str(result.get("stdout", "")).strip(),
                str(result.get("stderr", "")).strip(),
            )
        except PrivilegedError as error:
            return 126, "", str(error)
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


def _pam_auth_throttle_prune(records, now):
    stale_before = now - PAM_AUTH_THROTTLE_RETENTION_SECONDS
    stale = [
        key for key, entry in records.items()
        if entry.updated_at < stale_before and entry.blocked_until <= now
    ]
    for key in stale:
        records.pop(key, None)


def _pam_auth_throttle_entry(records, key, limit, now):
    entry = records.get(key)
    if entry is not None:
        return entry
    if len(records) >= limit:
        oldest = min(records, key=lambda item: records[item].updated_at)
        records.pop(oldest, None)
    entry = PamAuthThrottleEntry(updated_at=now)
    records[key] = entry
    return entry


def pam_auth_retry_after(source: str, username: str, now=None) -> int:
    """Return the remaining per-source or per-user authentication backoff."""
    current = time.monotonic() if now is None else now
    normalized_user = username.strip().casefold()
    with PAM_AUTH_THROTTLE_LOCK:
        _pam_auth_throttle_prune(PAM_AUTH_SOURCE_FAILURES, current)
        _pam_auth_throttle_prune(PAM_AUTH_USER_FAILURES, current)
        entries = (
            PAM_AUTH_SOURCE_FAILURES.get(source),
            PAM_AUTH_USER_FAILURES.get(normalized_user),
        )
        remaining = max(
            (entry.blocked_until - current for entry in entries if entry),
            default=0.0,
        )
    return max(0, math.ceil(remaining))


def record_pam_auth_failure(source: str, username: str, now=None) -> int:
    """Apply capped exponential backoff to both authentication dimensions."""
    current = time.monotonic() if now is None else now
    normalized_user = username.strip().casefold()
    delays = []
    with PAM_AUTH_THROTTLE_LOCK:
        _pam_auth_throttle_prune(PAM_AUTH_SOURCE_FAILURES, current)
        _pam_auth_throttle_prune(PAM_AUTH_USER_FAILURES, current)
        dimensions = (
            (PAM_AUTH_SOURCE_FAILURES, source, PAM_AUTH_THROTTLE_MAX_SOURCES),
            (PAM_AUTH_USER_FAILURES, normalized_user, PAM_AUTH_THROTTLE_MAX_USERS),
        )
        for records, key, limit in dimensions:
            entry = _pam_auth_throttle_entry(records, key, limit, current)
            entry.failures += 1
            delay = PAM_AUTH_BACKOFF_BASE_SECONDS
            for _ in range(entry.failures - 1):
                delay = min(PAM_AUTH_BACKOFF_MAX_SECONDS, delay * 2)
                if delay == PAM_AUTH_BACKOFF_MAX_SECONDS:
                    break
            entry.blocked_until = current + delay
            entry.updated_at = current
            delays.append(delay)
    return max(delays)


def clear_pam_auth_failures(source: str, username: str) -> None:
    normalized_user = username.strip().casefold()
    with PAM_AUTH_THROTTLE_LOCK:
        PAM_AUTH_SOURCE_FAILURES.pop(source, None)
        PAM_AUTH_USER_FAILURES.pop(normalized_user, None)


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


def revoke_token(req):
    """Invalidate the exact PAM session presented by a loopback client."""
    payload = req.get_json(silent=True) or {}
    token = req.headers.get("X-Timekpr-Token") or payload.get("token")
    if not token or not require_token(req):
        return False
    TIMEKPRA_TOKENS.pop(token, None)
    return True

def require_local_pam_session(req):
    return req.remote_addr in ("127.0.0.1", "::1") and bool(require_token(req))


def authorize_local_policy(req, policy):
    if req.remote_addr not in ("127.0.0.1", "::1"):
        return False
    if policy == "pam":
        return bool(require_token(req))
    if policy == "silent":
        # A wheel-authenticated local session may perform actions that are also
        # available through the machine-local silent token. This lets an SSH
        # local-forwarding client administer the host without copying api.token
        # away from that host.
        return require_local_admin(req) or bool(require_token(req))
    return False


def server_action_policy(server, action, security_config=None):
    config = security_config if security_config is not None else load_security_config()
    return server_policy(config, server, action)


def operation_policy(operation, security_config=None):
    config = security_config if security_config is not None else load_security_config()
    return global_policy(config, operation)


def require_local_operation(req, operation):
    return authorize_local_policy(req, operation_policy(operation))


def require_local_server_action(req, server, action):
    return authorize_local_policy(req, server_action_policy(server, action))


def limit_for_today(user: str) -> Optional[int]:
    """Vrátí limit pro dnešní den (v sekundách) z timekpr configu uživatele."""
    if PRIVILEGED_HELPER_ENABLED:
        try:
            result = privileged_call(
                "timekpr-config",
                {"user": user, "day": datetime.date.today().isoweekday()},
            )
            value = result.get("limit")
            return int(value) if value is not None else None
        except (PrivilegedError, TypeError, ValueError):
            return None
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
    if PRIVILEGED_HELPER_ENABLED:
        try:
            result = privileged_call(
                "timekpr-config", {"user": user, "day": day_idx},
            )
            value = result.get("hours")
            return str(value) if value is not None else None
        except PrivilegedError:
            return None
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
    if PRIVILEGED_HELPER_ENABLED:
        try:
            privileged_call(
                "timekpr-config",
                {"operation": "ensure-day", "user": user, "day": day_idx},
            )
        except PrivilegedError:
            pass
        return
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
        common = user_common_dir(platform, user)
        if not common:
            return jsonify({"platform": platform, "games": []})

        games = [
            d for d in os.listdir(common)
            if os.path.isdir(os.path.join(common, d))
            and not os.path.islink(os.path.join(common, d))
            and not is_excluded_game(platform, d)
        ]
        return jsonify({"platform": platform, "user": user, "games": sorted(games)})
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/list_shared", methods=["GET"])
def api_list_shared():
    platform = request.args.get("platform", "")
    if not platform:
        return jsonify({"message": "Missing platform"}), 400
    if platform not in PLATFORMS:
        return jsonify({"message": f"Unsupported platform '{platform}'"}), 400

    try:
        base = resolve_beneath(GAMES_ROOT, os.path.join(GAMES_ROOT, platform))
        if not os.path.isdir(base):
            return jsonify({"platform": platform, "games": []})
        games = [
            d for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))
            and not os.path.islink(os.path.join(base, d))
            and not is_excluded_game(platform, d)
        ]
        return jsonify({"platform": platform, "games": sorted(games)})
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/move_game", methods=["POST"])
def move_game():
    if not require_local_operation(request, "game.move"):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    platform = data.get("platform")
    game_name = data.get("game_name")
    user = data.get("user")

    if not platform or not game_name or not user:
        return jsonify({"message": "Missing parameters"}), 400
    if platform != "steam":
        return jsonify({"message": "Game Mover supports shared game data only for Steam"}), 400
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return jsonify(privileged_call("move-game", {
                "platform": platform, "game_name": game_name, "user": user,
            }, timeout=180))
        except PrivilegedError as error:
            return jsonify({"message": str(error)}), 400

    try:
        safe_path_component(game_name, "game name")
        source_common = user_common_dir(platform, user)
        if not source_common:
            return jsonify({"message": "Steam library not found for user"}), 404
        source_path = resolve_beneath(
            source_common, os.path.join(source_common, game_name),
        )
        if not os.path.isdir(source_path):
            return jsonify({"message": "Game not found in source"}), 404
        target_base = ensure_managed_directory(GAMES_ROOT, platform)
        target_path = resolve_beneath(
            target_base, os.path.join(target_base, game_name),
        )
        proxy_base = ensure_managed_directory(GAMES_LINKS_ROOT, user, platform)
        proxy_path = os.path.join(proxy_base, game_name)
        if os.path.lexists(target_path) or os.path.lexists(proxy_path):
            return jsonify({"message": "Target or proxy path already exists"}), 409

        shutil.move(source_path, target_path)
        set_group_perms(target_path)
        os.symlink(target_path, proxy_path)
        os.symlink(proxy_path, source_path)
        return jsonify({"message": f"Game '{game_name}' moved to shared, proxy and symlink created"})
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/create_symlink", methods=["POST"])
def create_symlink():
    if not require_local_operation(request, "game.link"):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    platform = data.get("platform")
    game_name = data.get("game_name")
    user = data.get("user")

    if not platform or not game_name or not user:
        return jsonify({"message": "Missing parameters"}), 400
    if platform != "steam":
        return jsonify({"message": "Game Mover supports shared game data only for Steam"}), 400
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return jsonify(privileged_call("create-symlink", {
                "platform": platform, "game_name": game_name, "user": user,
            }))
        except PrivilegedError as error:
            return jsonify({"message": str(error)}), 400

    try:
        safe_path_component(game_name, "game name")
        target_base = resolve_beneath(
            GAMES_ROOT, os.path.join(GAMES_ROOT, platform),
        )
        target_path = resolve_beneath(
            target_base, os.path.join(target_base, game_name),
        )
        if not os.path.isdir(target_path):
            return jsonify({"message": "Target path does not exist"}), 404
        source_common = user_common_dir(platform, user)
        if not source_common:
            return jsonify({"message": "Steam library not found for user"}), 404
        source_path = resolve_beneath(
            source_common, os.path.join(source_common, game_name),
        )

        if os.path.lexists(source_path):
            return jsonify({"message": "Symlink already exists"}), 400

        proxy_base = ensure_managed_directory(GAMES_LINKS_ROOT, user, platform)
        proxy_path = os.path.join(proxy_base, game_name)
        if os.path.lexists(proxy_path):
            if not os.path.islink(proxy_path) or os.path.realpath(proxy_path) != target_path:
                return jsonify({"message": "Proxy path conflicts with shared game"}), 409
        else:
            os.symlink(target_path, proxy_path)

        os.symlink(proxy_path, source_path)
        return jsonify({"message": "Steam symlink created via proxy"})
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/fix_perms", methods=["POST"])
def fix_perms():
    if not require_local_operation(request, "library.permissions"):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    target_id = data.get("target")
    if not target_id or "path" in data:
        return jsonify({"message": "Invalid permission target"}), 400
    if PRIVILEGED_HELPER_ENABLED:
        if target_id != "steam-library":
            return jsonify({"message": "Unknown permission target"}), 400
        try:
            return jsonify(privileged_call("fix-permissions", {} , timeout=180))
        except PrivilegedError as error:
            return jsonify({"message": str(error)}), 400
    try:
        path = permission_target_path(target_id)
        set_group_perms(path)
        return jsonify({"message": f"Permissions fixed for {path}"})
    except (ValueError, FileNotFoundError) as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route("/steam_cache_status", methods=["GET"])
def steam_cache_status():
    user = request.args.get("user", "")
    if not user:
        return jsonify({"message": "Missing user"}), 400
    if PRIVILEGED_HELPER_ENABLED:
        try:
            return jsonify(privileged_call(
                "set-steam-cache", {"user": user}, timeout=180,
            ))
        except PrivilegedError as error:
            return jsonify({"message": str(error)}), 400

    try:
        steamapps = steamapps_dir(user)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
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
    if not require_local_operation(request, "steam.cache"):
        return jsonify({"message": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    user = data.get("user")
    if not user:
        return jsonify({"message": "Missing user"}), 400

    try:
        steamapps = steamapps_dir(user)
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    if not steamapps:
        return jsonify({"message": "Steam knihovna nenalezena"}), 404

    try:
        download_path = os.path.join(steamapps, "downloading")
        shared_base = ensure_managed_directory(GAMES_ROOT, "steam-cache")
        shared_downloading = os.path.join(shared_base, "downloading")
        if os.path.islink(shared_downloading):
            return jsonify({"message": "Sdílená Steam cache nesmí být symlink"}), 400
        try:
            gid = grp.getgrnam(GROUP_NAME).gr_gid
            os.chown(shared_base, -1, gid)
            os.chmod(shared_base, 0o775)
        except Exception:
            pass

        if os.path.islink(download_path):
            target = os.path.realpath(download_path)
            if target == os.path.realpath(shared_downloading):
                return jsonify({"message": "Steam cache už je sdílená", "status": "shared"})
            os.unlink(download_path)
        elif os.path.isdir(download_path):
            if os.path.lexists(shared_downloading):
                return jsonify({"message": f"Cílový {shared_downloading} už existuje, nejprve jej odstraň nebo přesuň"}), 409
            shutil.move(download_path, shared_downloading)
        elif os.path.lexists(download_path):
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
    security_config = load_security_config(servers)
    if servers:
        with ThreadPoolExecutor(max_workers=min(8, len(servers))) as executor:
            statuses = list(executor.map(
                lambda server: game_server_status(server, security_config), servers,
            ))
    else:
        statuses = []
    gate_connections = gate_connections_by_server(servers)
    for status in statuses:
        gate_connection = gate_connections.get(status.get("id"))
        if gate_connection and status.get("kind") == "minecraft":
            status["gate_connection"] = gate_connection
        deletion = OPERATIONS.snapshot(f"minecraft-delete-{status.get('id', '')}")
        if deletion and deletion.get("running"):
            status["operation"] = deletion
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
        "operation_policies": dict(security_config["global"]),
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.route("/security/local-policies", methods=["GET"])
def local_operation_policies():
    """Expose this workstation's policy modes to its local GUI."""
    return jsonify({
        "operation_policies": dict(load_security_config()["global"]),
    })


@app.route("/notes", methods=["GET"])
def notes_catalog():
    try:
        return jsonify({"notes": list_all_notes(NOTES_DB_PATH)})
    except (OSError, sqlite3.Error) as error:
        return jsonify({"message": f"Databázi poznámek nelze použít: {error}"}), 500


@app.route("/games/installed", methods=["GET"])
def installed_games_catalog():
    """Scan this machine; this endpoint is intentionally local-only."""
    force = request.args.get("force") == "1"
    now = time.monotonic()
    with GAME_INVENTORY_LOCK:
        if force or now - GAME_INVENTORY_CACHE["updated"] > GAME_INVENTORY_CACHE_SECONDS:
            GAME_INVENTORY_CACHE["games"] = scan_installed_games()
            GAME_INVENTORY_CACHE["updated"] = now
        games = [dict(game) for game in GAME_INVENTORY_CACHE["games"]]
    return jsonify({
        "games": games,
        "minimum_game_size_bytes": 1024 * 1024 * 1024,
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.route("/notes/<target_type>/<target_id>", methods=["GET", "PUT"])
def target_notes(target_type, target_id):
    try:
        if request.method == "GET":
            return jsonify({
                "target_type": target_type,
                "target_id": target_id,
                "notes": list_notes(NOTES_DB_PATH, target_type, target_id),
            })
        if not require_local_operation(request, "knowledge.manage"):
            return jsonify({"message": "Unauthorized"}), 403
        notes = replace_notes(
            NOTES_DB_PATH, target_type, target_id,
            (request.get_json(silent=True) or {}).get("notes"),
        )
        return jsonify({
            "message": "Poznámky byly uloženy",
            "target_type": target_type,
            "target_id": target_id,
            "notes": notes,
        })
    except NotesError as error:
        return jsonify({"message": str(error)}), 400
    except (OSError, sqlite3.Error) as error:
        return jsonify({"message": f"Databázi poznámek nelze použít: {error}"}), 500


@app.route("/health", methods=["GET"])
def health():
    """Fast loopback probe used while establishing a managed SSH tunnel."""
    return jsonify({"status": "ok", "version": __version__})


@app.route("/launchers/status", methods=["GET"])
def launchers_status():
    launchers = launcher_statuses()
    if PRIVILEGED_HELPER_ENABLED:
        for launcher in launchers:
            if launcher.get("id") == "heroic":
                launcher["update_supported"] = True
                launcher["update_mode"] = "client-packagekit"
    return jsonify({
        "launchers": launchers,
        "update_policy": operation_policy("launcher.update"),
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.route("/system/resources", methods=["GET"])
def system_resources():
    """Return read-only memory pressure for the machine running this backend."""
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    memory_used = max(0, int(memory.total) - int(memory.available))
    return jsonify({
        "memory": {
            "total_bytes": int(memory.total),
            "used_bytes": memory_used,
            "available_bytes": int(memory.available),
            "percent": round(float(memory.percent), 1),
        },
        "swap": {
            "total_bytes": int(swap.total),
            "used_bytes": int(swap.used),
            "free_bytes": int(swap.free),
            "percent": round(float(swap.percent), 1),
        },
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


@app.route("/launchers/<launcher_id>/update", methods=["POST"])
def launcher_update(launcher_id):
    if not require_local_operation(request, "launcher.update"):
        return jsonify({"message": "Unauthorized"}), 403
    if PRIVILEGED_HELPER_ENABLED:
        if launcher_id != "heroic":
            return jsonify({"message": "Tento launcher nelze aktualizovat"}), 400
        return jsonify({"install_via_client": True, "launcher_id": launcher_id})
    try:
        return jsonify(update_launcher(launcher_id))
    except LauncherError as error:
        return jsonify({"message": str(error)}), 409


@app.route("/security/policies", methods=["GET", "PUT"])
def security_policies():
    # The policy controlling security.manage is intentionally not configurable.
    if not require_local_pam_session(request):
        return jsonify({"message": "Unauthorized"}), 403
    servers = load_game_servers()
    if request.method == "GET":
        return jsonify(public_security_payload(load_security_config(servers), servers))
    raw_config = (request.json or {}).get("policies")
    try:
        config = validate_security_update(raw_config, servers)
        save_security_config(config)
    except (OSError, TypeError, ValueError) as error:
        return jsonify({"message": str(error)}), 400
    return jsonify({
        "message": "Bezpečnostní zásady byly uloženy",
        **public_security_payload(config, servers),
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
    if not require_local_operation(request, "gate.config"):
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
    if not require_local_operation(request, "gate.routes"):
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
    if not require_local_operation(request, "gate.deploy"):
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
    if not require_local_operation(request, "gate.lifecycle"):
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
    adapter = str(server.get("adapter", "")).strip().lower()
    if adapter not in ("", "satisfactory"):
        raise ValueError("Invalid game adapter")
    endpoints = normalize_endpoints(server.get("endpoints"))
    if endpoints:
        item["endpoints"] = endpoints
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
        if not adapter and (
            server_id.lower() == "satisfactory" or unit.lower() == "satisfactory.service"
        ):
            adapter = "satisfactory"
    else:
        container = str(runtime.get("container_name") or server.get("container") or "").strip()
        if not CONTAINER_NAME_RE.fullmatch(container):
            raise ValueError("Invalid Podman container name")
        item["runtime"] = {"container_name": container}
        management_mode = str(server.get("management_mode", "adopted")).strip().lower()
        if management_mode not in ("adopted", "managed"):
            raise ValueError("Invalid Podman management mode")
        item["management_mode"] = management_mode

    if adapter:
        if adapter == "satisfactory" and backend_name != "systemd":
            raise ValueError("Satisfactory adapter currently requires systemd")
        item["adapter"] = adapter

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
    if not require_local_operation(request, "server.registry"):
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
    occupied_endpoints = {}
    for item in validated:
        candidates = list(item.get("endpoints", []))
        if item.get("adapter") == "satisfactory":
            try:
                candidates.extend(discover_satisfactory_endpoints(
                    item.get("runtime", {}).get("unit", item.get("service", ""))
                ))
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
                pass
        connection = item.get("connection")
        if isinstance(connection, dict) and connection.get("direct_port") is not None:
            candidates.append({
                "name": "Přímé připojení", "protocol": "tcp",
                "port": connection["direct_port"],
            })
        for endpoint in candidates:
            key = (endpoint["protocol"], endpoint["port"])
            owner = occupied_endpoints.get(key)
            if owner and owner != item["id"]:
                return jsonify({
                    "message": (
                        f"Síťový endpoint {key[0]}:{key[1]} už používá server {owner}"
                    ),
                }), 400
            occupied_endpoints[key] = item["id"]
    save_game_servers(validated)
    return jsonify({"servers": validated})


@app.route("/servers/backups", methods=["GET"])
def servers_backups():
    if not require_local_operation(request, "backup.catalog"):
        return jsonify({"message": "Unauthorized"}), 403
    source_id = request.args.get("source_id", "")
    try:
        backups = list_backups(BACKUP_ROOT, source_id)
    except (InstallError, OSError, ValueError) as error:
        return jsonify({"message": str(error)}), 400
    return jsonify({"source_id": source_id, "backups": backups})


def no_store_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/minecraft/modpacks/status", methods=["GET"])
def minecraft_modpack_catalog_status():
    provider = curseforge_catalog_provider()
    return no_store_json({
        "provider": "curseforge",
        "configured": provider.configured,
        "server_pack_install": True,
        "client_install": False,
        "cached": False,
    })


@app.route("/minecraft/modpacks/search", methods=["GET"])
def minecraft_modpack_search():
    provider = curseforge_catalog_provider()
    try:
        result = provider.search(
            query=request.args.get("query", ""),
            version=request.args.get("version", ""),
            loader=request.args.get("loader", "any"),
            sort=request.args.get("sort", "popularity"),
            index=request.args.get("index", 0),
            page_size=request.args.get("page_size", 20),
        )
    except CatalogNotConfigured as error:
        return no_store_json({"message": str(error)}, 503)
    except CatalogValidationError as error:
        return no_store_json({"message": str(error)}, 400)
    except CatalogUpstreamError as error:
        return no_store_json({"message": str(error)}, 502)
    return no_store_json(result)


@app.route("/minecraft/modpacks/<int:project_id>/files", methods=["GET"])
def minecraft_modpack_files(project_id):
    provider = curseforge_catalog_provider()
    try:
        result = provider.files(
            project_id,
            version=request.args.get("version", ""),
            loader=request.args.get("loader", "any"),
            index=request.args.get("index", 0),
            page_size=request.args.get("page_size", 50),
        )
    except CatalogNotConfigured as error:
        return no_store_json({"message": str(error)}, 503)
    except CatalogValidationError as error:
        return no_store_json({"message": str(error)}, 400)
    except CatalogUpstreamError as error:
        return no_store_json({"message": str(error)}, 502)
    return no_store_json(result)


@app.route("/servers/minecraft/install", methods=["GET", "POST"])
def minecraft_install():
    if not require_local_operation(request, "minecraft.install"):
        return jsonify({"message": "Unauthorized"}), 403
    if request.method == "GET":
        try:
            port = next_available_minecraft_port()
        except (InstallError, OSError, TypeError, ValueError) as error:
            return jsonify({"message": str(error)}), 409
        return jsonify({
            "suggested_port": port,
            "range": {
                "start": MINECRAFT_INSTALL_PORT_START,
                "end": MINECRAFT_INSTALL_PORT_END,
            },
        })
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
            elif config.get("curseforge"):
                OPERATIONS.update(
                    operation_id, phase="server-pack",
                    message="Ověřuji a stahuji CurseForge server pack", progress=10,
                )
                reference = config["curseforge"]
                catalog_provider = curseforge_catalog_provider()
                descriptor = catalog_provider.resolve_server_pack(
                    reference["project_id"], reference["file_id"],
                )

                def recipe_progress(index, total, downloaded, total_bytes):
                    progress = 12 + int(26 * downloaded / max(1, total_bytes))
                    OPERATIONS.update(
                        operation_id, phase="server-pack-recipe",
                        message=f"Stahuji serverové mody z CurseForge ({index}/{total})",
                        progress=min(38, progress),
                    )

                pack_result = install_curseforge_server_pack(
                    descriptor,
                    data_root=PODMAN_DATA_ROOT,
                    target_id=config["id"],
                    owner_user=PODMAN_USER,
                    api_key=catalog_provider.api_key,
                    recipe_resolver=catalog_provider.resolve_recipe_files,
                    recipe_progress=recipe_progress,
                )
                data_directory = pack_result["data_directory"]
                if not config["loader_version"]:
                    config["loader_version"] = (
                        pack_result.get("loader_version", "")
                        or detect_server_pack_loader_version(
                            data_directory, config["loader"], config["version"],
                        )
                    )
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
                environment=container_environment(config, owner_user=PODMAN_USER),
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
                userns="keep-id",
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
                "endpoints": [{
                    "name": "Minecraft", "protocol": "tcp",
                    "port": config["port"],
                }],
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
    except (
        CatalogNotConfigured, CatalogUpstreamError, CatalogValidationError,
        InstallError, KeyError, OSError, RuntimeError, ValueError,
    ) as error:
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


def _managed_server_deletion_paths(server, *, delete_data, delete_backups):
    server_id = str(server.get("id", ""))
    server_root = os.path.join(PODMAN_DATA_ROOT, server_id)
    expected_data = os.path.join(server_root, "data")
    data = server.get("data") if isinstance(server.get("data"), dict) else {}
    configured_data = os.path.realpath(str(data.get("directory", "")))
    if (
        not server_id
        or server_root == PODMAN_DATA_ROOT
        or os.path.dirname(server_root) != PODMAN_DATA_ROOT
        or configured_data != expected_data
        or os.path.islink(server_root)
    ):
        raise ValueError("Datový adresář serveru neodpovídá bezpečnému spravovanému umístění")
    backup_root = os.path.join(BACKUP_ROOT, server_id)
    if (
        backup_root == BACKUP_ROOT
        or os.path.dirname(backup_root) != BACKUP_ROOT
        or os.path.islink(backup_root)
    ):
        raise ValueError("Adresář záloh serveru neodpovídá bezpečnému spravovanému umístění")
    return {
        "server_root": server_root if delete_data else None,
        "backup_root": backup_root if delete_backups else None,
    }


def _publish_gate_config(config, gate_backend):
    layout = write_gate_layout(config, GATE_DATA_DIRECTORY)
    chown_gate_layout(layout, PODMAN_USER)
    save_gate_config(config)
    gate_workload = {
        "backend": "podman",
        "runtime": {"container_name": config["container_name"]},
    }
    deployed = gate_backend.container_exists(gate_workload)
    if deployed:
        result = gate_backend.restart(gate_workload)
        if result.returncode != 0:
            raise RuntimeError(
                result.error or result.output or "Gate Lite se nepodařilo restartovat"
            )
    return deployed


@app.route("/servers/minecraft/delete", methods=["DELETE"])
def minecraft_delete():
    if not require_local_operation(request, "minecraft.delete"):
        return jsonify({"message": "Unauthorized"}), 403
    payload = request.json or {}
    server_id = str(payload.get("id", "")).strip()
    if str(payload.get("confirmation", "")) != server_id:
        return jsonify({"message": "Potvrzení neodpovídá ID serveru"}), 400
    server = find_game_server(server_id)
    if not server:
        return jsonify({"message": "Server nebyl nalezen"}), 404
    if not (
        server.get("kind") == "minecraft"
        and server.get("backend") == "podman"
        and server.get("management_mode") == "managed"
    ):
        return jsonify({
            "message": "Úplné odstranění je povolené jen pro platformou spravovaný Podman Minecraft",
        }), 400
    runtime = server.get("runtime") if isinstance(server.get("runtime"), dict) else {}
    if runtime.get("container_name") != server_id:
        return jsonify({"message": "Jméno spravovaného containeru neodpovídá ID serveru"}), 400
    try:
        paths = _managed_server_deletion_paths(
            server,
            delete_data=payload.get("delete_data") is True,
            delete_backups=payload.get("delete_backups") is True,
        )
    except ValueError as error:
        return jsonify({"message": str(error)}), 400

    servers = load_game_servers()
    remaining_servers = [item for item in servers if item.get("id") != server_id]
    old_gate_config = None
    new_gate_config = None
    removed_routes = []
    if os.path.isfile(GATE_CONFIG_PATH):
        try:
            old_gate_config = load_gate_config()
            abstract_routes = abstract_gate_routes(
                old_gate_config, minecraft_route_targets(servers),
            )
            removed_routes = [
                route for route in abstract_routes if route.get("target_id") == server_id
            ]
            if any(route.get("host") == "*" for route in removed_routes):
                return jsonify({
                    "message": (
                        "Server je cílem výchozí Gate trasy *. "
                        "Nejdřív ji ve směrování přesuň na jiný server."
                    ),
                }), 409
            if removed_routes:
                removed_hosts = {route["host"] for route in removed_routes}
                remaining_routes = [
                    route for route in old_gate_config["routes"]
                    if route["host"] not in removed_hosts
                ]
                if not remaining_routes:
                    return jsonify({
                        "message": "Smazáním serveru by Gate Lite zůstal bez jediné trasy",
                    }), 409
                new_gate_config = normalize_gate_config({
                    **old_gate_config, "routes": remaining_routes,
                })
        except (GateConfigError, KeyError, OSError, TypeError, ValueError) as error:
            return jsonify({"message": f"Gate trasy nelze bezpečně ověřit: {error}"}), 500

    workload_backend = None
    gate_backend = None
    gate_changed = False
    runtime_absent = False
    operation_id = f"minecraft-delete-{server_id}"
    try:
        OPERATIONS.begin(
            operation_id,
            kind="minecraft-delete",
            target_id=server_id,
            message=f"Připravuji odstranění serveru {server.get('name', server_id)}",
        )
    except OperationAlreadyRunning:
        return jsonify({"message": "Odstranění tohoto serveru už probíhá"}), 409
    try:
        OPERATIONS.update(
            operation_id, phase="routing",
            message="Odpojuji server od Gate Lite", progress=15,
        )
        workload_backend = backend_for(
            server,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        gate_backend = backend_for(
            {
                "backend": "podman",
                "runtime": {
                    "container_name": (
                        old_gate_config or default_gate_config()
                    )["container_name"],
                },
            },
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        with workload_lock(server_id):
            if new_gate_config is not None:
                gate_changed = True
                _publish_gate_config(new_gate_config, gate_backend)
            OPERATIONS.update(
                operation_id, phase="container",
                message="Odstraňuji Podman container", progress=40,
            )
            if workload_backend.container_exists(server):
                result = workload_backend.remove_container(server, force=True)
                if result.returncode != 0:
                    raise RuntimeError(
                        result.error or result.output or "Odstranění containeru selhalo"
                    )
            runtime_absent = True
            OPERATIONS.update(
                operation_id, phase="data",
                message="Mažu vybraná persistentní data", progress=65,
            )
            for path in (paths["server_root"], paths["backup_root"]):
                if path and os.path.lexists(path):
                    shutil.rmtree(path)
            OPERATIONS.update(
                operation_id, phase="registry",
                message="Uklízím registr serverů a oprávnění", progress=88,
            )
            current_security = load_security_config(servers)
            save_security_config(normalize_security_config(
                current_security, remaining_servers,
            ))
            save_game_servers(remaining_servers)
            try:
                delete_target_notes(NOTES_DB_PATH, "server", server_id)
            except (NotesError, OSError, sqlite3.Error):
                pass
            with MINECRAFT_STATUS_LOCK:
                for key in list(MINECRAFT_STATUS_CACHE):
                    if key and key[0] == server_id:
                        MINECRAFT_STATUS_CACHE.pop(key, None)
                        MINECRAFT_STATUS_INFLIGHT.discard(key)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        if (
            gate_changed and not runtime_absent
            and old_gate_config is not None and gate_backend is not None
        ):
            try:
                _publish_gate_config(old_gate_config, gate_backend)
            except (KeyError, OSError, RuntimeError, ValueError):
                pass
        OPERATIONS.fail(operation_id, message=f"Odstranění selhalo: {error}")
        return jsonify({"message": f"Odstranění serveru selhalo: {error}"}), 500

    OPERATIONS.finish(
        operation_id, message=f"Server {server.get('name', server_id)} byl odstraněn",
    )
    return jsonify({
        "message": f"Server {server.get('name', server_id)} byl odstraněn",
        "id": server_id,
        "container_deleted": True,
        "data_deleted": paths["server_root"] is not None,
        "backups_deleted": paths["backup_root"] is not None,
        "gate_routes_deleted": [route["host"] for route in removed_routes],
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
        # A new, vanilla, or not-yet-started server may legitimately have no
        # mods directory.  Return an empty inventory so a remote client can
        # still compare its local mods without requiring a management tunnel.
        inventory = scan_mod_directory(mods_dir, missing_ok=True)
    except FileNotFoundError as e:
        return jsonify({"message": str(e)}), 404
    except PermissionError:
        return jsonify({"message": f"Nelze číst {mods_dir}"}), 403
    except Exception as e:
        return jsonify({"message": f"Inventář modů selhal: {e}"}), 500
    inventory["updated_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return jsonify(inventory)


@app.route("/servers/minecraft/properties", methods=["GET", "PUT"])
def minecraft_properties():
    if not require_local_operation(request, "minecraft.properties"):
        return jsonify({"message": "Unauthorized"}), 403
    server = find_game_server(request.args.get("server_id") or (request.json or {}).get("server_id", ""))
    if not server or server.get("kind") != "minecraft":
        return jsonify({"message": "Minecraft server not found"}), 404
    data = server.get("data") if isinstance(server.get("data"), dict) else {}
    data_directory = data.get("directory")
    try:
        if request.method == "GET":
            result = read_minecraft_properties(data_directory)
            return jsonify({
                "server_id": server["id"],
                "settings": result["settings"],
                "effective": {
                    "server-port": result["effective"].get("server-port", "25565"),
                    "enable-rcon": result["effective"].get("enable-rcon", "false"),
                },
            })
        result = write_minecraft_properties(data_directory, (request.json or {}).get("settings"))
    except MinecraftPropertiesError as error:
        return jsonify({"message": str(error)}), 400
    return jsonify({
        "message": "Nastavení Minecraft serveru bylo bezpečně uloženo",
        "server_id": server["id"],
        "settings": result["settings"],
        "changed": result["changed"],
    })


def execute_registered_minecraft_rcon(server, command, data_directory):
    """Execute a prevalidated command without exposing credentials or a shell."""
    if server.get("backend", "systemd") == "podman":
        adapter = backend_for(
            server,
            podman_user=PODMAN_USER,
            podman_socket_path=PODMAN_SOCKET_PATH,
        )
        result = adapter.minecraft_rcon(server, command)
        if result.returncode != 0:
            raise ValueError(result.error or result.output or "RCON příkaz selhal")
        return result.output
    rcon = configured_rcon(data_directory)
    if not rcon:
        raise ValueError("RCON není na tomto serveru nakonfigurován")
    return execute_rcon_command(
        "127.0.0.1", rcon["port"], rcon["password"], command, timeout=5.0,
    )


def requested_minecraft_server(payload):
    server = find_game_server(request.args.get("server_id") or payload.get("server_id", ""))
    return server if server and server.get("kind") == "minecraft" else None


@app.route("/servers/minecraft/operators", methods=["GET", "POST"])
def minecraft_operators():
    if not require_local_operation(request, "minecraft.operators"):
        return jsonify({"message": "Unauthorized"}), 403
    raw_payload = request.get_json(silent=True)
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    server = requested_minecraft_server(payload)
    if not server:
        return jsonify({"message": "Minecraft server not found"}), 404
    data = server.get("data") if isinstance(server.get("data"), dict) else {}
    data_directory = data.get("directory")
    try:
        if request.method == "GET":
            return jsonify({
                "server_id": server["id"],
                "operators": read_operators(data_directory),
            })

        command = operator_command(payload.get("action"), payload.get("player"))
        with workload_lock(server["id"]):
            response = execute_registered_minecraft_rcon(
                server, command, data_directory,
            )
        return jsonify({
            "server_id": server["id"],
            "message": response or "RCON příkaz byl proveden",
            "operators": read_operators(data_directory),
        })
    except (MinecraftOperatorsError, OSError, PermissionError, ValueError) as error:
        return jsonify({"message": str(error)}), 400


@app.route("/servers/minecraft/whitelist", methods=["GET", "POST"])
def minecraft_whitelist():
    if not require_local_operation(request, "minecraft.whitelist"):
        return jsonify({"message": "Unauthorized"}), 403
    raw_payload = request.get_json(silent=True)
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    server = requested_minecraft_server(payload)
    if not server:
        return jsonify({"message": "Minecraft server not found"}), 404
    data = server.get("data") if isinstance(server.get("data"), dict) else {}
    data_directory = data.get("directory")
    try:
        settings = read_minecraft_properties(data_directory)["settings"]
        if request.method == "GET":
            return jsonify({
                "server_id": server["id"],
                "enabled": settings.get("white-list", "false") == "true",
                "players": read_whitelist(data_directory),
            })
        action = str(payload.get("action", "")).strip().lower()
        command = whitelist_command(action, payload.get("player"))
        with workload_lock(server["id"]):
            response = execute_registered_minecraft_rcon(
                server, command, data_directory,
            )
        if action == "on":
            enabled = True
        elif action == "off":
            enabled = False
        else:
            enabled = read_minecraft_properties(data_directory)["settings"].get(
                "white-list", "false"
            ) == "true"
        return jsonify({
            "server_id": server["id"],
            "message": response or "Whitelist příkaz byl proveden",
            "enabled": enabled,
            "players": read_whitelist(data_directory),
        })
    except (
        MinecraftPropertiesError, MinecraftWhitelistError, OSError,
        PermissionError, ValueError,
    ) as error:
        return jsonify({"message": str(error)}), 400


@app.route("/servers/logs", methods=["GET"])
def server_logs():
    if not require_local_operation(request, "server.logs"):
        return jsonify({"message": "Unauthorized"}), 403
    server = find_game_server(request.args.get("server_id", ""))
    if not server:
        return jsonify({"message": "Server not found"}), 404
    try:
        tail = normalize_log_tail(request.args.get("tail", 100))
        data = server.get("data", {})
        minecraft_log = None
        if server.get("kind") == "minecraft" and isinstance(data, dict):
            minecraft_log = read_minecraft_latest_log(data.get("directory", ""), tail)
        if minecraft_log is not None:
            return jsonify({
                "server_id": server["id"],
                "backend": server.get("backend", "systemd"),
                "tail": tail,
                "output": minecraft_log["output"],
                "truncated": minecraft_log["truncated"],
                "source": minecraft_log["source"],
                "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            })
        backend = backend_for(
            server, podman_user=PODMAN_USER, podman_socket_path=PODMAN_SOCKET_PATH,
        )
        with workload_lock(server["id"]):
            result = backend.logs(server, tail)
        output, truncated = bounded_log_output(result)
    except (AttributeError, KeyError, ValueError, WorkloadLogError) as error:
        return jsonify({"message": str(error)}), 400
    if result.returncode != 0:
        return jsonify({"message": output or "Načtení logu selhalo"}), 500
    return jsonify({
        "server_id": server["id"],
        "backend": server.get("backend", "systemd"),
        "tail": tail,
        "output": output,
        "truncated": truncated,
        "source": f"{server.get('backend', 'systemd')}-runtime",
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    })


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
    if not require_local_operation(request, "dnsmasq.stop"):
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


@app.route("/dns/status", methods=["GET"])
def managed_dns_status():
    config = load_dns_config()
    runtime = runtime_dns_config(config, load_gate_config())
    service_name = PIHOLE_SERVICE_NAME if config["provider"] == "pihole_local" else DNS_SERVICE_NAME
    rc, service_status, error = systemctl_is_active(service_name)
    return jsonify({
        "version": __version__,
        "dns": {
            **config,
            "records": runtime["records"],
            "records_count": len(runtime["records"]),
            "service_status": service_status,
            "active": service_status == "active",
            "available": rc != 127,
            "error": error,
            "provider_catalog": dns_provider_catalog(),
        },
    })


@app.route("/dns/config", methods=["GET", "PUT"])
def managed_dns_config():
    if not require_local_operation(request, "dns.config"):
        return jsonify({"message": "Unauthorized"}), 403
    if request.method == "GET":
        return jsonify({
            "dns": load_dns_config(), "provider_catalog": dns_provider_catalog(),
        })
    try:
        config = save_dns_config((request.json or {}).get("dns"))
        if config["provider"] == "builtin":
            rc, output, error = systemctl_enable_now(DNS_SERVICE_NAME)
        else:
            rc, output, error = systemctl_disable_now(DNS_SERVICE_NAME)
        if rc != 0:
            return jsonify({
                "message": "DNS konfigurace byla uložena, ale službu se nepodařilo přepnout",
                "dns": config,
                "service_error": error or output,
            }), 500
    except (DnsConfigError, PiholeAdapterError, OSError, TypeError, ValueError) as error:
        return jsonify({"message": str(error)}), 400
    return jsonify({"message": "DNS konfigurace byla uložena", "dns": config})

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
@app.route("/timekpr/logout", methods=["POST"])
def timekpr_logout():
    if request.remote_addr not in ("127.0.0.1", "::1") or not revoke_token(request):
        return jsonify({"message": "Unauthorized"}), 403
    return jsonify({"message": "PAM relace byla okamžitě uzamčena"})


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
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    username = data.get("username", "")
    password = data.get("password", "")
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not username
        or not password
        or len(username) > 256
    ):
        return jsonify({"message": "Missing credentials"}), 400
    source = request.remote_addr or "unknown"
    # Serializing this low-volume endpoint prevents a parallel burst from
    # passing the limiter before the first failed PAM result is recorded.
    with PAM_AUTH_ATTEMPT_LOCK:
        retry_after = pam_auth_retry_after(source, username)
        if retry_after:
            app.logger.warning(
                "PAM authentication throttled for user=%r source=%s retry_after=%ss",
                username, source, retry_after,
            )
            response = jsonify({"message": "Příliš mnoho pokusů; zkuste to později"})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        try:
            if PRIVILEGED_HELPER_ENABLED:
                result = privileged_call(
                    "pam-auth", {"username": username, "password": password},
                    timeout=30,
                )
                authenticated = bool(result.get("authenticated"))
            else:
                try:
                    import pam
                except ImportError:
                    app.logger.error("PAM authentication module is unavailable")
                    return jsonify({"message": "Ověřovací služba není dostupná"}), 503
                authenticated = bool(pam.pam().authenticate(username, password))
        except Exception:
            app.logger.exception(
                "PAM authentication service failed for user=%r source=%s",
                username, source,
            )
            return jsonify({"message": "Ověřovací služba není dostupná"}), 503
        if not authenticated or not user_in_wheel(username):
            delay = record_pam_auth_failure(source, username)
            app.logger.warning(
                "PAM authentication failed for user=%r source=%s backoff=%ss",
                username, source, delay,
            )
            return jsonify({"message": "Neplatné přihlášení"}), 401
        clear_pam_auth_failures(source, username)
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
        "managed_users": interactive_usernames(),
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
    initialize_notes_database(NOTES_DB_PATH)
    app.run(
        host=os.getenv("GAME_MOVER_BIND_HOST", "127.0.0.1"),
        port=int(os.getenv("GAME_MOVER_PORT", "5000")),
        debug=False,
    )
