#!/usr/bin/env python3
"""Local discovery and safe transfer of Minecraft world directories."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile


MAX_WORLD_FILES = 250_000
MAX_WORLD_BYTES = 64 * 1024**3
MAX_ARCHIVE_BYTES = 16 * 1024**3
WORLD_DIRECTORY_RE = re.compile(r"[A-Za-z0-9_. -]{1,64}")


class MinecraftWorldError(ValueError):
    """A world cannot be discovered, archived, or imported safely."""


def _world_directory(path: Path) -> bool:
    try:
        directory = path.stat(follow_symlinks=False)
        level = (path / "level.dat").stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return stat.S_ISDIR(directory.st_mode) and stat.S_ISREG(level.st_mode)


def discover_minecraft_worlds(home: str | None = None) -> list[dict]:
    """Return worlds from common launchers in the current desktop profile."""
    root = Path(home or Path.home()).expanduser()
    sources = [
        ("Vanilla", root / ".minecraft" / "saves", None),
        ("CurseForge", root / "Documents" / "curseforge" / "minecraft" / "Instances", "instances"),
        ("CurseForge", root / "curseforge" / "minecraft" / "Instances", "instances"),
        ("CurseForge", root / ".curseforge" / "minecraft" / "Instances", "instances"),
        ("Prism Launcher", root / ".local" / "share" / "PrismLauncher" / "instances", "instances"),
        ("Prism Launcher", root / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher" / "instances", "instances"),
        ("MultiMC", root / ".local" / "share" / "multimc" / "instances", "instances"),
        ("MultiMC", root / "MultiMC" / "instances", "instances"),
    ]
    results = []
    seen = set()
    for launcher, base, layout in sources:
        candidates = []
        if layout == "instances":
            try:
                instances = sorted(base.iterdir(), key=lambda item: item.name.casefold())[:500]
            except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
                instances = []
            for instance in instances:
                for minecraft_dir in (instance / "saves", instance / ".minecraft" / "saves", instance / "minecraft" / "saves"):
                    try:
                        worlds = sorted(minecraft_dir.iterdir(), key=lambda item: item.name.casefold())[:500]
                    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
                        continue
                    candidates.extend((world, instance.name) for world in worlds)
        else:
            try:
                candidates = [(world, "") for world in sorted(base.iterdir(), key=lambda item: item.name.casefold())[:500]]
            except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
                candidates = []
        for world, instance_name in candidates:
            if not _world_directory(world):
                continue
            canonical = os.path.realpath(world)
            if canonical in seen:
                continue
            seen.add(canonical)
            context = f" · {instance_name}" if instance_name else ""
            results.append({
                "name": world.name,
                "path": str(world),
                "launcher": launcher,
                "instance": instance_name,
                "label": f"{launcher}{context} · {world.name}",
            })
    return sorted(results, key=lambda item: (item["launcher"].casefold(), item["label"].casefold()))


def validate_world_name(value: str) -> str:
    name = str(value or "").strip()
    if not WORLD_DIRECTORY_RE.fullmatch(name) or name in (".", ".."):
        raise MinecraftWorldError(
            "Název světa může obsahovat jen písmena, čísla, mezeru, tečku, pomlčku a podtržítko"
        )
    return name


def create_world_archive(source_directory: str, archive_path: str) -> dict:
    """Create a bounded archive without following links or special files."""
    source = Path(source_directory).expanduser()
    if not _world_directory(source):
        raise MinecraftWorldError("Vybraný adresář není Minecraft svět (chybí level.dat)")
    files = []
    total_bytes = 0
    for current, directory_names, file_names in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in [*directory_names, *file_names]:
            path = current_path / name
            details = path.stat(follow_symlinks=False)
            if stat.S_ISLNK(details.st_mode):
                raise MinecraftWorldError("Svět nesmí obsahovat symbolické odkazy")
            if stat.S_ISDIR(details.st_mode):
                continue
            if not stat.S_ISREG(details.st_mode):
                raise MinecraftWorldError("Svět smí obsahovat jen běžné soubory a adresáře")
            if details.st_nlink != 1:
                raise MinecraftWorldError("Svět nesmí obsahovat vícenásobně odkazované soubory")
            files.append(path)
            total_bytes += details.st_size
            if len(files) > MAX_WORLD_FILES or total_bytes > MAX_WORLD_BYTES:
                raise MinecraftWorldError("Svět překračuje bezpečný limit velikosti nebo počtu souborů")
    destination = Path(archive_path)
    with tarfile.open(destination, mode="x:gz", compresslevel=6) as archive:
        archive.add(source, arcname="world", recursive=True, filter=_safe_tar_filter)
    if destination.stat().st_size > MAX_ARCHIVE_BYTES:
        destination.unlink(missing_ok=True)
        raise MinecraftWorldError("Zabalený svět překračuje limit pro přenos")
    return {"files": len(files), "size_bytes": total_bytes, "archive_bytes": destination.stat().st_size}


def _safe_tar_filter(member: tarfile.TarInfo) -> tarfile.TarInfo:
    if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
        raise MinecraftWorldError("Svět smí obsahovat jen běžné soubory a adresáře")
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.mode = 0o750 if member.isdir() else 0o640
    return member


def extract_world_archive(archive_path: str, staging_directory: str) -> dict:
    """Validate and extract an uploaded archive without links or path traversal."""
    staging = Path(staging_directory)
    staging.mkdir(mode=0o700, parents=False, exist_ok=False)
    file_count = 0
    total_bytes = 0
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[0] != "world" or any(part in ("", ".", "..") for part in parts):
                    raise MinecraftWorldError("Archiv světa obsahuje neplatnou cestu")
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise MinecraftWorldError("Archiv světa obsahuje nepovolený typ souboru")
                if member.isfile():
                    file_count += 1
                    total_bytes += member.size
                    if file_count > MAX_WORLD_FILES or total_bytes > MAX_WORLD_BYTES:
                        raise MinecraftWorldError("Archiv světa překračuje bezpečný limit")
            for member in members:
                relative = PurePosixPath(member.name).parts[1:]
                destination = staging.joinpath("world", *relative)
                if member.isdir():
                    destination.mkdir(mode=0o750, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise MinecraftWorldError("Archiv světa nelze přečíst")
                with source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                destination.chmod(0o640)
        world = staging / "world"
        if not _world_directory(world):
            raise MinecraftWorldError("Archiv neobsahuje platný Minecraft svět s level.dat")
        return {"world_directory": str(world), "files": file_count, "size_bytes": total_bytes}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
