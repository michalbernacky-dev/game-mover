#!/usr/bin/env python3
"""Lifecycle backends for registered Game Platform workloads."""

from __future__ import annotations

from dataclasses import dataclass
import os
import pwd
import re
import subprocess
from typing import Callable


CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")


@dataclass
class BackendResult:
    returncode: int
    output: str = ""
    error: str = ""


@dataclass
class WorkloadState:
    status: str
    native_status: str
    message: str
    error: str = ""


CommandRunner = Callable[[list[str], int], BackendResult]


def run_command(command: list[str], timeout: int = 30) -> BackendResult:
    try:
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return BackendResult(
            process.returncode,
            process.stdout.strip(),
            process.stderr.strip(),
        )
    except FileNotFoundError:
        return BackendResult(127, error=f"Příkaz {command[0]} nebyl nalezen")
    except subprocess.TimeoutExpired:
        return BackendResult(124, error="Operace překročila časový limit")
    except Exception as exc:
        return BackendResult(1, error=str(exc))


class SystemdBackend:
    name = "systemd"

    def __init__(self, runner: CommandRunner = run_command):
        self.runner = runner

    @staticmethod
    def reference(workload: dict) -> str:
        runtime = workload.get("runtime", {})
        unit = str(runtime.get("unit") or workload.get("service") or "").strip()
        if not SYSTEMD_UNIT_RE.fullmatch(unit):
            raise ValueError("Neplatná systemd jednotka")
        return unit

    def status(self, workload: dict) -> WorkloadState:
        unit = self.reference(workload)
        result = self.runner(["systemctl", "is-active", unit], 15)
        native = result.output or "unknown"
        messages = {
            "active": "Běží",
            "activating": "Spouští se",
            "deactivating": "Zastavuje se",
            "inactive": "Neběží",
            "failed": "Chyba",
            "unknown": "Jednotka nenalezena",
        }
        return WorkloadState(
            native if native in messages else "unknown",
            native,
            messages.get(native, result.error or f"Stav: {native}"),
            result.error if result.returncode == 127 else "",
        )

    def _action(self, action: str, workload: dict, timeout: int = 30) -> BackendResult:
        return self.runner(["systemctl", action, self.reference(workload)], timeout)

    def start(self, workload: dict) -> BackendResult:
        self._action("reset-failed", workload)
        return self._action("start", workload)

    def stop(self, workload: dict) -> BackendResult:
        result = self._action("stop", workload)
        if result.returncode == 0:
            self._action("reset-failed", workload)
        return result

    def restart(self, workload: dict) -> BackendResult:
        self._action("reset-failed", workload)
        return self._action("restart", workload, 60)

    def runtime_metadata(self, workload: dict) -> dict:
        """Return the non-secret systemd identity needed to trace the backup."""
        return {"unit": self.reference(workload)}


class PodmanBackend:
    name = "podman"

    def __init__(
        self,
        user: str,
        runner: CommandRunner = run_command,
        socket_path: str | None = None,
    ):
        self.user = user
        self.runner = runner
        self.socket_path = socket_path or self._default_socket_path(user)

    @staticmethod
    def _default_socket_path(user: str) -> str:
        uid = pwd.getpwnam(user).pw_uid
        return f"/run/user/{uid}/podman/podman.sock"

    @staticmethod
    def reference(workload: dict) -> str:
        runtime = workload.get("runtime", {})
        container = str(runtime.get("container_name") or workload.get("container") or "").strip()
        if not CONTAINER_NAME_RE.fullmatch(container):
            raise ValueError("Neplatné jméno Podman containeru")
        return container

    def _command(self, arguments: list[str], timeout: int = 30) -> BackendResult:
        command = [
            os.getenv("GAME_PLATFORM_PODMAN_BIN", "/usr/bin/podman"),
            "--remote",
            "--url",
            f"unix://{self.socket_path}",
            *arguments,
        ]
        return self.runner(command, timeout)

    def status(self, workload: dict) -> WorkloadState:
        container = self.reference(workload)
        result = self._command(["inspect", "--format", "{{.State.Status}}", container], 15)
        if result.returncode != 0:
            return WorkloadState(
                "unknown", "unknown", "Container nelze načíst", result.error or result.output,
            )

        native = result.output.strip().lower() or "unknown"
        normalized = {
            "running": "active",
            "created": "inactive",
            "configured": "inactive",
            "exited": "inactive",
            "stopped": "inactive",
            "paused": "inactive",
            "stopping": "deactivating",
            "removing": "deactivating",
        }.get(native, "unknown")
        messages = {
            "running": "Běží",
            "created": "Připraven",
            "configured": "Připraven",
            "exited": "Neběží",
            "stopped": "Neběží",
            "paused": "Pozastaven",
            "stopping": "Zastavuje se",
            "removing": "Odstraňuje se",
        }
        return WorkloadState(normalized, native, messages.get(native, f"Stav: {native}"))

    def published_port(self, workload: dict, container_port: int) -> int | None:
        result = self._command([
            "port", self.reference(workload), f"{int(container_port)}/tcp",
        ], 15)
        if result.returncode != 0:
            return None
        for line in result.output.splitlines():
            match = re.search(r":([0-9]{1,5})$", line.strip())
            if match:
                port = int(match.group(1))
                if 1 <= port <= 65535:
                    return port
        return None

    def runtime_metadata(self, workload: dict) -> dict:
        """Return only non-secret fields needed for a future recreation."""
        container = self.reference(workload)
        image_name = self._command(["inspect", "--format", "{{.ImageName}}", container], 15)
        image_id = self._command(["inspect", "--format", "{{.Image}}", container], 15)
        ports = self._command(["port", container], 15)
        metadata = {"container_name": container}
        if image_name.returncode == 0 and image_name.output:
            metadata["image_name"] = image_name.output
        if image_id.returncode == 0 and image_id.output:
            metadata["image_id"] = image_id.output
            digest = self._command([
                "image", "inspect", "--format", "{{.Digest}}", image_id.output,
            ], 15)
            if digest.returncode == 0 and digest.output:
                metadata["image_digest"] = digest.output
        if ports.returncode == 0 and ports.output:
            metadata["published_ports"] = ports.output.splitlines()
        return metadata

    def start(self, workload: dict) -> BackendResult:
        return self._command(["start", self.reference(workload)], 60)

    def stop(self, workload: dict) -> BackendResult:
        return self._command(["stop", "--time", "120", self.reference(workload)], 150)

    def restart(self, workload: dict) -> BackendResult:
        return self._command(["restart", "--time", "120", self.reference(workload)], 180)


def backend_for(
    workload: dict,
    *,
    podman_user: str,
    runner: CommandRunner = run_command,
    podman_socket_path: str | None = None,
):
    backend = workload.get("backend", "systemd")
    if backend == "systemd":
        return SystemdBackend(runner)
    if backend == "podman":
        return PodmanBackend(podman_user, runner, podman_socket_path)
    raise ValueError(f"Nepodporovaný backend: {backend}")
