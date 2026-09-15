#!/usr/bin/env python3
"""Launch selected Epic-owned games through Rockstar's launcher."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import re
import shutil
import stat
import subprocess
import sys

import psutil


APP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SHARED_ROOT = os.getenv("GAME_MOVER_GAMES_ROOT", "/var/Games")
ENV_APP = "GAME_MOVER_ROCKSTAR_EPIC_APP"
ENV_GAME_ROOT = "GAME_MOVER_ROCKSTAR_EPIC_GAME_ROOT"
ENV_PREFIX = "GAME_MOVER_ROCKSTAR_EPIC_PREFIX"
ENV_PROTON = "GAME_MOVER_ROCKSTAR_EPIC_PROTON"
ENV_UMU = "GAME_MOVER_ROCKSTAR_EPIC_UMU"
MAX_METADATA_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class RockstarEpicGame:
    id: str
    name: str
    epic_app_name: str
    executable: str
    epic_launcher: str


@dataclass(frozen=True)
class HeroicInstallation:
    config_root: str
    prefix: str
    game_root: str
    proton: str


@dataclass(frozen=True)
class Prerequisite:
    id: str
    ok: bool
    message: str


ROCKSTAR_EPIC_GAMES = (
    RockstarEpicGame(
        id="grand-theft-auto-v-enhanced",
        name="Grand Theft Auto V Enhanced",
        epic_app_name="8769e24080ea413b8ebca3f1b8c50951",
        executable="PlayGTAV.exe",
        epic_launcher="EpicGamesLauncher.exe",
    ),
)


def game_by_app_name(app_name: str) -> RockstarEpicGame | None:
    return next((game for game in ROCKSTAR_EPIC_GAMES
                 if game.epic_app_name.casefold() == str(app_name).casefold()), None)


def game_metadata_for_name(name: str) -> dict:
    folded = str(name).strip().casefold()
    for game in ROCKSTAR_EPIC_GAMES:
        if folded == game.name.casefold():
            return {
                **asdict(game),
                "launcher_type": "rockstar_epic",
            }
    return {}


def _config_roots(home: str) -> tuple[str, ...]:
    return (
        os.path.join(home, ".config/heroic"),
        os.path.join(
            home, ".var/app/com.heroicgameslauncher.hgl/config/heroic",
        ),
    )


def _regular_json(path: str):
    try:
        metadata = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_METADATA_BYTES
        ):
            return None
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _safe_directory(value: str, roots: tuple[str, ...]) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        candidate = os.path.realpath(os.path.expanduser(value.strip()))
        if not os.path.isdir(candidate):
            return ""
        for root in roots:
            root = os.path.realpath(root)
            if os.path.commonpath((root, candidate)) == root:
                return candidate
    except (OSError, TypeError, ValueError):
        pass
    return ""


def _legendary_executable() -> str | None:
    candidates = (
        shutil.which("legendary"),
        "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
        "/opt/Heroic/resources/app.asar.unpacked/build/bin/linux/legendary",
    )
    return next((os.path.realpath(path) for path in candidates
                 if path and os.path.isfile(path) and os.access(path, os.X_OK)), None)


def _umu_executable(home: str) -> str | None:
    candidates = (
        os.path.join(home, ".config/heroic/tools/runtimes/umu/umu-run"),
        os.path.join(home, ".local/share/lutris/runtime/umu/umu-run"),
        shutil.which("umu-run"),
    )
    return next((os.path.realpath(path) for path in candidates
                 if path and os.path.isfile(path) and os.access(path, os.X_OK)), None)


def _proton_runner(home: str, wine_version) -> str:
    if not isinstance(wine_version, dict):
        return ""
    if str(wine_version.get("type", "")).casefold() != "proton":
        return ""
    binary = wine_version.get("bin") or wine_version.get("path")
    if isinstance(binary, str) and binary:
        binary = os.path.realpath(os.path.expanduser(binary))
        root = os.path.dirname(binary) if os.path.basename(binary) == "proton" else binary
        if os.path.isfile(os.path.join(root, "proton")):
            return root
    name = str(wine_version.get("name") or "").strip()
    for label in ("Proton - ", "Proton "):
        if name.startswith(label):
            name = name[len(label):]
            break
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return ""
    roots = (
        os.path.join(home, ".config/heroic/tools/proton"),
        os.path.join(home, ".local/share/Steam/compatibilitytools.d"),
        os.path.join(home, ".steam/root/compatibilitytools.d"),
    )
    return next((os.path.realpath(os.path.join(root, name)) for root in roots
                 if os.path.isfile(os.path.join(root, name, "proton"))), "")


def _installation(
    home: str, game: RockstarEpicGame, shared_root: str,
) -> HeroicInstallation | None:
    for config_root in _config_roots(home):
        legendary_root = os.path.join(config_root, "legendaryConfig/legendary")
        installed = _regular_json(os.path.join(legendary_root, "installed.json"))
        item = installed.get(game.epic_app_name) if isinstance(installed, dict) else None
        config = _regular_json(os.path.join(
            config_root, "GamesConfig", f"{game.epic_app_name}.json",
        ))
        options = config.get(game.epic_app_name) if isinstance(config, dict) else None
        if not isinstance(item, dict) or not isinstance(options, dict):
            continue
        if str(item.get("app_name", "")) != game.epic_app_name:
            continue
        game_root = _safe_directory(
            item.get("install_path", ""), (home, shared_root),
        )
        prefix = _safe_directory(options.get("winePrefix", ""), (home,))
        proton = _proton_runner(home, options.get("wineVersion"))
        if game_root and prefix:
            return HeroicInstallation(config_root, prefix, game_root, proton)
    return None


def _legendary_authenticated(installation: HeroicInstallation | None) -> bool:
    if not installation:
        return False
    user = _regular_json(os.path.join(
        installation.config_root, "legendaryConfig/legendary/user.json",
    ))
    return isinstance(user, dict) and bool(
        user.get("account_id") and user.get("refresh_token")
    )


def _wine_root(prefix: str) -> str:
    proton = os.path.join(prefix, "pfx")
    candidate = os.path.realpath(proton) if os.path.isdir(proton) else os.path.realpath(prefix)
    prefix = os.path.realpath(prefix)
    if os.path.commonpath((prefix, candidate)) != prefix:
        raise RuntimeError("The Proton pfx resolves outside the Heroic prefix")
    return candidate


def prerequisite_report(
    app_name: str, *, home: str | None = None, shared_root: str = SHARED_ROOT,
) -> dict:
    """Return local checks without exposing credentials."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    game = game_by_app_name(app_name)
    if not game or not APP_NAME_RE.fullmatch(str(app_name)):
        raise ValueError("Unsupported Rockstar/Epic app name")
    installation = _installation(home, game, shared_root)
    legendary = _legendary_executable()
    umu = _umu_executable(home)
    game_executable = os.path.join(
        installation.game_root, game.executable,
    ) if installation else ""
    epic_launcher = os.path.join(
        installation.game_root, game.epic_launcher,
    ) if installation else ""
    rockstar_launcher = os.path.join(
        _wine_root(installation.prefix) if installation else "",
        "drive_c/Program Files/Rockstar Games/Launcher/Launcher.exe",
    )
    authenticated = _legendary_authenticated(installation)
    checks = (
        Prerequisite(
            "heroic_install", bool(installation),
            "Heroic GTA installation and its private prefix were found."
            if installation else "Install GTA V Enhanced through Heroic first.",
        ),
        Prerequisite(
            "legendary", bool(legendary),
            "Legendary is available." if legendary
            else "Legendary is not available on PATH or in Heroic.",
        ),
        Prerequisite(
            "epic_login", authenticated,
            "The player's Heroic Epic login is configured."
            if authenticated
            else "Sign in to Epic in Heroic for this player first.",
        ),
        Prerequisite(
            "game_executable", os.path.isfile(game_executable),
            "PlayGTAV.exe is available." if os.path.isfile(game_executable)
            else "The GTA installation is incomplete: PlayGTAV.exe is missing.",
        ),
        Prerequisite(
            "epic_launcher", os.path.isfile(epic_launcher),
            "The GTA Epic launcher shim is available."
            if os.path.isfile(epic_launcher)
            else "The GTA installation is incomplete: EpicGamesLauncher.exe is missing.",
        ),
        Prerequisite(
            "rockstar_launcher", os.path.isfile(rockstar_launcher),
            "Rockstar Games Launcher is installed in this prefix."
            if os.path.isfile(rockstar_launcher)
            else "Rockstar Games Launcher is missing from the GTA prefix.",
        ),
        Prerequisite(
            "umu", bool(umu), "UMU is available." if umu
            else "UMU is missing; install it through Heroic or Lutris.",
        ),
        Prerequisite(
            "proton", bool(installation and installation.proton),
            "The configured Proton runner is available."
            if installation and installation.proton
            else "The GTA prefix has no usable Proton runner in Heroic metadata.",
        ),
    )
    return {
        "game": {**asdict(game), "launcher_type": "rockstar_epic"},
        "ready": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
        "runtime": {
            "legendary": legendary,
            "umu": umu,
            "proton": installation.proton if installation else None,
        },
    }


def _rockstar_running() -> bool:
    markers = (
        "launcher.exe", "rockstarservice.exe", "socialclubhelper.exe",
        "playgtav.exe", "gta5_enhanced.exe",
    )
    uid = os.getuid()
    for process in psutil.process_iter(["name", "cmdline", "uids"]):
        try:
            uids = process.info.get("uids")
            if not uids or uids.real != uid:
                continue
            command = " ".join([
                str(process.info.get("name") or ""),
                *(process.info.get("cmdline") or ()),
            ]).casefold()
            if any(marker in command for marker in markers):
                return True
        except (psutil.AccessDenied, psutil.NoSuchProcess, TypeError):
            continue
    return False


def _quarantine_titles(prefix: str) -> bool:
    titles = os.path.join(
        _wine_root(prefix),
        "drive_c/ProgramData/Rockstar Games/Launcher/titles.dat",
    )
    backup = f"{titles}.game-mover-disabled"
    try:
        metadata = os.stat(titles, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_METADATA_BYTES:
        raise RuntimeError("Rockstar titles.dat is not a safe regular metadata file")
    try:
        backup_metadata = os.stat(backup, follow_symlinks=False)
        if not stat.S_ISREG(backup_metadata.st_mode):
            raise RuntimeError("Rockstar titles.dat backup path is unsafe")
    except FileNotFoundError:
        pass
    os.replace(titles, backup)
    return True


def _helper_path() -> str:
    override = os.getenv("GAME_MOVER_ROCKSTAR_EPIC_HELPER")
    if override:
        return override
    installed = "/usr/bin/game-mover-rockstar-epic"
    return installed if os.path.isfile(installed) else os.path.realpath(__file__)


def launch(
    app_name: str, *, home: str | None = None, shared_root: str = SHARED_ROOT,
    popen=subprocess.Popen,
) -> int:
    home = os.path.realpath(home or os.path.expanduser("~"))
    report = prerequisite_report(app_name, home=home, shared_root=shared_root)
    if not report["ready"]:
        missing = "; ".join(
            check["message"] for check in report["checks"] if not check["ok"]
        )
        raise RuntimeError(missing)
    game = game_by_app_name(app_name)
    installation = _installation(home, game, shared_root)
    if not installation:
        raise RuntimeError("The Heroic GTA installation could not be resolved")
    if _rockstar_running():
        raise RuntimeError("Close GTA and Rockstar Games Launcher before launching")
    _quarantine_titles(installation.prefix)
    legendary_config = os.path.join(
        installation.config_root, "legendaryConfig/legendary",
    )
    environment = os.environ.copy()
    environment.update({
        "LEGENDARY_CONFIG_PATH": legendary_config,
        ENV_APP: app_name,
        ENV_GAME_ROOT: installation.game_root,
        ENV_PREFIX: installation.prefix,
        ENV_PROTON: installation.proton,
        ENV_UMU: report["runtime"]["umu"],
    })
    process = popen(
        [report["runtime"]["legendary"], "launch", app_name,
         "--wine", _helper_path(), "--wine-prefix", installation.prefix],
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    return process.pid


def run_wrapper(arguments: list[str], *, execve=os.execve, chdir=os.chdir) -> int:
    app_name = os.environ.get(ENV_APP, "")
    game_root = os.path.realpath(os.environ.get(ENV_GAME_ROOT, ""))
    prefix = os.path.realpath(os.environ.get(ENV_PREFIX, ""))
    proton = os.path.realpath(os.environ.get(ENV_PROTON, ""))
    umu = os.path.realpath(os.environ.get(ENV_UMU, ""))
    game = game_by_app_name(app_name)
    expected = os.path.realpath(os.path.join(game_root, game.executable)) if game else ""
    epic_launcher = os.path.realpath(
        os.path.join(game_root, game.epic_launcher),
    ) if game else ""
    supplied = os.path.realpath(arguments[0]) if arguments else ""
    if (
        not game or supplied != expected or not os.path.isfile(expected)
        or not os.path.isfile(epic_launcher) or not os.path.isdir(prefix)
        or not os.path.isfile(os.path.join(proton, "proton"))
        or not os.path.isfile(umu) or not os.access(umu, os.X_OK)
    ):
        raise RuntimeError("Invalid or incomplete Rockstar/Epic wrapper context")
    environment = os.environ.copy()
    environment.update({
        "WINEPREFIX": prefix, "PROTONPATH": proton,
        "GAMEID": f"umu-{app_name}", "STORE": "egs",
    })
    chdir(game_root)
    execve(
        umu, [umu, epic_launcher, expected, *arguments[1:]], environment,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get(ENV_APP) and argv and argv[0] not in ("check", "launch"):
        try:
            return run_wrapper(argv)
        except (OSError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(
        description="Check or launch an Epic-owned Rockstar game for this user.",
    )
    parser.add_argument("action", choices=("check", "launch"))
    parser.add_argument("app_name")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    try:
        report = prerequisite_report(options.app_name)
        if options.action == "check":
            if options.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                for check in report["checks"]:
                    print(f"{'OK' if check['ok'] else 'MISSING'}: {check['message']}")
            return 0 if report["ready"] else 2
        launch(options.app_name)
        print(f"Launch requested for {options.app_name}.")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
