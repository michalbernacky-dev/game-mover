"""Local installed-game inventory used by the knowledge-base catalog."""

import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

from game_mover_game_filters import is_excluded_game


DEFAULT_SMALL_INSTALL_BYTES = 1024 * 1024 * 1024
LAUNCHER_SLUGS = frozenset({
    "ea-app", "epic-games-store", "gog-galaxy", "heroic-games-launcher",
    "launcher", "plarium-launcher", "rockstar-games-launcher", "social-club",
    "ubisoft-connect",
})
KNOWN_ID_ALIASES = {
    "grand-theft-auto-v-enhanced": ["gta-v-enhanced"],
}


def game_slug(name):
    value = str(name).translate(str.maketrans({"™": "", "®": "", "©": ""}))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:128] or "unknown-game"


def knowledge_aliases(slug):
    aliases = [slug, *KNOWN_ID_ALIASES.get(slug, [])]
    if "-ii-" in f"-{slug}-":
        aliases.append(slug.replace("-ii-", "-2-"))
    return list(dict.fromkeys(aliases))


def directory_size(path):
    """Logical file size without following symlinked directories."""
    total = 0
    try:
        for root, directories, files in os.walk(path, followlinks=False):
            directories[:] = [name for name in directories if not os.path.islink(os.path.join(root, name))]
            for name in files:
                try:
                    stat = os.stat(os.path.join(root, name), follow_symlinks=False)
                    total += stat.st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _children(path, include_symlinks=False):
    try:
        return [
            entry for entry in os.scandir(path)
            if entry.is_dir(follow_symlinks=False)
            or (include_symlinks and entry.is_symlink() and os.path.isdir(entry.path))
        ]
    except OSError:
        return []


class Inventory:
    def __init__(self, small_install_bytes=DEFAULT_SMALL_INSTALL_BYTES):
        self.items = {}
        self.path_sizes = {}
        self.small_install_bytes = small_install_bytes

    def add(self, name, platform, path, user=None, app_id=None):
        name = str(name).strip()
        path = os.path.realpath(str(path)) if path else ""
        slug = game_slug(name)
        if (
            not name or slug in LAUNCHER_SLUGS
            or is_excluded_game(platform, name)
            or is_excluded_game(platform, os.path.basename(path))
            or not path or not os.path.isdir(path)
        ):
            return
        item = self.items.setdefault(slug, {
            "id": slug, "name": name, "platforms": set(), "users": set(),
            "paths": set(), "app_ids": set(), "knowledge_aliases": knowledge_aliases(slug),
        })
        item["platforms"].add(str(platform).lower())
        item["paths"].add(path)
        if user:
            item["users"].add(str(user))
        if app_id:
            item["app_ids"].add(str(app_id))

    def result(self):
        rows = []
        for item in self.items.values():
            size = 0
            for path in item["paths"]:
                real = os.path.realpath(path)
                if real not in self.path_sizes:
                    self.path_sizes[real] = directory_size(real)
                size += self.path_sizes[real]
            # The catalog intentionally models usable game installations, not
            # leftover prefixes/manifests.  The family policy treats directories
            # up to and including 1 GiB as remnants and hides them entirely.
            if size <= self.small_install_bytes:
                continue
            rows.append({
                **item,
                "platforms": sorted(item["platforms"]),
                "users": sorted(item["users"]),
                "paths": sorted(item["paths"]),
                "app_ids": sorted(item["app_ids"]),
                "size_bytes": size,
                "possible_residue": False,
            })
        return sorted(rows, key=lambda row: row["name"].casefold())


def _scan_shared(inventory, shared_root):
    roots = {
        "steam": ("steam",), "heroic": ("Heroic",), "epic": ("EpicGames", "Epic"),
        "gog": ("gog",), "ubisoft": ("ubisoft", "Ubisoft"),
        "rockstar": ("rockstar", "Rockstar"),
    }
    for platform, names in roots.items():
        for root_name in names:
            for entry in _children(os.path.join(shared_root, root_name)):
                real = os.path.realpath(entry.path)
                if any(real in item["paths"] for item in inventory.items.values()):
                    continue
                inventory.add(entry.name, platform, entry.path)


def _scan_steam_user(inventory, home, user):
    steam_roots = (
        os.path.join(home, ".local/share/Steam"), os.path.join(home, ".steam/steam"),
        os.path.join(home, ".var/app/com.valvesoftware.Steam/.local/share/Steam"),
    )
    seen = set()
    for steam_root in steam_roots:
        common = os.path.join(steam_root, "steamapps/common")
        for entry in _children(common, include_symlinks=True):
            real = os.path.realpath(entry.path)
            if real not in seen:
                inventory.add(entry.name, "steam", real, user)
                seen.add(real)
        try:
            manifests = Path(steam_root, "steamapps").glob("appmanifest_*.acf")
            for manifest in manifests:
                text = manifest.read_text(encoding="utf-8", errors="replace")
                app_id = re.search(r'"appid"\s+"(\d+)"', text)
                name = re.search(r'"name"\s+"([^"]+)"', text)
                install_dir = re.search(r'"installdir"\s+"([^"]+)"', text)
                if name and install_dir:
                    inventory.add(
                        name.group(1), "steam", os.path.join(common, install_dir.group(1)),
                        user, app_id.group(1) if app_id else None,
                    )
        except OSError:
            continue


def _scan_heroic_user(inventory, home, user):
    heroic = os.path.join(home, ".config/heroic")
    legendary = _load_json(os.path.join(heroic, "legendaryConfig/legendary/installed.json"))
    if isinstance(legendary, dict):
        for item in legendary.values():
            if isinstance(item, dict):
                inventory.add(item.get("title"), "epic", item.get("install_path"), user, item.get("app_name"))
    gog = _load_json(os.path.join(heroic, "gog_store/installed.json"))
    if isinstance(gog, dict):
        for item in gog.get("installed", []):
            if isinstance(item, dict):
                path = item.get("install_path", "")
                inventory.add(os.path.basename(path), "gog", path, user, item.get("appName"))
    sideload = _load_json(os.path.join(heroic, "sideload_apps/library.json"))
    if isinstance(sideload, dict):
        for item in sideload.get("games", []):
            if isinstance(item, dict):
                path = item.get("installPath") or item.get("install_path")
                inventory.add(item.get("title") or item.get("app_name") or os.path.basename(path or ""), "heroic", path, user)


def _scan_curseforge_user(inventory, home, user):
    candidates = [
        os.path.join(home, "Documents/curseforge/minecraft/Instances"),
        os.path.join(home, "curseforge/minecraft/Instances"),
    ]
    settings = _load_json(os.path.join(home, ".config/CurseForge/storage.json"))
    if isinstance(settings, dict) and isinstance(settings.get("minecraft-settings"), str):
        try:
            minecraft = json.loads(settings["minecraft-settings"])
            root = minecraft.get("minecraftRoot")
            if root:
                candidates.insert(0, os.path.join(root, "Instances"))
        except (TypeError, ValueError):
            pass
    seen = set()
    for root in candidates:
        for entry in _children(root):
            real = os.path.realpath(entry.path)
            if real in seen:
                continue
            metadata = _load_json(os.path.join(real, "minecraftinstance.json"))
            name = metadata.get("name") if isinstance(metadata, dict) else None
            inventory.add(name or entry.name, "curseforge", real, user)
            seen.add(real)


def _scan_lutris_user(inventory, home, user):
    database = os.path.join(home, ".local/share/lutris/pga.db")
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1)
        rows = connection.execute(
            "SELECT name, slug, runner, directory, service, service_id FROM games "
            "WHERE installed = 1"
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        return
    steam_common = os.path.join(home, ".local/share/Steam/steamapps/common")
    for name, slug, runner, directory, service, service_id in rows:
        if game_slug(slug or name) in LAUNCHER_SLUGS:
            continue
        platform = service or runner or "lutris"
        path = directory
        if platform == "steam" and not path:
            # Steam manifests/shared scanning will normally supply the precise path.
            path = os.path.join(steam_common, name)
        inventory.add(name, platform, path, user, service_id)


def _scan_common_user_directories(inventory, home, user):
    roots = {
        "epic": [os.path.join(home, "Games/Epic"), os.path.join(home, "Epic Games")],
        "gog": [os.path.join(home, "GOG Games")],
        "ubisoft": [
            os.path.join(home, "Ubisoft Game Launcher/games"),
            os.path.join(home, "Games/Ubisoft Connect"),
            os.path.join(home, "Games/Ubisoft"),
            os.path.join(home, "Games/ubisoft-connect/drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/games"),
        ],
        "rockstar": [
            os.path.join(home, "Rockstar Games"), os.path.join(home, "Games/Rockstar Games"),
            os.path.join(home, "Games/rockstar-games-launcher/drive_c/Program Files/Rockstar Games"),
        ],
        "ea": [
            os.path.join(home, "Games/ea-app/drive_c/Program Files/EA Games"),
            os.path.join(home, "Games/ea-app/drive_c/Program Files (x86)/EA Games"),
        ],
    }
    # Heroic/Lutris commonly give each GOG title its own Wine prefix.
    for prefix in _children(os.path.join(home, "Games/gog")):
        roots["gog"].append(os.path.join(prefix.path, "drive_c/GOG Games"))
    for platform, candidates in roots.items():
        for root in candidates:
            for entry in _children(root, include_symlinks=True):
                inventory.add(entry.name, platform, entry.path, user)


def scan_installed_games(home_root="/home", shared_root="/var/Games", small_install_bytes=DEFAULT_SMALL_INSTALL_BYTES):
    inventory = Inventory(small_install_bytes)
    for entry in _children(home_root):
        user, home = entry.name, entry.path
        _scan_steam_user(inventory, home, user)
        _scan_heroic_user(inventory, home, user)
        _scan_curseforge_user(inventory, home, user)
        _scan_lutris_user(inventory, home, user)
        _scan_common_user_directories(inventory, home, user)
    _scan_shared(inventory, shared_root)
    return inventory.result()
