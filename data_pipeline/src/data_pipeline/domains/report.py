from __future__ import annotations

import json
import socket  # noqa: F401
from dataclasses import asdict
from pathlib import Path

from data_pipeline.domains.probe import ProbeResult


def load_candidate_hostnames(path: Path) -> tuple[str, ...]:
    hostnames: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        hostname = raw_line.strip().lower().rstrip(".")
        if not hostname or hostname.startswith("#"):
            continue
        if hostname != "hust.edu.vn" and not hostname.endswith(".hust.edu.vn"):
            raise ValueError(f"{path}:{line_number}: hostname outside HUST scope: {hostname}")
        hostnames.add(hostname)
    return tuple(sorted(hostnames))


def render_active_domains(hostnames: list[str]) -> str:
    return "".join(f"{hostname}\n" for hostname in sorted(set(hostnames)))


def render_report(results: list[ProbeResult]) -> str:
    return "".join(
        json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
        for result in sorted(results, key=lambda item: item.hostname)
    )


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)
