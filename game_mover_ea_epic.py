#!/usr/bin/env python3
"""Launch Epic-owned EA games through Legendary, UMU and a per-user prefix."""

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
from urllib.parse import urlsplit

from game_mover_ea import EaInstallation, heroic_ea_installations


APP_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
AUTH_RE = re.compile(r"(?i)(AUTH_PASSWORD(?:=|%3D))([^&\s'\"]+)")
SHARED_ROOT = os.getenv("GAME_MOVER_GAMES_ROOT", "/var/Games")
ENV_PREFIX = "GAME_MOVER_EA_EPIC_PREFIX"
ENV_PROTON = "GAME_MOVER_EA_EPIC_PROTON"
ENV_UMU = "GAME_MOVER_EA_EPIC_UMU"
ENV_APP = "GAME_MOVER_EA_EPIC_APP"


@dataclass(frozen=True)
class EaEpicGame:
    id: str
    name: str
    launcher_type: str
    epic_app_name: str
    shared_storage_family: str
    shared_directory: str
    ea_prefix: str
    registry_key: str
    executable: str
    locale: str = "en_US"

    def shared_path(self, shared_root: str = SHARED_ROOT) -> str:
        return os.path.join(
            shared_root, self.shared_storage_family, self.shared_directory,
        )


@dataclass(frozen=True)
class Prerequisite:
    id: str
    ok: bool
    message: str


EA_EPIC_GAMES = (
    EaEpicGame(
        id="star-wars-battlefront-ii-celebration-edition",
        name="STAR WARS Battlefront II: Celebration Edition",
        launcher_type="ea_epic",
        epic_app_name="MtMassive",
        shared_storage_family="EA",
        shared_directory="STAR WARS Battlefront II",
        ea_prefix="EA_app",
        registry_key=r"Software\EA Games\STAR WARS Battlefront II",
        executable="starwarsbattlefrontii.exe",
    ),
)


def redact_sensitive(value: str) -> str:
    """Remove Legendary's short-lived exchange code from diagnostic text."""
    return AUTH_RE.sub(r"\1<redacted>", str(value))


def game_by_app_name(app_name: str) -> EaEpicGame | None:
    return next((game for game in EA_EPIC_GAMES
                 if game.epic_app_name.casefold() == str(app_name).casefold()), None)


def game_metadata_for_name(name: str) -> dict:
    folded = str(name).strip().casefold()
    for game in EA_EPIC_GAMES:
        if folded in {game.name.casefold(), game.shared_directory.casefold()}:
            return {**asdict(game), "shared_data": game.shared_path()}
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
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2 * 1024 * 1024:
            return None
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _legendary_config(home: str) -> str | None:
    return next((
        os.path.join(root, "legendaryConfig/legendary")
        for root in _config_roots(home)
        if os.path.isdir(os.path.join(root, "legendaryConfig/legendary"))
    ), None)


def _heroic_available(home: str) -> bool:
    return bool(
        shutil.which("heroic")
        or os.path.isfile("/opt/Heroic/heroic")
        or os.path.isdir(os.path.join(
            home, ".var/app/com.heroicgameslauncher.hgl",
        ))
    )


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


def _runner_root(home: str, installation: EaInstallation) -> str | None:
    version = installation.wine_version or {}
    if str(version.get("type", "")).casefold() != "proton":
        return None
    binary = version.get("bin") or version.get("path")
    if isinstance(binary, str) and binary:
        binary = os.path.realpath(os.path.expanduser(binary))
        root = os.path.dirname(binary) if os.path.basename(binary) == "proton" else binary
        if os.path.isfile(os.path.join(root, "proton")):
            return root
    name = str(version.get("name") or "").strip()
    for prefix in ("Proton - ", "Proton "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    roots = (
        os.path.join(home, ".config/heroic/tools/proton"),
        os.path.join(home, ".local/share/Steam/compatibilitytools.d"),
        os.path.join(home, ".steam/root/compatibilitytools.d"),
    )
    return next((os.path.realpath(os.path.join(root, name)) for root in roots
                 if os.path.isfile(os.path.join(root, name, "proton"))), None)


def _select_installation(home: str, game: EaEpicGame) -> EaInstallation | None:
    installations = heroic_ea_installations(home)
    expected = game.ea_prefix.replace("_", " ").casefold()
    matching = [item for item in installations if
                os.path.basename(item.prefix).replace("_", " ").casefold() == expected]
    if len(matching) == 1:
        return matching[0]
    return installations[0] if len(installations) == 1 else None


def prerequisite_report(
    app_name: str, *, home: str | None = None, shared_root: str = SHARED_ROOT,
) -> dict:
    """Return bounded checks only; never return credentials or exchange codes."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    game = game_by_app_name(app_name)
    if not game or not APP_NAME_RE.fullmatch(str(app_name)):
        raise ValueError("Unsupported EA/Epic app name")
    config = _legendary_config(home)
    user_data = _regular_json(os.path.join(config, "user.json")) if config else None
    authenticated = isinstance(user_data, dict) and bool(
        user_data.get("account_id") and user_data.get("refresh_token")
    )
    installation = _select_installation(home, game)
    wine_root = installation.wine_root if installation else ""
    start_exe = os.path.join(
        wine_root, "drive_c/windows/system32/start.exe",
    ) if wine_root else ""
    umu = _umu_executable(home)
    proton = _runner_root(home, installation) if installation else None
    legendary = _legendary_executable()
    shared = game.shared_path(shared_root)
    source = os.path.join(installation.games_root, game.shared_directory) if installation else ""
    shared_exists = os.path.isdir(shared)
    source_exists = os.path.isdir(source)
    correct_link = bool(
        shared_exists and os.path.islink(source)
        and os.path.realpath(source) == os.path.realpath(shared)
    )
    checks = (
        Prerequisite("heroic", _heroic_available(home),
                     "Heroic Games Launcher is available." if _heroic_available(home)
                     else "Heroic Games Launcher is not installed for this player."),
        Prerequisite("legendary_config", bool(config),
                     "Heroic Legendary config was found." if config
                     else "Heroic Legendary config is missing for this player."),
        Prerequisite("legendary", bool(legendary),
                     "Legendary is available." if legendary
                     else "Legendary is not available on PATH or in Heroic."),
        Prerequisite("epic_login", authenticated,
                     "The player's Heroic Epic login is configured." if authenticated
                     else "Sign in to Epic in Heroic for this player first."),
        Prerequisite("ea_prefix", bool(installation),
                     "A dedicated Heroic EA App prefix was found." if installation
                     else "EA App is not configured for this player. Install EA App in Heroic using a dedicated prefix first."),
        Prerequisite("ea_app", bool(installation),
                     "EA App is installed in the dedicated prefix." if installation
                     else "EA App is not installed in a discoverable dedicated Heroic prefix."),
        Prerequisite("start_exe", bool(start_exe and os.path.isfile(start_exe)),
                     "The prefix contains Windows start.exe." if start_exe and os.path.isfile(start_exe)
                     else "The EA App prefix does not contain a usable Windows start.exe."),
        Prerequisite("umu", bool(umu),
                     "UMU is available." if umu else "UMU is missing; install it through Heroic or Lutris."),
        Prerequisite("proton", bool(proton),
                     "The Proton runner configured for the EA App prefix is available." if proton
                     else "The EA App prefix has no usable Proton runner in Heroic metadata."),
        Prerequisite("shared_data", shared_exists or source_exists,
                     "Shared game data exists." if shared_exists
                     else ("Game data can be moved to shared storage with Game Mover."
                           if source_exists else "Battlefront II game data is missing.")),
        Prerequisite("shared_link", correct_link,
                     "The EA prefix links to the shared game data." if correct_link
                     else "Link this player's EA game directory to shared storage with Game Mover."),
    )
    return {
        "game": {**asdict(game), "shared_data": shared},
        "ready": all(check.ok for check in checks),
        "checks": [asdict(check) for check in checks],
        "runtime": {
            "prefix": installation.prefix if installation else None,
            "umu": umu,
            "proton": proton,
            "legendary": legendary,
        },
    }


def _windows_game_path(installation: EaInstallation, game: EaEpicGame) -> str:
    drive_c = os.path.realpath(os.path.join(installation.wine_root, "drive_c"))
    games_root = os.path.realpath(installation.games_root)
    try:
        if os.path.commonpath((drive_c, games_root)) != drive_c:
            raise RuntimeError("The EA game directory is outside drive_c")
        relative = os.path.join(
            os.path.relpath(games_root, drive_c), game.shared_directory,
        )
    except ValueError as error:
        raise RuntimeError("The EA game directory is outside drive_c") from error
    return "C:\\" + relative.replace(os.sep, "\\")


def _ea_registration_present(
    installation: EaInstallation, game: EaEpicGame, windows_path: str,
) -> bool:
    """Read Wine's machine registry without starting another Wine process."""
    executable = os.path.join(
        installation.games_root, game.shared_directory, game.executable,
    )
    if not os.path.isfile(executable):
        return False
    registry = os.path.join(installation.wine_root, "system.reg")
    try:
        metadata = os.stat(registry, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024 * 1024:
            return False
        with open(registry, encoding="utf-8", errors="replace") as stream:
            contents = stream.read()
    except OSError:
        return False
    section = "[" + game.registry_key.replace("\\", "\\\\") + "]"
    install_dir = windows_path.rstrip("\\") + "\\"
    value = '"Install Dir"="' + install_dir.replace("\\", "\\\\") + '"'
    return section in contents and value in contents


def _ensure_ea_registration(
    installation: EaInstallation, game: EaEpicGame, proton: str, umu: str,
    *, run=None,
) -> bool:
    """Run the game's own EA installer helper when its install key is absent."""
    windows_path = _windows_game_path(installation, game)
    if _ea_registration_present(installation, game, windows_path):
        return False
    touchup = os.path.realpath(os.path.join(
        installation.games_root, game.shared_directory, "__Installer/Touchup.exe",
    ))
    if not os.path.isfile(touchup):
        raise RuntimeError(f"{game.name} Touchup.exe is missing from the game data")
    run = run or subprocess.run
    environment = os.environ.copy()
    environment.update({
        "WINEPREFIX": installation.prefix,
        "PROTONPATH": proton,
        "GAMEID": f"umu-{game.epic_app_name}",
        "STORE": "egs",
    })
    run(
        [umu, touchup, "install", "-locale", game.locale,
         "-installPath", windows_path, "-autologging"],
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=True,
    )
    return True


def _helper_path() -> str:
    override = os.getenv("GAME_MOVER_EA_EPIC_HELPER")
    if override:
        return override
    installed = "/usr/bin/game-mover-ea-epic"
    return installed if os.path.isfile(installed) else os.path.realpath(__file__)


def launch(
    app_name: str, *, home: str | None = None, shared_root: str = SHARED_ROOT,
    popen=subprocess.Popen,
) -> int:
    home = os.path.realpath(home or os.path.expanduser("~"))
    report = prerequisite_report(app_name, home=home, shared_root=shared_root)
    failed = [check["message"] for check in report["checks"] if not check["ok"]]
    legendary = report["runtime"]["legendary"]
    if failed:
        raise RuntimeError("\n".join(dict.fromkeys(failed)))
    game = game_by_app_name(app_name)
    installation = _select_installation(home, game) if game else None
    if not game or not installation:
        raise RuntimeError("The EA App installation could not be resolved")
    _ensure_ea_registration(
        installation, game, report["runtime"]["proton"], report["runtime"]["umu"],
    )
    environment = os.environ.copy()
    environment.update({
        "LEGENDARY_CONFIG_PATH": _legendary_config(home),
        ENV_PREFIX: report["runtime"]["prefix"],
        ENV_PROTON: report["runtime"]["proton"],
        ENV_UMU: report["runtime"]["umu"],
        ENV_APP: app_name,
    })
    process = popen(
        [legendary, "launch", app_name, "--origin", "--wine", _helper_path(),
         "--wine-prefix", report["runtime"]["prefix"]],
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    return process.pid


def _safe_link2ea_url(value: str, app_name: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme.casefold() == "link2ea"
        and parsed.netloc.casefold() == "launchgame"
        and parsed.path.strip("/").casefold() == app_name.casefold()
    )


def run_wrapper(arguments: list[str], *, execve=os.execve) -> int:
    if arguments and arguments[0] == "start":
        arguments = arguments[1:]
    app_name = os.environ.get(ENV_APP, "")
    prefix = os.environ.get(ENV_PREFIX, "")
    proton = os.environ.get(ENV_PROTON, "")
    umu = os.environ.get(ENV_UMU, "")
    if (
        not APP_NAME_RE.fullmatch(app_name) or not arguments
        or not _safe_link2ea_url(arguments[0], app_name)
        or not os.path.isdir(prefix)
        or not os.path.isfile(os.path.join(proton, "proton"))
        or not os.path.isfile(umu) or not os.access(umu, os.X_OK)
    ):
        raise RuntimeError("Invalid or incomplete EA/Epic wrapper context")
    start_exe = os.path.join(prefix, "pfx/drive_c/windows/system32/start.exe")
    if not os.path.isfile(start_exe):
        start_exe = os.path.join(prefix, "drive_c/windows/system32/start.exe")
    if not os.path.isfile(start_exe):
        raise RuntimeError("The EA App prefix does not contain start.exe")
    environment = os.environ.copy()
    environment.update({
        "WINEPREFIX": prefix, "PROTONPATH": proton,
        "GAMEID": f"umu-{app_name}", "STORE": "egs",
    })
    execve(umu, [umu, start_exe, *arguments], environment)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "start":
        try:
            return run_wrapper(argv)
        except (OSError, RuntimeError, ValueError) as error:
            print(redact_sensitive(str(error)), file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(
        description="Check or launch an Epic-owned EA game for the current user.",
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
        print(redact_sensitive(str(error)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
