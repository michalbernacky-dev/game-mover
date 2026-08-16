#!/usr/bin/env python3
"""Optional local Pi-hole DNS adapter.

The adapter uses Pi-hole FTL's supported configuration interface and keeps a
separate ownership file.  Records not created by Game Mover are never removed
or overwritten.
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
from pathlib import Path


class PiholeAdapterError(RuntimeError):
    pass


def _run(command: list[str], *, runner=subprocess.run) -> str:
    try:
        result = runner(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise PiholeAdapterError(f"Pi-hole nelze spustit: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "neznámá chyba").strip()
        raise PiholeAdapterError(f"Pi-hole odmítl změnu DNS: {detail}")
    return result.stdout.strip()


def read_pihole_hosts(executable="/usr/bin/pihole-FTL", *, runner=subprocess.run) -> list[str]:
    output = _run([executable, "--config", "dns.hosts"], runner=runner)
    try:
        records = json.loads(output)
    except json.JSONDecodeError as error:
        raise PiholeAdapterError("Pi-hole vrátil neplatný seznam Local DNS záznamů") from error
    if not isinstance(records, list) or any(not isinstance(item, str) for item in records):
        raise PiholeAdapterError("Pi-hole vrátil neplatný seznam Local DNS záznamů")
    return records


def _host_record(value: str) -> tuple[str, tuple[str, ...]] | None:
    parts = value.split()
    if len(parts) < 2:
        return None
    try:
        address = str(ipaddress.ip_address(parts[0]))
    except ValueError:
        return None
    return address, tuple(host.lower().rstrip(".") for host in parts[1:])


def _read_owned(path: str) -> list[str]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PiholeAdapterError(f"Stav Pi-hole integrace nelze načíst: {error}") from error
    records = payload.get("records", []) if isinstance(payload, dict) else []
    if not isinstance(records, list) or any(not isinstance(item, str) for item in records):
        raise PiholeAdapterError("Stav Pi-hole integrace je poškozený")
    return records


def _write_owned(path: str, records: list[str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps({"version": 1, "records": records}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(target)


def sync_pihole_records(
    records: list[dict],
    state_path: str,
    *,
    enabled: bool,
    executable="/usr/bin/pihole-FTL",
    runner=subprocess.run,
) -> dict:
    """Synchronize only records owned by Game Mover.

    Existing equal records are reused but not claimed.  A conflicting manual
    record aborts the operation instead of overwriting user configuration.
    """
    current = read_pihole_hosts(executable, runner=runner)
    previously_owned = _read_owned(state_path)
    remaining = [item for item in current if item not in previously_owned]
    desired = []
    if enabled:
        for record in records:
            name = str(record.get("name", "")).strip().lower().rstrip(".")
            try:
                address = str(ipaddress.ip_address(str(record.get("address", "")).strip()))
            except ValueError as error:
                raise PiholeAdapterError(f"Neplatná DNS adresa pro {name or 'záznam'}") from error
            desired.append((name, address))

    owned = []
    for name, address in desired:
        matches = []
        for item in remaining:
            parsed = _host_record(item)
            if parsed and name in parsed[1]:
                matches.append(parsed[0])
        if matches:
            if any(existing != address for existing in matches):
                raise PiholeAdapterError(
                    f"Pi-hole už obsahuje ruční záznam {name} s jinou adresou"
                )
            continue
        value = f"{address} {name}"
        remaining.append(value)
        owned.append(value)

    changed = remaining != current
    if changed:
        _run(
            [executable, "--config", "dns.hosts", json.dumps(remaining, separators=(",", ":"))],
            runner=runner,
        )
    _write_owned(state_path, owned)
    return {
        "changed": changed,
        "managed_records": len(owned),
        "reused_records": len(desired) - len(owned),
    }
