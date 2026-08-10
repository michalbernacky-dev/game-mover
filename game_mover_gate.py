#!/usr/bin/env python3
"""Validated Gate Lite routing configuration shared by API and deployment code."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import pwd
import re


GATE_CONTAINER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
GATE_NETWORK_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
GATE_BACKEND_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
GATE_ROUTE_HOST_RE = re.compile(r"^[A-Za-z0-9*?_.:-]+$")
GATE_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)


class GateConfigError(ValueError):
    """Gate Lite configuration is invalid or unsafe."""


def default_gate_config() -> dict:
    """Return the tested staging route without changing the production Forge port."""
    return {
        "image": (
            "ghcr.io/minekube/gate@sha256:"
            "8e67b92f20fd0f643fc8172657c99bd58e50cd5467142a5acfb039da6238e3ff"
        ),
        "container_name": "gate",
        "network": "game-platform",
        "listen": {"host": "0.0.0.0", "port": 25581},
        "routes": [
            {
                "host": "*",
                "backend": {"host": "host.containers.internal", "port": 25565},
            }
        ],
        "cache_ping_ttl_seconds": -1,
    }


def _port(value, label):
    if isinstance(value, bool):
        raise GateConfigError(f"{label} musí být platný TCP port")
    try:
        value = int(value)
    except (TypeError, ValueError) as error:
        raise GateConfigError(f"{label} musí být platný TCP port") from error
    if not 1 <= value <= 65535:
        raise GateConfigError(f"{label} musí být platný TCP port")
    return value


def _backend_host(value):
    value = str(value or "").strip()
    if not value or not GATE_BACKEND_HOST_RE.fullmatch(value):
        raise GateConfigError("Adresa backendu není platný hostitel")
    try:
        ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        if value.startswith("-") or value.endswith("-") or ".." in value:
            raise GateConfigError("Adresa backendu není platný hostitel")
    return value


def _route_host(value):
    value = str(value or "").strip().lower()
    if not value or not GATE_ROUTE_HOST_RE.fullmatch(value):
        raise GateConfigError("Hostname trasy není platný")
    if value != "*" and (value.startswith("-") or value.endswith("-") or ".." in value):
        raise GateConfigError("Hostname trasy není platný")
    return value


def normalize_gate_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise GateConfigError("Konfigurace Gate Lite musí být objekt")
    defaults = default_gate_config()
    image = str(raw.get("image", defaults["image"])).strip()
    if not GATE_IMAGE_RE.fullmatch(image):
        raise GateConfigError("Neplatná reference image Gate Lite")
    container_name = str(raw.get("container_name", defaults["container_name"])).strip()
    if not GATE_CONTAINER_RE.fullmatch(container_name):
        raise GateConfigError("Neplatné jméno containeru Gate Lite")
    network = str(raw.get("network", defaults["network"])).strip().lower()
    if not GATE_NETWORK_RE.fullmatch(network):
        raise GateConfigError("Neplatné jméno Podman sítě Gate Lite")

    listen = raw.get("listen") if isinstance(raw.get("listen"), dict) else {}
    listen_host = str(listen.get("host", defaults["listen"]["host"])).strip()
    try:
        listen_address = ipaddress.ip_address(listen_host)
    except ValueError as error:
        raise GateConfigError("Poslechová adresa Gate Lite není platná") from error

    raw_routes = raw.get("routes", defaults["routes"])
    if not isinstance(raw_routes, list) or not raw_routes:
        raise GateConfigError("Gate Lite potřebuje alespoň jednu trasu")
    routes = []
    seen_hosts = set()
    for raw_route in raw_routes:
        if not isinstance(raw_route, dict):
            raise GateConfigError("Neplatná Gate Lite trasa")
        host = _route_host(raw_route.get("host"))
        if host in seen_hosts:
            raise GateConfigError("Duplicitní hostname Gate Lite trasy")
        seen_hosts.add(host)
        raw_backend = raw_route.get("backend")
        if not isinstance(raw_backend, dict):
            raise GateConfigError("Gate Lite trasa nemá platný backend")
        routes.append({
            "host": host,
            "backend": {
                "host": _backend_host(raw_backend.get("host")),
                "port": _port(raw_backend.get("port"), "Port backendu"),
            },
        })
    wildcard_positions = [index for index, route in enumerate(routes) if route["host"] == "*"]
    if wildcard_positions and wildcard_positions[-1] != len(routes) - 1:
        raise GateConfigError("Výchozí * trasa musí být poslední")

    try:
        cache_ttl = int(raw.get("cache_ping_ttl_seconds", defaults["cache_ping_ttl_seconds"]))
    except (TypeError, ValueError) as error:
        raise GateConfigError("Cache server-list pingu není platná") from error
    if cache_ttl != -1 and not 0 <= cache_ttl <= 3600:
        raise GateConfigError("Cache server-list pingu musí být -1 nebo 0 až 3600 sekund")

    return {
        "image": image,
        "container_name": container_name,
        "network": network,
        "listen": {
            "host": str(listen_address),
            "port": _port(listen.get("port", defaults["listen"]["port"]), "Poslechový port"),
        },
        "routes": routes,
        "cache_ping_ttl_seconds": cache_ttl,
    }


def upsert_gate_route(config: dict, hostname: str, backend_host: str, backend_port: int) -> dict:
    """Add or replace a hostname route while preserving the final wildcard fallback."""
    config = normalize_gate_config(config)
    hostname = _route_host(hostname)
    if hostname == "*":
        raise GateConfigError("Automatická hostname trasa nesmí přepsat výchozí * fallback")
    route = {
        "host": hostname,
        "backend": {
            "host": _backend_host(backend_host),
            "port": _port(backend_port, "Port backendu"),
        },
    }
    routes = [item for item in config["routes"] if item["host"] != hostname]
    wildcard_index = next(
        (index for index, item in enumerate(routes) if item["host"] == "*"), len(routes),
    )
    routes.insert(wildcard_index, route)
    config["routes"] = routes
    return normalize_gate_config(config)


def render_gate_yaml(config: dict) -> str:
    """Render only validated scalar values; arbitrary YAML is never accepted."""
    config = normalize_gate_config(config)
    lines = [
        "config:",
        "  bind: 0.0.0.0:25565",
        "  onlineMode: true",
        "  lite:",
        "    enabled: true",
        "    routes:",
    ]
    for route in config["routes"]:
        backend = route["backend"]
        backend_address = f"{backend['host']}:{backend['port']}"
        lines.extend([
            f"      - host: {json.dumps(route['host'])}",
            f"        backend: {json.dumps(backend_address)}",
            f"        cachePingTTL: {config['cache_ping_ttl_seconds']}",
        ])
    return "\n".join(lines) + "\n"


def write_gate_layout(config: dict, data_directory: str) -> dict:
    config = normalize_gate_config(config)
    root = Path(data_directory).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = root / "config.yml"
    temporary_path = root / ".config.yml.tmp"
    with temporary_path.open("w", encoding="utf-8") as stream:
        stream.write(render_gate_yaml(config))
        stream.flush()
        os.fsync(stream.fileno())
    temporary_path.chmod(0o600)
    os.replace(temporary_path, config_path)
    return {"data_directory": str(root), "config_path": str(config_path)}


def chown_gate_layout(layout: dict, owner_user: str) -> None:
    account = pwd.getpwnam(owner_user)
    for key in ("data_directory", "config_path"):
        os.chown(layout[key], account.pw_uid, account.pw_gid)
