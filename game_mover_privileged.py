#!/usr/bin/env python3
"""Narrow root broker for operations unavailable to the unprivileged API.

The public Flask process never imports or invokes the handlers directly.  It
speaks a length-bounded, one-request JSON protocol over a root-owned Unix
socket.  Every action revalidates semantic inputs; arbitrary commands and
arbitrary filesystem paths are deliberately not part of the protocol.
"""

from __future__ import annotations

import configparser
import grp
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import sys
from typing import Any

from game_mover_steam_cache import inspect_steam_cache
from game_mover_ea import (
    ea_game_install_in_progress,
    ea_runtime_active,
    heroic_ea_installations,
    heroic_ea_shared_default_detected,
)


SOCKET_PATH = os.getenv(
    "GAME_MOVER_PRIVILEGED_SOCKET", "/run/game-mover/privileged.sock",
)
API_USER = os.getenv("GAME_MOVER_API_USER", "gameplatform")
GAMES_ROOT = os.path.realpath(os.getenv("GAME_MOVER_GAMES_ROOT", "/var/Games"))
GAMES_LINKS_ROOT = os.path.realpath(
    os.getenv("GAME_MOVER_GAMES_LINKS_ROOT", "/var/Games_links"),
)
TIMEKPR_ROOT = os.path.realpath(
    os.getenv("GAME_MOVER_TIMEKPR_ROOT", "/var/lib/timekpr/config"),
)
GROUP_NAME = "gemers"
ALLOWED_UNITS_PATH = os.getenv(
    "GAME_MOVER_ALLOWED_UNITS_PATH", "/etc/game_mover/allowed-services.json",
)
EA_MOUNT_STATE_ROOT = os.getenv(
    "GAME_MOVER_EA_MOUNT_STATE_ROOT", "/etc/game_mover/ea-mounts",
)
SYSTEMD_UNIT_ROOT = os.getenv(
    "GAME_MOVER_SYSTEMD_UNIT_ROOT", "/etc/systemd/system",
)
EA_BIND_SERVICE_PREFIX = "game-mover-ea-bind@"
MAX_REQUEST_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,160}$")
# Existing Linux account names are case-sensitive; preserve their exact spelling.
USERNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
GAME_NAME_RE = re.compile(r"^[^/\\\x00-\x1f\x7f]{1,255}$")
DNS_NAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$",
    re.IGNORECASE,
)
TIMEKPR_ADD_FLAGS = {"--addtime", "--add-allowedtime", "--addallowedtime"}
TIMEKPR_ACTION_FLAGS = {
    "--settimeleft", "--setallowedhours", "--userinfo", "--disable",
    *TIMEKPR_ADD_FLAGS,
}
SYSTEMD_ACTIONS = {"is-active", "start", "stop", "restart", "reset-failed"}
FIXED_SYSTEM_UNITS = {
    "dnsmasq.service", "game-mover-dns.service",
    "pihole-FTL.service", "forge-srv.service", "satisfactory.service",
}
ENABLE_SYSTEM_UNITS = {
    "game-mover-dns.service", "pihole-FTL.service", "dnsmasq.service",
}
DENIED_SYSTEM_UNITS = {
    "game_mover", "game_mover.service", "game-mover-privileged",
    "game-mover-privileged.service",
}
USER_FILESYSTEM_ACTIONS = {
    "move-game", "create-symlink", "set-steam-cache", "steam-cache-status",
}


class PrivilegedError(RuntimeError):
    """A broker request was rejected or could not be completed."""


def _bounded_text(value: Any, limit: int = MAX_OUTPUT_BYTES) -> str:
    encoded = str(value or "").encode("utf-8", errors="replace")
    if len(encoded) > limit:
        encoded = encoded[-limit:]
    return encoded.decode("utf-8", errors="replace")


def privileged_call(
    action: str,
    parameters: dict | None = None,
    *,
    socket_path: str = SOCKET_PATH,
    timeout: int = 30,
) -> dict:
    """Call the local broker and return its result payload."""
    request = json.dumps(
        {"action": action, "parameters": parameters or {}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(request) > MAX_REQUEST_BYTES:
        raise PrivilegedError("Požadavek pro privilegovaný helper je příliš velký")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(max(1, min(int(timeout), 900)))
            client.connect(socket_path)
            client.sendall(request)
            received = bytearray()
            while len(received) <= MAX_REQUEST_BYTES:
                chunk = client.recv(8192)
                if not chunk:
                    break
                received.extend(chunk)
                if b"\n" in chunk:
                    break
    except (OSError, ValueError) as error:
        raise PrivilegedError(f"Privilegovaný helper není dostupný: {error}") from error
    if len(received) > MAX_REQUEST_BYTES:
        raise PrivilegedError("Odpověď privilegovaného helperu je příliš velká")
    try:
        response = json.loads(bytes(received).split(b"\n", 1)[0])
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PrivilegedError("Privilegovaný helper vrátil neplatnou odpověď") from error
    if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
        raise PrivilegedError("Privilegovaný helper vrátil neplatnou odpověď")
    if not response["ok"]:
        raise PrivilegedError(str(response.get("error") or "Operace byla odmítnuta"))
    result = response.get("result", {})
    if not isinstance(result, dict):
        raise PrivilegedError("Privilegovaný helper vrátil neplatný výsledek")
    return result


def _safe_username(value: Any) -> str:
    username = str(value or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise PrivilegedError("Neplatné uživatelské jméno")
    try:
        account = pwd.getpwnam(username)
    except KeyError as error:
        raise PrivilegedError("Uživatel neexistuje") from error
    if account.pw_uid < 1000 or not account.pw_dir.startswith("/home/"):
        raise PrivilegedError("Uživatel není interaktivní domácí účet")
    return username


def _safe_game_name(value: Any) -> str:
    name = str(value or "")
    if name in (".", "..") or not GAME_NAME_RE.fullmatch(name):
        raise PrivilegedError("Neplatný název hry")
    return name


def _beneath(root: str, *components: str, allow_missing: bool = False) -> str:
    root = os.path.realpath(root)
    candidate = os.path.join(root, *components)
    parent = os.path.realpath(os.path.dirname(candidate))
    if os.path.commonpath((root, parent)) != root:
        raise PrivilegedError("Cesta opouští spravovaný kořen")
    if not allow_missing and os.path.lexists(candidate) and os.path.islink(candidate):
        raise PrivilegedError("Symbolický odkaz není na této cestě povolen")
    return candidate


def _steamapps(username: str, *, missing_ok: bool = False) -> str | None:
    home = pwd.getpwnam(username).pw_dir
    candidates = (
        os.path.join(home, ".steam/steam/steamapps"),
        os.path.join(home, ".local/share/Steam/steamapps"),
    )
    for candidate in candidates:
        real = os.path.realpath(candidate)
        if os.path.commonpath((os.path.realpath(home), real)) != os.path.realpath(home):
            continue
        if os.path.isdir(real) and not os.path.islink(candidate):
            return real
    if missing_ok:
        return None
    raise PrivilegedError("Steam knihovna nebyla nalezena")


def _set_shared_permissions(path: str) -> None:
    root = os.path.realpath(path)
    if os.path.commonpath((GAMES_ROOT, root)) != GAMES_ROOT:
        raise PrivilegedError("Oprávnění lze měnit pouze ve sdílené knihovně")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_fd = os.open(path, flags)
    try:
        gid = grp.getgrnam(GROUP_NAME).gr_gid
        os.fchown(directory_fd, -1, gid)
        os.fchmod(directory_fd, 0o775)
        descriptor = f"/proc/self/fd/{directory_fd}"
        common = {
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": 120,
            "pass_fds": (directory_fd,),
        }
        subprocess.run(
            ["/usr/bin/setfacl", "-R", "-m", f"g:{gid}:rwX,m::rwX", descriptor],
            **common,
        )
        subprocess.run(
            ["/usr/bin/setfacl", "-R", "-d", "-m", f"g:{gid}:rwx,m::rwx", descriptor],
            **common,
        )
    finally:
        os.close(directory_fd)


def _prepare_user_proxy_base(username: str, platform: str) -> str:
    """Create the exact per-player proxy directory before dropping privileges."""
    if platform not in ("steam", "ea"):
        raise PrivilegedError("Nepodporovaná platforma proxy adresáře")
    account = pwd.getpwnam(username)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        root_fd = os.open(GAMES_LINKS_ROOT, flags)
    except OSError as error:
        raise PrivilegedError(
            "Kořen spravovaných odkazů není bezpečně dostupný"
        ) from error

    def open_player_directory(parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            raise PrivilegedError(
                "Proxy adresář hráče není bezpečný adresář"
            ) from error
        metadata = os.fstat(descriptor)
        if metadata.st_uid not in (0, account.pw_uid):
            os.close(descriptor)
            raise PrivilegedError("Proxy adresář patří jinému uživateli")
        os.fchown(descriptor, account.pw_uid, account.pw_gid)
        os.fchmod(descriptor, 0o700)
        return descriptor

    try:
        user_fd = open_player_directory(root_fd, username)
        try:
            platform_fd = open_player_directory(user_fd, platform)
            os.close(platform_fd)
        finally:
            os.close(user_fd)
    finally:
        os.close(root_fd)
    return _beneath(GAMES_LINKS_ROOT, username, platform)


def _move_game(parameters: dict) -> dict:
    platform = parameters.get("platform")
    if platform not in ("steam", "ea"):
        raise PrivilegedError("Sdílení dat je podporováno pouze pro Steam a EA App")
    username = _safe_username(parameters.get("user"))
    game = _safe_game_name(parameters.get("game_name"))
    shared_directory = "steam"
    if platform == "steam":
        common = _beneath(_steamapps(username), "common")
    else:
        home = pwd.getpwnam(username).pw_dir
        if heroic_ea_shared_default_detected(home):
            raise PrivilegedError(
                "EA App používá sdílený výchozí Heroic prefix; nejprve jí "
                "nastav samostatný prefix"
            )
        matches = []
        for installation in heroic_ea_installations(home):
            candidate = _beneath(installation.games_root, game, allow_missing=True)
            if os.path.isdir(candidate) and not os.path.islink(candidate):
                matches.append((installation, candidate))
        if len(matches) != 1:
            raise PrivilegedError(
                "EA hra nebyla nalezena v právě jednom Heroic EA App prefixu"
            )
        installation, _candidate = matches[0]
        if ea_game_install_in_progress(installation, game):
            raise PrivilegedError("Instalace hry v EA App ještě není dokončená")
        if ea_runtime_active():
            raise PrivilegedError("Před přesunem ukonči Heroic a EA App")
        common = installation.games_root
        shared_directory = "EA"
    source = _beneath(common, game)
    if not os.path.isdir(source) or os.path.islink(source):
        raise PrivilegedError("Zdrojová hra nebyla nalezena")
    target_base = _beneath(GAMES_ROOT, shared_directory, allow_missing=True)
    os.makedirs(target_base, mode=0o775, exist_ok=True)
    target = _beneath(target_base, game, allow_missing=True)
    proxy = None
    if platform == "steam":
        proxy_base = _beneath(
            GAMES_LINKS_ROOT, username, platform, allow_missing=True,
        )
        os.makedirs(proxy_base, mode=0o775, exist_ok=True)
        proxy = _beneath(proxy_base, game, allow_missing=True)
    if os.path.lexists(target) or (proxy and os.path.lexists(proxy)):
        raise PrivilegedError("Cílová nebo integrační cesta už existuje")
    shutil.move(source, target)
    try:
        _set_shared_permissions(target)
        if platform == "steam":
            os.symlink(target, proxy)
            os.symlink(proxy, source)
        else:
            os.mkdir(source, mode=0o755)
    except Exception:
        if os.path.islink(source):
            os.unlink(source)
        elif os.path.isdir(source) and not os.listdir(source):
            os.rmdir(source)
        if proxy and os.path.islink(proxy):
            os.unlink(proxy)
        if not os.path.lexists(source) and os.path.isdir(target):
            shutil.move(target, source)
        raise
    result = {"message": f"Hra '{game}' byla přesunuta do sdílené knihovny"}
    if platform == "ea":
        result["ea_mountpoint"] = source
    return result


def _create_symlink(parameters: dict) -> dict:
    platform = parameters.get("platform")
    if platform not in ("steam", "ea"):
        raise PrivilegedError("Sdílení dat je podporováno pouze pro Steam a EA App")
    username = _safe_username(parameters.get("user"))
    game = _safe_game_name(parameters.get("game_name"))
    shared_directory = "steam" if platform == "steam" else "EA"
    target = _beneath(_beneath(GAMES_ROOT, shared_directory), game)
    if not os.path.isdir(target) or os.path.islink(target):
        raise PrivilegedError("Sdílená hra nebyla nalezena")
    if platform == "steam":
        common = _beneath(_steamapps(username), "common")
    else:
        home = pwd.getpwnam(username).pw_dir
        if heroic_ea_shared_default_detected(home):
            raise PrivilegedError(
                "EA App používá sdílený výchozí Heroic prefix; nejprve jí "
                "nastav samostatný prefix"
            )
        installations = heroic_ea_installations(home)
        prefixes = {item.prefix for item in installations}
        if len(prefixes) != 1 or not installations:
            raise PrivilegedError("Heroic EA App prefix nebyl nalezen jednoznačně")
        if ea_runtime_active():
            raise PrivilegedError("Před vytvořením odkazu ukonči Heroic a EA App")
        common = installations[0].games_root
    source = _beneath(common, game, allow_missing=True)
    if platform == "ea":
        if os.path.islink(source):
            if os.path.realpath(source) != os.path.realpath(target):
                raise PrivilegedError("Existující EA odkaz míří na jiná data")
            os.unlink(source)
            os.mkdir(source, mode=0o755)
        elif os.path.isdir(source):
            try:
                already_mounted = os.path.samefile(source, target)
            except OSError:
                already_mounted = False
            if (
                not already_mounted
                and not os.path.ismount(source)
                and os.listdir(source)
            ):
                raise PrivilegedError("Cílový EA adresář není prázdný")
        elif os.path.lexists(source):
            raise PrivilegedError("Cílová EA cesta není adresář")
        else:
            os.mkdir(source, mode=0o755)
        return {
            "message": "Mountpoint EA hry je připravený",
            "ea_mountpoint": source,
        }
    if os.path.lexists(source):
        raise PrivilegedError("Cesta už existuje")
    proxy_base = _beneath(GAMES_LINKS_ROOT, username, platform, allow_missing=True)
    os.makedirs(proxy_base, mode=0o775, exist_ok=True)
    proxy = _beneath(proxy_base, game, allow_missing=True)
    if os.path.lexists(proxy):
        if not os.path.islink(proxy) or os.path.realpath(proxy) != os.path.realpath(target):
            raise PrivilegedError("Proxy cesta koliduje s jinou hrou")
    else:
        os.symlink(target, proxy)
    os.symlink(proxy, source)
    return {"message": "Odkaz hry byl vytvořen přes spravovanou proxy"}


def _ea_mount_unit_name(mountpoint: str) -> str:
    result = _run(
        ["/usr/bin/systemd-escape", "--path", "--suffix=mount", mountpoint],
        10,
    )
    unit = result["stdout"].strip()
    if (
        result["returncode"]
        or not unit.endswith(".mount")
        or unit != os.path.basename(unit)
        or len(unit.encode("utf-8")) > 255
        or not re.fullmatch(r"[A-Za-z0-9_.@:\\x-]+", unit)
    ):
        raise PrivilegedError("Název systemd mount jednotky nelze bezpečně vytvořit")
    return unit


def _ea_mount_id(mountpoint: str) -> str:
    return hashlib.sha256(mountpoint.encode("utf-8")).hexdigest()


def _ea_bind_service(mount_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", mount_id):
        raise PrivilegedError("Neplatný identifikátor EA bind mountu")
    return f"{EA_BIND_SERVICE_PREFIX}{mount_id}.service"


def _write_managed_file(path: str, contents: str, mode: int) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    temporary = f"{path}.tmp-{os.getpid()}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, mode,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_managed_marker(mount_id: str) -> dict:
    """Load root-owned EA mount state without following a marker symlink."""
    _ea_bind_service(mount_id)
    marker_path = os.path.join(EA_MOUNT_STATE_ROOT, f"{mount_id}.json")
    try:
        metadata = os.stat(marker_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
            or metadata.st_size > 4096
        ):
            raise PrivilegedError("Evidence EA mountu nemá bezpečné vlastnosti")
        descriptor = os.open(
            marker_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PrivilegedError("Evidenci EA mountu nelze bezpečně načíst") from error
    if not isinstance(payload, dict):
        raise PrivilegedError("Evidence EA mountu je neplatná")
    return payload


def _validated_ea_marker(mount_id: str) -> tuple[str, str]:
    """Resolve the fixed source and per-player target from trusted mount state."""
    payload = _read_managed_marker(mount_id)
    if set(payload) != {
        "version", "unit", "user", "game_name", "source", "mountpoint",
    } or payload.get("version") != 2:
        raise PrivilegedError("Evidence EA mountu má nepodporovaný formát")
    username = _safe_username(payload.get("user"))
    game = _safe_game_name(payload.get("game_name"))
    service = _ea_bind_service(mount_id)
    home = os.path.realpath(pwd.getpwnam(username).pw_dir)
    source = _beneath(_beneath(GAMES_ROOT, "EA"), game)
    mountpoint = os.path.abspath(str(payload.get("mountpoint") or ""))
    parent = os.path.realpath(os.path.dirname(mountpoint))
    if (
        payload.get("unit") != service
        or payload.get("source") != source
        or _ea_mount_id(mountpoint) != mount_id
        or os.path.basename(mountpoint) != game
        or os.path.commonpath((home, parent)) != home
        or not os.path.isdir(source)
        or os.path.islink(source)
        or not os.path.isdir(mountpoint)
        or os.path.islink(mountpoint)
    ):
        raise PrivilegedError("Evidence EA mountu neodpovídá bezpečnému umístění")
    return source, mountpoint


def _run_ea_bind_service(mount_id: str, *, unmount: bool = False) -> None:
    """Mount or unmount one marker-selected EA payload as a systemd service."""
    if os.geteuid() != 0:
        raise PrivilegedError("EA bind helper musí běžet jako root")
    source, mountpoint = _validated_ea_marker(mount_id)
    if unmount:
        if not os.path.ismount(mountpoint):
            return
        result = _run(["/usr/bin/umount", mountpoint], 60)
        if result["returncode"]:
            raise PrivilegedError(result["stderr"] or "EA bind mount nelze odpojit")
        return
    if os.path.ismount(mountpoint):
        result = _run(["/usr/bin/umount", mountpoint], 60)
        if result["returncode"]:
            raise PrivilegedError(result["stderr"] or "Starý EA mount nelze odpojit")
    result = _run(["/usr/bin/mount", "--bind", source, mountpoint], 60)
    if result["returncode"]:
        raise PrivilegedError(result["stderr"] or "EA bind mount selhal")
    result = _run([
        "/usr/bin/mount", "-o", "remount,bind,nosuid,nodev", source,
        mountpoint,
    ], 60)
    if result["returncode"]:
        _run(["/usr/bin/umount", mountpoint], 60)
        raise PrivilegedError(result["stderr"] or "Zabezpečení EA bind mountu selhalo")


def _ensure_ea_bind_mount(parameters: dict, mountpoint: Any) -> dict:
    """Install one persistent bind mount derived from a dropped-UID worker."""
    username = _safe_username(parameters.get("user"))
    game = _safe_game_name(parameters.get("game_name"))
    home = os.path.realpath(pwd.getpwnam(username).pw_dir)
    target = _beneath(_beneath(GAMES_ROOT, "EA"), game)
    mountpoint = os.path.abspath(str(mountpoint or ""))
    parent = os.path.realpath(os.path.dirname(mountpoint))
    if (
        os.path.basename(mountpoint) != game
        or os.path.commonpath((home, parent)) != home
        or not os.path.isdir(mountpoint)
        or os.path.islink(mountpoint)
    ):
        raise PrivilegedError("EA mountpoint není bezpečný adresář v profilu hráče")
    if not os.path.isdir(target) or os.path.islink(target):
        raise PrivilegedError("Sdílená EA hra nebyla nalezena")

    legacy_unit = _ea_mount_unit_name(mountpoint)
    legacy_unit_path = os.path.join(SYSTEMD_UNIT_ROOT, legacy_unit)
    mount_id = _ea_mount_id(mountpoint)
    unit = _ea_bind_service(mount_id)
    marker_path = os.path.join(EA_MOUNT_STATE_ROOT, f"{mount_id}.json")
    expected_marker = json.dumps({
        "version": 2, "unit": unit, "user": username, "game_name": game,
        "source": target, "mountpoint": mountpoint,
    }, ensure_ascii=False, sort_keys=True) + "\n"
    legacy_marker = json.dumps({
        "unit": legacy_unit, "source": target, "mountpoint": mountpoint,
    }, ensure_ascii=False, sort_keys=True) + "\n"
    if os.path.lexists(legacy_unit_path) and not os.path.lexists(marker_path):
        raise PrivilegedError("Systemd mount jednotka koliduje s cizí konfigurací")
    managed_existing = os.path.lexists(marker_path)
    if managed_existing:
        try:
            with open(marker_path, encoding="utf-8") as stream:
                current_marker = stream.read()
                if current_marker not in (expected_marker, legacy_marker):
                    raise PrivilegedError("Evidence EA mountu neodpovídá požadavku")
        except OSError as error:
            raise PrivilegedError("Evidenci EA mountu nelze ověřit") from error
    metadata = os.stat(mountpoint, follow_symlinks=False)
    try:
        already_mounted = os.path.samefile(mountpoint, target)
    except OSError:
        already_mounted = False
    if (
        not already_mounted
        and not managed_existing
        and metadata.st_uid != pwd.getpwnam(username).pw_uid
    ):
        raise PrivilegedError("EA mountpoint nepatří vybranému hráči")
    if managed_existing and os.path.lexists(legacy_unit_path):
        metadata = os.stat(legacy_unit_path, follow_symlinks=False)
        try:
            with open(legacy_unit_path, encoding="utf-8") as stream:
                managed_unit = stream.read().startswith(
                    "# Managed by Game Mover; do not edit manually.\n",
                )
        except OSError as error:
            raise PrivilegedError("Starou EA mount jednotku nelze ověřit") from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
            or not managed_unit
        ):
            raise PrivilegedError("Stará EA mount jednotka není bezpečně spravovaná")
        _run(["/usr/bin/systemctl", "disable", legacy_unit], 60)
        _run(["/usr/bin/systemctl", "stop", legacy_unit], 60)
        os.unlink(legacy_unit_path)
    _write_managed_file(marker_path, expected_marker, 0o600)
    reload_result = _run(["/usr/bin/systemctl", "daemon-reload"], 30)
    if reload_result["returncode"]:
        raise PrivilegedError(reload_result["stderr"] or "Systemd reload selhal")
    enable_result = _run(["/usr/bin/systemctl", "enable", unit], 60)
    if enable_result["returncode"]:
        raise PrivilegedError(
            enable_result["stderr"] or "Aktivace EA bind mountu selhala",
        )
    restart_result = _run(["/usr/bin/systemctl", "restart", unit], 60)
    if restart_result["returncode"]:
        raise PrivilegedError(
            restart_result["stderr"] or "Aktivace EA bind mountu selhala",
        )
    return {
        "message": f"EA hra '{game}' je připojena trvalým bind mountem",
        "mount_unit": unit,
    }


def _steam_cache_status(parameters: dict) -> dict:
    username = _safe_username(parameters.get("user"))
    return inspect_steam_cache(_steamapps(username, missing_ok=True), GAMES_ROOT)


def _set_steam_cache(parameters: dict) -> dict:
    username = _safe_username(parameters.get("user"))
    steamapps = _steamapps(username)
    download = _beneath(steamapps, "downloading", allow_missing=True)
    shared_base = _beneath(GAMES_ROOT, "steam-cache", allow_missing=True)
    os.makedirs(shared_base, mode=0o775, exist_ok=True)
    shared = _beneath(shared_base, "downloading", allow_missing=True)
    if os.path.islink(shared):
        raise PrivilegedError("Sdílená Steam cache nesmí být symbolický odkaz")
    if os.path.islink(download):
        if os.path.realpath(download) == os.path.realpath(shared):
            return {"message": "Steam cache už je sdílená", "status": "shared"}
        os.unlink(download)
    elif os.path.isdir(download):
        if os.path.lexists(shared):
            raise PrivilegedError("Cílová sdílená cache už existuje")
        shutil.move(download, shared)
    elif os.path.lexists(download):
        raise PrivilegedError("Lokální Steam cache není adresář ani odkaz")
    else:
        os.makedirs(shared, mode=0o775, exist_ok=True)
    os.symlink(shared, download)
    _set_shared_permissions(shared)
    return {"message": "Steam cache byla bezpečně nasdílena", "status": "shared", "shared_path": shared}


def _configured_system_units() -> set[str]:
    try:
        descriptor = os.open(
            ALLOWED_UNITS_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or metadata.st_size > 64 * 1024
        ):
            os.close(descriptor)
            raise PrivilegedError("Allowlist systemd jednotek nemá bezpečné vlastnosti")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            payload = json.load(stream)
    except FileNotFoundError:
        return set()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PrivilegedError("Allowlist systemd jednotek nelze načíst") from error
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise PrivilegedError("Allowlist systemd jednotek je neplatný")
    allowed = set()
    for raw in payload:
        unit = raw.strip()
        if not SYSTEMD_UNIT_RE.fullmatch(unit):
            raise PrivilegedError("Allowlist obsahuje neplatnou systemd jednotku")
        if "." not in unit.rsplit("@", 1)[-1]:
            unit = f"{unit}.service"
        if not unit.endswith(".service") or unit in DENIED_SYSTEM_UNITS:
            raise PrivilegedError("Allowlist obsahuje nepovolenou systemd jednotku")
        allowed.add(unit)
    return allowed


def _systemd_unit(value: Any) -> str:
    unit = str(value or "").strip()
    if not SYSTEMD_UNIT_RE.fullmatch(unit) or unit in DENIED_SYSTEM_UNITS:
        raise PrivilegedError("Systemd jednotka není povolena")
    if "." not in unit.rsplit("@", 1)[-1]:
        unit = f"{unit}.service"
    if not unit.endswith(".service"):
        raise PrivilegedError("Povoleny jsou pouze systemd služby")
    if unit not in FIXED_SYSTEM_UNITS and unit not in _configured_system_units():
        raise PrivilegedError("Systemd jednotka není v root allowlistu")
    return unit


def _run(command: list[str], timeout: int) -> dict:
    timeout = max(1, min(int(timeout), 900))
    process = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
    )
    return {
        "returncode": process.returncode,
        "stdout": _bounded_text(process.stdout),
        "stderr": _bounded_text(process.stderr),
    }


def _systemd(parameters: dict) -> dict:
    action = str(parameters.get("verb") or "")
    unit = _systemd_unit(parameters.get("unit"))
    timeout = int(parameters.get("timeout", 30))
    if action in SYSTEMD_ACTIONS:
        return _run(["/usr/bin/systemctl", action, unit], timeout)
    if action in {"enable-now", "disable-now"} and unit in ENABLE_SYSTEM_UNITS:
        verb = "enable" if action == "enable-now" else "disable"
        return _run(["/usr/bin/systemctl", verb, "--now", unit], timeout)
    if action == "show-execstart":
        return _run([
            "/usr/bin/systemctl", "show", "--property=ExecStart", "--value",
            "--no-pager", unit,
        ], timeout)
    if action == "logs":
        try:
            tail = int(parameters.get("tail", 100))
        except (TypeError, ValueError) as error:
            raise PrivilegedError("Neplatný počet řádků logu") from error
        if not 10 <= tail <= 500:
            raise PrivilegedError("Počet řádků logu není povolen")
        return _run([
            "/usr/bin/journalctl", "--unit", unit, "--no-pager",
            "--output=short-iso", "--lines", str(tail),
        ], timeout)
    raise PrivilegedError("Systemd operace není povolena")


def _timekpra_binary() -> list[str]:
    detected = shutil.which("timekpra")
    if detected:
        return [detected]
    for candidate in ("/usr/bin/timekpra", "/usr/sbin/timekpra"):
        if os.path.isfile(candidate):
            return [candidate]
    raise PrivilegedError("timekpra není nainstalováno")


def _timekpr_args(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw or len(raw) > 5:
        raise PrivilegedError("Neplatné argumenty Timekpr")
    args = [str(item) for item in raw]
    if any(not item or len(item) > 256 or "\x00" in item for item in args):
        raise PrivilegedError("Neplatné argumenty Timekpr")
    if args[0] not in TIMEKPR_ACTION_FLAGS:
        raise PrivilegedError("Timekpr operace není povolena")
    if len(args) < 2 or not USERNAME_RE.fullmatch(args[1]):
        raise PrivilegedError("Neplatný uživatel Timekpr")
    _safe_username(args[1])
    if args[0] == "--userinfo" and len(args) != 2:
        raise PrivilegedError("Neplatné argumenty Timekpr")
    if args[0] in TIMEKPR_ADD_FLAGS:
        if len(args) != 3 or not args[2].isdigit() or not 1 <= int(args[2]) <= 86400:
            raise PrivilegedError("Neplatná hodnota času")
    if args[0] == "--settimeleft":
        if len(args) != 4 or args[2] not in {"+", "="} or not args[3].isdigit():
            raise PrivilegedError("Neplatná hodnota zbývajícího času")
        if not 0 <= int(args[3]) <= 86400:
            raise PrivilegedError("Neplatná hodnota zbývajícího času")
    if args[0] == "--setallowedhours":
        if len(args) != 4 or args[2] not in {str(day) for day in range(1, 8)}:
            raise PrivilegedError("Neplatný den Timekpr")
        if not re.fullmatch(r"[0-2][0-9](?:[.:][0-5][0-9])?-[0-2][0-9](?:[.:][0-5][0-9])?", args[3]):
            raise PrivilegedError("Neplatný rozsah hodin Timekpr")
    return args


def _timekpr(parameters: dict) -> dict:
    return _run([*_timekpra_binary(), *_timekpr_args(parameters.get("args"))], 30)


def _pam_auth(parameters: dict) -> dict:
    username = str(parameters.get("username") or "").strip()
    password = parameters.get("password")
    if (
        not USERNAME_RE.fullmatch(username)
        or not isinstance(password, str)
        or not password
        or len(password) > 4096
        or "\x00" in password
    ):
        raise PrivilegedError("Neplatné přihlašovací údaje")
    try:
        account = pwd.getpwnam(username)
    except KeyError:
        return {"authenticated": False}
    if account.pw_uid < 1000 or not account.pw_dir.startswith("/home/"):
        return {"authenticated": False}
    try:
        import pam
    except ImportError as error:
        raise PrivilegedError("PAM modul není dostupný") from error
    try:
        authenticated = bool(pam.pam().authenticate(username, password))
    except Exception as error:
        raise PrivilegedError("PAM ověření selhalo") from error
    return {"authenticated": authenticated}


def _pihole(parameters: dict) -> dict:
    records = parameters.get("records")
    command = ["/usr/bin/pihole-FTL", "--config", "dns.hosts"]
    if records is not None:
        if not isinstance(records, list) or len(records) > 1024:
            raise PrivilegedError("Neplatný seznam Pi-hole záznamů")
        normalized = []
        for record in records:
            if not isinstance(record, str) or len(record) > 512:
                raise PrivilegedError("Neplatný Pi-hole záznam")
            parts = record.split()
            if len(parts) < 2:
                raise PrivilegedError("Neplatný Pi-hole záznam")
            try:
                address = str(ipaddress.ip_address(parts[0]))
            except ValueError as error:
                raise PrivilegedError("Neplatná Pi-hole adresa") from error
            names = [name.lower().rstrip(".") for name in parts[1:]]
            if any(not DNS_NAME_RE.fullmatch(name) for name in names):
                raise PrivilegedError("Neplatné Pi-hole DNS jméno")
            normalized.append(" ".join((address, *names)))
        command.append(json.dumps(normalized, separators=(",", ":")))
    return _run(command, 20)


def _timekpr_config(parameters: dict) -> dict:
    username = _safe_username(parameters.get("user"))
    day = int(parameters.get("day"))
    if day not in range(1, 8):
        raise PrivilegedError("Neplatný den Timekpr")
    path = _beneath(TIMEKPR_ROOT, f"timekpr.{username}.conf")
    parser = configparser.ConfigParser()
    parser.optionxform = str
    if not parser.read(path):
        raise PrivilegedError("Konfiguraci Timekpr nelze načíst")
    section = username
    if action := parameters.get("operation"):
        if action != "ensure-day":
            raise PrivilegedError("Neplatná operace konfigurace Timekpr")
        allowed = [item for item in parser.get(section, "ALLOWED_WEEKDAYS", fallback="").split(";") if item]
        if str(day) not in allowed:
            allowed.append(str(day))
            parser.set(section, "ALLOWED_WEEKDAYS", ";".join(allowed))
            temporary = f"{path}.game-mover.tmp"
            with open(temporary, "x", encoding="utf-8") as stream:
                parser.write(stream)
            original = os.stat(path, follow_symlinks=False)
            os.chown(temporary, original.st_uid, original.st_gid)
            os.chmod(temporary, stat.S_IMODE(original.st_mode))
            os.replace(temporary, path)
    allowed_hours = parser.get(section, f"ALLOWED_HOURS_{day}", fallback=None)
    allowed_days = [item for item in parser.get(section, "ALLOWED_WEEKDAYS", fallback="").split(";") if item]
    limits = []
    for item in parser.get(section, "LIMITS_PER_WEEKDAYS", fallback="").split(";"):
        try:
            limits.append(int(item))
        except ValueError:
            continue
    mapping = dict(zip(allowed_days, limits))
    limit = mapping.get(str(day))
    if limit is None and len(limits) == 7:
        limit = limits[day - 1]
    return {"hours": allowed_hours, "limit": limit}


def _fix_permissions(_parameters: dict) -> dict:
    _set_shared_permissions(_beneath(GAMES_ROOT, "steam"))
    return {"message": "Oprávnění sdílené knihovny byla opravena"}


def dispatch(action: str, parameters: dict) -> dict:
    if not isinstance(parameters, dict):
        raise PrivilegedError("Parametry musí být objekt")
    handlers = {
        "systemd": _systemd,
        "pam-auth": _pam_auth,
        "timekpr": _timekpr,
        "pihole": _pihole,
        "timekpr-config": _timekpr_config,
        "move-game": _move_game,
        "create-symlink": _create_symlink,
        "set-steam-cache": _set_steam_cache,
        "steam-cache-status": _steam_cache_status,
        "fix-permissions": _fix_permissions,
    }
    handler = handlers.get(action)
    if handler is None:
        raise PrivilegedError("Neznámá privilegovaná operace")
    return handler(parameters)


def _filesystem_as_user(action: str, parameters: dict) -> dict:
    """Execute user-controlled filesystem traversal without root privileges."""
    username = _safe_username(parameters.get("user"))
    account = pwd.getpwnam(username)
    group_id = grp.getgrnam(GROUP_NAME).gr_gid
    if (
        action in ("move-game", "create-symlink")
        and parameters.get("platform") == "steam"
    ):
        _prepare_user_proxy_base(username, parameters.get("platform"))
    payload = json.dumps(parameters, ensure_ascii=False, separators=(",", ":"))
    completed = subprocess.run(
        [sys.executable, "-B", os.path.realpath(__file__), "--filesystem-worker", action],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        timeout=(
            900
            if action == "move-game" and parameters.get("platform") == "ea"
            else 300
        ),
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        user=account.pw_uid,
        group=account.pw_gid,
        extra_groups=[group_id],
    )
    if completed.returncode != 0:
        raise PrivilegedError(
            _bounded_text(completed.stderr, 2048) or "Uživatelská operace selhala",
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise PrivilegedError("Uživatelská operace vrátila neplatný výsledek") from error
    if not isinstance(result, dict):
        raise PrivilegedError("Uživatelská operace vrátila neplatný výsledek")
    return result


def _broker_dispatch(action: str, parameters: dict) -> dict:
    if action in USER_FILESYSTEM_ACTIONS:
        result = _filesystem_as_user(action, parameters)
        if (
            action in ("move-game", "create-symlink")
            and parameters.get("platform") == "ea"
        ):
            return _ensure_ea_bind_mount(
                parameters, result.get("ea_mountpoint"),
            )
        return result
    return dispatch(action, parameters)


def _serve_filesystem_worker(action: str) -> None:
    if action not in USER_FILESYSTEM_ACTIONS or os.geteuid() == 0:
        raise SystemExit("Neplatný uživatelský worker")
    try:
        raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise PrivilegedError("Neplatná délka požadavku")
        parameters = json.loads(raw)
        if not isinstance(parameters, dict):
            raise PrivilegedError("Parametry musí být objekt")
        username = _safe_username(parameters.get("user"))
        if pwd.getpwnam(username).pw_uid != os.geteuid():
            raise PrivilegedError("Worker neběží pod požadovaným uživatelem")
        result = dispatch(action, parameters)
    except (PrivilegedError, ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(_bounded_text(error, 2048), file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        peer = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _pid, uid, _gid = struct.unpack("3i", peer)
        expected_uid = pwd.getpwnam(API_USER).pw_uid
        if uid != expected_uid:
            self._reply(False, error="Nepovolená identita klienta")
            return
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST_BYTES:
            self._reply(False, error="Neplatná délka požadavku")
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError
            result = _broker_dispatch(
                request.get("action"), request.get("parameters", {}),
            )
        except (PrivilegedError, ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
            self._reply(False, error=_bounded_text(error, 2048))
            return
        self._reply(True, result=result)

    def _reply(self, ok: bool, **payload: Any) -> None:
        response = json.dumps({"ok": ok, **payload}, ensure_ascii=False).encode("utf-8")
        self.wfile.write(response[:MAX_REQUEST_BYTES - 1] + b"\n")


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


def serve(socket_path: str = SOCKET_PATH) -> None:
    if os.geteuid() != 0:
        raise SystemExit("Privilegovaný helper musí běžet jako root")
    path = Path(socket_path)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.unlink()
    old_umask = os.umask(0o117)
    try:
        server = _Server(socket_path, _RequestHandler)
    finally:
        os.umask(old_umask)
    group = grp.getgrnam(API_USER).gr_gid
    os.chown(socket_path, 0, group)
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--filesystem-worker":
        _serve_filesystem_worker(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] in ("--ea-mount", "--ea-unmount"):
        try:
            _run_ea_bind_service(
                sys.argv[2], unmount=sys.argv[1] == "--ea-unmount",
            )
        except (PrivilegedError, OSError, subprocess.SubprocessError) as error:
            print(_bounded_text(error, 2048), file=sys.stderr)
            raise SystemExit(1) from error
    else:
        serve()
