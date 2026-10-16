from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from data_pipeline.corpus.graph import build_graph
from data_pipeline.enrich.application.export import export_enrichment
from data_pipeline.enrich.application.inventory import source_key
from data_pipeline.enrich.infrastructure.sqlite.work_state import WorkState
from data_pipeline.shared.infrastructure.files import swap_into_place

PAYLOAD = (
    "articles.jsonl",
    "urls.txt",
    "README.json",
    "documents.jsonl",
    "images.jsonl",
    "links.jsonl",
    "extraction-report.json",
)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _assert_enrichment_complete(state: WorkState, source: Path) -> None:
    """Refuse a release that is empty or still has unresolved enrichment work."""
    if not state.inventory_done(source_key(source)):
        msg = f"cannot package: inventory has not finished for {source}"
        raise ValueError(msg)
    summary = state.summary()
    if summary.pending:
        msg = f"cannot package while downloads are pending: {summary.pending}"
        raise ValueError(msg)
    if summary.cloud_failed or summary.quota_blocked:
        msg = (
            f"cannot package: {summary.cloud_failed} documents failed in LlamaParse and "
            f"{summary.quota_blocked} are blocked by quota; run enrich extract again"
        )
        raise ValueError(msg)
    unparsed = sum(1 for _ in state.pending_parse())
    if unparsed:
        msg = f"cannot package while {unparsed} downloaded files are unparsed"
        raise ValueError(msg)


def _line_count(path: Path) -> int:
    with path.open("rb") as stream:
        return sum(1 for _ in stream)


def package_corpus(source: Path, state: WorkState, output: Path) -> Path:
    """Build a corpus package next to ``output`` and swap it in atomically."""
    source_dir = source if source.is_dir() else source.parent
    _assert_enrichment_complete(state, source)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f"{output.name}.tmp-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o755)
    staging.chmod(0o755)
    try:
        for name in ("articles.jsonl", "urls.txt", "README.json"):
            if (source_dir / name).exists():
                shutil.copy2(source_dir / name, staging / name)
        export_enrichment(state, staging)
        build_graph(source, staging)
        files = {
            name: {"sha256": _sha(staging / name), "size": (staging / name).stat().st_size}
            for name in PAYLOAD
            if (staging / name).exists()
        }
        digest = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest: dict[str, Any] = {
            "schema_version": "1.0",
            "snapshot_id": digest,
            "files": files,
            "counts": {
                name: _line_count(staging / name) for name in files if name.endswith(".jsonl")
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        swap_into_place(staging, output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return output


def verify_corpus(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = path / "manifest.json"
    if not manifest.exists():
        return {"valid": False, "errors": ["manifest.json missing"]}
    try:
        value = json.loads(manifest.read_text())
    except Exception as exc:  # noqa: BLE001 -- any unreadable manifest is reported as invalid, not raised
        return {"valid": False, "errors": [f"invalid manifest: {exc}"]}
    listed = set(value.get("files", {})) | {"manifest.json"}
    errors.extend(
        f"unlisted file {item.name}" for item in sorted(path.iterdir()) if item.name not in listed
    )
    for name, meta in value.get("files", {}).items():
        file = path / name
        if not file.exists():
            errors.append(f"missing {name}")
            continue
        if file.stat().st_size != meta.get("size"):
            errors.append(f"size mismatch {name}")
        if _sha(file) != meta.get("sha256"):
            errors.append(f"checksum mismatch {name}")
    expected = hashlib.sha256(
        json.dumps(value.get("files", {}), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if expected != value.get("snapshot_id"):
        errors.append("snapshot_id mismatch")
    return {"valid": not errors, "snapshot_id": value.get("snapshot_id"), "errors": errors}
