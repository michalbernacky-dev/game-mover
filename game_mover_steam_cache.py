"""Read-only Steam download-cache inspection shared by API and user worker."""

import os


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
