#!/usr/bin/env python3
"""Provider-neutral DNS registry and a small authoritative DNS service."""

from __future__ import annotations

import argparse
import ipaddress
import json
import socketserver
import struct
from pathlib import Path


DNS_CONFIG_VERSION = 1
DNS_PROVIDER_CATALOG = (
    {"id": "disabled", "name": "Vypnuto"},
    {"id": "builtin", "name": "Vestavěný autoritativní DNS"},
    {"id": "pihole_local", "name": "Pi-hole na tomto hostiteli"},
)
DNS_PROVIDER_IDS = tuple(item["id"] for item in DNS_PROVIDER_CATALOG)


class DnsConfigError(ValueError):
    pass


def dns_provider_catalog() -> list[dict]:
    return [dict(item) for item in DNS_PROVIDER_CATALOG]


def default_dns_config() -> dict:
    return {
        "version": DNS_CONFIG_VERSION,
        "provider": "disabled",
        "zone": "mc.home.arpa",
        "ttl": 60,
        "listen_addresses": [],
        "answer_addresses": [],
    }


def _hostname(value: object, *, label: str) -> str:
    value = str(value or "").strip().lower().rstrip(".")
    if not value or len(value) > 253:
        raise DnsConfigError(f"{label} není platné DNS jméno")
    labels = value.split(".")
    if any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise DnsConfigError(f"{label} není platné DNS jméno")
    return value


def normalize_dns_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise DnsConfigError("DNS konfigurace musí být objekt")
    defaults = default_dns_config()
    provider = str(raw.get("provider", defaults["provider"])).strip().lower()
    if provider not in DNS_PROVIDER_IDS:
        raise DnsConfigError("Neznámý DNS provider")
    zone = _hostname(raw.get("zone", defaults["zone"]), label="DNS zóna")
    try:
        ttl = int(raw.get("ttl", defaults["ttl"]))
    except (TypeError, ValueError) as error:
        raise DnsConfigError("DNS TTL musí být celé číslo") from error
    if not 5 <= ttl <= 86400:
        raise DnsConfigError("DNS TTL musí být 5 až 86400 sekund")

    normalized = {
        "version": DNS_CONFIG_VERSION,
        "provider": provider,
        "zone": zone,
        "ttl": ttl,
    }
    for key in ("listen_addresses", "answer_addresses"):
        values = raw.get(key, defaults[key])
        if not isinstance(values, list):
            raise DnsConfigError(f"{key} musí být seznam IP adres")
        addresses = []
        for value in values:
            address = str(ipaddress.ip_address(str(value).strip()))
            if address not in addresses:
                addresses.append(address)
        normalized[key] = addresses
    if provider == "builtin" and not normalized["listen_addresses"]:
        raise DnsConfigError("Vestavěný DNS provider potřebuje alespoň jednu poslechovou adresu")
    if provider != "disabled" and not normalized["answer_addresses"]:
        raise DnsConfigError("Aktivní DNS provider potřebuje alespoň jednu cílovou adresu")
    return normalized


def records_from_gate(config: dict, gate_config: dict) -> list[dict]:
    """Derive exact A/AAAA records; wildcard Gate fallbacks never become DNS."""
    config = normalize_dns_config(config)
    records = []
    seen = set()
    suffix = "." + config["zone"]
    routes = gate_config.get("routes", []) if isinstance(gate_config, dict) else []
    for route in routes:
        raw_host = str(route.get("host", "")).strip().lower().rstrip(".")
        if not raw_host or any(marker in raw_host for marker in ("*", "?")):
            continue
        host = _hostname(raw_host, label="Hostname Gate trasy")
        if host != config["zone"] and not host.endswith(suffix):
            continue
        for address in config["answer_addresses"]:
            key = (host, address)
            if key not in seen:
                records.append({"name": host, "address": address})
                seen.add(key)
    return records


def runtime_dns_config(config: dict, gate_config: dict) -> dict:
    config = normalize_dns_config(config)
    return {**config, "records": records_from_gate(config, gate_config)}


def write_runtime_config(path: str, config: dict, gate_config: dict) -> dict:
    runtime = runtime_dns_config(config, gate_config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(runtime, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.chmod(0o644)
    temporary.replace(target)
    return runtime


def _read_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels = []
    while True:
        if offset >= len(packet):
            raise ValueError("Zkrácený DNS dotaz")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0 or length > 63 or offset + length > len(packet):
            raise ValueError("Neplatné DNS jméno v dotazu")
        labels.append(packet[offset:offset + length].decode("ascii"))
        offset += length
    return ".".join(labels).lower(), offset


def build_dns_response(packet: bytes, runtime: dict) -> bytes:
    if len(packet) < 12:
        raise ValueError("Zkrácený DNS dotaz")
    query_id, flags, questions, _, _, _ = struct.unpack("!HHHHHH", packet[:12])
    if questions != 1:
        raise ValueError("DNS služba podporuje právě jednu otázku")
    name, offset = _read_name(packet, 12)
    if offset + 4 > len(packet):
        raise ValueError("Zkrácená DNS otázka")
    query_type, query_class = struct.unpack("!HH", packet[offset:offset + 4])
    question = packet[12:offset + 4]
    matching = [item for item in runtime.get("records", []) if item.get("name") == name]
    answers = []
    if query_class == 1:
        for item in matching:
            address = ipaddress.ip_address(item["address"])
            record_type = 1 if address.version == 4 else 28
            if query_type in (record_type, 255):
                answers.append((record_type, address.packed))
    zone = str(runtime.get("zone", "")).lower().rstrip(".")
    in_zone = name == zone or name.endswith("." + zone)
    response_flags = 0x8000 | (flags & 0x0100)
    if in_zone:
        response_flags |= 0x0400
    if not in_zone:
        response_flags |= 5
    elif not matching:
        response_flags |= 3
    header = struct.pack("!HHHHHH", query_id, response_flags, 1, len(answers), 0, 0)
    body = bytearray(header + question)
    ttl = int(runtime.get("ttl", 60))
    for record_type, packed in answers:
        body.extend(b"\xc0\x0c")
        body.extend(struct.pack("!HHIH", record_type, 1, ttl, len(packed)))
        body.extend(packed)
    return bytes(body)


class _UdpHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        try:
            runtime = json.loads(Path(self.server.config_path).read_text(encoding="utf-8"))
            sock.sendto(build_dns_response(packet, runtime), self.client_address)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return


class _ThreadingUdpServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True
    daemon_threads = True


class _TcpHandler(socketserver.BaseRequestHandler):
    def handle(self):
        size_data = self.request.recv(2)
        if len(size_data) != 2:
            return
        expected = struct.unpack("!H", size_data)[0]
        chunks = bytearray()
        while len(chunks) < expected:
            chunk = self.request.recv(expected - len(chunks))
            if not chunk:
                return
            chunks.extend(chunk)
        try:
            runtime = json.loads(Path(self.server.config_path).read_text(encoding="utf-8"))
            response = build_dns_response(bytes(chunks), runtime)
            self.request.sendall(struct.pack("!H", len(response)) + response)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(config_path: str) -> None:
    runtime = json.loads(Path(config_path).read_text(encoding="utf-8"))
    config = normalize_dns_config(runtime)
    if config["provider"] != "builtin":
        raise DnsConfigError("Vestavěný DNS provider není aktivní")
    servers = []
    try:
        for address in config["listen_addresses"]:
            family = 10 if ipaddress.ip_address(address).version == 6 else 2
            for base, handler in ((_ThreadingUdpServer, _UdpHandler), (_ThreadingTcpServer, _TcpHandler)):
                server_type = type("BoundDnsServer", (base,), {"address_family": family})
                server = server_type((address, 53), handler)
                server.config_path = config_path
                servers.append(server)
        import threading
        for server in servers[1:]:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        servers[0].serve_forever()
    finally:
        for server in servers:
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    serve(arguments.config)
