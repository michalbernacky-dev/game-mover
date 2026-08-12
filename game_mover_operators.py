"""Validated Minecraft operator catalog and command construction."""

import json
import os
import re
import stat


PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
OPS_FILE_MAX_BYTES = 1024 * 1024


class MinecraftOperatorsError(ValueError):
    pass


def validate_player_name(value):
    name = str(value or "").strip()
    if not PLAYER_NAME_RE.fullmatch(name):
        raise MinecraftOperatorsError(
            "Minecraft jméno musí mít 3–16 znaků: písmena, číslice nebo podtržítko"
        )
    return name


def operator_command(action, player):
    action = str(action or "").strip().lower()
    if action not in ("op", "deop"):
        raise MinecraftOperatorsError("Neplatná operace s operátorem")
    return f"{action} {validate_player_name(player)}"


def read_operators(data_directory):
    root = os.path.realpath(str(data_directory or ""))
    if not root or not os.path.isdir(root):
        raise MinecraftOperatorsError("Datový adresář Minecraft serveru neexistuje")
    path = os.path.join(root, "ops.json")
    try:
        if os.path.islink(path):
            raise MinecraftOperatorsError("ops.json nesmí být symbolický odkaz")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise MinecraftOperatorsError(f"ops.json nelze otevřít: {error}") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise MinecraftOperatorsError("ops.json musí být běžný soubor")
        if details.st_size > OPS_FILE_MAX_BYTES:
            raise MinecraftOperatorsError("ops.json je příliš velký")
        raw = os.read(descriptor, OPS_FILE_MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MinecraftOperatorsError(f"ops.json nelze načíst: {error}") from error
    if not isinstance(payload, list):
        raise MinecraftOperatorsError("ops.json nemá očekávaný formát")
    operators = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if not PLAYER_NAME_RE.fullmatch(name):
            continue
        level = item.get("level", 4)
        operators.append({
            "name": name,
            "uuid": str(item.get("uuid", "")),
            "level": level if isinstance(level, int) and 1 <= level <= 4 else 4,
            "bypasses_player_limit": bool(item.get("bypassesPlayerLimit", False)),
        })
    return sorted(operators, key=lambda item: item["name"].lower())
