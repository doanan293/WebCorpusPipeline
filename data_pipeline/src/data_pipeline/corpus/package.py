from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from data_pipeline.corpus.graph import build_graph

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


def _assert_enrichment_complete(db_path: Path) -> None:
    """Refuse a release while the resumable pipeline still has unresolved work."""

    conn = sqlite3.connect(db_path)
    try:
        pending = conn.execute(
            "select status,count(*) from inventory "
            "where status in ('pending','retry','in_progress','finalizing') "
            "group by status"
        ).fetchall()
        if pending:
            details = ", ".join(f"{status}={count}" for status, count in pending)
            msg = f"cannot package while downloads are pending: {details}"
            raise ValueError(msg)

        unparsed = conn.execute(
            "select count(*) from inventory i "
            "left join parse_results p on p.url=i.url "
            "where i.status='downloaded' and p.url is null"
        ).fetchone()[0]
        if unparsed:
            msg = f"cannot package while {unparsed} downloaded files are unparsed"
            raise ValueError(msg)

        blocked = conn.execute(
            "select status,count(*) from parse_results "
            "where status in ('quota_blocked','submission_unknown') group by status"
        ).fetchall()
        if blocked:
            details = ", ".join(f"{status}={count}" for status, count in blocked)
            msg = f"cannot package while cloud extraction is blocked: {details}"
            raise ValueError(msg)
    finally:
        conn.close()


def package_corpus(source: Path, enrichment: Path, output: Path) -> Path:
    source_dir = source if source.is_dir() else source.parent
    state_db = enrichment / "state.sqlite3"
    if state_db.exists():
        _assert_enrichment_complete(state_db)
    with tempfile.TemporaryDirectory(prefix="ir-corpus-") as temp:
        staging = Path(temp)
        for name in ("articles.jsonl", "urls.txt", "README.json"):
            src = source_dir / name
            if src.exists():
                shutil.copy2(src, staging / name)
        if state_db.exists():
            # Export uses the same schema without altering the source crawl.

            conn = sqlite3.connect(state_db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("select * from inventory where status='downloaded'")
            with (staging / "documents.jsonl").open("w", encoding="utf-8") as doc:
                for row in rows:
                    parsed = conn.execute(
                        "select result_json from parse_results where url=?", (row["url"],)
                    ).fetchone()
                    result = json.loads(parsed[0]) if parsed and parsed[0] else None
                    refs = [
                        dict(x)
                        for x in conn.execute(
                            "select * from refs where target_url=?", (row["url"],)
                        )
                    ]
                    value = {
                        "url": row["url"],
                        "final_url": row["final_url"],
                        "sha256": row["sha256"],
                        "mime_type": row["mime_type"],
                        "size": row["size"],
                        "extraction_status": result.get("status", "pending")
                        if result
                        else "pending",
                        "parts": result.get("parts", []) if result else [],
                        "warnings": result.get("warnings", []) if result else [],
                        "parser_versions": result.get("parser_versions", {}) if result else {},
                        "referrers": refs,
                    }
                    doc.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            conn.close()
        else:
            (staging / "documents.jsonl").write_text("")
        build_graph(source, enrichment, staging)
        report = staging / "extraction-report.json"
        exported_report = enrichment / "extraction-report.json"
        if exported_report.exists():
            shutil.copy2(exported_report, report)
        elif not report.exists():
            if state_db.exists():
                conn = sqlite3.connect(state_db)
                value = {
                    "downloaded": conn.execute(
                        "select count(*) from inventory where status='downloaded'"
                    ).fetchone()[0],
                    "parsed": conn.execute(
                        "select count(*) from parse_results where status='extracted'"
                    ).fetchone()[0],
                    "failed": conn.execute(
                        "select count(*) from failures where retry_at is null"
                    ).fetchone()[0],
                    "pending": conn.execute(
                        "select count(*) from inventory where status in "
                        "('pending','retry','in_progress','finalizing')"
                    ).fetchone()[0],
                    "quota_blocked": conn.execute(
                        "select count(*) from parse_results where status='quota_blocked'"
                    ).fetchone()[0],
                    "transferred_bytes": conn.execute(
                        "select coalesce(sum(size),0) from inventory where status='downloaded'"
                    ).fetchone()[0],
                }
                conn.close()
            else:
                value = {"status": "not_run"}
            report.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        files = {
            name: {"sha256": _sha(staging / name), "size": (staging / name).stat().st_size}
            for name in PAYLOAD
            if (staging / name).exists()
        }
        digest = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest = {
            "schema_version": "1.0",
            "snapshot_id": digest,
            "files": files,
            "counts": {},
            "status": "ready",
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(staging, output, ignore=shutil.ignore_patterns("_enrichment"))
    return output


def verify_corpus(path: Path) -> dict:
    errors = []
    manifest = path / "manifest.json"
    if not manifest.exists():
        return {"valid": False, "errors": ["manifest.json missing"]}
    try:
        value = json.loads(manifest.read_text())
    except Exception as exc:
        return {"valid": False, "errors": [f"invalid manifest: {exc}"]}
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
