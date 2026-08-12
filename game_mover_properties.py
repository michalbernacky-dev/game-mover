"""Safe, validated access to the editable subset of Minecraft server.properties."""

import os
import re
import tempfile


class MinecraftPropertiesError(ValueError):
    pass


EDITABLE_FIELDS = (
    "motd",
    "level-name",
    "gamemode",
    "difficulty",
    "max-players",
    "white-list",
    "online-mode",
    "pvp",
    "allow-flight",
    "enable-command-block",
    "view-distance",
    "simulation-distance",
)

DEFAULT_VALUES = {
    "motd": "A Minecraft Server",
    "level-name": "world",
    "gamemode": "survival",
    "difficulty": "easy",
    "max-players": "20",
    "white-list": "false",
    "online-mode": "true",
    "pvp": "true",
    "allow-flight": "false",
    "enable-command-block": "false",
    "view-distance": "10",
    "simulation-distance": "10",
}

BOOLEAN_FIELDS = {
    "white-list", "online-mode", "pvp", "allow-flight", "enable-command-block",
}
CHOICE_FIELDS = {
    "gamemode": {"survival", "creative", "adventure", "spectator"},
    "difficulty": {"peaceful", "easy", "normal", "hard"},
}
INTEGER_RANGES = {
    "max-players": (1, 1000),
    "view-distance": (3, 32),
    "simulation-distance": (3, 32),
}
WORLD_NAME_RE = re.compile(r"[A-Za-z0-9_. -]{1,64}")


def properties_path(data_directory: str) -> str:
    root = os.path.realpath(str(data_directory or ""))
    if not root or not os.path.isdir(root):
        raise MinecraftPropertiesError("Datový adresář Minecraft serveru neexistuje")
    path = os.path.join(root, "server.properties")
    try:
        details = os.lstat(path)
    except FileNotFoundError as error:
        raise MinecraftPropertiesError("Soubor server.properties neexistuje") from error
    if os.path.islink(path) or not os.path.isfile(path):
        raise MinecraftPropertiesError("server.properties musí být běžný soubor v datovém adresáři")
    return path


def _parse_lines(content: str) -> tuple[list[str], dict[str, str]]:
    lines = content.splitlines()
    values = {}
    for line in lines:
        if not line or line.lstrip().startswith(("#", "!")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in values:
            values[key] = value
    return lines, values


def read_minecraft_properties(data_directory: str) -> dict:
    path = properties_path(data_directory)
    try:
        with open(path, "r", encoding="utf-8") as stream:
            lines, values = _parse_lines(stream.read())
    except (OSError, UnicodeError) as error:
        raise MinecraftPropertiesError(f"server.properties nelze přečíst: {error}") from error
    return {
        "settings": {key: values.get(key, DEFAULT_VALUES[key]) for key in EDITABLE_FIELDS},
        "effective": values,
        "path": path,
        "line_count": len(lines),
    }


def validate_minecraft_properties(raw_settings) -> dict[str, str]:
    if not isinstance(raw_settings, dict):
        raise MinecraftPropertiesError("Nastavení Minecraftu musí být objekt")
    unknown = set(raw_settings) - set(EDITABLE_FIELDS)
    if unknown:
        raise MinecraftPropertiesError("Nepovolené položky nastavení: " + ", ".join(sorted(unknown)))
    missing = set(EDITABLE_FIELDS) - set(raw_settings)
    if missing:
        raise MinecraftPropertiesError("Chybí položky nastavení: " + ", ".join(sorted(missing)))
    settings = {}
    for key in EDITABLE_FIELDS:
        value = raw_settings[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        elif not isinstance(value, str):
            raise MinecraftPropertiesError(f"{key} má neplatný typ")
        value = value.strip()
        if key in BOOLEAN_FIELDS:
            if value.lower() not in ("true", "false"):
                raise MinecraftPropertiesError(f"{key} musí být true nebo false")
            settings[key] = value.lower()
        elif key in CHOICE_FIELDS:
            if value.lower() not in CHOICE_FIELDS[key]:
                raise MinecraftPropertiesError(f"{key} má nepovolenou hodnotu")
            settings[key] = value.lower()
        elif key in INTEGER_RANGES:
            try:
                numeric = int(value)
            except ValueError as error:
                raise MinecraftPropertiesError(f"{key} musí být celé číslo") from error
            minimum, maximum = INTEGER_RANGES[key]
            if not minimum <= numeric <= maximum:
                raise MinecraftPropertiesError(f"{key} musí být mezi {minimum} a {maximum}")
            settings[key] = str(numeric)
        elif key == "level-name":
            if not WORLD_NAME_RE.fullmatch(value) or value in (".", ".."):
                raise MinecraftPropertiesError("Název světa může obsahovat jen písmena, čísla, mezeru, tečku, pomlčku a podtržítko")
            settings[key] = value
        elif key == "motd":
            if not value or "\n" in value or "\r" in value or len(value) > 120:
                raise MinecraftPropertiesError("MOTD musí mít 1 až 120 znaků a nesmí obsahovat nový řádek")
            settings[key] = value
    return settings


def _render_updated_lines(lines: list[str], settings: dict[str, str]) -> str:
    seen = set()
    rendered = []
    for line in lines:
        if line and not line.lstrip().startswith(("#", "!")) and "=" in line:
            key, _value = line.split("=", 1)
            key = key.strip()
            if key in settings:
                rendered.append(f"{key}={settings[key]}")
                seen.add(key)
                continue
        rendered.append(line)
    if set(settings) - seen:
        if rendered and rendered[-1] != "":
            rendered.append("")
        rendered.extend(f"{key}={settings[key]}" for key in EDITABLE_FIELDS if key not in seen)
    return "\n".join(rendered) + "\n"


def _copy_xattrs(source: str, destination: str) -> None:
    try:
        for name in os.listxattr(source, follow_symlinks=False):
            try:
                os.setxattr(destination, name, os.getxattr(source, name, follow_symlinks=False), follow_symlinks=False)
            except OSError:
                pass
    except (AttributeError, OSError):
        pass


def write_minecraft_properties(data_directory: str, raw_settings) -> dict:
    path = properties_path(data_directory)
    settings = validate_minecraft_properties(raw_settings)
    try:
        with open(path, "r", encoding="utf-8") as stream:
            lines, previous = _parse_lines(stream.read())
        content = _render_updated_lines(lines, settings)
        original = os.stat(path, follow_symlinks=False)
        descriptor, temporary_path = tempfile.mkstemp(prefix=".server.properties.", dir=os.path.dirname(path), text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, original.st_mode & 0o7777)
            os.chown(temporary_path, original.st_uid, original.st_gid)
            _copy_xattrs(path, temporary_path)
            os.replace(temporary_path, path)
            directory_fd = os.open(os.path.dirname(path), os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
    except (OSError, UnicodeError) as error:
        raise MinecraftPropertiesError(f"server.properties nelze bezpečně uložit: {error}") from error
    changed = [key for key in EDITABLE_FIELDS if previous.get(key, DEFAULT_VALUES[key]) != settings[key]]
    return {"settings": settings, "path": path, "changed": changed}
