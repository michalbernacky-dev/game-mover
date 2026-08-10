#!/usr/bin/env python3
"""Validated Velocity proxy configuration shared by API and deployment code."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import pwd


VELOCITY_SERVER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VELOCITY_NETWORK_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,31}$")
VELOCITY_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
VELOCITY_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)
VELOCITY_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-SNAPSHOT)?$")


class VelocityConfigError(ValueError):
    """Velocity configuration is invalid or unsafe."""


def default_velocity_config() -> dict:
    """Return a safe staging configuration that does not replace production yet."""
    return {
        "image": "docker.io/itzg/mc-proxy:java21",
        "velocity_version": "3.5.1",
        "velocity_build_id": "615",
        "container_name": "velocity",
        "network": "game-platform",
        "listen": {"host": "0.0.0.0", "port": 25580},
        "online_mode": True,
        "forwarding_mode": "none",
        "announce_forge": True,
        "ping_passthrough": "mods",
        "read_timeout_ms": 120000,
        "backends": [
            {
                "id": "forge",
                "host": "host.containers.internal",
                "port": 25565,
            }
        ],
        "try": ["forge"],
        "forced_hosts": {},
        "plugins": [
            {
                "provider": "modrinth",
                "project": "ambassador",
                "minecraft_version": "1.20.1",
            }
        ],
    }


def _port(value, label):
    if isinstance(value, bool):
        raise VelocityConfigError(f"{label} musí být platný TCP port")
    try:
        value = int(value)
    except (TypeError, ValueError) as error:
        raise VelocityConfigError(f"{label} musí být platný TCP port") from error
    if not 1 <= value <= 65535:
        raise VelocityConfigError(f"{label} musí být platný TCP port")
    return value


def _host(value, label, *, allow_wildcard=False):
    value = str(value or "").strip()
    if allow_wildcard and value in ("0.0.0.0", "::"):
        return value
    if not value or not VELOCITY_HOST_RE.fullmatch(value):
        raise VelocityConfigError(f"{label} není platný hostitel")
    try:
        ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        if value.startswith("-") or value.endswith("-") or ".." in value:
            raise VelocityConfigError(f"{label} není platný hostitel")
    return value


def normalize_velocity_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise VelocityConfigError("Konfigurace Velocity musí být objekt")
    defaults = default_velocity_config()
    image = str(raw.get("image", defaults["image"])).strip()
    container_name = str(raw.get("container_name", defaults["container_name"])).strip()
    if not VELOCITY_IMAGE_RE.fullmatch(image):
        raise VelocityConfigError("Neplatná reference image Velocity")
    velocity_version = str(
        raw.get("velocity_version", defaults["velocity_version"])
    ).strip()
    if not VELOCITY_VERSION_RE.fullmatch(velocity_version):
        raise VelocityConfigError("Neplatná verze Velocity")
    velocity_build_id = str(
        raw.get("velocity_build_id", defaults["velocity_build_id"])
    ).strip()
    if not velocity_build_id.isdigit() or int(velocity_build_id) < 1:
        raise VelocityConfigError("Neplatný build Velocity")
    if not VELOCITY_SERVER_ID_RE.fullmatch(container_name):
        raise VelocityConfigError("Neplatné jméno containeru Velocity")
    network = str(raw.get("network", defaults["network"])).strip().lower()
    if not VELOCITY_NETWORK_RE.fullmatch(network):
        raise VelocityConfigError("Neplatné jméno Podman sítě Velocity")

    listen = raw.get("listen") if isinstance(raw.get("listen"), dict) else {}
    forwarding_mode = str(
        raw.get("forwarding_mode", defaults["forwarding_mode"])
    ).strip().lower()
    if forwarding_mode not in ("none", "modern"):
        raise VelocityConfigError("Velocity podporuje pouze none nebo modern forwarding")
    ping_passthrough = str(
        raw.get("ping_passthrough", defaults["ping_passthrough"])
    ).strip().lower()
    if ping_passthrough not in ("disabled", "mods", "description", "all"):
        raise VelocityConfigError("Neplatný režim předávání server-list pingu")

    raw_backends = raw.get("backends", defaults["backends"])
    if not isinstance(raw_backends, list) or not raw_backends:
        raise VelocityConfigError("Velocity potřebuje alespoň jeden backend")
    backends = []
    backend_ids = set()
    for raw_backend in raw_backends:
        if not isinstance(raw_backend, dict):
            raise VelocityConfigError("Neplatný Velocity backend")
        backend_id = str(raw_backend.get("id", "")).strip().lower()
        if not VELOCITY_SERVER_ID_RE.fullmatch(backend_id) or backend_id in backend_ids:
            raise VelocityConfigError("Neplatné nebo duplicitní ID Velocity backendu")
        backend_ids.add(backend_id)
        backends.append({
            "id": backend_id,
            "host": _host(raw_backend.get("host"), "Adresa backendu"),
            "port": _port(raw_backend.get("port"), "Port backendu"),
        })

    try_order = raw.get("try", defaults["try"])
    if (
        not isinstance(try_order, list)
        or not try_order
        or any(str(item) not in backend_ids for item in try_order)
    ):
        raise VelocityConfigError("Pořadí try odkazuje na neznámý backend")
    try_order = [str(item) for item in try_order]

    raw_forced_hosts = raw.get("forced_hosts", defaults["forced_hosts"])
    if not isinstance(raw_forced_hosts, dict):
        raise VelocityConfigError("forced_hosts musí být objekt")
    forced_hosts = {}
    for hostname, routes in raw_forced_hosts.items():
        hostname = _host(hostname, "Forced hostname")
        if (
            not isinstance(routes, list)
            or not routes
            or any(str(item) not in backend_ids for item in routes)
        ):
            raise VelocityConfigError("Forced hostname odkazuje na neznámý backend")
        forced_hosts[hostname.lower()] = [str(item) for item in routes]

    plugins = raw.get("plugins", defaults["plugins"])
    if not isinstance(plugins, list):
        raise VelocityConfigError("Seznam Velocity pluginů není platný")
    normalized_plugins = []
    for plugin in plugins:
        if not isinstance(plugin, dict):
            raise VelocityConfigError("Neplatný Velocity plugin")
        provider = str(plugin.get("provider", "")).strip().lower()
        project = str(plugin.get("project", "")).strip().lower()
        version = str(plugin.get("minecraft_version", "")).strip()
        if provider != "modrinth" or not VELOCITY_SERVER_ID_RE.fullmatch(project) or not version:
            raise VelocityConfigError("Neplatná reference Velocity pluginu")
        normalized_plugins.append({
            "provider": provider, "project": project, "minecraft_version": version,
        })

    read_timeout_ms = int(raw.get("read_timeout_ms", defaults["read_timeout_ms"]))
    if not 5000 <= read_timeout_ms <= 600000:
        raise VelocityConfigError("Velocity read timeout musí být 5 až 600 sekund")
    return {
        "image": image,
        "velocity_version": velocity_version,
        "velocity_build_id": velocity_build_id,
        "container_name": container_name,
        "network": network,
        "listen": {
            "host": _host(
                listen.get("host", defaults["listen"]["host"]),
                "Poslechová adresa", allow_wildcard=True,
            ),
            "port": _port(
                listen.get("port", defaults["listen"]["port"]), "Poslechový port",
            ),
        },
        "online_mode": bool(raw.get("online_mode", defaults["online_mode"])),
        "forwarding_mode": forwarding_mode,
        "announce_forge": bool(raw.get("announce_forge", defaults["announce_forge"])),
        "ping_passthrough": ping_passthrough,
        "read_timeout_ms": read_timeout_ms,
        "backends": backends,
        "try": try_order,
        "forced_hosts": forced_hosts,
        "plugins": normalized_plugins,
    }


def _toml_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def render_velocity_toml(config: dict) -> str:
    """Render normalized values without accepting arbitrary TOML fragments."""
    config = normalize_velocity_config(config)
    listen = config["listen"]
    lines = [
        'config-version = "2.8"',
        f'bind = {_toml_string(f"0.0.0.0:{listen["port"]}")}',
        'motd = "<green>Game Mover Velocity"',
        'show-max-players = 100',
        f'online-mode = {str(config["online_mode"]).lower()}',
        'force-key-authentication = true',
        'prevent-client-proxy-connections = false',
        f'player-info-forwarding-mode = {_toml_string(config["forwarding_mode"])}',
        'forwarding-secret-file = "forwarding.secret"',
        f'announce-forge = {str(config["announce_forge"]).lower()}',
        f'ping-passthrough = {_toml_string(config["ping_passthrough"])}',
        'enable-player-address-logging = false',
        '',
        '[servers]',
    ]
    for backend in config["backends"]:
        lines.append(
            f'{backend["id"]} = {_toml_string(f"{backend["host"]}:{backend["port"]}")}'
        )
    lines.append(f'try = [{", ".join(_toml_string(item) for item in config["try"])}]')
    lines.extend(['', '[forced-hosts]'])
    for hostname, routes in sorted(config["forced_hosts"].items()):
        route_list = ", ".join(_toml_string(item) for item in routes)
        lines.append(f'{_toml_string(hostname)} = [{route_list}]')
    lines.extend([
        '',
        '[advanced]',
        f'read-timeout = {config["read_timeout_ms"]}',
        'tcp-fast-open = false',
        'bungee-plugin-message-channel = true',
        'show-ping-requests = false',
        'failover-on-unexpected-server-disconnect = true',
        'announce-proxy-commands = true',
        'log-command-executions = false',
        'log-player-connections = true',
        '',
        '[query]',
        'enabled = false',
        '',
    ])
    return "\n".join(lines)


def write_velocity_layout(config: dict, data_directory: str) -> dict:
    """Atomically publish config and create a secret once; caller controls ownership."""
    config = normalize_velocity_config(config)
    root = Path(data_directory).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret_path = root / "forwarding.secret"
    if not secret_path.exists():
        secret_path.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        secret_path.chmod(0o600)
    config_path = root / "velocity.toml"
    temporary_path = root / ".velocity.toml.tmp"
    with temporary_path.open("w", encoding="utf-8") as stream:
        stream.write(render_velocity_toml(config))
    temporary_path.chmod(0o600)
    os.replace(temporary_path, config_path)
    return {
        "data_directory": str(root),
        "config_path": str(config_path),
        "secret_path": str(secret_path),
    }


def chown_velocity_layout(layout: dict, owner_user: str) -> None:
    account = pwd.getpwnam(owner_user)
    for key in ("data_directory", "config_path", "secret_path"):
        os.chown(layout[key], account.pw_uid, account.pw_gid)
