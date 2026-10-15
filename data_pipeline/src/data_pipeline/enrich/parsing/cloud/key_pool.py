from __future__ import annotations

import hashlib
import threading


class KeysExhaustedError(RuntimeError):
    """Every LlamaParse key ran out of quota (or none was configured)."""


class KeyPool:
    def __init__(self, keys: list[tuple[str, str]]):
        self.keys = keys
        self.exhausted: set[str] = set()
        self._lock = threading.Lock()

    def current(self) -> tuple[str, str]:
        with self._lock:
            for item in self.keys:
                if item[0] not in self.exhausted:
                    return item
        msg = "all LlamaParse keys are quota exhausted"
        raise KeysExhaustedError(msg)

    def mark_quota_exhausted(self, label: str) -> None:
        with self._lock:
            self.exhausted.add(label)

    @staticmethod
    def fingerprint(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()[:16]
