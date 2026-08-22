"""Safe, local-only evaluation of knowledge-base checks.

Check definitions may come from a remote Game Mover host, so this module never
executes commands and never returns file contents.  It only reports a boolean
result and a short, predefined/local-path description.
"""

import json
import os
import re
from pathlib import Path


# CurseForge instance metadata legitimately grows above 3 MiB.  Keep the read
# bounded, but large enough for its current aggregate/instance JSON files.
MAX_READ_BYTES = 8 * 1024 * 1024


def _expand(path):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(str(path))))


def _read_text(path):
    path = _expand(path)
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_READ_BYTES:
        return None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _json_value(path, keys):
    text = _read_text(path)
    if text is None:
        raise ValueError("soubor není dostupný")
    value = json.loads(text)
    for key in keys:
        # CurseForge ukládá některé vnořené objekty jako JSON řetězec.
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, list):
            value = value[int(key)]
        else:
            value = value[str(key)]
    return value


def steam_library_roots():
    """Return every Steam library found in libraryfolders.vdf."""
    homes = [
        _expand("~/.local/share/Steam"),
        _expand("~/.steam/steam"),
        _expand("~/.var/app/com.valvesoftware.Steam/.local/share/Steam"),
    ]
    roots = []
    for home in homes:
        if os.path.isdir(home) and home not in roots:
            roots.append(home)
        text = _read_text(os.path.join(home, "steamapps", "libraryfolders.vdf"))
        if not text:
            continue
        for encoded in re.findall(r'"path"\s+"([^"]+)"', text):
            candidate = encoded.replace("\\\\", "\\")
            if os.path.isdir(candidate) and candidate not in roots:
                roots.append(candidate)
    return roots


def _steam_app(app_id):
    app_id = str(app_id)
    for root in steam_library_roots():
        manifest = os.path.join(root, "steamapps", f"appmanifest_{app_id}.acf")
        text = _read_text(manifest)
        if text is None:
            continue
        name_match = re.search(r'"name"\s+"([^"]+)"', text)
        name = name_match.group(1) if name_match else f"AppID {app_id}"
        return True, f"nalezeno ve Steamu: {name}"
    return False, f"Steam AppID {app_id} nebylo nalezeno"


def evaluate_check(check):
    """Evaluate one validated check definition on this client."""
    label = str(check.get("label", "Kontrola"))
    category = str(check.get("category", "configuration"))
    check_type = check.get("type")
    try:
        if check_type == "path_exists":
            paths = check.get("paths") or [check.get("path", "")]
            found = next((_expand(path) for path in paths if path and os.path.exists(_expand(path))), None)
            ok, detail = bool(found), (f"nalezeno: {found}" if found else "nenalezeno")
        elif check_type == "path_absent":
            path = _expand(check["path"])
            ok, detail = not os.path.exists(path), ("není přítomno" if not os.path.exists(path) else f"stále existuje: {path}")
        elif check_type == "file_contains":
            path = _expand(check["path"])
            text = _read_text(path)
            needles = check.get("all", [])
            ok = text is not None and all(str(needle) in text for needle in needles)
            detail = "nastavení odpovídá" if ok else f"nastavení chybí v {path}"
        elif check_type == "json_value":
            actual = _json_value(check["path"], check.get("keys", []))
            ok = actual == check.get("equals")
            detail = "nastavení odpovídá" if ok else "nastavení má jinou hodnotu"
        elif check_type == "steam_app":
            ok, detail = _steam_app(check["app_id"])
        else:
            return {"label": label, "category": category, "status": "unknown", "detail": "nepodporovaný typ kontroly"}
        return {"label": label, "category": category, "status": "ok" if ok else "missing", "detail": detail}
    except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        return {"label": label, "category": category, "status": "unknown", "detail": f"nelze ověřit: {error}"}


def evaluate_checks(checks):
    return [evaluate_check(check) for check in checks if isinstance(check, dict)]
