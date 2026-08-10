#!/usr/bin/env python3
"""Thread-safe progress state for long-running Game Platform operations."""

from __future__ import annotations

import threading


class OperationAlreadyRunning(RuntimeError):
    pass


class OperationRegistry:
    """Keep small, non-secret progress snapshots addressable by operation ID."""

    def __init__(self):
        self._lock = threading.Lock()
        self._operations = {}

    def begin(self, operation_id, *, kind, target_id, message):
        with self._lock:
            current = self._operations.get(operation_id, {})
            if current.get("running"):
                raise OperationAlreadyRunning("Operace už probíhá")
            self._operations[operation_id] = {
                "id": str(operation_id),
                "kind": str(kind),
                "target_id": str(target_id),
                "running": True,
                "phase": "preparing",
                "message": str(message),
                "progress": 0,
            }
            return dict(self._operations[operation_id])

    def update(self, operation_id, *, phase, message, progress):
        with self._lock:
            operation = self._operations[operation_id]
            operation.update({
                "phase": str(phase),
                "message": str(message),
                "progress": max(0, min(100, int(progress))),
            })
            return dict(operation)

    def finish(self, operation_id, *, message):
        return self._complete(operation_id, "complete", message)

    def fail(self, operation_id, *, message):
        return self._complete(operation_id, "failed", message)

    def _complete(self, operation_id, phase, message):
        with self._lock:
            operation = self._operations[operation_id]
            operation.update({
                "running": False,
                "phase": phase,
                "message": str(message),
                "progress": 100,
            })
            return dict(operation)

    def snapshot(self, operation_id):
        with self._lock:
            operation = self._operations.get(operation_id)
            return dict(operation) if operation else None

