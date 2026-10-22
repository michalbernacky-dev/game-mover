"""Steam download and shader-cache management shared by API and user worker."""

import os
import re
import shutil
import stat


APP_ID_RE = re.compile(r"^[1-9][0-9]{0,9}$")
MANIFEST_NAME_RE = re.compile(r'^\s*"name"\s+"(.*)"\s*$')
MAX_SHADER_CACHE_ENTRIES = 128


def _allocated_tree_size(path: str) -> int:
    """Return allocated bytes without following symbolic links."""
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            metadata = os.stat(current, follow_symlinks=False)
        except OSError:
            continue
        total += metadata.st_blocks * 512
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        try:
            with os.scandir(current) as entries:
                pending.extend(entry.path for entry in entries)
        except OSError:
            continue
    return total


def _manifest_names(steamapps: str) -> dict[str, str]:
    names = {}
    try:
        manifests = os.scandir(steamapps)
    except OSError:
        return names
    with manifests:
        for entry in manifests:
            match = re.fullmatch(r"appmanifest_([1-9][0-9]{0,9})\.acf", entry.name)
            if not match or not entry.is_file(follow_symlinks=False):
                continue
            try:
                if entry.stat(follow_symlinks=False).st_size > 1024 * 1024:
                    continue
                with open(entry.path, encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        name_match = MANIFEST_NAME_RE.match(line)
                        if name_match:
                            name = name_match.group(1).strip()
                            names[match.group(1)] = name[:160]
                            break
            except OSError:
                continue
    return names


def inspect_shader_cache(steamapps: str) -> dict:
    shader_path = os.path.join(steamapps, "shadercache")
    result = {
        "status": "missing",
        "path": shader_path,
        "total_size_bytes": 0,
        "entries": [],
        "entry_count": 0,
        "truncated": False,
    }
    if os.path.islink(shader_path):
        result["status"] = "symlink"
        return result
    if not os.path.lexists(shader_path):
        return result
    if not os.path.isdir(shader_path):
        result["status"] = "unsafe"
        return result

    result["status"] = "local"
    try:
        result["total_size_bytes"] = (
            os.stat(shader_path, follow_symlinks=False).st_blocks * 512
        )
    except OSError:
        result["status"] = "unreadable"
        return result
    names = _manifest_names(steamapps)
    entries = []
    entry_count = 0
    try:
        children = os.scandir(shader_path)
    except OSError:
        result["status"] = "unreadable"
        return result
    with children:
        for child in children:
            child_size = _allocated_tree_size(child.path)
            result["total_size_bytes"] += child_size
            if not APP_ID_RE.fullmatch(child.name):
                continue
            try:
                if not child.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            entry_count += 1
            entries.append({
                "app_id": child.name,
                "name": names.get(child.name, f"Steam App {child.name}"),
                "size_bytes": child_size,
            })
    entries.sort(key=lambda item: (-item["size_bytes"], item["app_id"]))
    result["entry_count"] = entry_count
    result["truncated"] = entry_count > MAX_SHADER_CACHE_ENTRIES
    result["entries"] = entries[:MAX_SHADER_CACHE_ENTRIES]
    return result


def clear_shader_cache_entries(steamapps: str, app_ids: object) -> dict:
    """Remove selected numeric AppID directories using a fixed parent fd."""
    if (
        not isinstance(app_ids, list)
        or not app_ids
        or len(app_ids) > MAX_SHADER_CACHE_ENTRIES
        or any(not isinstance(app_id, str) or not APP_ID_RE.fullmatch(app_id)
               for app_id in app_ids)
        or len(set(app_ids)) != len(app_ids)
    ):
        raise ValueError("Vyber jednu nebo více platných shader cache")

    shader_path = os.path.join(steamapps, "shadercache")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        shader_fd = os.open(shader_path, flags)
    except OSError as error:
        raise ValueError("Adresář shader cache není bezpečně dostupný") from error

    removed = []
    freed_bytes = 0
    try:
        candidates = []
        for app_id in app_ids:
            try:
                metadata = os.stat(app_id, dir_fd=shader_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"Shader cache {app_id} není bezpečný adresář")
            entry_path = os.path.join(shader_path, app_id)
            candidates.append((app_id, _allocated_tree_size(entry_path)))
        for app_id, entry_size in candidates:
            try:
                shutil.rmtree(app_id, dir_fd=shader_fd)
            except OSError as error:
                raise ValueError(f"Shader cache {app_id} se nepodařilo odstranit") from error
            freed_bytes += entry_size
            removed.append(app_id)
    finally:
        os.close(shader_fd)
    return {
        "message": f"Odstraněno shader cache: {len(removed)}",
        "removed_app_ids": removed,
        "freed_bytes": freed_bytes,
    }


def inspect_steam_cache(steamapps: str | None, games_root: str) -> dict:
    if steamapps is None:
        return {"message": "Steam knihovna nenalezena", "status": "missing"}
    download_path = os.path.join(steamapps, "downloading")
    shared_base = os.path.join(games_root, "steam-cache")
    shared_downloading = os.path.join(shared_base, "downloading")
    shared_base_real = os.path.realpath(shared_base)
    shared_downloading_real = os.path.join(shared_base_real, "downloading")

    status = "missing"
    target = None
    message = ""
    if os.path.islink(download_path):
        target = os.path.realpath(download_path)
        try:
            if (
                target == shared_downloading_real
                or target == shared_base_real
                or os.path.commonpath([target, shared_base_real]) == shared_base_real
            ):
                status = "shared"
            else:
                status = "custom"
            message = f"Symlink {download_path} -> {target}"
        except FileNotFoundError:
            status = "custom"
            message = f"Symlink {download_path} má neexistující cíl"
    elif os.path.isdir(download_path):
        status = "local"
        message = f"Používá lokální cestu {download_path}"
    elif os.path.exists(download_path):
        status = "unknown"
        message = f"{download_path} není adresář ani symlink"
    else:
        message = f"{download_path} neexistuje"

    return {
        "status": status,
        "download_path": download_path,
        "target": target,
        "shared_path": shared_downloading,
        "message": message,
    }
