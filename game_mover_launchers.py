"""Installed game-launcher discovery and tightly scoped update providers."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
import requests


GITHUB_API = "https://api.github.com"
HEROIC_REPOSITORY = "Heroic-Games-Launcher/HeroicGamesLauncher"
MAX_RPM_BYTES = 256 * 1024 * 1024
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


def update_heroic(*, runner=_run, request_get=requests.get):
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
        result = runner(["dnf", "install", "-y", temporary_path], timeout=900)
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
