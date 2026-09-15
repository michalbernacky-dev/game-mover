"""Installed game-launcher discovery and tightly scoped update providers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
import requests
import yaml


GITHUB_API = "https://api.github.com"
HEROIC_REPOSITORY = "Heroic-Games-Launcher/HeroicGamesLauncher"
MAX_RPM_BYTES = 256 * 1024 * 1024
MAX_LOCAL_METADATA_BYTES = 64 * 1024 * 1024
VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:[-+](.*))?$")


class LauncherError(RuntimeError):
    """Safe, user-facing launcher provider failure."""


@dataclass(frozen=True)
class LauncherDefinition:
    id: str
    name: str
    package: str
    executable: str
    icon: str
    source: str
    update_supported: bool = False


LAUNCHERS = (
    LauncherDefinition(
        "heroic", "Heroic Games Launcher", "heroic", "/usr/bin/heroic", "heroic",
        "Oficiální GitHub release", True,
    ),
    LauncherDefinition(
        "lutris", "Lutris", "lutris", "/usr/bin/lutris", "net.lutris.Lutris",
        "Systémový RPM repozitář",
    ),
    LauncherDefinition(
        "steam", "Steam", "steam", "/usr/bin/steam", "steam",
        "Systémový RPM repozitář",
    ),
)


@dataclass(frozen=True)
class ManagedLauncherDefinition:
    id: str
    name: str
    icon: str
    aliases: tuple[str, ...]


MANAGED_LAUNCHERS = (
    ManagedLauncherDefinition(
        "ea-app", "EA App", "lutris_ea-app", ("ea-app", "ea-desktop"),
    ),
    ManagedLauncherDefinition(
        "ubisoft-connect", "Ubisoft Connect", "lutris_ubisoft-connect",
        ("ubisoft-connect", "ubisoft-connect-pc", "uplay", "ubisoft-game-launcher"),
    ),
    ManagedLauncherDefinition(
        "gog-galaxy", "GOG Galaxy", "lutris_gog-galaxy",
        ("gog-galaxy", "gog-galaxy-2", "gog-galaxy-2-0"),
    ),
    ManagedLauncherDefinition(
        "rockstar-games-launcher", "Rockstar Games Launcher",
        "lutris_rockstar-games-launcher", ("rockstar-games-launcher",),
    ),
    ManagedLauncherDefinition(
        "epic-games-launcher", "Epic Games Launcher", "lutris_epic-games-store",
        ("epic-games-launcher", "epic-games-store", "epic-games-store-launcher"),
    ),
    ManagedLauncherDefinition(
        "battle-net", "Battle.net", "lutris_battlenet",
        ("battle-net", "battle-net-launcher", "battlenet", "blizzard-battle-net"),
    ),
    ManagedLauncherDefinition(
        "amazon-games", "Amazon Games", "lutris_amazon-games",
        ("amazon-games", "amazon-games-app"),
    ),
    ManagedLauncherDefinition(
        "origin", "Origin", "lutris_origin", ("origin", "origin-client"),
    ),
    ManagedLauncherDefinition(
        "riot-client", "Riot Client", "lutris_riot-client",
        ("riot-client", "riot-games-client"),
    ),
)
MANAGED_LAUNCHER_BY_ALIAS = {
    alias: definition
    for definition in MANAGED_LAUNCHERS
    for alias in definition.aliases
}


def _run(command, *, timeout=30):
    return subprocess.run(
        command, text=True, capture_output=True, check=False, timeout=timeout,
    )


def _version_key(version):
    match = VERSION_RE.fullmatch(str(version or "").strip())
    if not match:
        return (), (1, str(version or ""))
    numbers = tuple(int(part) for part in match.group(1).split("."))
    suffix = match.group(2)
    # A stable release sorts after a prerelease with the same numeric version.
    return numbers, (1, "") if not suffix else (0, suffix.lower())


def _launcher_slug(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:128]


def _managed_definition(*values):
    for value in values:
        definition = MANAGED_LAUNCHER_BY_ALIAS.get(_launcher_slug(value))
        if definition:
            return definition
    return None


def _safe_local_path(home, value, *, file=False):
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        home = os.path.realpath(home)
        candidate = os.path.abspath(os.path.expanduser(value.strip()))
        resolved = os.path.realpath(candidate)
        if os.path.commonpath((home, resolved)) != home or os.path.islink(candidate):
            return ""
        exists = os.path.isfile(resolved) if file else os.path.isdir(resolved)
        return resolved if exists else ""
    except (OSError, ValueError, TypeError):
        return ""


def _load_local_json(path):
    try:
        metadata = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LOCAL_METADATA_BYTES:
            return None
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _metadata_version(*payloads):
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("installedVersion", "appVersion", "app_version", "version"):
            value = str(payload.get(key) or "").strip()
            if VERSION_RE.fullmatch(value):
                return value.removeprefix("v").removeprefix("V")
    return ""


def _registry_value(line, key):
    match = re.fullmatch(rf'"{re.escape(key)}"="([^"\r\n]*)"', line.strip())
    return match.group(1).replace(r"\\", "\\") if match else ""


def _wine_registry_version(prefix, definition):
    """Read an optional product version without executing anything in Wine."""
    candidates = []
    for root in (os.path.join(prefix, "pfx"), prefix):
        registry = os.path.join(root, "system.reg")
        try:
            metadata = os.stat(registry, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_LOCAL_METADATA_BYTES:
                continue
            current = {}
            with open(registry, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if line.startswith("["):
                        name = current.get("name", "")
                        if _managed_definition(name) == definition:
                            candidates.extend(current.get("versions", ()))
                        current = {"versions": []}
                        continue
                    name = _registry_value(line, "DisplayName")
                    if name:
                        current["name"] = name
                    for key in ("DisplayVersion", "BundleVersion", "Version"):
                        version = _registry_value(line, key)
                        if VERSION_RE.fullmatch(version):
                            current.setdefault("versions", []).append(version)
                name = current.get("name", "")
                if _managed_definition(name) == definition:
                    candidates.extend(current.get("versions", ()))
        except OSError:
            continue
    return max(candidates, key=_version_key) if candidates else ""


def _heroic_managed_launchers(home):
    found = []
    config_roots = (
        os.path.join(home, ".config/heroic"),
        os.path.join(home, ".var/app/com.heroicgameslauncher.hgl/config/heroic"),
    )
    for config_root in config_roots:
        library = _load_local_json(
            os.path.join(config_root, "sideload_apps/library.json"),
        )
        games = library.get("games") if isinstance(library, dict) else None
        if not isinstance(games, list):
            continue
        for item in games:
            if not isinstance(item, dict):
                continue
            app_id = str(
                item.get("app_name") or item.get("appName") or item.get("id") or "",
            )
            definition = _managed_definition(item.get("title"), app_id)
            if (
                not definition
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", app_id)
            ):
                continue
            config = _load_local_json(
                os.path.join(config_root, "GamesConfig", f"{app_id}.json"),
            )
            entry = config.get(app_id) if isinstance(config, dict) else None
            prefix = _safe_local_path(
                home, entry.get("winePrefix") if isinstance(entry, dict) else "",
            )
            install_path = _safe_local_path(
                home, item.get("installPath") or item.get("install_path") or "",
            )
            if item.get("is_installed") is False:
                continue
            if item.get("is_installed") is not True and not prefix and not install_path:
                continue
            version = _metadata_version(item, entry)
            if not version and prefix:
                version = _wine_registry_version(prefix, definition)
            found.append((definition, "Heroic", version))
    return found


def _lutris_managed_launchers(home):
    found = []
    databases = (
        (
            os.path.join(home, ".local/share/lutris/pga.db"),
            os.path.join(home, ".config/lutris/games"),
        ),
        (
            os.path.join(home, ".var/app/net.lutris.Lutris/data/lutris/pga.db"),
            os.path.join(home, ".var/app/net.lutris.Lutris/config/lutris/games"),
        ),
    )
    for database, config_root in databases:
        try:
            metadata = os.stat(database, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_LOCAL_METADATA_BYTES
            ):
                continue
            connection = sqlite3.connect(
                f"file:{database}?mode=ro", uri=True, timeout=1,
            )
            rows = connection.execute(
                "SELECT name, slug, directory, configpath FROM games "
                "WHERE installed = 1",
            ).fetchall()
            connection.close()
        except (OSError, sqlite3.Error):
            continue
        for name, slug, directory, config_path in rows:
            definition = _managed_definition(name, slug)
            if not definition:
                continue
            prefix = ""
            filename = str(config_path or "")
            if filename and os.path.basename(filename) == filename:
                if not filename.endswith((".yml", ".yaml")):
                    filename += ".yml"
                config_file = _safe_local_path(
                    home, os.path.join(config_root, filename), file=True,
                )
                try:
                    if config_file and os.path.getsize(config_file) <= 1024 * 1024:
                        with open(config_file, encoding="utf-8") as stream:
                            config = yaml.safe_load(stream)
                        game = config.get("game") if isinstance(config, dict) else None
                        if isinstance(game, dict):
                            prefix = _safe_local_path(home, game.get("prefix"))
                except (OSError, yaml.YAMLError):
                    pass
            prefix = prefix or _safe_local_path(home, directory)
            version = _wine_registry_version(prefix, definition) if prefix else ""
            found.append((definition, "Lutris", version))
    return found


def managed_launcher_statuses(home=None):
    """Return launchers explicitly installed as Heroic/Lutris library entries."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    merged = {}
    for definition, source, version in (
        *_heroic_managed_launchers(home), *_lutris_managed_launchers(home),
    ):
        item = merged.setdefault(definition.id, {
            "id": f"managed-{definition.id}",
            "name": definition.name,
            "icon": definition.icon,
            "sources": set(),
            "versions": [],
        })
        item["sources"].add(source)
        if version:
            item["versions"].append(version)
    statuses = []
    for item in merged.values():
        versions = item.pop("versions")
        sources = item.pop("sources")
        statuses.append({
            **item,
            "source": " + ".join(sorted(sources)),
            "installed": True,
            "installed_version": max(versions, key=_version_key) if versions else "",
            "latest_version": "",
            "update_available": False,
            "update_supported": False,
            "managed_externally": True,
            "error": "",
        })
    return sorted(statuses, key=lambda item: item["name"].casefold())


def version_is_newer(candidate, installed):
    return _version_key(candidate) > _version_key(installed)


def rpm_installed_version(package, runner=_run):
    try:
        result = runner(["rpm", "-q", "--qf", "%{VERSION}", package])
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def repo_versions(packages, runner=_run):
    package_names = tuple(dict.fromkeys(str(package) for package in packages if package))
    if not package_names:
        return {}
    try:
        result = runner([
            "dnf", "--cacheonly", "--quiet", "repoquery", "--available", "--latest-limit", "1",
            "--qf", "%{name}|%{version}\\n", *package_names,
        ], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    found = {}
    for line in result.stdout.splitlines():
        name, separator, version = line.strip().partition("|")
        if not separator or name not in package_names or not version:
            continue
        if name not in found or version_is_newer(version, found[name]):
            found[name] = version
    return found


def repo_version(package, runner=_run):
    return repo_versions((package,), runner=runner).get(package, "")


def heroic_release(request_get=requests.get):
    try:
        response = request_get(
            f"{GITHUB_API}/repos/{HEROIC_REPOSITORY}/releases/latest",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Game-Mover",
                "Cache-Control": "no-cache",
            },
            timeout=20,
        )
        response.raise_for_status()
        raw = response.json()
    except (requests.RequestException, ValueError, TypeError) as error:
        raise LauncherError("Kontrola vydání Heroicu na GitHubu selhala") from error
    version = str(raw.get("tag_name", "")).removeprefix("v").strip()
    if not VERSION_RE.fullmatch(version):
        raise LauncherError("GitHub vrátil neplatnou verzi Heroicu")
    expected_name = f"Heroic-{version}-linux-x86_64.rpm"
    asset = next((
        item for item in raw.get("assets", [])
        if isinstance(item, dict) and item.get("name") == expected_name
    ), None)
    url = str((asset or {}).get("browser_download_url", ""))
    expected_prefix = (
        f"https://github.com/{HEROIC_REPOSITORY}/releases/download/"
        f"v{version}/"
    )
    if not asset or url != expected_prefix + expected_name:
        raise LauncherError("Oficiální RPM pro tuto verzi Heroicu nebylo nalezeno")
    return {
        "version": version,
        "published_at": str(raw.get("published_at", "")),
        "release_url": str(raw.get("html_url", "")),
        "asset_name": expected_name,
        "download_url": url,
        "digest": str(asset.get("digest") or ""),
        "size": int(asset.get("size") or 0),
    }


def launcher_statuses(*, runner=_run, request_get=requests.get):
    installed_versions = {
        definition.id: rpm_installed_version(definition.package, runner=runner)
        for definition in LAUNCHERS
    }
    installed_flags = {
        definition.id: bool(installed_versions[definition.id])
        or os.path.isfile(definition.executable)
        for definition in LAUNCHERS
    }
    repository_latest = repo_versions((
        definition.package for definition in LAUNCHERS
        if definition.id != "heroic" and installed_flags[definition.id]
    ), runner=runner)
    statuses = []
    for definition in LAUNCHERS:
        installed_version = installed_versions[definition.id]
        installed = installed_flags[definition.id]
        latest_version = ""
        error = ""
        release_url = ""
        if definition.id == "heroic":
            try:
                release = heroic_release(request_get=request_get)
                latest_version = release["version"]
                release_url = release["release_url"]
            except LauncherError as exc:
                error = str(exc)
        elif installed:
            latest_version = repository_latest.get(definition.package, "")
        update_available = bool(
            installed_version and latest_version
            and version_is_newer(latest_version, installed_version)
        )
        statuses.append({
            "id": definition.id,
            "name": definition.name,
            "icon": definition.icon,
            "source": definition.source,
            "installed": installed,
            "installed_version": installed_version,
            "latest_version": latest_version,
            "update_available": update_available,
            "update_supported": definition.update_supported,
            "release_url": release_url,
            "error": error,
        })
    return statuses


def _heroic_is_running(runner):
    result = runner(["pgrep", "-f", "/opt/Heroic/heroic"])
    return result.returncode == 0 and bool(result.stdout.strip())


def _validate_downloaded_rpm(path, version, runner):
    result = runner([
        "rpm", "-qp", "--qf", "%{NAME}\n%{VERSION}\n%{ARCH}\n", path,
    ])
    values = result.stdout.splitlines()
    if result.returncode != 0 or values != ["heroic", version, "x86_64"]:
        raise LauncherError("Stažený soubor neodpovídá očekávanému Heroic RPM")


def update_heroic(*, runner=_run, request_get=requests.get, installer=None):
    installed = rpm_installed_version("heroic", runner=runner)
    if not installed:
        raise LauncherError("Heroic není nainstalovaný jako RPM")
    if _heroic_is_running(runner):
        raise LauncherError("Nejprve ukonči Heroic Games Launcher")
    release = heroic_release(request_get=request_get)
    if not version_is_newer(release["version"], installed):
        return {"changed": False, "version": installed, "message": "Heroic je aktuální"}
    if release["size"] and release["size"] > MAX_RPM_BYTES:
        raise LauncherError("Heroic RPM překračuje povolenou velikost")

    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix="game-mover-heroic-", suffix=".rpm", delete=False,
        ) as output:
            temporary_path = output.name
            try:
                response = request_get(
                    release["download_url"], stream=True, timeout=(20, 180),
                    headers={"User-Agent": "Game-Mover"},
                )
                response.raise_for_status()
                digest = hashlib.sha256()
                total = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_RPM_BYTES:
                        raise LauncherError("Heroic RPM překračuje povolenou velikost")
                    output.write(chunk)
                    digest.update(chunk)
            except requests.RequestException as error:
                raise LauncherError("Stažení Heroic RPM selhalo") from error
        expected_digest = release["digest"]
        if expected_digest.startswith("sha256:"):
            if digest.hexdigest() != expected_digest.removeprefix("sha256:"):
                raise LauncherError("Kontrolní součet Heroic RPM nesouhlasí")
        _validate_downloaded_rpm(temporary_path, release["version"], runner)
        result = (
            installer(temporary_path)
            if installer is not None
            else runner(["dnf", "install", "-y", temporary_path], timeout=900)
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            suffix = f": {detail[-1]}" if detail else ""
            raise LauncherError(f"Instalace Heroic RPM selhala{suffix}")
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
    return {
        "changed": True,
        "version": release["version"],
        "message": f"Heroic byl aktualizován na {release['version']}",
    }


def update_launcher(launcher_id, **kwargs):
    if launcher_id != "heroic":
        raise LauncherError("Tento launcher zatím Game Mover aktualizovat neumí")
    return update_heroic(**kwargs)
