"""Discover EA App game payloads inside an explicitly configured Heroic prefix.

The Wine prefix contains launcher state, credentials and per-user registry data.
Only the individual directories below ``Program Files/EA Games`` are game
payloads suitable for sharing; callers must never move the prefix itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat


MAX_CONFIG_BYTES = 2 * 1024 * 1024
EA_APP_TITLES = frozenset(("ea app", "ea desktop"))
EA_LAUNCHER_MARKERS = (
    "drive_c/Program Files/Electronic Arts/EA Desktop",
    "drive_c/Program Files (x86)/Electronic Arts/EA Desktop",
)
EA_GAME_ROOTS = (
    "drive_c/Program Files/EA Games",
    "drive_c/Program Files (x86)/EA Games",
)
EA_INSTALL_DATA = "drive_c/ProgramData/EA Desktop/InstallData"
INCOMPLETE_SUFFIXES = (".eajrn", ".eazstate", ".tmp", ".part", ".partial")
RUNTIME_MARKERS = (
    b"heroic",
    b"eadesktop.exe",
    b"ealauncher.exe",
    b"eabackgroundservice.exe",
    b"link2ea.exe",
)


@dataclass(frozen=True)
class EaInstallation:
    """One Heroic sideload entry that owns an EA App Wine prefix."""

    app_id: str
    prefix: str
    games_root: str
    install_data: str


def _heroic_config_roots(home: str) -> tuple[str, ...]:
    return (
        os.path.join(home, ".config/heroic"),
        os.path.join(
            home, ".var/app/com.heroicgameslauncher.hgl/config/heroic",
        ),
    )


def _load_json(path: str):
    try:
        metadata = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            return None
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _safe_component(value) -> str | None:
    value = str(value or "")
    if (
        not value
        or value in (".", "..")
        or value != value.strip()
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        return None
    return value


def _beneath_home(home: str, path: str) -> str | None:
    try:
        home = os.path.realpath(home)
        if not os.path.isabs(path) or os.path.islink(path):
            return None
        resolved = os.path.realpath(path)
        if os.path.commonpath((home, resolved)) != home:
            return None
        return resolved
    except (OSError, ValueError, TypeError):
        return None


def _ea_app_ids(config_root: str) -> list[str]:
    library = _load_json(os.path.join(config_root, "sideload_apps/library.json"))
    games = library.get("games") if isinstance(library, dict) else None
    if not isinstance(games, list):
        return []
    app_ids = []
    for item in games:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip().casefold()
        if title not in EA_APP_TITLES:
            continue
        app_id = _safe_component(
            item.get("app_name") or item.get("appName") or item.get("id")
        )
        if app_id and app_id not in app_ids:
            app_ids.append(app_id)
    return app_ids


def _shared_default_prefixes(config_root: str, home: str) -> set[str]:
    config = _load_json(os.path.join(config_root, "config.json"))
    defaults = config.get("defaultSettings") if isinstance(config, dict) else None
    if not isinstance(defaults, dict):
        return set()
    paths = set()
    for key in ("winePrefix", "defaultWinePrefix"):
        value = defaults.get(key)
        resolved = _beneath_home(home, value) if isinstance(value, str) else None
        if resolved:
            paths.add(resolved)
    return paths


def heroic_ea_installations(
    home: str, *, allow_shared_default: bool = False,
) -> list[EaInstallation]:
    """Return EA prefixes linked by Heroic, excluding its shared default."""
    home = os.path.realpath(home)
    installations = []
    seen = set()
    for config_root in _heroic_config_roots(home):
        shared_defaults = _shared_default_prefixes(config_root, home)
        for app_id in _ea_app_ids(config_root):
            config = _load_json(
                os.path.join(config_root, "GamesConfig", f"{app_id}.json")
            )
            entry = config.get(app_id) if isinstance(config, dict) else None
            prefix = entry.get("winePrefix") if isinstance(entry, dict) else None
            prefix = _beneath_home(home, prefix) if isinstance(prefix, str) else None
            if not prefix or not os.path.isdir(prefix) or prefix in seen:
                continue
            if not any(
                os.path.isdir(os.path.join(prefix, marker))
                for marker in EA_LAUNCHER_MARKERS
            ):
                continue
            if prefix in shared_defaults and not allow_shared_default:
                continue
            for relative_root in EA_GAME_ROOTS:
                games_root = os.path.join(prefix, relative_root)
                if not os.path.isdir(games_root) or os.path.islink(games_root):
                    continue
                installations.append(EaInstallation(
                    app_id=app_id,
                    prefix=prefix,
                    games_root=os.path.realpath(games_root),
                    install_data=os.path.join(prefix, EA_INSTALL_DATA),
                ))
            seen.add(prefix)
    return installations


def heroic_ea_shared_default_detected(home: str) -> bool:
    """Return true when EA App is assigned Heroic's global shared prefix."""
    strict = {item.prefix for item in heroic_ea_installations(home)}
    all_prefixes = {
        item.prefix for item in heroic_ea_installations(
            home, allow_shared_default=True,
        )
    }
    return bool(all_prefixes - strict)


def ea_game_install_in_progress(installation: EaInstallation, game_name: str) -> bool:
    """Conservatively recognize EA's staged/download journal for one game."""
    game_name = _safe_component(game_name)
    if not game_name or not os.path.isdir(installation.install_data):
        return False
    try:
        with os.scandir(installation.install_data) as entries:
            matching = next(
                entry for entry in entries
                if entry.name.casefold() == game_name.casefold()
                and entry.is_dir(follow_symlinks=False)
            )
    except (OSError, StopIteration):
        return False
    checked = 0
    try:
        for root, directories, files in os.walk(
            matching.path, followlinks=False,
        ):
            directories[:] = [
                name for name in directories
                if not os.path.islink(os.path.join(root, name))
            ]
            for name in files:
                checked += 1
                folded = name.casefold()
                if "dip_staged" in folded or folded.endswith(INCOMPLETE_SUFFIXES):
                    return True
                if checked >= 4096:
                    return True
    except OSError:
        return True
    return False


def ea_game_path_install_in_progress(game_path: str) -> bool:
    """Apply the staged-download check to a discovered EA game directory."""
    path = Path(os.path.abspath(game_path))
    try:
        drive_c = next(parent for parent in path.parents if parent.name == "drive_c")
    except StopIteration:
        return False
    installation = EaInstallation(
        app_id="",
        prefix=str(drive_c.parent),
        games_root=str(path.parent),
        install_data=str(drive_c.parent / EA_INSTALL_DATA),
    )
    return ea_game_install_in_progress(installation, path.name)


def ea_runtime_active(proc_root: str = "/proc") -> bool:
    """Return true when this user's Heroic or EA processes are still running."""
    uid = os.geteuid()
    try:
        processes = os.scandir(proc_root)
    except OSError:
        return True
    with processes:
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                if process.stat(follow_symlinks=False).st_uid != uid:
                    continue
                with open(os.path.join(process.path, "cmdline"), "rb") as stream:
                    command = stream.read(64 * 1024).replace(b"\x00", b" ").lower()
            except OSError:
                continue
            if any(marker in command for marker in RUNTIME_MARKERS):
                return True
    return False
