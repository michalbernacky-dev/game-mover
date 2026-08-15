"""Generic host network endpoints shared by every game server type."""

from __future__ import annotations

import re


ENDPOINT_PROTOCOLS = ("tcp", "udp")
MAX_ENDPOINTS = 32
_CONFIG_ENTRY_RE = re.compile(
    r"^(?P<name>[^=;]{1,80})=(?P<protocol>tcp|udp):(?P<port>[0-9]{1,5})$",
    re.IGNORECASE,
)


def normalize_endpoints(value):
    """Validate and normalize endpoints stored in the server registry."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ENDPOINTS:
        raise ValueError("Invalid network endpoints")

    endpoints = []
    occupied = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("Invalid network endpoint")
        name = str(raw.get("name", "")).strip()
        protocol = str(raw.get("protocol", "")).strip().lower()
        port = raw.get("port")
        if (
            not name
            or len(name) > 80
            or any(character in name for character in "=;")
            or protocol not in ENDPOINT_PROTOCOLS
            or isinstance(port, bool)
        ):
            raise ValueError("Invalid network endpoint")
        try:
            port = int(port)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid network endpoint port") from error
        if not 1 <= port <= 65535:
            raise ValueError("Invalid network endpoint port")
        key = (protocol, port)
        if key in occupied:
            raise ValueError(f"Duplicate network endpoint {protocol}:{port}")
        occupied.add(key)
        endpoints.append({"name": name, "protocol": protocol, "port": port})
    return endpoints


def parse_endpoint_config(text):
    """Parse the compact GUI form: ``Name=tcp:1234; Query=udp:1234``."""
    text = str(text or "").strip()
    if not text:
        return []
    endpoints = []
    for part in text.split(";"):
        match = _CONFIG_ENTRY_RE.fullmatch(part.strip())
        if not match:
            raise ValueError(
                "Endpointy zapiš jako Název=tcp:port; Další=udp:port"
            )
        endpoints.append({
            "name": match.group("name").strip(),
            "protocol": match.group("protocol").lower(),
            "port": int(match.group("port")),
        })
    return normalize_endpoints(endpoints)


def format_endpoint_config(endpoints):
    return "; ".join(
        f"{endpoint['name']}={endpoint['protocol']}:{endpoint['port']}"
        for endpoint in normalize_endpoints(endpoints)
    )


def format_endpoint_summary(endpoints, include_source=False):
    normalized = normalize_endpoints(endpoints)
    summaries = []
    for index, endpoint in enumerate(normalized):
        raw = endpoints[index] if isinstance(endpoints[index], dict) else {}
        source = str(raw.get("source", "")).strip()
        source_text = f" ({source})" if include_source and source else ""
        summaries.append(
            f"{endpoint['name']}: {endpoint['protocol'].upper()}/{endpoint['port']}"
            f"{source_text}"
        )
    return " · ".join(summaries)
