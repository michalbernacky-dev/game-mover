"""Local installed-game inventory used by the knowledge-base catalog."""

import json
import os
import re
import shutil
import sqlite3
import unicodedata
from pathlib import Path

import yaml

from game_mover_ea import ea_game_install_in_progress, heroic_ea_installations
from game_mover_ea_epic import game_by_app_name, game_metadata_for_name
from game_mover_game_filters import is_excluded_game


DEFAULT_SMALL_INSTALL_BYTES = 1024 * 1024 * 1024
LAUNCHER_SLUGS = frozenset({
    "bethesda-launcer", "bethesda-launcher", "ea-app", "epic-games-store",
    "epicgame-store", "gog-galaxy", "heroic-games-launcher", "launcher",
    "multimc", "origin", "plarium-launcher", "rockstar-games-launcher",
    "social-club", "ubisoft-connect", "zbrush",
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
    try:
        if os.path.isfile(path):
            return os.stat(path, follow_symlinks=False).st_size
    except OSError:
        return 0
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

    def add(
        self, name, platform, path, user=None, app_id=None, verified=False,
        aliases=None, possible_residue=False,
    ):
        name = str(name).strip()
        path = os.path.realpath(str(path)) if path else ""
        metadata = game_metadata_for_name(name)
        app_game = game_by_app_name(app_id) if app_id else None
        if app_game:
            metadata = game_metadata_for_name(app_game.name)
        if metadata:
            name = metadata["name"]
        slug = metadata.get("id") or game_slug(name)
        if (
            not name or slug in LAUNCHER_SLUGS
            or is_excluded_game(platform, name)
            or is_excluded_game(platform, os.path.basename(path))
            or not path or not os.path.exists(path)
        ):
            return
        item = self.items.setdefault(slug, {
            "id": slug, "name": name, "platforms": set(), "users": set(),
            "paths": set(), "app_ids": set(),
            "knowledge_aliases": set(knowledge_aliases(slug)),
            "verified": False,
            "possible_residue": False,
            "_metadata": {},
        })
        item["_metadata"].update(metadata)
        item["platforms"].add(str(platform).lower())
        item["paths"].add(path)
        if user:
            item["users"].add(str(user))
        if app_id:
            item["app_ids"].add(str(app_id))
        for alias in aliases or ():
            item["knowledge_aliases"].add(game_slug(alias))
        item["verified"] = item["verified"] or bool(verified)
        item["possible_residue"] = (
            item["possible_residue"] or bool(possible_residue)
        )

    def result(self):
        rows = []
        for item in self.items.values():
            size = 0
            for path in item["paths"]:
                real = os.path.realpath(path)
                if real not in self.path_sizes:
                    self.path_sizes[real] = directory_size(real)
                size += self.path_sizes[real]
            rows.append({
                **item,
                **item["_metadata"],
                "platforms": sorted(item["platforms"]),
                "users": sorted(item["users"]),
                "paths": sorted(item["paths"]),
                "app_ids": sorted(item["app_ids"]),
                "knowledge_aliases": sorted(item["knowledge_aliases"]),
                "size_bytes": size,
                "possible_residue": not item["verified"] and (
                    item["possible_residue"] or size <= self.small_install_bytes
                ),
            })
            rows[-1].pop("verified", None)
            rows[-1].pop("_metadata", None)
        return sorted(rows, key=lambda row: row["name"].casefold())


def _scan_shared(inventory, shared_root):
    roots = {
        "steam": ("steam",), "heroic": ("Heroic",), "epic": ("EpicGames", "Epic"),
        "gog": ("gog",), "ubisoft": ("ubisoft", "Ubisoft"),
        "rockstar": ("rockstar", "Rockstar"),
        "ea": ("EA", "ea"),
    }
    for platform, names in roots.items():
        for root_name in names:
            for entry in _children(os.path.join(shared_root, root_name)):
                real = os.path.realpath(entry.path)
                if any(real in item["paths"] for item in inventory.items.values()):
                    continue
                inventory.add(
                    entry.name, platform, entry.path, verified=False,
                    possible_residue=True,
                )


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
                inventory.add(
                    entry.name, "steam", real, user, possible_residue=True,
                )
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
                        user, app_id.group(1) if app_id else None, verified=True,
                    )
        except OSError:
            continue


def _scan_heroic_user(inventory, home, user):
    heroic = os.path.join(home, ".config/heroic")
    legendary = _load_json(os.path.join(heroic, "legendaryConfig/legendary/installed.json"))
    if isinstance(legendary, dict):
        for item in legendary.values():
            if isinstance(item, dict):
                inventory.add(item.get("title"), "epic", item.get("install_path"), user, item.get("app_name"), verified=True)
    gog = _load_json(os.path.join(heroic, "gog_store/installed.json"))
    if isinstance(gog, dict):
        for item in gog.get("installed", []):
            if isinstance(item, dict):
                path = item.get("install_path", "")
                inventory.add(os.path.basename(path), "gog", path, user, item.get("appName"), verified=True)
    sideload = _load_json(os.path.join(heroic, "sideload_apps/library.json"))
    if isinstance(sideload, dict):
        for item in sideload.get("games", []):
            if isinstance(item, dict):
                path = item.get("installPath") or item.get("install_path")
                inventory.add(item.get("title") or item.get("app_name") or os.path.basename(path or ""), "heroic", path, user, verified=True)


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
            inventory.add(name or entry.name, "curseforge", real, user, verified=True)
            seen.add(real)


def _lutris_configuration(home, config_path):
    if not config_path:
        return {}
    filename = str(config_path)
    if not filename.endswith((".yml", ".yaml")):
        filename += ".yml"
    try:
        with open(os.path.join(home, ".config/lutris/games", filename), encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
            return payload if isinstance(payload, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _lutris_install_path(home, runner, directory, configuration):
    game = configuration.get("game", {})
    if not isinstance(game, dict):
        game = {}

    def expanded(value):
        if not isinstance(value, str) or not value.strip():
            return ""
        return os.path.abspath(os.path.expanduser(value.strip()))

    prefix = expanded(game.get("prefix"))
    if prefix and os.path.exists(prefix):
        return prefix
    main_file = expanded(game.get("main_file"))
    if main_file and os.path.exists(main_file):
        return os.path.dirname(main_file)
    game_path = expanded(game.get("path"))
    if game_path and os.path.exists(game_path):
        return game_path
    app_id = str(game.get("appid", "")).strip()
    if runner == "linux" and app_id:
        executable = shutil.which(app_id)
        if executable:
            return executable
    executable = expanded(game.get("exe"))
    if executable and os.path.exists(executable):
        return executable if runner == "linux" else os.path.dirname(executable)
    directory = expanded(directory)
    return directory if directory and os.path.exists(directory) else ""


def _scan_lutris_user(inventory, home, user):
    database = os.path.join(home, ".local/share/lutris/pga.db")
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1)
        try:
            rows = connection.execute(
                "SELECT name, slug, runner, directory, service, service_id, configpath "
                "FROM games WHERE installed = 1"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = [(*row, None) for row in connection.execute(
                "SELECT name, slug, runner, directory, service, service_id FROM games "
                "WHERE installed = 1"
            ).fetchall()]
        connection.close()
    except sqlite3.Error:
        return
    steam_common = os.path.join(home, ".local/share/Steam/steamapps/common")
    for name, slug, runner, directory, service, service_id, config_path in rows:
        if game_slug(slug or name) in LAUNCHER_SLUGS:
            continue
        platform = service or runner or "lutris"
        configuration = _lutris_configuration(home, config_path)
        path = _lutris_install_path(home, runner, directory, configuration)
        if platform == "steam" and not path:
            # Steam manifests/shared scanning will normally supply the precise path.
            path = os.path.join(steam_common, name)
        inventory.add(
            name, platform, path, user, service_id, verified=True,
            aliases=[slug] if slug else None,
        )


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
    ea_installations = heroic_ea_installations(home, allow_shared_default=True)
    roots["ea"].extend(str(installation.games_root) for installation in ea_installations)
    # Heroic/Lutris commonly give each GOG title its own Wine prefix.
    for prefix in _children(os.path.join(home, "Games/gog")):
        roots["gog"].append(os.path.join(prefix.path, "drive_c/GOG Games"))
    for platform, candidates in roots.items():
        for root in candidates:
            for entry in _children(root, include_symlinks=True):
                incomplete = False
                if platform == "ea":
                    incomplete = any(
                        os.path.realpath(installation.games_root)
                        == os.path.realpath(root)
                        and ea_game_install_in_progress(installation, entry.name)
                        for installation in ea_installations
                    )
                inventory.add(
                    entry.name, platform, entry.path, user,
                    verified=platform == "ea" and not incomplete,
                    possible_residue=incomplete,
                )


def _scan_user(inventory, home, user):
    _scan_steam_user(inventory, home, user)
    _scan_heroic_user(inventory, home, user)
    _scan_curseforge_user(inventory, home, user)
    _scan_lutris_user(inventory, home, user)
    _scan_common_user_directories(inventory, home, user)


def scan_user_installed_games(
    home, user, shared_root="/var/Games",
    small_install_bytes=DEFAULT_SMALL_INSTALL_BYTES,
):
    """Scan only the current player's private home plus shared game data."""
    inventory = Inventory(small_install_bytes)
    _scan_user(inventory, os.path.realpath(home), user)
    _scan_shared(inventory, shared_root)
    return inventory.result()


def scan_installed_games(home_root="/home", shared_root="/var/Games", small_install_bytes=DEFAULT_SMALL_INSTALL_BYTES):
    inventory = Inventory(small_install_bytes)
    for entry in _children(home_root):
        user, home = entry.name, entry.path
        _scan_user(inventory, home, user)
    _scan_shared(inventory, shared_root)
    return inventory.result()
