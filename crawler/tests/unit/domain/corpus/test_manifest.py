from __future__ import annotations

from crawler.domain.corpus.manifest import build_manifest, snapshot_id, verify_manifest


def test_verify_reports_each_problem_once() -> None:
    files = {"articles.jsonl": {"sha256": "a" * 64, "size": 3}}
    manifest = build_manifest(files, {"articles.jsonl": 1})
    assert manifest["snapshot_id"] == snapshot_id(files)
    assert "status" not in manifest

    errors = verify_manifest(
        manifest, {"articles.jsonl": ("b" * 64, 3), "notes.txt": ("c" * 64, 1)}
    )

    assert errors == ["unlisted file notes.txt", "checksum mismatch articles.jsonl"]


def test_verify_rejects_another_schema_version() -> None:
    files = {"html.jsonl": {"sha256": "a" * 64, "size": 3}}
    manifest = {**build_manifest(files, {"html.jsonl": 1}), "schema_version": "1.0"}
    errors = verify_manifest(manifest, {"html.jsonl": ("a" * 64, 3)})
    assert errors == ["schema_version 1.0 is not 2.0"]
