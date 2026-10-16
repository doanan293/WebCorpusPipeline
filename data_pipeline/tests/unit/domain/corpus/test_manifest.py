from __future__ import annotations

from data_pipeline.domain.corpus.manifest import build_manifest, snapshot_id, verify_manifest


def test_verify_reports_each_problem_once() -> None:
    files = {"articles.jsonl": {"sha256": "a" * 64, "size": 3}}
    manifest = build_manifest(files, {"articles.jsonl": 1})
    assert manifest["snapshot_id"] == snapshot_id(files)
    assert "status" not in manifest

    errors = verify_manifest(
        manifest, {"articles.jsonl": ("b" * 64, 3), "notes.txt": ("c" * 64, 1)}
    )

    assert errors == ["unlisted file notes.txt", "checksum mismatch articles.jsonl"]
