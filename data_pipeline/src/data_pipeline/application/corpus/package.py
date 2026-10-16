from __future__ import annotations

from pathlib import Path
from typing import Any

from data_pipeline.application.corpus.graph import build_graph
from data_pipeline.application.corpus.ports import (
    CorpusFiles,
    EdgeIndexFactory,
    EnrichmentStatus,
    ExportDocuments,
)
from data_pipeline.domain.corpus.manifest import (
    MANIFEST,
    PAYLOAD,
    SOURCE_FILES,
    Fingerprint,
    build_manifest,
    verify_manifest,
)


def _assert_enrichment_complete(state: EnrichmentStatus, source_key: str, source: Path) -> None:
    """Refuse a release that is empty or still has unresolved enrichment work."""
    if not state.inventory_done(source_key):
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


def package_corpus(
    source: Path,
    state: EnrichmentStatus,
    output: Path,
    *,
    export_documents: ExportDocuments,
    files: CorpusFiles,
    open_index: EdgeIndexFactory,
) -> Path:
    """Build a corpus package next to ``output`` and swap it in atomically."""
    articles = files.articles_path(source)
    # The enrich work state keys its inventory by the resolved articles file.
    _assert_enrichment_complete(state, str(articles.resolve()), source)
    output = output.resolve()
    with files.staging(output) as staging:
        for name in SOURCE_FILES:
            if files.exists(articles.parent / name):
                files.copy(articles.parent / name, staging / name)
        export_documents(staging)
        build_graph(files.read_jsonl(articles), staging, open_index)
        listed: dict[str, Any] = {}
        for name in PAYLOAD:
            if files.exists(staging / name):
                sha256, size = files.fingerprint(staging / name)
                listed[name] = {"sha256": sha256, "size": size}
        counts = {
            name: files.line_count(staging / name) for name in listed if name.endswith(".jsonl")
        }
        files.write_json(staging / MANIFEST, build_manifest(listed, counts))
        files.replace(staging, output)
    return output


def verify_corpus(path: Path, files: CorpusFiles) -> dict[str, Any]:
    manifest = path / MANIFEST
    if not files.exists(manifest):
        return {"valid": False, "errors": ["manifest.json missing"]}
    try:
        value = files.read_json(manifest)
    except Exception as exc:  # noqa: BLE001 -- any unreadable manifest is reported as invalid, not raised
        return {"valid": False, "errors": [f"invalid manifest: {exc}"]}
    listed = value.get("files", {})
    present: dict[str, Fingerprint | None] = {
        name: files.fingerprint(path / name) if name in listed else None
        for name in files.names(path)
    }
    errors = verify_manifest(value, present)
    return {"valid": not errors, "snapshot_id": value.get("snapshot_id"), "errors": errors}
