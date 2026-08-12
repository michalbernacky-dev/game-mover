"""Validated Minecraft whitelist catalog and command construction."""

import json
import os
import stat

from game_mover_operators import PLAYER_NAME_RE, validate_player_name


WHITELIST_FILE_MAX_BYTES = 1024 * 1024


class MinecraftWhitelistError(ValueError):
    pass


def whitelist_command(action, player=None):
    action = str(action or "").strip().lower()
    if action in ("on", "off", "reload"):
        return f"whitelist {action}"
    if action in ("add", "remove"):
        try:
            name = validate_player_name(player)
        except ValueError as error:
            raise MinecraftWhitelistError(str(error)) from error
        return f"whitelist {action} {name}"
    raise MinecraftWhitelistError("Neplatná operace s whitelistem")


def read_whitelist(data_directory):
    root = os.path.realpath(str(data_directory or ""))
    if not root or not os.path.isdir(root):
        raise MinecraftWhitelistError("Datový adresář Minecraft serveru neexistuje")
    path = os.path.join(root, "whitelist.json")
    try:
        if os.path.islink(path):
            raise MinecraftWhitelistError("whitelist.json nesmí být symbolický odkaz")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise MinecraftWhitelistError(f"whitelist.json nelze otevřít: {error}") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise MinecraftWhitelistError("whitelist.json musí být běžný soubor")
        if details.st_size > WHITELIST_FILE_MAX_BYTES:
            raise MinecraftWhitelistError("whitelist.json je příliš velký")
        raw = os.read(descriptor, WHITELIST_FILE_MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MinecraftWhitelistError(f"whitelist.json nelze načíst: {error}") from error
    if not isinstance(payload, list):
        raise MinecraftWhitelistError("whitelist.json nemá očekávaný formát")
    players = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        if not PLAYER_NAME_RE.fullmatch(name):
            continue
        players.append({"name": name, "uuid": str(item.get("uuid", ""))})
    return sorted(players, key=lambda item: item["name"].lower())
