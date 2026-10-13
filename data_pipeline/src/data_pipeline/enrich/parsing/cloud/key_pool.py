from __future__ import annotations

import hashlib


class KeyPool:
    def __init__(self, keys: list[tuple[str, str]]):
        self.keys = keys
        self.exhausted: set[str] = set()

    def current(self) -> tuple[str, str]:
        for item in self.keys:
            if item[0] not in self.exhausted:
                return item
        raise RuntimeError("all LlamaParse keys are quota exhausted")

    def mark_quota_exhausted(self, label: str) -> None:
        self.exhausted.add(label)

    @staticmethod
    def fingerprint(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()[:16]
