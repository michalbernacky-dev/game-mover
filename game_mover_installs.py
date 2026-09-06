#!/usr/bin/env python3
"""Validated Minecraft Podman installation and backup restore helpers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import stat
import tarfile
import tempfile
from urllib.parse import urljoin, urlparse
import zipfile

import requests

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
CURSEFORGE_DOWNLOAD_HOSTS = frozenset((
    "edge.forgecdn.net",
    "mediafiles.forgecdn.net",
    "mediafilez.forgecdn.net",
))
CURSEFORGE_DOWNLOAD_MAX_BYTES = 4 * 1024 * 1024 * 1024
CURSEFORGE_DOWNLOAD_MAX_REDIRECTS = 5
CURSEFORGE_EXTRACT_MAX_BYTES = 16 * 1024 * 1024 * 1024
CURSEFORGE_EXTRACT_MAX_FILES = 100_000
CURSEFORGE_RECIPE_MAX_FILES = 1000
CURSEFORGE_RECIPE_MAX_BYTES = 4 * 1024 * 1024 * 1024
CURSEFORGE_FILE_PATH_RE = re.compile(r"^/files/([0-9]+)/([0-9]+)/[^/]+$")
FORGE_INSTALLER_PATH_RE = re.compile(
    r"^/maven/net/minecraftforge/forge/([^/]+)/forge-([^/]+)-installer[.]jar$"
)


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

    curseforge = raw.get("curseforge")
    normalized_curseforge = None
    if curseforge is not None:
        if not isinstance(curseforge, dict):
            raise InstallError("Neplatný zdroj CurseForge server packu")
        try:
            project_id = int(curseforge.get("project_id"))
            file_id = int(curseforge.get("file_id"))
        except (TypeError, ValueError) as error:
            raise InstallError("Neplatná reference CurseForge server packu") from error
        if project_id < 1 or file_id < 1:
            raise InstallError("Neplatná reference CurseForge server packu")
        normalized_curseforge = {"project_id": project_id, "file_id": file_id}
    if normalized_backup and normalized_curseforge:
        raise InstallError("Nelze současně obnovit zálohu a instalovat CurseForge server pack")

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
        "curseforge": normalized_curseforge,
        "accept_eula": True,
    }


def container_environment(config: dict, *, owner_user: str | None = None) -> dict:
    config = normalize_install_request(config)
    owner = pwd.getpwnam(owner_user) if owner_user else None
    environment = {
        "EULA": "TRUE",
        "TYPE": config["loader"],
        "VERSION": config["version"],
        "MEMORY": f"{config['memory_mb']}M",
        "UID": str(owner.pw_uid if owner else 1000),
        "GID": str(owner.pw_gid if owner else 1000),
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


def _validated_curseforge_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in CURSEFORGE_DOWNLOAD_HOSTS
        or parsed.port not in (None, 443)
    ):
        raise InstallError("CurseForge vrátil nepovolenou adresu server packu")
    return parsed.geturl()


def _expected_curseforge_hash(hashes: list[dict]) -> tuple[str, str] | None:
    # CurseForge algorithms: 1 = SHA-1, 2 = MD5. Prefer SHA-1 when both exist.
    supported = {1: "sha1", 2: "md5"}
    by_algorithm = {
        int(item.get("algorithm") or 0): str(item.get("value", "")).lower()
        for item in hashes if isinstance(item, dict)
    }
    for algorithm in (1, 2):
        value = by_algorithm.get(algorithm, "")
        expected_length = 40 if algorithm == 1 else 32
        if re.fullmatch(rf"[0-9a-f]{{{expected_length}}}", value):
            return supported[algorithm], value
    return None


def _download_curseforge_archive(
    descriptor: dict, target: Path, api_key: str, requester=None, *, label="server pack",
) -> dict:
    expected_size = int(descriptor.get("file_length") or 0)
    if not 1 <= expected_size <= CURSEFORGE_DOWNLOAD_MAX_BYTES:
        raise InstallError(f"CurseForge {label} má neplatnou nebo příliš velkou velikost")
    expected_hash = _expected_curseforge_hash(descriptor.get("hashes") or [])
    if expected_hash is None:
        raise InstallError(f"CurseForge {label} nemá podporovaný kontrolní součet")
    hash_name, expected_digest = expected_hash
    digest = hashlib.new(hash_name)
    url = _validated_curseforge_url(descriptor.get("download_url", ""))
    api_key = str(api_key or "").strip()
    if not api_key or len(api_key) > 4096 or any(ord(character) < 0x20 for character in api_key):
        raise InstallError("CurseForge API klíč není nakonfigurovaný")
    if requester is None:
        session = requests.Session()
        session.trust_env = False
        requester = session.get
    response = None
    redirect_statuses = {301, 302, 303, 307, 308}
    for redirect_count in range(CURSEFORGE_DOWNLOAD_MAX_REDIRECTS + 1):
        try:
            response = requester(
                url, stream=True, allow_redirects=False, timeout=(5, 120),
                headers={"Accept": "application/zip", "x-api-key": api_key},
            )
        except requests.RequestException as error:
            raise InstallError(f"CurseForge {label} nelze stáhnout") from error
        if response.status_code not in redirect_statuses:
            break
        location = response.headers.get("Location", "")
        response.close()
        response = None
        if redirect_count >= CURSEFORGE_DOWNLOAD_MAX_REDIRECTS:
            raise InstallError(f"Stažení CurseForge {label} obsahuje příliš mnoho přesměrování")
        url = _validated_curseforge_url(urljoin(url, location))
    if response is None:
        raise InstallError(f"CurseForge {label} nelze stáhnout")
    written = 0
    try:
        if response.status_code != 200:
            raise InstallError(
                f"Stažení CurseForge {label} vrátilo HTTP {response.status_code}"
            )
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) != expected_size:
                    raise InstallError(f"Velikost CurseForge {label} nesouhlasí")
            except ValueError as error:
                raise InstallError(f"Stažení vrátilo neplatnou délku CurseForge {label}") from error
        with target.open("xb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > expected_size or written > CURSEFORGE_DOWNLOAD_MAX_BYTES:
                    raise InstallError(f"Stažený CurseForge {label} překročil povolenou velikost")
                output.write(chunk)
                digest.update(chunk)
    except requests.RequestException as error:
        raise InstallError(f"Stahování CurseForge {label} bylo přerušeno") from error
    finally:
        response.close()
    if written != expected_size:
        raise InstallError(f"Stažený CurseForge {label} není úplný")
    if digest.hexdigest().lower() != expected_digest:
        raise InstallError(f"Kontrolní součet CurseForge {label} nesouhlasí")
    return {"size_bytes": written, hash_name: expected_digest}


def _curseforge_file_id_from_url(value: str) -> int | None:
    parsed = urlparse(str(value or ""))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in CURSEFORGE_DOWNLOAD_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = CURSEFORGE_FILE_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    return int(f"{int(match.group(1))}{int(match.group(2)):03d}")


def _server_recipe_entries(root: Path) -> dict | None:
    manifest = root / "mods.csv"
    if not manifest.is_file():
        return None
    if manifest.is_symlink() or manifest.stat().st_size > 1024 * 1024:
        raise InstallError("mods.csv v server packu je neplatný nebo příliš velký")
    try:
        rows = list(csv.reader(io.StringIO(manifest.read_text(encoding="utf-8-sig"))))
    except (OSError, UnicodeError, csv.Error) as error:
        raise InstallError("mods.csv v server packu nelze přečíst") from error
    if not rows or len(rows) > CURSEFORGE_RECIPE_MAX_FILES + 1:
        raise InstallError("mods.csv obsahuje neplatný počet položek")

    entries = []
    targets = set()
    file_ids = set()
    forge_installer_seen = False
    forge_loader_version = ""
    for row in rows:
        if len(row) != 2:
            raise InstallError("mods.csv obsahuje neplatný řádek")
        url = row[0].strip()
        target = row[1].strip()
        path = PurePosixPath(target)
        if (
            not target or "\\" in target or path.is_absolute() or ".." in path.parts
            or target in targets
        ):
            raise InstallError("mods.csv obsahuje neplatnou cílovou cestu")
        targets.add(target)

        file_id = _curseforge_file_id_from_url(url)
        if file_id is not None:
            if len(path.parts) != 2 or path.parts[0] != "mods" or not path.name.lower().endswith(".jar"):
                raise InstallError("CurseForge mod z receptu musí směřovat do mods/*.jar")
            if file_id in file_ids:
                raise InstallError("mods.csv obsahuje duplicitní CurseForge soubor")
            file_ids.add(file_id)
            entries.append({"file_id": file_id, "target": target})
            continue

        parsed = urlparse(url)
        forge_match = FORGE_INSTALLER_PATH_RE.fullmatch(parsed.path)
        if (
            not forge_installer_seen
            and target == "forge-installer.jar"
            and parsed.scheme == "https"
            and parsed.hostname == "files.minecraftforge.net"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and forge_match
            and forge_match.group(1) == forge_match.group(2)
        ):
            # The managed itzg image installs the selected Forge loader itself.
            forge_installer_seen = True
            forge_loader_version = forge_match.group(1).rsplit("-", 1)[-1]
            if not VERSION_RE.fullmatch(forge_loader_version):
                raise InstallError("mods.csv obsahuje neplatnou verzi Forge")
            continue
        raise InstallError("mods.csv odkazuje na nepodporovaný zdroj nebo cíl")
    if not entries:
        raise InstallError("mods.csv neobsahuje žádné CurseForge serverové mody")
    return {"entries": entries, "loader_version": forge_loader_version}


def _install_server_recipe(
    root: Path, entries: list[dict], resolver, api_key: str, requester=None, progress=None,
) -> dict:
    if resolver is None:
        raise InstallError(
            "Server pack vyžaduje deklarativní setup recept, ale resolver není dostupný"
        )
    descriptors = resolver([entry["file_id"] for entry in entries])
    if not isinstance(descriptors, list):
        raise InstallError("CurseForge vrátilo neplatné soubory setup receptu")
    descriptor_by_id = {
        int(item.get("file_id") or 0): item for item in descriptors if isinstance(item, dict)
    }
    if set(descriptor_by_id) != {entry["file_id"] for entry in entries}:
        raise InstallError("CurseForge nevrátilo všechny soubory setup receptu")
    total_bytes = sum(int(item.get("file_length") or 0) for item in descriptors)
    if not 1 <= total_bytes <= CURSEFORGE_RECIPE_MAX_BYTES:
        raise InstallError("Setup recept má neplatnou nebo příliš velkou celkovou velikost")

    downloaded = 0
    for index, entry in enumerate(entries, start=1):
        descriptor = descriptor_by_id[entry["file_id"]]
        destination = root.joinpath(*PurePosixPath(entry["target"]).parts)
        if destination.exists() or destination.is_symlink():
            raise InstallError("Setup recept se pokouší přepsat existující serverový mod")
        destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        _download_curseforge_archive(
            descriptor, destination, api_key, requester=requester, label="mod receptu",
        )
        destination.chmod(0o640)
        downloaded += int(descriptor.get("file_length") or 0)
        if progress is not None:
            progress(index, len(entries), downloaded, total_bytes)

    for name in ("mods.csv", "setup_server.sh", "setup-server.sh", "setup_server.bat"):
        candidate = root / name
        if candidate.is_file() and not candidate.is_symlink():
            candidate.unlink()
    return {"recipe_files": len(entries), "recipe_bytes": downloaded}


def _safe_zip_members(archive: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], str | None]:
    members = archive.infolist()
    files = [member for member in members if not member.is_dir()]
    if not files:
        raise InstallError("CurseForge server pack je prázdný")
    if len(files) > CURSEFORGE_EXTRACT_MAX_FILES:
        raise InstallError("CurseForge server pack obsahuje příliš mnoho souborů")
    total_size = 0
    top_levels = set()
    has_root_file = False
    for member in members:
        if "\\" in member.filename:
            raise InstallError("CurseForge server pack obsahuje neplatnou cestu")
        path = PurePosixPath(member.filename)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise InstallError("CurseForge server pack obsahuje cestu mimo datový adresář")
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise InstallError("CurseForge server pack obsahuje nepovolený odkaz nebo speciální soubor")
        if member.flag_bits & 0x1:
            raise InstallError("CurseForge server pack obsahuje šifrovaný soubor")
        total_size += member.file_size
        if total_size > CURSEFORGE_EXTRACT_MAX_BYTES:
            raise InstallError("Rozbalený CurseForge server pack je příliš velký")
        top_levels.add(path.parts[0])
        if len(path.parts) == 1 and not member.is_dir():
            has_root_file = True
    wrapper = next(iter(top_levels)) if len(top_levels) == 1 and not has_root_file else None
    return members, wrapper


def install_curseforge_server_pack(
    descriptor: dict, *, data_root: str, target_id: str, owner_user: str, api_key: str,
    requester=None, recipe_resolver=None, recipe_progress=None,
) -> dict:
    """Download, verify and atomically publish one CurseForge server-pack ZIP."""
    if not INSTALL_ID_RE.fullmatch(target_id):
        raise InstallError("Neplatné cílové ID serveru")
    root = Path(data_root).resolve()
    server_root = root / target_id
    data_directory = server_root / "data"
    if server_root.exists():
        raise InstallError("Cílový datový adresář už existuje")
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target_id}-curseforge-", dir=root))
    archive_path = staging / "server-pack.zip"
    published = False
    try:
        verification = _download_curseforge_archive(
            descriptor, archive_path, api_key, requester=requester,
        )
        extracted = staging / "extracted"
        extracted.mkdir(mode=0o750)
        try:
            with zipfile.ZipFile(archive_path, mode="r") as archive:
                members, wrapper = _safe_zip_members(archive)
                if archive.testzip() is not None:
                    raise InstallError("CurseForge server pack je poškozený")
                for member in members:
                    path = PurePosixPath(member.filename)
                    parts = path.parts[1:] if wrapper else path.parts
                    if not parts:
                        continue
                    destination = extracted.joinpath(*parts)
                    if member.is_dir():
                        destination.mkdir(mode=0o750, parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    archived_mode = (member.external_attr >> 16) & 0o777
                    destination.chmod(archived_mode or 0o640)
        except zipfile.BadZipFile as error:
            raise InstallError("CurseForge server pack není platný ZIP archiv") from error
        if not any(extracted.iterdir()):
            raise InstallError("CurseForge server pack neobsahuje serverová data")
        recipe_result = {}
        recipe = _server_recipe_entries(extracted)
        if recipe is not None:
            recipe_result = _install_server_recipe(
                extracted, recipe["entries"], recipe_resolver, api_key,
                requester=requester, progress=recipe_progress,
            )
            if recipe["loader_version"]:
                recipe_result["loader_version"] = recipe["loader_version"]
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
        "project_id": int(descriptor["project_id"]),
        "file_id": int(descriptor["file_id"]),
        **verification,
        **recipe_result,
    }


def detect_server_pack_loader_version(
    data_directory: str, loader: str, minecraft_version: str,
) -> str:
    """Best-effort detection of a pinned loader already bundled in server data."""
    root = Path(data_directory)
    loader = str(loader or "").upper()
    minecraft_version = str(minecraft_version or "")
    candidates = []
    if loader == "FORGE":
        prefix = f"{minecraft_version}-"
        for path in root.glob("libraries/net/minecraftforge/forge/*/unix_args.txt"):
            if path.parent.name.startswith(prefix):
                candidates.append(path.parent.name[len(prefix):])
        jar_prefix = f"forge-{minecraft_version}-"
        for path in root.glob(f"{jar_prefix}*-installer.jar"):
            candidates.append(path.name[len(jar_prefix):-len("-installer.jar")])
    elif loader == "NEOFORGE":
        candidates.extend(
            path.parent.name
            for path in root.glob("libraries/net/neoforged/neoforge/*/unix_args.txt")
        )

    variables = root / "variables.txt"
    try:
        if variables.is_file() and not variables.is_symlink() and variables.stat().st_size <= 64 * 1024:
            for line in variables.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() in {
                    "FORGE_VERSION", "NEOFORGE_VERSION", "MOD_LOADER_VERSION",
                    "MODLOADER_VERSION",
                }:
                    candidates.append(value.strip().strip('"\''))
    except (OSError, UnicodeError):
        pass
    return next((value for value in candidates if VERSION_RE.fullmatch(value)), "")


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
