#!/usr/bin/env python3
"""Minecraft mod inventory and comparison helpers shared by GUI and API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile

try:
    import tomllib
except ImportError:  # pragma: no cover - Fedora package uses Python 3.11+
    tomllib = None


def _manifest_version(archive: zipfile.ZipFile) -> str:
    try:
        text = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except KeyError:
        return ""
    for line in text.splitlines():
        if line.lower().startswith("implementation-version:"):
            return line.split(":", 1)[1].strip()
    return ""


def _forge_metadata(archive: zipfile.ZipFile) -> list[dict]:
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(archive.read("META-INF/mods.toml").decode("utf-8", "replace"))
    except (KeyError, ValueError):
        return []
    jar_version = _manifest_version(archive)
    result = []
    for mod in data.get("mods", []):
        mod_id = str(mod.get("modId", "")).strip()
        if not mod_id:
            continue
        version = str(mod.get("version", "")).strip()
        if version == "${file.jarVersion}":
            version = jar_version
        result.append({
            "id": mod_id,
            "name": str(mod.get("displayName", mod_id)).strip(),
            "version": version,
        })
    return result


def _fabric_metadata(archive: zipfile.ZipFile) -> list[dict]:
    try:
        data = json.loads(archive.read("fabric.mod.json").decode("utf-8", "replace"))
    except (KeyError, ValueError):
        return []
    mod_id = str(data.get("id", "")).strip()
    if not mod_id:
        return []
    return [{
        "id": mod_id,
        "name": str(data.get("name", mod_id)).strip(),
        "version": str(data.get("version", "")).strip(),
    }]


def _fallback_id(filename: str) -> str:
    stem = filename[:-4] if filename.lower().endswith(".jar") else filename
    stem = re.sub(r"(?:[-_ ](?:forge|neoforge|fabric|mc)?v?\d[\w.+-]*)+$", "", stem, flags=re.I)
    return re.sub(r"[^a-z0-9_.-]+", "_", stem.lower()).strip("_.-") or stem.lower()


def inspect_mod_jar(path: str) -> dict:
    filename = os.path.basename(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    mods = []
    metadata_error = ""
    try:
        with zipfile.ZipFile(path) as archive:
            mods = _forge_metadata(archive) or _fabric_metadata(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        metadata_error = str(exc)

    if not mods:
        mods = [{"id": _fallback_id(filename), "name": filename, "version": ""}]
    return {
        "filename": filename,
        "size": os.path.getsize(path),
        "sha256": digest.hexdigest(),
        "mods": mods,
        "metadata_error": metadata_error,
    }


def scan_mod_directory(path: str) -> dict:
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Adresář s mody neexistuje: {path}")
    jars = []
    for filename in sorted(os.listdir(path), key=str.casefold):
        full_path = os.path.join(path, filename)
        if filename.lower().endswith(".jar") and os.path.isfile(full_path):
            jars.append(inspect_mod_jar(full_path))
    return {"path": os.path.abspath(path), "jar_count": len(jars), "jars": jars}


def _mods_by_id(inventory: dict) -> dict[str, dict]:
    result = {}
    for jar in inventory.get("jars", []):
        for mod in jar.get("mods", []):
            mod_id = str(mod.get("id", "")).strip()
            if mod_id and mod_id not in result:
                result[mod_id] = {**mod, "jar": jar}
    return result


def compare_inventories(server: dict, client: dict) -> dict:
    server_mods = _mods_by_id(server)
    client_mods = _mods_by_id(client)
    missing = []
    extra = []
    version_mismatch = []
    content_mismatch = []

    for mod_id in sorted(server_mods.keys() - client_mods.keys()):
        item = server_mods[mod_id]
        missing.append({"id": mod_id, "version": item.get("version", ""), "file": item["jar"]["filename"]})
    for mod_id in sorted(client_mods.keys() - server_mods.keys()):
        item = client_mods[mod_id]
        extra.append({"id": mod_id, "version": item.get("version", ""), "file": item["jar"]["filename"]})

    compared_jars = set()
    for mod_id in sorted(server_mods.keys() & client_mods.keys()):
        server_item = server_mods[mod_id]
        client_item = client_mods[mod_id]
        server_version = server_item.get("version", "")
        client_version = client_item.get("version", "")
        if server_version and client_version and server_version != client_version:
            version_mismatch.append({
                "id": mod_id,
                "server_version": server_version,
                "client_version": client_version,
                "server_file": server_item["jar"]["filename"],
                "client_file": client_item["jar"]["filename"],
            })
            continue
        jar_pair = (server_item["jar"]["filename"], client_item["jar"]["filename"])
        if jar_pair in compared_jars:
            continue
        compared_jars.add(jar_pair)
        if server_item["jar"].get("sha256") != client_item["jar"].get("sha256"):
            content_mismatch.append({
                "id": mod_id,
                "server_file": server_item["jar"]["filename"],
                "client_file": client_item["jar"]["filename"],
            })

    return {
        "missing": missing,
        "extra": extra,
        "version_mismatch": version_mismatch,
        "content_mismatch": content_mismatch,
        "server_jar_count": server.get("jar_count", 0),
        "client_jar_count": client.get("jar_count", 0),
    }
