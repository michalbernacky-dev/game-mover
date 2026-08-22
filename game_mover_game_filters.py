"""Shared filtering and recognition of game installation directories."""

import os
import re
from pathlib import Path


EXCLUDE_PREFIXES = ("SteamLinuxRuntime", "Proton", "GE-Proton")
EXCLUDE_LIST = {
    "steam": (
        "Half-Life Dedicated Server",
        "Steam Controller Configs",
        "Steamworks Common Redistributables",
        "Steamworks Shared",
    ),
    "gog": (),
    "epic": (),
    "ubisoft": (),
    "rockstar": (),
}


def is_excluded_game(platform, name):
    """Return true when Mover and Tips should ignore this directory/title."""
    platform = str(platform).strip().lower()
    name_folded = str(name).strip().casefold()
    if any(name_folded.startswith(prefix.casefold()) for prefix in EXCLUDE_PREFIXES):
        return True
    return name_folded in {
        excluded.casefold() for excluded in EXCLUDE_LIST.get(platform, ())
    }


def _steam_manifest_install_dirs(common_directory):
    """Return install directories registered below the matching steamapps root."""
    steamapps = Path(common_directory).parent
    install_dirs = set()
    try:
        manifests = steamapps.glob("appmanifest_*.acf")
        for manifest in manifests:
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
            if match:
                install_dirs.add(match.group(1).strip().casefold())
    except OSError:
        pass
    return install_dirs


def _looks_like_complete_gog_install(path):
    """Recognize native/manual GOG installs without requiring a launcher account."""
    try:
        names = {entry.name.casefold() for entry in os.scandir(path)}
    except OSError:
        return False
    if {"start.sh", "gameinfo"} & names:
        return True
    return any(
        name.startswith(("goggame-", "uninstall-"))
        and name.endswith((".info", ".sh"))
        for name in names
    )


def is_possible_game_residue(platform, path):
    """Conservatively identify an unregistered or incomplete game directory.

    A true result is only a UI hint.  The Mover keeps an explicit switch that
    exposes these directories, so manually managed installations stay reachable.
    """
    platform = str(platform).strip().lower()
    path = os.path.abspath(str(path))
    name = os.path.basename(path.rstrip(os.sep))
    if is_excluded_game(platform, name):
        return False
    if platform == "steam":
        return name.casefold() not in _steam_manifest_install_dirs(os.path.dirname(path))
    if platform == "gog":
        return not _looks_like_complete_gog_install(path)
    # Heroic, Ubisoft and Rockstar do not yet have a sufficiently reliable
    # local registry reader.  Do not hide their directories based on size.
    return False
