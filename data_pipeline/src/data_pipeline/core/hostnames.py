from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit


def normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    if not hostname or any(char.isspace() for char in hostname):
        msg = f"invalid hostname: {value!r}"
        raise ValueError(msg)
    parsed = urlsplit(f"//{hostname}")
    if parsed.hostname != hostname or parsed.port is not None or "/" in hostname:
        msg = f"expected hostname, got: {value!r}"
        raise ValueError(msg)
    return hostname


def load_hostnames(path: Path) -> frozenset[str]:
    hostnames: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if value.lower().startswith(("http://", "https://")):
            msg = f"{path}:{line_number}: expected hostname, got URL: {value!r}"
            raise ValueError(msg)
        if value.startswith("-") or ":" in value or " " in value:
            continue
        try:
            hostnames.add(normalize_hostname(value))
        except ValueError as exc:
            msg = f"{path}:{line_number}: {exc}"
            raise ValueError(msg) from exc
    return frozenset(hostnames)
