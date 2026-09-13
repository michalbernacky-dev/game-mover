"""Shared filtering and recognition of game installation directories."""

import os
import re
from pathlib import Path

from game_mover_ea import ea_game_path_install_in_progress


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
    "ea": (),
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


def _steam_manifest_installs(common_directory):
    """Return registered install directories and their declared payload sizes."""
    steamapps = Path(common_directory).parent
    installs = {}
    try:
        manifests = steamapps.glob("appmanifest_*.acf")
        for manifest in manifests:
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            directory = re.search(
                r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE
            )
            size = re.search(r'"SizeOnDisk"\s+"(\d+)"', text, re.IGNORECASE)
            if directory:
                installs[directory.group(1).strip().casefold()] = (
                    int(size.group(1)) if size else 0
                )
    except OSError:
        pass
    return installs


def _directory_reaches_size(path, minimum_size):
    """Stop walking as soon as a directory contains enough logical payload."""
    total = 0
    try:
        for root, directories, files in os.walk(path, followlinks=False):
            directories[:] = [
                name for name in directories
                if not os.path.islink(os.path.join(root, name))
            ]
            for name in files:
                try:
                    total += os.stat(
                        os.path.join(root, name), follow_symlinks=False
                    ).st_size
                except OSError:
                    continue
                if total >= minimum_size:
                    return True
    except OSError:
        return False
    return total >= minimum_size


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
        installs = _steam_manifest_installs(os.path.dirname(path))
        expected_size = installs.get(name.casefold())
        if expected_size is None:
            return True
        # A stale Steam manifest may survive while only saves or configuration
        # remain.  Require at least half of the declared payload for non-trivial
        # games; a partially downloaded game is not safe to move either.
        if expected_size >= 64 * 1024 * 1024:
            return not _directory_reaches_size(path, expected_size // 2)
        return False
    if platform == "gog":
        return not _looks_like_complete_gog_install(path)
    if platform == "ea":
        return ea_game_path_install_in_progress(path)
    # Heroic, Ubisoft and Rockstar do not yet have a sufficiently reliable
    # local registry reader.  Do not hide their directories based on size.
    return False
