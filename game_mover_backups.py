#!/usr/bin/env python3
"""Consistent, atomic backups of registered workload persistent data."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import tarfile


BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class BackupError(RuntimeError):
    """A backup could not be completed safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chown(path: Path, user: str) -> None:
    account = pwd.getpwnam(user)
    os.chown(path, account.pw_uid, account.pw_gid)


def _write_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    path.chmod(0o600)


def create_workload_backup(
    workload: dict,
    backend,
    *,
    backup_root: str,
    owner_user: str,
) -> dict:
    """Stop an active workload, archive its data directory, then restore state."""
    backend_name = str(workload.get("backend", "systemd")).strip().lower()
    if backend_name not in ("systemd", "podman"):
        raise BackupError("Backend serveru nepodporuje úplné zálohy")

    workload_id = str(workload.get("id", "")).strip()
    if not BACKUP_ID_RE.fullmatch(workload_id):
        raise BackupError("Neplatné ID serveru")
    data = workload.get("data") if isinstance(workload.get("data"), dict) else {}
    data_directory = Path(str(data.get("directory", ""))).resolve()
    if not data_directory.is_dir():
        raise BackupError("Persistentní datový adresář serveru neexistuje")

    root = Path(backup_root).resolve()
    if root == data_directory or data_directory in root.parents:
        raise BackupError("Adresář záloh nesmí ležet uvnitř zálohovaných dat")
    destination = root / workload_id
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.chmod(0o700)
    _chown(root, owner_user)
    _chown(destination, owner_user)

    state = backend.status(workload)
    if state.status not in ("active", "inactive"):
        raise BackupError(f"Server nemá stabilní stav pro zálohu: {state.message}")
    was_running = state.status == "active"
    stopped = False
    backup_error = None
    result = None
    published = False

    now = datetime.datetime.now(datetime.timezone.utc)
    backup_id = now.strftime("%Y%m%dT%H%M%S.%fZ")
    archive_name = f"{backup_id}.tar.gz"
    manifest_name = f"{backup_id}.manifest.json"
    checksum_name = f"{archive_name}.sha256"
    archive_tmp = destination / f".{archive_name}.partial"
    manifest_tmp = destination / f".{manifest_name}.partial"
    checksum_tmp = destination / f".{checksum_name}.partial"
    archive_path = destination / archive_name
    manifest_path = destination / manifest_name
    checksum_path = destination / checksum_name

    try:
        if was_running:
            stop_result = backend.stop(workload)
            if stop_result.returncode != 0:
                raise BackupError(
                    stop_result.error or stop_result.output or "Server se nepodařilo zastavit"
                )
            stopped = True

        with tarfile.open(archive_tmp, mode="x:gz", compresslevel=6) as archive:
            archive.add(data_directory, arcname="data", recursive=True)
        archive_tmp.chmod(0o600)

        with tarfile.open(archive_tmp, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or members[0].name != "data":
                raise BackupError("Kontrola vytvořeného archivu selhala")

        checksum = _sha256(archive_tmp)
        runtime_metadata = backend.runtime_metadata(workload)
        manifest = {
            "schema_version": 1,
            "backup_id": backup_id,
            "created_at": now.isoformat(),
            "workload": {
                "id": workload_id,
                "name": workload.get("name", workload_id),
                "kind": workload.get("kind", "generic"),
                "backend": backend_name,
                "runtime": runtime_metadata,
                "data_directory": str(data_directory),
            },
            "archive": {
                "file": archive_name,
                "format": "tar+gzip",
                "sha256": checksum,
                "size_bytes": archive_tmp.stat().st_size,
                "root": "data",
            },
            "server_was_running": was_running,
        }
        _write_json(manifest_tmp, manifest)
        checksum_tmp.write_text(f"{checksum}  {archive_name}\n", encoding="ascii")
        checksum_tmp.chmod(0o600)
        for path in (archive_tmp, manifest_tmp, checksum_tmp):
            _chown(path, owner_user)

        os.replace(archive_tmp, archive_path)
        os.replace(checksum_tmp, checksum_path)
        os.replace(manifest_tmp, manifest_path)  # completion marker, published last
        published = True
        result = {
            "id": backup_id,
            "created_at": manifest["created_at"],
            "archive": str(archive_path),
            "manifest": str(manifest_path),
            "sha256": checksum,
            "size_bytes": manifest["archive"]["size_bytes"],
        }
    except Exception as error:
        backup_error = error
    finally:
        for path in (archive_tmp, manifest_tmp, checksum_tmp):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if backup_error and not published:
            for path in (archive_path, manifest_path, checksum_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        if was_running and stopped:
            restart_result = backend.start(workload)
            if restart_result.returncode != 0:
                restart_message = (
                    restart_result.error
                    or restart_result.output
                    or "Původně běžící server se nepodařilo znovu spustit"
                )
                if backup_error:
                    raise BackupError(f"{backup_error}; navíc selhal restart: {restart_message}")
                raise BackupError(f"Záloha vznikla, ale selhal restart serveru: {restart_message}")

    if backup_error:
        if isinstance(backup_error, BackupError):
            raise backup_error
        raise BackupError(str(backup_error)) from backup_error
    return result
