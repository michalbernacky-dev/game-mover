import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import zipfile

from game_mover_installs import (
    InstallError,
    container_environment,
    detect_server_pack_loader_version,
    install_curseforge_server_pack,
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

    def test_normalizes_curseforge_reference_and_rejects_backup_combination(self):
        config = self.config()
        config.pop("backup")
        config["curseforge"] = {"project_id": "123", "file_id": 789}
        normalized = normalize_install_request(config)
        self.assertEqual(normalized["curseforge"], {"project_id": 123, "file_id": 789})

        config["backup"] = {"source_id": "minecraft", "id": "backup-1"}
        with self.assertRaisesRegex(InstallError, "současně"):
            normalize_install_request(config)

    @staticmethod
    def curseforge_response(payload, *, status=200, headers=None):
        response = Mock()
        response.status_code = status
        response.headers = headers or {"Content-Length": str(len(payload))}
        response.iter_content.return_value = [payload[:7], payload[7:]]
        return response

    def test_follows_only_validated_curseforge_redirects(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("mods/example.jar", b"mod")
        payload = stream.getvalue()
        descriptor = {
            "project_id": 123, "file_id": 790,
            "download_url": "https://edge.forgecdn.net/files/1/server.zip",
            "file_length": len(payload),
            "hashes": [{"algorithm": 1, "value": hashlib.sha1(payload).hexdigest()}],
        }
        redirect = self.curseforge_response(
            b"", status=302,
            headers={"Location": "https://mediafiles.forgecdn.net/files/1/server.zip"},
        )
        requester = Mock(side_effect=[redirect, self.curseforge_response(payload)])

        with patch("game_mover_installs._chown_tree"):
            install_curseforge_server_pack(
                descriptor, data_root=str(self.data_root), target_id="redirect-pack",
                owner_user="gameplatform", requester=requester,
            )

        self.assertEqual(requester.call_count, 2)
        self.assertTrue(redirect.close.called)

    def test_rejects_curseforge_redirect_to_unapproved_host(self):
        descriptor = {
            "project_id": 123, "file_id": 790,
            "download_url": "https://edge.forgecdn.net/files/1/server.zip",
            "file_length": 3,
            "hashes": [{"algorithm": 1, "value": hashlib.sha1(b"zip").hexdigest()}],
        }
        redirect = self.curseforge_response(
            b"", status=302, headers={"Location": "https://example.invalid/server.zip"},
        )
        with self.assertRaisesRegex(InstallError, "nepovolenou adresu"):
            install_curseforge_server_pack(
                descriptor, data_root=str(self.data_root), target_id="unsafe-redirect",
                owner_user="gameplatform", requester=Mock(return_value=redirect),
            )
        self.assertTrue(redirect.close.called)

    def test_installs_verified_curseforge_server_pack_atomically(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Family Server/mods/example.jar", b"mod")
            archive.writestr("Family Server/config/example.toml", b"enabled=true\n")
        payload = stream.getvalue()
        descriptor = {
            "project_id": 123, "file_id": 790,
            "download_url": "https://edge.forgecdn.net/files/1/server.zip",
            "file_length": len(payload),
            "hashes": [{"algorithm": 1, "value": hashlib.sha1(payload).hexdigest()}],
        }
        requester = Mock(return_value=self.curseforge_response(payload))

        with patch("game_mover_installs._chown_tree"):
            result = install_curseforge_server_pack(
                descriptor, data_root=str(self.data_root), target_id="family-pack",
                owner_user="gameplatform", requester=requester,
            )

        data = Path(result["data_directory"])
        self.assertEqual((data / "mods" / "example.jar").read_bytes(), b"mod")
        self.assertEqual((data / "config" / "example.toml").read_text(), "enabled=true\n")
        self.assertFalse(list(self.data_root.glob(".*-curseforge-*")))

    def test_rejects_unsafe_curseforge_zip_and_cleans_target(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../outside.txt", b"unsafe")
        payload = stream.getvalue()
        descriptor = {
            "project_id": 123, "file_id": 790,
            "download_url": "https://edge.forgecdn.net/files/1/server.zip",
            "file_length": len(payload),
            "hashes": [{"algorithm": 1, "value": hashlib.sha1(payload).hexdigest()}],
        }
        with self.assertRaisesRegex(InstallError, "mimo datový"):
            install_curseforge_server_pack(
                descriptor, data_root=str(self.data_root), target_id="unsafe-pack",
                owner_user="gameplatform",
                requester=Mock(return_value=self.curseforge_response(payload)),
            )
        self.assertFalse((self.data_root / "unsafe-pack").exists())

    def test_rejects_server_pack_that_requires_external_setup_script(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("mods.csv", "https://edge.forgecdn.net/files/1/2/mod.jar,mods/mod.jar\n")
            archive.writestr("setup_server.sh", "wget something\n")
            archive.writestr("config/example.toml", "enabled=true\n")
        payload = stream.getvalue()
        descriptor = {
            "project_id": 123, "file_id": 790,
            "download_url": "https://edge.forgecdn.net/files/1/server.zip",
            "file_length": len(payload),
            "hashes": [{"algorithm": 1, "value": hashlib.sha1(payload).hexdigest()}],
        }

        with self.assertRaisesRegex(InstallError, "externího setup skriptu"):
            install_curseforge_server_pack(
                descriptor, data_root=str(self.data_root), target_id="recipe-pack",
                owner_user="gameplatform",
                requester=Mock(return_value=self.curseforge_response(payload)),
            )
        self.assertFalse((self.data_root / "recipe-pack").exists())

    def test_detects_loader_version_bundled_in_server_pack(self):
        data = self.data_root / "detected" / "data"
        forge = data / "libraries/net/minecraftforge/forge/1.20.1-47.4.4"
        forge.mkdir(parents=True)
        (forge / "unix_args.txt").write_text("", encoding="utf-8")
        self.assertEqual(
            detect_server_pack_loader_version(str(data), "FORGE", "1.20.1"),
            "47.4.4",
        )

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
