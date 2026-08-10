#!/usr/bin/env python3
"""Validated Minecraft Podman installation and backup restore helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import tarfile
import tempfile

from game_mover_workloads import OCI_IMAGE_RE


INSTALL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
HOSTNAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}[a-z0-9]$")
SUPPORTED_LOADERS = {"VANILLA", "FORGE", "FABRIC", "NEOFORGE"}
LOADER_VERSION_ENV = {
    "FORGE": "FORGE_VERSION",
    "FABRIC": "FABRIC_LOADER_VERSION",
    "NEOFORGE": "NEOFORGE_VERSION",
}


class InstallError(RuntimeError):
    """A Minecraft installation or restore could not be completed safely."""


def _port(value):
    if isinstance(value, bool):
        raise InstallError("Port musí být platné číslo")
    try:
        value = int(value)
    except (TypeError, ValueError) as error:
        raise InstallError("Port musí být platné číslo") from error
    if not 1 <= value <= 65535:
        raise InstallError("Port musí být v rozsahu 1 až 65535")
    return value


def normalize_install_request(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise InstallError("Instalační požadavek musí být objekt")
    server_id = str(raw.get("id", "")).strip().lower()
    name = str(raw.get("name", "")).strip()
    loader = str(raw.get("loader", "VANILLA")).strip().upper()
    version = str(raw.get("version", "")).strip()
    loader_version = str(raw.get("loader_version", "")).strip()
    image = str(raw.get("image", "docker.io/itzg/minecraft-server:java17")).strip()
    hostname = str(raw.get("hostname", "")).strip().lower()
    if raw.get("accept_eula") is not True:
        raise InstallError("Před instalací je nutné potvrdit Minecraft EULA")
    if not INSTALL_ID_RE.fullmatch(server_id):
        raise InstallError("ID serveru musí začínat písmenem a obsahovat jen a-z, 0-9, _ nebo -")
    if not name or len(name) > 120:
        raise InstallError("Název serveru je povinný a smí mít nejvýše 120 znaků")
    if loader not in SUPPORTED_LOADERS:
        raise InstallError("Nepodporovaný Minecraft loader")
    if not VERSION_RE.fullmatch(version):
        raise InstallError("Neplatná verze Minecraftu")
    if loader != "VANILLA" and loader_version and not VERSION_RE.fullmatch(loader_version):
        raise InstallError("Neplatná verze loaderu")
    if not OCI_IMAGE_RE.fullmatch(image) or not image.startswith("docker.io/itzg/minecraft-server"):
        raise InstallError("Instalace nyní podporuje pouze validovaný itzg Minecraft image")
    if hostname and (not HOSTNAME_RE.fullmatch(hostname) or ".." in hostname):
        raise InstallError("Neplatný hostname serveru")
    try:
        memory_mb = int(raw.get("memory_mb", 4096))
    except (TypeError, ValueError) as error:
        raise InstallError("Paměť musí být celé číslo v MiB") from error
    if not 1024 <= memory_mb <= 24576:
        raise InstallError("Paměť musí být 1024 až 24576 MiB")

    backup = raw.get("backup")
    normalized_backup = None
    if backup is not None:
        if not isinstance(backup, dict):
            raise InstallError("Neplatný zdroj zálohy")
        source_id = str(backup.get("source_id", "")).strip()
        backup_id = str(backup.get("id", "")).strip()
        if not INSTALL_ID_RE.fullmatch(source_id) or not VERSION_RE.fullmatch(backup_id):
            raise InstallError("Neplatné ID zdrojové zálohy")
        normalized_backup = {"source_id": source_id, "id": backup_id}

    return {
        "id": server_id,
        "name": name,
        "loader": loader,
        "version": version,
        "loader_version": loader_version,
        "image": image,
        "memory_mb": memory_mb,
        "port": _port(raw.get("port", 25565)),
        "hostname": hostname,
        "backup": normalized_backup,
        "accept_eula": True,
    }


def container_environment(config: dict) -> dict:
    config = normalize_install_request(config)
    environment = {
        "EULA": "TRUE",
        "TYPE": config["loader"],
        "VERSION": config["version"],
        "MEMORY": f"{config['memory_mb']}M",
        "UID": "1000",
        "GID": "1000",
    }
    loader_key = LOADER_VERSION_ENV.get(config["loader"])
    if loader_key and config["loader_version"]:
        environment[loader_key] = config["loader_version"]
    return environment


def list_backups(backup_root: str, source_id: str) -> list[dict]:
    if not INSTALL_ID_RE.fullmatch(str(source_id or "")):
        raise InstallError("Neplatné ID zdrojového serveru")
    directory = Path(backup_root).resolve() / source_id
    if not directory.is_dir():
        return []
    results = []
    for manifest_path in sorted(directory.glob("*.manifest.json"), reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("schema_version") != 1:
                continue
            if manifest.get("workload", {}).get("id") != source_id:
                continue
            archive = manifest.get("archive", {})
            archive_path = directory / str(archive.get("file", ""))
            if not archive_path.is_file():
                continue
            results.append({
                "id": manifest.get("backup_id"),
                "created_at": manifest.get("created_at"),
                "size_bytes": archive.get("size_bytes"),
                "source_backend": manifest.get("workload", {}).get("backend"),
            })
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            continue
    return results


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members:
        raise InstallError("Záložní archiv je prázdný")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or path.parts[0] != "data" or ".." in path.parts:
            raise InstallError("Záložní archiv obsahuje cestu mimo datový adresář")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise InstallError("Záložní archiv obsahuje nepovolený speciální soubor nebo odkaz")
        if not (member.isdir() or member.isfile()):
            raise InstallError("Záložní archiv obsahuje nepodporovaný typ položky")
    return members


def _extract_members(archive: tarfile.TarFile, members: list[tarfile.TarInfo], target: Path) -> None:
    for member in members:
        relative = PurePosixPath(member.name)
        destination = target.joinpath(*relative.parts)
        if member.isdir():
            destination.mkdir(mode=member.mode & 0o777, parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise InstallError(f"Nelze číst položku zálohy {member.name}")
        with source, destination.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        destination.chmod(member.mode & 0o777)


def _chown_tree(path: Path, owner_user: str) -> None:
    account = pwd.getpwnam(owner_user)
    os.chown(path, account.pw_uid, account.pw_gid)
    for root, directories, files in os.walk(path):
        for name in directories:
            os.chown(os.path.join(root, name), account.pw_uid, account.pw_gid)
        for name in files:
            os.chown(os.path.join(root, name), account.pw_uid, account.pw_gid)


def restore_backup(
    *,
    backup_root: str,
    source_id: str,
    backup_id: str,
    data_root: str,
    target_id: str,
    owner_user: str,
) -> dict:
    if not INSTALL_ID_RE.fullmatch(source_id) or not VERSION_RE.fullmatch(backup_id):
        raise InstallError("Neplatná reference zálohy")
    if not INSTALL_ID_RE.fullmatch(target_id):
        raise InstallError("Neplatné cílové ID serveru")
    backup_directory = Path(backup_root).resolve() / source_id
    manifest_path = backup_directory / f"{backup_id}.manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InstallError("Manifest zálohy nelze načíst") from error
    if (
        manifest.get("schema_version") != 1
        or manifest.get("backup_id") != backup_id
        or manifest.get("workload", {}).get("id") != source_id
    ):
        raise InstallError("Manifest zálohy neodpovídá požadovanému serveru")
    archive_info = manifest.get("archive") if isinstance(manifest.get("archive"), dict) else {}
    archive_name = str(archive_info.get("file", ""))
    if archive_name != f"{backup_id}.tar.gz":
        raise InstallError("Manifest obsahuje neplatné jméno archivu")
    archive_path = backup_directory / archive_name
    if not archive_path.is_file() or _sha256(archive_path) != archive_info.get("sha256"):
        raise InstallError("Kontrolní součet zálohy nesouhlasí")

    root = Path(data_root).resolve()
    server_root = root / target_id
    data_directory = server_root / "data"
    if server_root.exists():
        raise InstallError("Cílový datový adresář už existuje")
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target_id}-restore-", dir=root))
    published = False
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = _safe_members(archive)
            _extract_members(archive, members, staging)
        extracted = staging / "data"
        if not extracted.is_dir() or not (extracted / "server.properties").is_file():
            raise InstallError("Záloha neobsahuje úplná data Minecraft serveru")
        server_root.mkdir(mode=0o750)
        os.replace(extracted, data_directory)
        _chown_tree(server_root, owner_user)
        published = True
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not published:
            shutil.rmtree(server_root, ignore_errors=True)
    return {
        "data_directory": str(data_directory),
        "manifest": str(manifest_path),
        "sha256": archive_info["sha256"],
    }


def fresh_data_directory(*, data_root: str, target_id: str, owner_user: str) -> str:
    if not INSTALL_ID_RE.fullmatch(target_id):
        raise InstallError("Neplatné cílové ID serveru")
    server_root = Path(data_root).resolve() / target_id
    if server_root.exists():
        raise InstallError("Cílový datový adresář už existuje")
    data_directory = server_root / "data"
    data_directory.mkdir(mode=0o750, parents=True)
    _chown_tree(server_root, owner_user)
    return str(data_directory)
