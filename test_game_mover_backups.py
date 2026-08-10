import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from game_mover_backups import BackupError, create_workload_backup
from game_mover_workloads import BackendResult, WorkloadState


class FakePodmanBackend:
    def __init__(self, state="active"):
        self.state = state
        self.stops = 0
        self.starts = 0

    def status(self, _workload):
        return WorkloadState(self.state, "running" if self.state == "active" else "exited", "OK")

    def stop(self, _workload):
        self.stops += 1
        return BackendResult(0)

    def start(self, _workload):
        self.starts += 1
        return BackendResult(0)

    def runtime_metadata(self, _workload):
        return {
            "container_name": "mc-test",
            "image_digest": "sha256:test",
            "published_ports": ["25565/tcp -> 0.0.0.0:25570"],
        }


class WorkloadBackupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "servers" / "mc-test" / "data"
        (self.data / "world").mkdir(parents=True)
        (self.data / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
        (self.data / "world" / "level.dat").write_bytes(b"world-data")
        self.workload = {
            "id": "mc-test", "name": "Minecraft Test", "backend": "podman",
            "kind": "minecraft", "data": {"directory": str(self.data)},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, backend):
        with patch("game_mover_backups._chown"):
            return create_workload_backup(
                self.workload, backend,
                backup_root=str(self.root / "backups"), owner_user="gameplatform",
            )

    def test_active_server_is_backed_up_verified_and_restarted(self):
        backend = FakePodmanBackend("active")

        result = self.create(backend)

        self.assertEqual((backend.stops, backend.starts), (1, 1))
        archive_path = Path(result["archive"])
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertTrue(archive_path.is_file())
        self.assertEqual(manifest["archive"]["sha256"], result["sha256"])
        self.assertEqual(manifest["workload"]["runtime"]["image_digest"], "sha256:test")
        with tarfile.open(archive_path, "r:gz") as archive:
            self.assertIn("data/world/level.dat", archive.getnames())

    def test_inactive_server_is_not_started(self):
        backend = FakePodmanBackend("inactive")

        self.create(backend)

        self.assertEqual((backend.stops, backend.starts), (0, 0))

    def test_failure_after_stop_still_restarts_server_and_removes_partial(self):
        backend = FakePodmanBackend("active")
        with (
            patch("game_mover_backups._chown"),
            patch("game_mover_backups._sha256", side_effect=OSError("disk error")),
            self.assertRaisesRegex(BackupError, "disk error"),
        ):
            create_workload_backup(
                self.workload, backend,
                backup_root=str(self.root / "backups"), owner_user="gameplatform",
            )

        self.assertEqual((backend.stops, backend.starts), (1, 1))
        self.assertFalse(list((self.root / "backups").rglob("*.partial")))

    def test_systemd_workload_is_backed_up_with_unit_metadata(self):
        self.workload["backend"] = "systemd"
        self.workload["service"] = "forge-srv.service"
        backend = FakePodmanBackend("inactive")
        backend.runtime_metadata = lambda _workload: {"unit": "forge-srv.service"}

        result = self.create(backend)

        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["workload"]["backend"], "systemd")
        self.assertEqual(
            manifest["workload"]["runtime"], {"unit": "forge-srv.service"},
        )

    def test_unknown_backend_is_rejected(self):
        self.workload["backend"] = "unknown"
        with self.assertRaisesRegex(BackupError, "nepodporuje úplné zálohy"):
            self.create(FakePodmanBackend())


if __name__ == "__main__":
    unittest.main()
