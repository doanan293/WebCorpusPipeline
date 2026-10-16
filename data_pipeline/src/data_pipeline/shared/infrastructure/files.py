from __future__ import annotations

import os
import shutil
from pathlib import Path

from data_pipeline.shared.domain.hostnames import normalize_hostname


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


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def swap_into_place(staging: Path, output: Path) -> None:
    """Replace ``output`` with ``staging``; the old output survives until the swap succeeds."""
    previous = output.with_name(f"{output.name}.old-{os.getpid()}")
    had_output = output.exists()
    if had_output:
        output.replace(previous)
    try:
        staging.replace(output)
    except OSError:
        if had_output:
            previous.replace(output)  # put the old package back
        raise
    shutil.rmtree(previous, ignore_errors=True)
