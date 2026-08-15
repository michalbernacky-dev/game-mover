"""Satisfactory-specific discovery layered on the generic workload model."""

from __future__ import annotations

import re
import subprocess


DEFAULT_GAME_PORT = 7777
DEFAULT_RELIABLE_PORT = 8888
_SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
_PORT_OPTION_RE = re.compile(
    r"(?:^|[\s\"])-(?P<option>ExternalReliablePort|ReliablePort|Port)"
    r"(?:=|\s+)(?P<port>[0-9]{1,5})(?=[\s;\"]|$)",
    re.IGNORECASE,
)


def parse_satisfactory_exec_start(value):
    """Return effective Satisfactory ports from systemd's ExecStart rendering."""
    options = {}
    rendered = str(value or "").replace("\\x20", " ").replace("\\x3d", "=")
    for match in _PORT_OPTION_RE.finditer(rendered):
        port = int(match.group("port"))
        if 1 <= port <= 65535:
            options[match.group("option").lower()] = port
    game_port = options.get("port", DEFAULT_GAME_PORT)
    reliable_port = options.get(
        "externalreliableport",
        options.get("reliableport", DEFAULT_RELIABLE_PORT),
    )
    return {
        "game_port": game_port,
        "game_source": (
            "systemd ExecStart" if "port" in options else "výchozí Satisfactory"
        ),
        "reliable_port": reliable_port,
        "reliable_source": (
            "systemd ExecStart"
            if "externalreliableport" in options or "reliableport" in options
            else "výchozí Satisfactory"
        ),
    }


def discover_satisfactory_endpoints(unit, timeout=3):
    """Read the effective systemd command without interpreting it through a shell."""
    unit = str(unit or "").strip()
    if not _SYSTEMD_UNIT_RE.fullmatch(unit):
        raise ValueError("Neplatná systemd jednotka pro Satisfactory adaptér")
    completed = subprocess.run(
        ["systemctl", "show", "--property=ExecStart", "--value", "--no-pager", unit],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.strip() or "Nelze načíst ExecStart Satisfactory služby"
        )
    ports = parse_satisfactory_exec_start(completed.stdout)
    return [
        {
            "name": "Game/API", "protocol": "tcp", "port": ports["game_port"],
            "source": ports["game_source"],
        },
        {
            "name": "Game/Query", "protocol": "udp", "port": ports["game_port"],
            "source": ports["game_source"],
        },
        {
            "name": "Reliable messaging", "protocol": "tcp",
            "port": ports["reliable_port"], "source": ports["reliable_source"],
        },
    ]
