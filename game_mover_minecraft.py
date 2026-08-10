#!/usr/bin/env python3
"""Read-only Minecraft status and persisted-player helpers."""

import json
import os
import re
import socket
import struct


MAX_STATUS_PACKET = 1024 * 1024
MAX_RCON_PACKET = 64 * 1024
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def local_server_addresses():
    """Return local addresses suitable for probing services bound on this host."""
    addresses = []

    def add(address):
        if address and address not in ("0.0.0.0", "::") and address not in addresses:
            addresses.append(address)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_socket:
            route_socket.connect(("192.0.2.1", 9))
            add(route_socket.getsockname()[0])
    except OSError:
        pass
    try:
        for address_info in socket.getaddrinfo(
            socket.gethostname(), None, type=socket.SOCK_STREAM,
        ):
            add(address_info[4][0])
    except OSError:
        pass
    add("127.0.0.1")
    add("::1")
    return addresses


def _encode_varint(value):
    value &= 0xFFFFFFFF
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        encoded.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(encoded)


def _read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.recv(length - len(chunks))
        if not chunk:
            raise OSError("Minecraft status connection closed unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_varint(stream):
    value = 0
    for index in range(5):
        byte = _read_exact(stream, 1)[0]
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return value
    raise ValueError("Minecraft status VarInt is too large")


def query_server_status(host, port, timeout=8.0, protocol_version=763):
    """Return online/max player counts using the Minecraft status protocol."""
    encoded_host = host.encode("utf-8")
    if len(encoded_host) > 255:
        raise ValueError("Minecraft host name is too long")

    handshake = (
        _encode_varint(0)
        + _encode_varint(protocol_version)
        + _encode_varint(len(encoded_host))
        + encoded_host
        + struct.pack(">H", int(port))
        + _encode_varint(1)
    )
    with socket.create_connection((host, int(port)), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(_encode_varint(len(handshake)) + handshake)
        stream.sendall(b"\x01\x00")
        packet_length = _read_varint(stream)
        if not 0 < packet_length <= MAX_STATUS_PACKET:
            raise ValueError("Invalid Minecraft status packet length")
        packet_id = _read_varint(stream)
        if packet_id != 0:
            raise ValueError("Unexpected Minecraft status packet")
        json_length = _read_varint(stream)
        if not 0 <= json_length <= packet_length <= MAX_STATUS_PACKET:
            raise ValueError("Invalid Minecraft status JSON length")
        payload = json.loads(_read_exact(stream, json_length).decode("utf-8"))

    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, dict):
        raise ValueError("Minecraft status does not contain player counts")
    return {
        "online": int(players.get("online", 0)),
        "max": int(players.get("max", 0)),
    }


def _rcon_packet(request_id, packet_type, payload):
    encoded_payload = payload.encode("utf-8")
    body = struct.pack("<ii", request_id, packet_type) + encoded_payload + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def _read_rcon_packet(stream):
    packet_length = struct.unpack("<i", _read_exact(stream, 4))[0]
    if not 10 <= packet_length <= MAX_RCON_PACKET:
        raise ValueError("Invalid Minecraft RCON packet length")
    packet = _read_exact(stream, packet_length)
    request_id, packet_type = struct.unpack("<ii", packet[:8])
    if packet[-2:] != b"\x00\x00":
        raise ValueError("Invalid Minecraft RCON packet terminator")
    return request_id, packet_type, packet[8:-2].decode("utf-8")


def query_server_rcon(host, port, password, timeout=3.0):
    """Return player counts through the authenticated, read-only RCON list command."""
    request_id = 0x474D
    with socket.create_connection((host, int(port)), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(_rcon_packet(request_id, 3, password))
        auth_id, _auth_type, _auth_payload = _read_rcon_packet(stream)
        if auth_id != request_id:
            raise PermissionError("Minecraft RCON authentication failed")
        stream.sendall(_rcon_packet(request_id, 2, "list"))
        response_id, _response_type, response = _read_rcon_packet(stream)
        if response_id != request_id:
            raise ValueError("Unexpected Minecraft RCON response")

    match = re.search(
        r"There are\s+(\d+)\s+of a max of\s+(\d+)\s+players online",
        response,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Minecraft RCON list response does not contain player counts")
    return {"online": int(match.group(1)), "max": int(match.group(2))}


def _level_name(data_directory):
    properties_path = os.path.join(data_directory, "server.properties")
    try:
        with open(properties_path, "r", encoding="utf-8") as properties:
            for line in properties:
                key, separator, value = line.partition("=")
                if separator and key.strip() == "level-name":
                    return value.strip() or "world"
    except (OSError, UnicodeError):
        pass
    return "world"


def configured_server_port(data_directory):
    """Read the effective Minecraft TCP port from server.properties."""
    if not data_directory:
        return None
    properties_path = os.path.join(data_directory, "server.properties")
    try:
        with open(properties_path, "r", encoding="utf-8") as properties:
            for line in properties:
                key, separator, value = line.partition("=")
                if separator and key.strip() == "server-port":
                    port = int(value.strip())
                    return port if 1 <= port <= 65535 else None
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def configured_rcon(data_directory):
    """Return enabled local RCON settings, without exposing them through the API."""
    if not data_directory:
        return None
    properties_path = os.path.join(data_directory, "server.properties")
    values = {}
    try:
        with open(properties_path, "r", encoding="utf-8") as properties:
            for line in properties:
                key, separator, value = line.partition("=")
                key = key.strip()
                if separator and key in ("enable-rcon", "rcon.password", "rcon.port"):
                    values[key] = value.strip()
        if values.get("enable-rcon", "false").lower() != "true":
            return None
        password = values.get("rcon.password", "")
        port = int(values.get("rcon.port", "25575"))
        if not password or not 1 <= port <= 65535:
            return None
        return {"port": port, "password": password}
    except (OSError, UnicodeError, ValueError):
        return None


def count_known_players(data_directory):
    """Count distinct UUIDs found in persistent world data or user cache."""
    if not data_directory:
        return None
    data_root = os.path.realpath(data_directory)
    world_directory = os.path.realpath(os.path.join(data_root, _level_name(data_root)))
    try:
        if os.path.commonpath((data_root, world_directory)) != data_root:
            return None
    except ValueError:
        return None
    player_ids = set()
    source_found = False
    for relative_directory, suffix in (
        ("playerdata", ".dat"),
        ("stats", ".json"),
        ("advancements", ".json"),
    ):
        directory = os.path.join(world_directory, relative_directory)
        try:
            names = os.listdir(directory)
            source_found = True
        except OSError:
            continue
        for name in names:
            if not name.endswith(suffix):
                continue
            player_id = name[:-len(suffix)]
            if UUID_RE.fullmatch(player_id):
                player_ids.add(player_id.lower())

    user_cache_path = os.path.join(data_root, "usercache.json")
    try:
        with open(user_cache_path, "r", encoding="utf-8") as user_cache:
            entries = json.load(user_cache)
        source_found = True
        if isinstance(entries, list):
            for entry in entries:
                player_id = entry.get("uuid") if isinstance(entry, dict) else None
                if isinstance(player_id, str) and UUID_RE.fullmatch(player_id):
                    player_ids.add(player_id.lower())
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass

    return len(player_ids) if source_found else None
