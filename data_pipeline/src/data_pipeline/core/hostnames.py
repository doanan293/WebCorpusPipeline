from __future__ import annotations

import re
from pathlib import Path

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    labels = hostname.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):  # noqa: PLR2004 -- a name and a TLD
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
        try:
            hostnames.add(normalize_hostname(value))
        except ValueError as exc:
            msg = f"{path}:{line_number}: {exc}"
            raise ValueError(msg) from exc
    return frozenset(hostnames)
