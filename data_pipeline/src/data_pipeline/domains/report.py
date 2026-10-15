from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from data_pipeline.core.hostnames import load_hostnames
from data_pipeline.domains.probe import ProbeResult


def load_candidate_hostnames(path: Path) -> tuple[str, ...]:
    hostnames = load_hostnames(path)
    outside = sorted(
        host for host in hostnames if host != "hust.edu.vn" and not host.endswith(".hust.edu.vn")
    )
    if outside:
        msg = f"{path}: hostname outside HUST scope: {', '.join(outside)}"
        raise ValueError(msg)
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
