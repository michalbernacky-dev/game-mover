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

        arguments = [call[0][4:] for call in runner.calls]
        self.assertEqual(arguments, [
            ["start", "mc-test"],
            ["stop", "--time", "120", "mc-test"],
            ["restart", "--time", "120", "mc-test"],
        ])
        self.assertEqual([call[1] for call in runner.calls], [60, 150, 180])

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

if __name__ == "__main__":
    unittest.main()
