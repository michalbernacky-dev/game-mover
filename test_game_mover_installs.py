import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from game_mover_installs import (
    InstallError,
    container_environment,
    list_backups,
    normalize_install_request,
    restore_backup,
)


class MinecraftInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backups = self.root / "backups"
        self.data_root = self.root / "servers"

    def tearDown(self):
        self.temporary.cleanup()

    def config(self):
        return {
            "id": "forge-copy", "name": "Forge Copy", "loader": "forge",
            "version": "1.20.1", "loader_version": "47.4.4",
            "memory_mb": 8192, "port": 25571, "hostname": "forge-copy.mc.example",
            "accept_eula": True,
            "backup": {"source_id": "minecraft", "id": "20260810T183500.980349Z"},
        }

    def make_backup(self, *, unsafe=False):
        source = self.backups / "minecraft"
        source.mkdir(parents=True)
        backup_id = "20260810T183500.980349Z"
        archive_path = source / f"{backup_id}.tar.gz"
        payload = self.root / "payload"
        payload.mkdir()
        (payload / "server.properties").write_text("server-port=25565\n")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(payload, arcname="data")
            if unsafe:
                info = tarfile.TarInfo("data/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
        checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 1,
            "backup_id": backup_id,
            "created_at": "2026-08-10T18:35:00+00:00",
            "workload": {"id": "minecraft", "backend": "systemd"},
            "archive": {
                "file": archive_path.name, "sha256": checksum,
                "size_bytes": archive_path.stat().st_size,
            },
        }
        (source / f"{backup_id}.manifest.json").write_text(json.dumps(manifest))
        return backup_id

    def test_normalizes_forge_install_and_builds_environment(self):
        config = normalize_install_request(self.config())
        self.assertEqual(config["loader"], "FORGE")
        self.assertEqual(config["port"], 25571)
        self.assertEqual(container_environment(config), {
            "EULA": "TRUE", "TYPE": "FORGE", "VERSION": "1.20.1",
            "MEMORY": "8192M", "UID": "1000", "GID": "1000",
            "FORGE_VERSION": "47.4.4",
        })

    def test_rejects_unapproved_image_and_bad_hostname(self):
        config = self.config()
        config["image"] = "docker.io/library/alpine:latest"
        with self.assertRaisesRegex(InstallError, "itzg"):
            normalize_install_request(config)
        config = self.config()
        config["hostname"] = "bad_host"
        with self.assertRaisesRegex(InstallError, "hostname"):
            normalize_install_request(config)
        config = self.config()
        config["accept_eula"] = False
        with self.assertRaisesRegex(InstallError, "EULA"):
            normalize_install_request(config)

    def test_lists_and_atomically_restores_verified_backup(self):
        backup_id = self.make_backup()
        self.assertEqual(list_backups(str(self.backups), "minecraft")[0]["id"], backup_id)
        with patch("game_mover_installs._chown_tree"):
            result = restore_backup(
                backup_root=str(self.backups), source_id="minecraft", backup_id=backup_id,
                data_root=str(self.data_root), target_id="forge-copy", owner_user="gameplatform",
            )
        restored = Path(result["data_directory"])
        self.assertEqual((restored / "server.properties").read_text(), "server-port=25565\n")
        self.assertFalse(list(self.data_root.glob(".*-restore-*")))

    def test_restore_rejects_links_and_leaves_no_target(self):
        backup_id = self.make_backup(unsafe=True)
        with self.assertRaisesRegex(InstallError, "odkaz"):
            restore_backup(
                backup_root=str(self.backups), source_id="minecraft", backup_id=backup_id,
                data_root=str(self.data_root), target_id="forge-copy", owner_user="gameplatform",
            )
        self.assertFalse((self.data_root / "forge-copy").exists())


if __name__ == "__main__":
    unittest.main()
