from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from data_pipeline.domain.hosts.canonical import candidate_hostnames_in_scope
from data_pipeline.domain.hosts.models import ProbeResult
from data_pipeline.infrastructure.shared.files import atomic_write_text, load_hostnames


def render_active_domains(hostnames: list[str]) -> str:
    return "".join(f"{hostname}\n" for hostname in sorted(set(hostnames)))


def render_report(results: list[ProbeResult]) -> str:
    return "".join(
        json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
        for result in sorted(results, key=lambda item: item.hostname)
    )


class FileAuditReports:
    """The audit's candidate list, active list and per-host JSONL report on disk."""

    def __init__(self, all_path: Path, active_path: Path, report_path: Path) -> None:
        self.all_path = all_path
        self.active_path = active_path
        self.report_path = report_path

    def load_candidates(self) -> tuple[str, ...]:
        return candidate_hostnames_in_scope(load_hostnames(self.all_path), str(self.all_path))

    def write(
        self, all_hostnames: list[str], active: list[str], results: list[ProbeResult]
    ) -> None:
        atomic_write_text(self.all_path, "".join(f"{hostname}\n" for hostname in all_hostnames))
        atomic_write_text(self.active_path, render_active_domains(active))
        atomic_write_text(self.report_path, render_report(results))
