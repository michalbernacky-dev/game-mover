import unittest

from game_mover_workloads import (
    BackendResult,
    PodmanBackend,
    SystemdBackend,
)


class RecordingRunner:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append((command, timeout))
        if self.results:
            return self.results.pop(0)
        return BackendResult(0)


class WorkloadBackendTest(unittest.TestCase):
    def test_legacy_systemd_workload_uses_only_registered_unit(self):
        runner = RecordingRunner([
            BackendResult(0),
            BackendResult(0),
            BackendResult(0),
            BackendResult(0),
        ])
        backend = SystemdBackend(runner)
        workload = {"service": "forge-srv.service"}

        backend.start(workload)
        backend.stop(workload)

        self.assertEqual(
            [call[0] for call in runner.calls],
            [
                ["systemctl", "reset-failed", "forge-srv.service"],
                ["systemctl", "start", "forge-srv.service"],
                ["systemctl", "stop", "forge-srv.service"],
                ["systemctl", "reset-failed", "forge-srv.service"],
            ],
        )

    def test_systemd_backup_metadata_contains_only_registered_unit(self):
        backend = SystemdBackend(RecordingRunner())

        metadata = backend.runtime_metadata({"service": "forge-srv.service"})

        self.assertEqual(metadata, {"unit": "forge-srv.service"})

    def test_podman_status_is_normalized_and_uses_private_socket(self):
        runner = RecordingRunner([BackendResult(0, "running")])
        backend = PodmanBackend(
            "gameplatform",
            runner,
            "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman",
            "runtime": {"container_name": "mc-test"},
        }

        state = backend.status(workload)

        self.assertEqual(state.status, "active")
        self.assertEqual(state.native_status, "running")
        self.assertEqual(
            runner.calls[0],
            (
                [
                    "/usr/bin/podman",
                    "--remote",
                    "--url",
                    "unix:///run/user/955/podman/podman.sock",
                    "inspect",
                    "--format",
                    "{{.State.Status}}",
                    "mc-test",
                ],
                15,
            ),
        )

    def test_podman_lifecycle_has_fixed_arguments_and_timeouts(self):
        runner = RecordingRunner()
        backend = PodmanBackend(
            "gameplatform",
            runner,
            "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman",
            "runtime": {"container_name": "mc-test"},
        }

        backend.start(workload)
        backend.stop(workload)
        backend.restart(workload)
        backend.remove_container(workload, force=True)

        arguments = [call[0][4:] for call in runner.calls]
        self.assertEqual(arguments, [
            ["start", "mc-test"],
            ["stop", "--time", "120", "mc-test"],
            ["restart", "--time", "120", "mc-test"],
            ["rm", "--force", "mc-test"],
        ])
        self.assertEqual([call[1] for call in runner.calls], [60, 150, 180, 180])

    def test_podman_discovers_published_minecraft_port(self):
        runner = RecordingRunner([BackendResult(0, "0.0.0.0:25570")])
        backend = PodmanBackend(
            "gameplatform", runner, "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman",
            "runtime": {"container_name": "mc-test"},
        }

        self.assertEqual(backend.published_port(workload, 25565), 25570)
        self.assertEqual(runner.calls[0][0][4:], [
            "port", "mc-test", "25565/tcp",
        ])

    def test_podman_backup_metadata_excludes_environment(self):
        runner = RecordingRunner([
            BackendResult(0, "docker.io/itzg/minecraft-server:latest"),
            BackendResult(0, "sha256:image-id"),
            BackendResult(0, "25565/tcp -> 0.0.0.0:25570"),
            BackendResult(0, "sha256:image-digest"),
        ])
        backend = PodmanBackend(
            "gameplatform", runner, "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman", "runtime": {"container_name": "mc-test"},
        }

        metadata = backend.runtime_metadata(workload)

        self.assertEqual(metadata["image_digest"], "sha256:image-digest")
        self.assertEqual(metadata["published_ports"], ["25565/tcp -> 0.0.0.0:25570"])
        self.assertNotIn("environment", metadata)

    def test_podman_rejects_unvalidated_reference_before_execution(self):
        runner = RecordingRunner()
        backend = PodmanBackend(
            "gameplatform",
            runner,
            "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman",
            "runtime": {"container_name": "--all"},
        }

        with self.assertRaises(ValueError):
            backend.start(workload)

    def test_podman_creates_validated_velocity_container(self):
        runner = RecordingRunner()
        backend = PodmanBackend(
            "gameplatform", runner, "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman", "runtime": {"container_name": "velocity"},
        }

        backend.create_container(
            workload, "docker.io/itzg/mc-proxy:java21",
            environment={"TYPE": "VELOCITY"},
            mounts=[{"source": "/var/lib/game-platform/proxies/velocity", "target": "/server"}],
            ports=[{"host": "0.0.0.0", "host_port": 25580, "container_port": 25580}],
            labels={"io.game-platform.kind": "minecraft-proxy"},
            restart_policy="unless-stopped",
            networks=["game-platform"],
        )

        arguments = runner.calls[0][0][4:]
        self.assertEqual(arguments[0:5], [
            "create", "--name", "velocity", "--restart", "unless-stopped",
        ])
        self.assertIn("TYPE=VELOCITY", arguments)
        self.assertIn("game-platform", arguments)
        self.assertIn("0.0.0.0:25580:25580/tcp", arguments)
        self.assertEqual(arguments[-1], "docker.io/itzg/mc-proxy:java21")

    def test_podman_rejects_unknown_restart_policy(self):
        runner = RecordingRunner()
        backend = PodmanBackend(
            "gameplatform", runner, "/run/user/955/podman/podman.sock",
        )
        workload = {
            "backend": "podman", "runtime": {"container_name": "velocity"},
        }

        with self.assertRaisesRegex(ValueError, "restart policy"):
            backend.create_container(
                workload, "docker.io/itzg/mc-proxy:java21",
                restart_policy="$(malicious)",
            )
        self.assertEqual(runner.calls, [])

    def test_podman_creates_validated_private_network(self):
        runner = RecordingRunner()
        backend = PodmanBackend(
            "gameplatform", runner, "/run/user/955/podman/podman.sock",
        )

        backend.create_network(
            "game-platform", labels={"io.game-platform.managed": "true"},
        )

        self.assertEqual(runner.calls[0][0][4:], [
            "network", "create", "--driver", "bridge", "--label",
            "io.game-platform.managed=true", "game-platform",
        ])

if __name__ == "__main__":
    unittest.main()
