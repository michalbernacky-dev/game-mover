#!/usr/bin/env python3
"""Lifecycle backends for registered Game Platform workloads."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import pwd
import re
import subprocess
from typing import Callable


CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
NETWORK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
OCI_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"(?:@sha256:[0-9a-f]{64})?$"
)
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LABEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RESTART_POLICIES = {"no", "on-failure", "always", "unless-stopped"}
LOG_TAIL_MINIMUM = 10
LOG_TAIL_MAXIMUM = 500
LOG_OUTPUT_MAXIMUM_BYTES = 256 * 1024


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

    def logs(self, workload: dict, tail: int = 100) -> BackendResult:
        tail = normalize_log_tail(tail)
        return self.runner([
            "journalctl", "--unit", self.reference(workload), "--no-pager",
            "--output=short-iso", "--lines", str(tail),
        ], 20)


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

    def container_exists(self, workload: dict) -> bool:
        result = self._command(["container", "exists", self.reference(workload)], 15)
        return result.returncode == 0

    def pull_image(self, image: str) -> BackendResult:
        image = str(image).strip()
        if not OCI_IMAGE_RE.fullmatch(image):
            raise ValueError("Neplatná reference container image")
        return self._command(["pull", "--quiet", image], 600)

    def network_exists(self, network: str) -> bool:
        network = str(network).strip()
        if not NETWORK_NAME_RE.fullmatch(network):
            raise ValueError("Neplatné jméno Podman sítě")
        return self._command(["network", "exists", network], 15).returncode == 0

    def create_network(self, network: str, *, labels: dict | None = None) -> BackendResult:
        network = str(network).strip()
        if not NETWORK_NAME_RE.fullmatch(network):
            raise ValueError("Neplatné jméno Podman sítě")
        arguments = ["network", "create", "--driver", "bridge"]
        for key, value in sorted((labels or {}).items()):
            if not LABEL_NAME_RE.fullmatch(str(key)) or "\x00" in str(value):
                raise ValueError("Neplatný label Podman sítě")
            arguments.extend(["--label", f"{key}={value}"])
        arguments.append(network)
        return self._command(arguments, 60)

    def create_container(
        self,
        workload: dict,
        image: str,
        *,
        environment: dict | None = None,
        mounts: list[dict] | None = None,
        ports: list[dict] | None = None,
        labels: dict | None = None,
        restart_policy: str = "no",
        networks: list[str] | None = None,
    ) -> BackendResult:
        """Create one validated adopted container without accepting raw CLI arguments."""
        container = self.reference(workload)
        image = str(image).strip()
        if not OCI_IMAGE_RE.fullmatch(image):
            raise ValueError("Neplatná reference container image")
        restart_policy = str(restart_policy).strip().lower()
        if restart_policy not in RESTART_POLICIES:
            raise ValueError("Neplatná restart policy containeru")
        arguments = [
            "create", "--name", container, "--restart", restart_policy,
        ]
        for network in networks or []:
            network = str(network).strip()
            if not NETWORK_NAME_RE.fullmatch(network):
                raise ValueError("Neplatné jméno Podman sítě")
            arguments.extend(["--network", network])
        for key, value in sorted((environment or {}).items()):
            if not ENV_NAME_RE.fullmatch(str(key)) or "\x00" in str(value):
                raise ValueError("Neplatná proměnná prostředí containeru")
            arguments.extend(["--env", f"{key}={value}"])
        for mount in mounts or []:
            raw_source = str(mount.get("source", ""))
            source = os.path.realpath(raw_source)
            target = str(mount.get("target", ""))
            if not raw_source.startswith("/") or not target.startswith("/") or ":" in target:
                raise ValueError("Neplatný mount containeru")
            suffix = ":Z" if mount.get("selinux", True) else ""
            arguments.extend(["--volume", f"{source}:{target}{suffix}"])
        for mapping in ports or []:
            host = str(mapping.get("host", "0.0.0.0"))
            try:
                ipaddress.ip_address(host)
            except ValueError as error:
                raise ValueError("Neplatná adresa publikovaného portu") from error
            host_port = int(mapping.get("host_port"))
            container_port = int(mapping.get("container_port"))
            if not 1 <= host_port <= 65535 or not 1 <= container_port <= 65535:
                raise ValueError("Neplatný publikovaný port")
            host_display = f"[{host}]" if ":" in host else host
            arguments.extend([
                "--publish", f"{host_display}:{host_port}:{container_port}/tcp",
            ])
        for key, value in sorted((labels or {}).items()):
            if not LABEL_NAME_RE.fullmatch(str(key)) or "\x00" in str(value):
                raise ValueError("Neplatný label containeru")
            arguments.extend(["--label", f"{key}={value}"])
        arguments.append(image)
        return self._command(arguments, 120)

    def start(self, workload: dict) -> BackendResult:
        return self._command(["start", self.reference(workload)], 60)

    def stop(self, workload: dict) -> BackendResult:
        return self._command(["stop", "--time", "120", self.reference(workload)], 150)

    def restart(self, workload: dict) -> BackendResult:
        return self._command(["restart", "--time", "120", self.reference(workload)], 180)

    def remove_container(self, workload: dict, *, force=False) -> BackendResult:
        arguments = ["rm"]
        if force:
            arguments.append("--force")
        arguments.append(self.reference(workload))
        return self._command(arguments, 180)

    def logs(self, workload: dict, tail: int = 100) -> BackendResult:
        return self._command([
            "logs", "--timestamps", "--tail", str(normalize_log_tail(tail)),
            self.reference(workload),
        ], 30)


def normalize_log_tail(value) -> int:
    try:
        tail = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Počet řádků logu musí být celé číslo") from error
    if not LOG_TAIL_MINIMUM <= tail <= LOG_TAIL_MAXIMUM:
        raise ValueError(
            f"Počet řádků logu musí být mezi {LOG_TAIL_MINIMUM} a {LOG_TAIL_MAXIMUM}"
        )
    return tail


def bounded_log_output(result: BackendResult) -> tuple[str, bool]:
    output = result.output or result.error or ""
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) <= LOG_OUTPUT_MAXIMUM_BYTES:
        return output, False
    clipped = encoded[-LOG_OUTPUT_MAXIMUM_BYTES:].decode("utf-8", errors="replace")
    return "[Starší část výpisu byla zkrácena]\n" + clipped, True


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
